"""Run the editable AI canvas, then atomically save its designated prose output."""
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..models import CanvasWritingJob, Chapter, ChapterVersion, ChapterPlan, ChapterPlanVersion, ChapterPlanUse, WritingJob, CurrentStateVersion, CanvasWritingSettings
from ..services import read_content, content_sha256, count_words, next_version_number
from ..llm_failover import snapshot_chain, run_with_fallback
from . import chapter_plans, current_state, materials
from .summary import update_summary

router = APIRouter(prefix="/api", tags=["Canvas prose workflow"])
ACTIVE = ("queued", "preparing", "submitting", "running", "archiving")


class CanvasUnavailable(ValueError):
    pass


async def canvas(method, path, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, settings.canvas_api_url.rstrip('/') + path, **kwargs)
    except httpx.RequestError as exc:
        raise CanvasUnavailable("无法连接画布服务，请检查 3010 是否运行。") from exc
    if response.status_code >= 500:
        raise CanvasUnavailable(f"画布服务暂不可用（HTTP {response.status_code}），保留运行记录。")
    if response.is_error:
        # Canvas errors do not contain credentials; retain actionable validation details.
        raise ValueError(f"画布服务 HTTP {response.status_code}：{response.text[:1200]}")
    return response


def job_out(j):
    if not j:
        return None
    return {key: getattr(j, key) for key in ('id', 'book_id', 'chapter_id', 'status', 'message',
        'workflow_id', 'run_id', 'saved_version_id', 'created_at', 'updated_at')} | {'has_output': bool(j.raw_output), 'node_progress': j.config.get('node_progress', []), 'logs': j.config.get('logs', []),
        'snapshot': j.source.get('snapshot', {}) if isinstance(j.source, dict) else {}}


def require_job(db, job_id):
    j = db.get(CanvasWritingJob, job_id)
    if not j:
        raise HTTPException(404, "工作流任务不存在")
    return j


def bound_plan(db, chapter):
    p = db.scalar(select(ChapterPlan).where(ChapterPlan.book_id == chapter.book_id, ChapterPlan.chapter_id == chapter.id))
    if not p:
        raise HTTPException(409, "当前正文尚未绑定章纲，请先在章纲页手动绑定。")
    v = db.get(ChapterPlanVersion, p.current_version_id)
    return p, v


def sources(db, j):
    c = db.get(Chapter, j.chapter_id)
    if not c or c.current_version_id != j.base_version_id:
        raise HTTPException(409, "当前正文版本已变化，结果不会覆盖，请核对后重新生成。")
    p, v = bound_plan(db, c)
    prior = db.scalars(select(Chapter).where(Chapter.book_id == j.book_id, Chapter.chapter_number < c.chapter_number)
                       .order_by(Chapter.chapter_number)).all()
    if [x.chapter_number for x in prior] != list(range(1, c.chapter_number)) or any(x.word_count == 0 for x in prior):
        raise HTTPException(409, "前文存在缺章或空白正文，请先完成前面的章节。")
    manifest, context = current_state.snapshot(db, j.book_id, before_chapter_number=c.chapter_number)
    selected = []
    for key in j.config['material_keys']:
        m = materials.latest(db, j.book_id, key)
        if not m or not m.content.strip():
            raise HTTPException(409, f"画布选中的资料缺失或为空：{key}")
        selected.append({'material_key': key, 'title': m.title, 'content': m.content, 'version_id': m.id, 'version_number': m.version_number})
    g = chapter_plans.guidance(db, j.book_id)
    manifest.update(target={'id': c.id, 'number': c.chapter_number, 'title': c.title, 'version': c.current_version_id},
        plan={'id': p.id, 'version': v.id}, guidance=g,
        materials=[{'key': m['material_key'], 'version': m['version_id']} for m in selected])
    manifest['writing_rules'] = [{k: m[k] for k in ('material_key', 'title', 'version_number')} |
        {'content_sha256': content_sha256(m['content'])} for m in selected]
    context['writing_rules'] = selected
    # Persisted JSON and Python tuples must compare the same after a restart.
    return json.loads(json.dumps(manifest, ensure_ascii=False)), context, selected, p, v


TEXT_PORTS = {
    'text': ['text'], 'llm': ['text'], 'material-context': ['text'],
    'revision-apply': ['text', 'report'],
    'chapter-context': ['text', 'bookInfo', 'chapterTitle', 'previousText', 'bodyText',
                        'summaryText', 'outlineText', 'planText', 'stateText'],
}


def validate_graph(graph, book_id, output_node_id='final-prose', output_port='text', *, reader_required=True, gemini_only=True):
    nodes = {n['id']: n for n in graph['nodes'] if n.get('enabled') is not False}
    out = nodes.get(output_node_id)
    if not out or output_port not in TEXT_PORTS.get(out['type'], []):
        raise HTTPException(409, '所选正文输出已删除、禁用或不再提供文本，请重新配置工作流。')
    if not any(n['type'] == 'chapter-context' for n in nodes.values()):
        raise HTTPException(409, '正文工作流需要至少一个书籍提取节点接收当前章节资料。')
    keys, input_nodes = [], []
    for n in nodes.values():
        if n['type'] not in TEXT_PORTS:
            raise HTTPException(409, f"工作流节点「{n.get('label') or n['id']}」类型 {n['type']} 暂不支持，任务尚未启动。")
        if gemini_only and n['type'] == 'llm' and not str(n['inputs'].get('model', '')).lower().startswith('gemini'):
            raise HTTPException(409, '正文流程固定使用 Gemini，不会切换备用模型。')
        if n['type'] in ('chapter-context', 'material-context'):
            input_nodes.append(n['id'])
        if n['type'] == 'material-context':
            if str(n['inputs'].get('bookId')) != str(book_id):
                raise HTTPException(409, '知识库节点选择的书籍与当前书籍不同，请在画布选择本书资料。')
            selected = n['inputs'].get('materialKeys')
            if not isinstance(selected, list) or not selected or any(not isinstance(k, str) for k in selected) or len(set(selected)) != len(selected):
                raise HTTPException(409, '请在知识库节点选择有效资料。')
            keys.extend(k for k in selected if k not in keys)
    unchecked = [n for n in nodes.values() if n['id'] in input_nodes and
                 not (n['inputs'].get('requireReader') is True or n['inputs'].get('sourceRequestId') == 'from-reader')]
    if reader_required and unchecked:
        names = '、'.join(f"「{n.get('label') or n['id']}」（{n['id']}）" for n in unchecked)
        raise HTTPException(409, f'请在工作流的 {names} 勾选“仅从正文页传入”并保存，再从正文页生成。任务尚未启动。')
    return {'material_keys': keys, 'input_nodes': input_nodes, 'output_node_id': output_node_id,
            'output_port': output_port, 'node_labels': {n['id']: n.get('label', n['id']) for n in nodes.values()}}


@router.get('/canvas-writing/workflows')
async def workflow_choices():
    try:
        result = (await canvas('GET', '/workflows')).json()
        return [{'id': w['id'], 'name': w['name']} for w in result['items']]
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get('/canvas-writing/workflows/{workflow_id}/outputs')
async def workflow_outputs(workflow_id: str):
    try:
        graph = (await canvas('GET', f'/workflows/{workflow_id}')).json()
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc
    return [{'node_id': n['id'], 'label': n.get('label') or n['type'], 'port': port}
            for n in graph['nodes'] if n.get('enabled') is not False
            for port in TEXT_PORTS.get(n['type'], [])]


@router.get('/books/{book_id}/workflow-writing/settings')
def get_workflow_settings(book_id: int, db: Session = Depends(get_db)):
    chapter_plans.require_book(db, book_id)
    row = db.get(CanvasWritingSettings, book_id)
    return {'workflow_id': row.workflow_id if row else settings.prose_workflow_id,
            'output_node_id': row.output_node_id if row else 'final-prose',
            'output_port': row.output_port if row else 'text', 'revision': row.revision if row else 0}


class WorkflowSettingsSave(BaseModel):
    workflow_id: str
    output_node_id: str
    output_port: str = 'text'
    base_revision: int


@router.put('/books/{book_id}/workflow-writing/settings')
async def save_workflow_settings(book_id: int, payload: WorkflowSettingsSave, db: Session = Depends(get_db)):
    try:
        graph = (await canvas('GET', f'/workflows/{payload.workflow_id}')).json()
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc
    validate_graph(graph, book_id, payload.output_node_id, payload.output_port)
    chapter_plans.lock(db, book_id)
    row = db.get(CanvasWritingSettings, book_id)
    if payload.base_revision != (row.revision if row else 0):
        raise HTTPException(409, '工作流设置已更新，请重新打开设置。')
    if not row:
        row = CanvasWritingSettings(book_id=book_id, revision=0)
        db.add(row)
    row.workflow_id, row.output_node_id, row.output_port = payload.workflow_id, payload.output_node_id, payload.output_port
    row.revision += 1
    db.commit()
    return get_workflow_settings(book_id, db)


@router.get('/books/{book_id}/chapters/{chapter_number}/writing-context')
def standalone_context(book_id: int, chapter_number: int, db: Session = Depends(get_db)):
    # Read-only standalone extraction: no model calls, no chapter creation or saving.
    if chapter_number < 1:
        raise HTTPException(400, '目标章节必须大于零')
    manifest, ctx = current_state.snapshot(db, book_id, before_chapter_number=chapter_number)
    c = db.scalar(select(Chapter).where(Chapter.book_id == book_id, Chapter.chapter_number == chapter_number))
    p = db.scalar(select(ChapterPlan).where(ChapterPlan.book_id == book_id, ChapterPlan.chapter_id == c.id)) if c else None
    if not p:
        p = db.scalar(select(ChapterPlan).where(ChapterPlan.book_id == book_id, ChapterPlan.position == chapter_number, ChapterPlan.chapter_id.is_(None)))
    v = db.get(ChapterPlanVersion, p.current_version_id) if p else None
    g = chapter_plans.guidance(db, book_id)
    plan_text = f"第{chapter_number}章 {v.title}（{'已确认' if v.status == 'confirmed' else '草稿，尚未确认'}）\n{v.content}" if v else '目标章尚无章纲'
    if g:
        plan_text += '\n\n共同执行说明：\n' + g['content']
    state = next((v for v in db.scalars(select(CurrentStateVersion).where(CurrentStateVersion.book_id == book_id)
        .order_by(CurrentStateVersion.version_number.desc())) if v.source_manifest == manifest and v.content.strip()), None)
    state_text = state.content if state else ('开篇基准：无前文，所有计划尚未发生。' if not ctx['recent_chapters'] else
        '暂无与本章前文匹配的最新速查；请先更新前文梗概与速查，或从正文页调用以自动补齐。不能将旧版或未来事件当作事实。')
    previous_text = '\n\n'.join(f"第{x['chapter_number']}章 {x['title']}\n{x['content']}" for x in ctx['recent_chapters']) or '无前文（第一章）'
    summary_text = '\n\n'.join(f"第{x['chapter_number']}章 {x['title']}\n{x['summary']}" for x in ctx['all_summaries']) or '无前文梗概（第一章）'
    outline_text = ctx['outline']['content'] if ctx['outline'] else '暂无已保存大纲'
    current_body = read_content(db.get(ChapterVersion, c.current_version_id)).strip() if c and c.current_version_id else ''
    return {'bookInfo': f"书名：{ctx['book_title']}\n本次仅准备第{chapter_number}章",
            'chapterTitle': v.title if v else f'第{chapter_number}章', 'previousText': previous_text,
            'bodyText': current_body or '本章暂无正文（待生成）', 'summaryText': summary_text, 'outlineText': outline_text,
            'planText': plan_text, 'stateText': state_text,
            'stateVersionId': state.id if state else None, 'stateManifest': manifest}


class Start(BaseModel):
    base_version_id: int
    replace_existing: bool = False


@router.post('/chapters/{chapter_id}/workflow-writing')
async def start(chapter_id: int, payload: Start, db: Session = Depends(get_db)):
    return await start_job(chapter_id, payload, db)


async def start_job(chapter_id, payload, db, *, frozen=None, owner_id=None, commit=True):
    c = db.get(Chapter, chapter_id)
    if not c:
        raise HTTPException(404, "章节不存在")
    selected_workflow = frozen or get_workflow_settings(c.book_id, db)
    if frozen:
        config = dict(frozen['config'])
        etag = frozen['etag']
    else:
        try:
            response = await canvas('GET', f"/workflows/{selected_workflow['workflow_id']}")
        except (ValueError, httpx.RequestError) as exc:
            raise HTTPException(502, str(exc)) from exc
        config = validate_graph(response.json(), c.book_id, selected_workflow['output_node_id'], selected_workflow['output_port'])
        etag = response.headers['etag']
    chapter_plans.lock(db, c.book_id)
    c = db.get(Chapter, chapter_id)
    if c.current_version_id != payload.base_version_id:
        raise HTTPException(409, "正文已更新，请重新载入。")
    if c.word_count and not payload.replace_existing:
        raise HTTPException(409, "已有正文，请明确选择生成新版本；原版本会保留。")
    from ..models import PlanWritingJob
    if db.scalar(select(PlanWritingJob.id).where(PlanWritingJob.book_id == c.book_id, PlanWritingJob.status.in_(('queued', 'submitting', 'running', 'stopping')))):
        raise HTTPException(409, '本书正在续写章纲，请等待完成')
    if db.scalar(select(CanvasWritingJob.id).where(CanvasWritingJob.book_id == c.book_id, CanvasWritingJob.status.in_(ACTIVE))) or db.scalar(
        select(WritingJob.id).where(WritingJob.book_id == c.book_id, WritingJob.status.in_(['queued', 'running', 'cancelling']), WritingJob.id != (owner_id or -1))):
        raise HTTPException(409, "本书已有正文任务运行，请等待完成。")
    if owner_id:
        config['continuous_job_id'] = owner_id
    j = CanvasWritingJob(id=uuid4().hex, book_id=c.book_id, chapter_id=c.id, base_version_id=c.current_version_id,
        workflow_id=selected_workflow['workflow_id'], workflow_etag=etag, config=config,
        status='queued', source={}, manifest={}, raw_output='')
    sources(db, j)  # Validate before creating a task or spending model tokens.
    db.add(j)
    j.message = '任务已创建，等待准备资料'
    append_log(j, j.message, 'queued')
    db.flush()
    if commit:
        db.commit()
    return job_out(j)


@router.get('/books/{book_id}/workflow-writing/latest')
def latest_job(book_id: int, db: Session = Depends(get_db)):
    j = db.scalar(select(CanvasWritingJob).where(CanvasWritingJob.book_id == book_id).order_by(CanvasWritingJob.created_at.desc()))
    return job_out(j) if j else None


@router.get('/chapters/{chapter_id}/workflow-writing/snapshots')
def chapter_snapshots(chapter_id: int, db: Session = Depends(get_db)):
    """List durable frozen inputs kept for this chapter's generation history."""
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, '章节不存在')
    jobs = db.scalars(select(CanvasWritingJob).where(CanvasWritingJob.chapter_id == chapter_id)
                      .order_by(CanvasWritingJob.created_at.desc())).all()
    return [{'id': j.id, 'status': j.status, 'created_at': j.created_at.isoformat(),
             'updated_at': j.updated_at.isoformat(), 'base_version_id': j.base_version_id,
             'saved_version_id': j.saved_version_id, 'workflow_id': j.workflow_id,
             'snapshot': j.source.get('snapshot', {}) if isinstance(j.source, dict) else {},
             'has_source': bool(j.source), 'manifest': j.manifest} for j in jobs]


@router.get('/workflow-writing/jobs/{job_id}')
def detail(job_id: str, db: Session = Depends(get_db)):
    return job_out(require_job(db, job_id))


@router.get('/workflow-writing/jobs/{job_id}/output')
def output(job_id: str, db: Session = Depends(get_db)):
    return {'text': require_job(db, job_id).raw_output}


@router.get('/workflow-writing/jobs/{job_id}/source')
def frozen_source(job_id: str, db: Session = Depends(get_db)):
    j = require_job(db, job_id)
    if not j.source:
        raise HTTPException(409, "资料尚未准备完成")
    return j.source


@router.post('/workflow-writing/jobs/{job_id}/cancel')
async def cancel(job_id: str, db: Session = Depends(get_db)):
    j = require_job(db, job_id)
    if j.status in ACTIVE:
        mark(db, j, 'cancelled', '已停止；已保存正文和历史版本保留。')
        if j.run_id:
            await canvas('POST', f'/runs/{j.run_id}/cancel')
    return job_out(j)


def append_log(j, text, key):
    logs = j.config.get('logs', [])
    if logs and logs[-1].get('key') == key:
        return
    entry = {'id': logs[-1]['id'] + 1 if logs else 1,
             'time': datetime.now(timezone.utc).isoformat(), 'text': text, 'key': key}
    j.config = {**j.config, 'logs': [*logs, entry][-100:]}


def mark(db, j, status, message):
    j.status, j.message = status, message
    append_log(j, message, f'{status}:{message}')
    db.commit()


async def prepare(db, j):
    manifest, ctx, selected, p, v = sources(db, j)
    missing = ctx['missing_summary_chapter_ids'] + ctx['stale_summary_chapter_ids']
    if missing:
        mark(db, j, 'preparing', '正在补齐前文梗概')
        saved_chain = j.config.get('continuous_memory', {}).get('batch_summary')
        result = await update_summary(missing[0], db, **({'config': {'_chain': saved_chain}} if saved_chain else {}))
        if not result.updated:
            raise ValueError(result.message)
        return
    if not ctx['outline'] or not ctx['outline']['content'].strip():
        raise ValueError('大纲为空，请先保存全书大纲。')
    c = db.get(Chapter, j.chapter_id)
    if not c:
        raise ValueError('目标章节不存在，无法准备正文资料。')
    mark(db, j, 'preparing', '正在核对并整理本章开始前的状态')
    state_text = '开篇基准：本书尚无前文，全部章纲和大纲事件均为未来计划。按前置设定的开篇状态写作。'
    state_version_id = None
    # State provenance covers only preceding chapters, never the draft being generated.
    # `sources()` adds target/plan/material details for the prose job.  The
    # current-state generator owns a separate, chapter-scoped manifest; use
    # the same raw manifest as the state page so a saved state can be matched
    # again by the reader and by a later rewrite.
    state_manifest, _ = current_state.snapshot(db, j.book_id, before_chapter_number=c.chapter_number)
    saved_state = next((s for s in db.scalars(select(CurrentStateVersion).where(
        CurrentStateVersion.book_id == j.book_id).order_by(CurrentStateVersion.version_number.desc()))
        if s.source_manifest == state_manifest and s.content.strip()), None)
    if saved_state:
        state_text = saved_state.content
        state_version_id = saved_state.id
    elif ctx['recent_chapters']:
        previous_state = current_state.latest(db, j.book_id)
        state_base = previous_state.id if previous_state else None
        chain = j.config.get('continuous_memory', {}).get('current_state') or snapshot_chain(db, 'current_state')
        prompt = current_state.get_settings(j.book_id, db)['prompt'] + '\n\n' + current_state.OUTLINE_GUIDANCE
        state_text, actual = await run_with_fallback(chain, 'current_state', lambda provider: current_state.call_model(provider, prompt, ctx))
        current_state.lock_book(db, j.book_id)
        if j.status == 'cancelled':
            return
        if sources(db, j)[0] != manifest:
            raise HTTPException(409, '整理速查期间来源已变化，请核对后重试。')
        saved = current_state.append(db, j.book_id, state_base, state_text, state_manifest,
            source='llm', model=actual.model, prompt=prompt,
            note=f'正文工作流前置整理：仅截至第{db.get(Chapter, j.chapter_id).chapter_number - 1}章，不包含本次待生成正文')
        state_version_id = saved.get('id') if isinstance(saved, dict) else None
    db.expire_all()
    if j.status == 'cancelled':
        return
    if sources(db, j)[0] != manifest:
        raise HTTPException(409, '准备资料期间来源已变化，请核对后重试。')
    c = db.get(Chapter, j.chapter_id)
    target_body = read_content(db.get(ChapterVersion, c.current_version_id)).strip() if c.current_version_id else ''
    body_text = target_body or '本章暂无正文（待生成）'
    previous_text = '\n\n'.join(f"第{x['chapter_number']}章 {x['title']}\n{x['content']}" for x in ctx['recent_chapters']) or '无前文（第一章）'
    summary_text = '\n\n'.join(f"第{x['chapter_number']}章 {x['title']}\n{x['summary']}" for x in ctx['all_summaries']) or '无前文梗概（第一章）'
    guidance = chapter_plans.guidance(db, j.book_id)
    plan_text = f"第{c.chapter_number}章 {v.title}\n所属卷：{v.volume}\n{v.content}\n\n共同执行说明：\n" + (guidance['content'] if guidance else '无')
    j.source = {'book_id': j.book_id, 'snapshot': {'chapter_number': c.chapter_number,
        'state_version_id': state_version_id, 'state_scope': f'仅截至第{c.chapter_number - 1}章'}, 'materials': selected, 'bookOutputs': {
        'text': f'{plan_text}\n\n{state_text}', 'bookInfo': f"书名：{ctx['book_title']}\n本次仅生成第{c.chapter_number}章",
        'chapterTitle': v.title, 'previousText': previous_text, 'bodyText': body_text, 'summaryText': summary_text,
        'outlineText': ctx['outline']['content'], 'planText': plan_text, 'stateText': state_text}}
    j.manifest = manifest
    mark(db, j, 'submitting', '资料已冻结，正在调用生成正文画布')
    run = (await canvas('POST', '/runs', json={'workflowId': j.workflow_id, 'expectedVersion': j.workflow_etag,
        'inputOverrides': {key: {'bookId': str(j.book_id), 'sourceRequestId': j.id}
                           for key in j.config.get('input_nodes', ['book-source', 'materials-source'])}})).json()
    db.expire_all()
    j.run_id = run['id']
    if j.status == 'cancelled':
        db.commit(); await canvas('POST', f"/runs/{j.run_id}/cancel"); return
    mark(db, j, 'running', '画布正在提取资料、拼装提示词并调用 Gemini')


def save_output(db, j, raw):
    from .writing_jobs import parse_chapter_text
    j.raw_output = raw
    db.commit()  # Preserve completed model text even when source checks fail.
    title, content = parse_chapter_text(raw)
    chapter_plans.lock(db, j.book_id)
    if j.status != 'running' or j.saved_version_id:
        return
    if sources(db, j)[0] != j.manifest:
        raise HTTPException(409, '生成期间章纲、大纲、资料或前文已变化，输出已保留，未覆盖正文。')
    c = db.get(Chapter, j.chapter_id)
    p, _ = bound_plan(db, c)
    version = ChapterVersion(chapter_id=c.id, version_number=next_version_number(db, c.id),
        content_path='', content_text=content, word_count=count_words(content), source='workflow',
        note=f'画布 {j.workflow_id}，运行 {j.run_id}，章纲版本 {p.current_version_id}')
    db.add(version); db.flush()
    c.current_version_id, c.word_count, c.title = version.id, version.word_count, title
    c.status = 'draft'
    db.add(ChapterPlanUse(plan_version_id=p.current_version_id, chapter_version_id=version.id))
    j.saved_version_id = version.id
    mark(db, j, 'completed', '正文已保存，待审阅修改；下次生成前再更新梗概与速查。')


async def tick(job_id):
    from ..llm_failover import writing_job_context
    with SessionLocal() as db:
        owner = require_job(db, job_id).config.get('continuous_job_id')
    token = writing_job_context.set(owner)
    try:
        await _tick(job_id)
    finally:
        writing_job_context.reset(token)


async def _tick(job_id):
    with SessionLocal() as db:
        j = require_job(db, job_id)
        if j.config.get('continuous_job_id') and j.status in ('queued', 'preparing'):
            parent = db.get(WritingJob, j.config['continuous_job_id'])
            if not parent or parent.status != 'running':
                return
        if j.config.get('kind') in ('review', 'revision'):
            from .chapter_review import tick_review
            await tick_review(db, j)
            return
        if j.status in ('queued', 'preparing'):
            from .writing_jobs import other_generation
            if other_generation(db, j.book_id):
                mark(db, j, j.status, '等待本书正在运行的梗概或速查任务完成')
                return
        if j.status in ('queued', 'preparing'):
            await prepare(db, j)
        elif j.status == 'running':
            run = (await canvas('GET', f'/runs/{j.run_id}')).json()
            db.expire_all()
            if j.status != 'running':
                return
            progress = [{'node_id': n['nodeId'], 'label': j.config.get('node_labels', {}).get(n['nodeId'], n['nodeId']), 'status': n['status']} for n in run['nodes']]
            if progress != j.config.get('node_progress'):
                previous = {n.get('node_id'): n['status'] for n in j.config.get('node_progress', [])}
                labels = {'SUCCEEDED': '完成', 'RUNNING': '运行中', 'PENDING': '等待', 'READY': '就绪',
                          'FAILED': '失败', 'CANCELLED': '停止', 'WAITING': '等待返回'}
                for n in progress:
                    if previous.get(n['node_id']) != n['status']:
                        append_log(j, f"{n['label']} · {labels.get(n['status'], n['status'])}", f"node:{n['node_id']}:{n['status']}")
                j.config = {**j.config, 'node_progress': progress}
                j.message = '画布运行中：' + ('、'.join(x['label'] for x in progress if x['status'] in ('RUNNING', 'WAITING')) or '等待下一节点')
                db.commit()
            if run['status'] in ('FAILED', 'CANCELLED'):
                j.config = {**j.config, 'remote_terminal': True}
                db.commit()
                errors = [n.get('error', {}).get('message', '') for n in run['nodes'] if n.get('error')]
                raise ValueError('画布执行未完成：' + '；'.join(errors))
            if run['status'] == 'SUCCEEDED':
                node = next((n for n in run['nodes'] if n['nodeId'] == j.config.get('output_node_id', 'final-prose') and n['status'] == 'SUCCEEDED'), None)
                raw = node.get('outputs', {}).get(j.config.get('output_port', 'text')) if node else None
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError('画布最终正文节点未返回有效文本')
                save_output(db, j, raw)
        elif j.status == 'archiving':
            # Legacy saved jobs must finish without starting any post-generation LLM calls.
            mark(db, j, 'completed', '正文已保存，待审阅修改；下次生成前再更新梗概与速查。')


def recover_interrupted():
    with SessionLocal() as db:
        for j in db.scalars(select(CanvasWritingJob).where(CanvasWritingJob.status == 'submitting')):
            j.config = {**j.config, 'submission_uncertain': True}
            mark(db, j, 'failed', '画布提交结果未确认。请先查看画布运行记录，避免重复调用模型。')


async def worker():
    while True:
        with SessionLocal() as db:
            ids = list(db.scalars(select(CanvasWritingJob.id).where(CanvasWritingJob.status.in_(ACTIVE))))
        for job_id in ids:
            try:
                await tick(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with SessionLocal() as db:
                    j = db.get(CanvasWritingJob, job_id)
                    if j and j.status != 'cancelled':
                        if isinstance(exc, CanvasUnavailable) and j.status == 'running':
                            mark(db, j, j.status, '暂时无法读取画布进度，将重新查询原运行；不会重复调用模型。')
                            continue
                        if j.status == 'submitting':
                            j.config = {**j.config, 'submission_uncertain': True}
                        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                        mark(db, j, 'conflict' if isinstance(exc, HTTPException) and exc.status_code == 409 else 'failed', message[:2000])
        await asyncio.sleep(2)
