"""Append chapter plans after a canvas review loop; persist every chapter checkpoint."""
import asyncio
import copy
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import (Book, BookMaterialVersion, Chapter, ChapterVersion, ChapterPlanVersion,
                      PlanWritingJob, PlanWritingSettings, WritingJob, CanvasWritingJob)
from ..services import content_sha256, read_content
from ..config import settings
from . import chapter_plans as plans, canvas_writing as cw, outline

router = APIRouter(prefix='/api', tags=['Chapter plan continuation'])
ACTIVE = ('queued', 'submitting', 'running', 'stopping')


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chapter_count: int = Field(default=1, ge=1, le=100)
    workflow_id: str = Field(default='', max_length=128)
    requirements: str = Field(default='', max_length=100000)
    volume: str = Field(default='', max_length=200)
    start_position: int | None = Field(default=None, ge=1)


def require_job(db, job_id):
    j = db.get(PlanWritingJob, job_id)
    if not j:
        raise HTTPException(404, '章纲续写任务不存在')
    return j


def out(j):
    return {k: getattr(j, k) for k in ('id', 'book_id', 'status', 'message', 'target_count',
            'completed_count', 'workflow_id', 'run_id', 'created_at', 'updated_at')} | {
        'items': j.config.get('items', []), 'round': j.config.get('round', 1),
        'start_position': j.config.get('start_position'),
        'has_output': bool(j.raw_output), 'can_resume': j.status == 'paused' and not j.config.get('submission_uncertain') and not j.config.get('remote_failed')}


def ensure_idle(db, book_id, owner=None):
    if db.scalar(select(PlanWritingJob.id).where(PlanWritingJob.book_id == book_id,
            PlanWritingJob.status.in_(ACTIVE), PlanWritingJob.id != (owner or ''))):
        raise HTTPException(409, '本书已有章纲续写任务运行')
    if db.scalar(select(WritingJob.id).where(WritingJob.book_id == book_id, WritingJob.status.in_(('queued', 'running', 'cancelling')))) or db.scalar(select(CanvasWritingJob.id).where(CanvasWritingJob.book_id == book_id, CanvasWritingJob.status.in_(cw.ACTIVE))):
        raise HTTPException(409, '本书已有正文生成或审核任务运行，请等待完成')


def material_versions(db, book_id):
    rows = db.scalars(select(BookMaterialVersion).where(BookMaterialVersion.book_id == book_id)
                      .order_by(BookMaterialVersion.material_key, BookMaterialVersion.version_number.desc())).all()
    result = {}
    for row in rows:
        result.setdefault(row.material_key, row)
    return result


def source_snapshot(db, book_id, keys, requirements='', volume='', start_position=None):
    book = plans.require_book(db, book_id)
    ov = outline.latest_version(db, book_id)
    if not ov or not ov.content.strip():
        raise ValueError('请先保存非空全书大纲')
    all_plans = plans.plans(db, book_id)
    if [p.position for p in all_plans] != list(range(1, len(all_plans) + 1)):
        raise ValueError('现有章纲顺序不完整，请先整理章纲')
    position = start_position if start_position is not None else len(all_plans) + 1
    if position < 1 or position > len(all_plans) + 1:
        raise ValueError('起始章纲不能跳过尚未建立的位置')
    previous = [p for p in all_plans if p.position < position]
    plan_text = '\n\n'.join(f'第{p.position}章【计划，尚不代表已发生】\n{db.get(ChapterPlanVersion, p.current_version_id).title}\n{db.get(ChapterPlanVersion, p.current_version_id).content}' for p in previous)
    guidance = plans.guidance(db, book_id)
    if guidance:
        plan_text += '\n\n【章纲共同执行说明】\n' + guidance['content']
    target = next((p for p in all_plans if p.position == position), None)
    target_body = ''
    if target and target.chapter_id:
        chapter = db.get(Chapter, target.chapter_id)
        if chapter and chapter.current_version_id:
            target_body = read_content(db.get(ChapterVersion, chapter.current_version_id))
    if target and target_body.strip():
        old = db.get(ChapterPlanVersion, target.current_version_id)
        plan_text += f'\n\n【本次第{position}章旧章纲，供重生成比较，不是必须保留的剧情；后续旧章纲不作为承接依据】\n{old.title}\n{old.content}'
    available = material_versions(db, book_id)
    selected = []
    for key in keys:
        m = available.get(key)
        if not m or not m.content.strip():
            raise ValueError(f'所选资料缺失或为空：{key}')
        selected.append({'material_key': key, 'title': m.title, 'content': m.content})
    chapters = list(db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)))
    # The existing body can constrain future planning, but cannot rewrite the outline.
    facts = []
    for c in chapters:
        if c.chapter_number >= position:
            continue
        body = read_content(db.get(ChapterVersion, c.current_version_id)) if c.current_version_id else ''
        if not body.strip():
            continue
        summary_current = c.summary_source_version_id == c.current_version_id and c.summary.strip() and c.summary_content_sha256 == content_sha256(body)
        facts.append(f'第{c.chapter_number}章 {c.title}\n' + (c.summary if summary_current and c.chapter_number < position - 5 else body))
    task = f'{"重新生成" if target else "续写"}第{position}章章纲，仅输出这一章。' + (f'所属卷：{volume}。' if volume else '')
    if target:
        task += '\n依据当前大纲与设定重建本章，保留无冲突的有效内容。已有正文可能待修订，不得改纲迁就旧正文。保存时保留本章标识、正文绑定及旧版历史。'
    task += '\n输出 JSON：{"title":"章节名","volume":"所属卷","content":"完整章纲"}。不得一次输出多章。\n' + requirements
    outputs = {'bookInfo': f'书名：{book.title}\n简介：{book.description}', 'chapterTitle': task,
               'previousText': '\n\n'.join(facts),
               'bodyText': ('【本章已有正文，仅用于比对；与新版大纲冲突处将在正文修订中处理，不据此改纲】\n' + target_body) if target_body.strip() else '',
               'summaryText': '',
               'outlineText': ov.content, 'planText': plan_text or '尚无前文章纲。',
               'stateText': '前文章纲属于计划；仅已有正文证据可以视为已经发生。'}
    outputs['text'] = '\n\n'.join(f'【{k}】\n{v}' for k, v in outputs.items() if v)
    source = {'book_id': book_id, 'bookOutputs': outputs, 'materials': selected}
    manifest = {'hash': content_sha256(json.dumps(source, ensure_ascii=False, sort_keys=True)),
                'plans': plans.snapshot(db, book_id), 'outline_version': ov.id,
                'materials': [(k, available[k].id) for k in keys],
                'chapters': [(c.id, c.chapter_number, c.current_version_id) for c in chapters]}
    return source, json.loads(json.dumps(manifest)), position


def job_snapshot(db, j):
    start = j.config.get('start_position')
    position = start + j.completed_count if start is not None else None
    return source_snapshot(db, j.book_id, j.config['material_keys'],
                           j.config['requirements'], j.config['volume'], position)


def validate_workflow(graph, book_id):
    nodes = [n for n in graph['nodes'] if n.get('enabled') is not False]
    conditions = [n for n in nodes if n['type'] == 'review-condition']
    if len(conditions) != 1:
        raise ValueError('章纲续写工作流需要一个启用的尾部条件节点')
    sources = [n for n in nodes if n['type'] == 'chapter-context']
    if not sources:
        raise ValueError('章纲续写工作流需要书籍提取节点接收全部前文章纲和大纲')
    keys = []
    for n in nodes:
        if n['type'] not in ('text', 'llm', 'chapter-context', 'material-context', 'revision-apply', 'review-condition'):
            raise ValueError('章纲续写暂不支持此工作流中的节点类型')
        if n['type'] == 'material-context':
            if str(n['inputs'].get('bookId')) != str(book_id):
                raise ValueError('知识库节点必须选择当前书籍')
            selected = n['inputs'].get('materialKeys', [])
            if not isinstance(selected, list) or not selected or any(not isinstance(k, str) for k in selected):
                raise ValueError('知识库节点尚未选择资料')
            keys.extend(k for k in selected if k not in keys)
    return {'output_node_id': conditions[0]['id'], 'material_keys': keys,
            'input_nodes': [n['id'] for n in nodes if n['type'] in ('chapter-context', 'material-context')]}


async def default_workflow(db, book_id):
    existing = db.get(PlanWritingSettings, book_id)
    if existing:
        return existing.workflow_id
    # Reuse the author's configured model identities, never embed credentials.
    gen = (await cw.canvas('GET', f'/workflows/{settings.prose_workflow_id}')).json()
    rev = (await cw.canvas('GET', f'/workflows/{settings.review_workflow_id}')).json()
    generator = next((n for n in gen['nodes'] if n['type'] == 'llm' and n.get('enabled') is not False), None)
    reviewer = next((n for n in rev['nodes'] if n['id'] == 'final-review-llm' and n.get('enabled') is not False), None)
    reviewer = reviewer or next((n for n in rev['nodes'] if n['type'] == 'llm' and n.get('enabled') is not False), None)
    if not generator or not reviewer:
        raise ValueError('请先配置生成与审核模型，或选择已有的章纲循环工作流')
    def node(id, type, label, x, y, inputs, config=None):
        return {'id': id, 'type': type, 'label': label, 'version': 1, 'position': {'x': x, 'y': y}, 'inputs': inputs, 'config': config or {}}
    keys = [k for k, v in material_versions(db, book_id).items() if v.content.strip()]
    nodes = [node('book-source', 'chapter-context', '大纲与全部前文章纲', 0, 0, {'bookId': str(book_id), 'requireReader': True}),
             node('old-plan', 'text', '旧章纲与修改意见（首轮留空）', 0, 450, {'text': ''})]
    edges = []
    def prompt(target, sources, text):
        parts = [{'type': 'text', 'text': text}]
        for source, port in sources:
            eid = f'{source}-{target}'
            edges.append({'id': eid, 'sourceNodeId': source, 'sourceHandle': port, 'targetNodeId': target, 'targetHandle': 'prompt'})
            parts.extend([{'type': 'text', 'text': '\n\n'}, {'type': 'reference', 'edgeId': eid}])
        return {'kind': 'prompt-template', 'parts': parts}
    sources = [('book-source', 'text')]
    if keys:
        nodes.append(node('materials', 'material-context', '世界观与书籍资料', 0, 850, {'bookId': str(book_id), 'materialKeys': keys, 'requireReader': True}))
        sources.append(('materials', 'text'))
    rule = '全书大纲是最高剧情执行参考，只读。不能改纲迁就章纲。前文章纲是计划，不等于已发生事实。依据资料检查承接、主角主动行动、冲突、实际回报、人物反应、信息增量与阅读流畅度，避免强行要求每章相同结构。资料冲突须交作者决定。面向高中生和小白读者，白话、易懂、流畅优先，不使用生僻术语堆叠或说明书式计算。普通物资和灵石用概数，特殊关键物品才保留决定剧情的精确数量。'
    for id, original, x, role_sources, instruction in [
        ('generate-plan', generator, 650, [*sources, ('old-plan', 'text')], rule + '\n生成本章完整章纲。旧章纲为空时生成初稿；不为空时结合审核意见修订，保留无问题内容。遵守本章任务给定的单章 JSON 输出格式。'),
        ('review-plan', reviewer, 1250, [*sources, ('generate-plan', 'text')], rule + '\n审核最后给出的本轮章纲，核对本章编号和 JSON 格式，不为找问题而找问题。只输出 JSON：{"decision":"pass","opinions":""}。decision 为 pass / revise / needs_author；打回或需要作者决定必须给出具体 opinions；可选建议不阻断通过。')]:
        inputs = {k: copy.deepcopy(v) for k, v in original['inputs'].items() if k not in ('system', 'prompt')}
        inputs['prompt'] = prompt(id, role_sources, instruction)
        nodes.append(node(id, 'llm', '章纲生成' if id == 'generate-plan' else '章纲审核', x, 150, inputs, copy.deepcopy(original['config'])))
    nodes.append(node('condition', 'review-condition', '通过定稿 / 打回修改', 1850, 150, {'targetNodeId': 'old-plan', 'maxRevisions': 3, 'feedbackTemplate': '【上一轮章纲】\n{{draft}}\n\n【审核意见】\n{{opinions}}'}))
    for src, port in [('generate-plan', 'draft'), ('review-plan', 'review')]:
        edges.append({'id': src + '-condition', 'sourceNodeId': src, 'sourceHandle': 'text', 'targetNodeId': 'condition', 'targetHandle': port})
    for i, n in enumerate(nodes, 1):
        n['number'] = i
    graph = {'id': uuid4().hex, 'name': f'章纲续写 · {plans.require_book(db, book_id).title}'[:100], 'nodes': nodes, 'edges': edges, 'nextNodeNumber': len(nodes) + 1}
    db.commit()
    await cw.canvas('POST', '/workflows', json=graph)
    plans.lock(db, book_id)
    saved = db.get(PlanWritingSettings, book_id)
    if not saved:
        saved = PlanWritingSettings(book_id=book_id, workflow_id=graph['id'])
        db.add(saved)
    db.commit()
    return saved.workflow_id


@router.get('/books/{book_id}/plan-writing/settings')
def get_settings(book_id: int, db: Session = Depends(get_db)):
    plans.require_book(db, book_id)
    value = db.get(PlanWritingSettings, book_id)
    return {'workflow_id': value.workflow_id if value else ''}


@router.post('/books/{book_id}/plan-writing')
async def start(book_id: int, payload: Start, db: Session = Depends(get_db)):
    plans.lock(db, book_id)
    ensure_idle(db, book_id)
    try:
        source_snapshot(db, book_id, [], payload.requirements, payload.volume, payload.start_position)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    try:
        wid = payload.workflow_id or await default_workflow(db, book_id)
        response = await cw.canvas('GET', f'/workflows/{wid}')
        cfg = validate_workflow(response.json(), book_id)
        plans.lock(db, book_id)
        ensure_idle(db, book_id)
        source, manifest, position = source_snapshot(db, book_id, cfg['material_keys'], payload.requirements, payload.volume, payload.start_position)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    j = PlanWritingJob(id=uuid4().hex, book_id=book_id, target_count=payload.chapter_count,
        workflow_id=wid, workflow_etag=response.headers['etag'], source=source, manifest=manifest,
        config={**cfg, 'requirements': payload.requirements, 'volume': payload.volume, 'items': [], 'start_position': position})
    saved = db.get(PlanWritingSettings, book_id)
    if saved:
        saved.workflow_id = wid
    else:
        db.add(PlanWritingSettings(book_id=book_id, workflow_id=wid))
    db.add(j); db.commit()
    return out(j)


@router.get('/books/{book_id}/plan-writing/latest')
def latest(book_id: int, db: Session = Depends(get_db)):
    plans.require_book(db, book_id)
    j = db.scalar(select(PlanWritingJob).where(PlanWritingJob.book_id == book_id).order_by(PlanWritingJob.created_at.desc()))
    return out(j) if j else None


@router.get('/plan-writing/jobs/{job_id}')
def get(job_id: str, db: Session = Depends(get_db)):
    return out(require_job(db, job_id))


@router.get('/plan-writing/jobs/{job_id}/source')
def source(job_id: str, db: Session = Depends(get_db)):
    return require_job(db, job_id).source


@router.get('/plan-writing/jobs/{job_id}/output')
def output(job_id: str, db: Session = Depends(get_db)):
    return {'text': require_job(db, job_id).raw_output}


@router.post('/plan-writing/jobs/{job_id}/cancel')
async def cancel(job_id: str, db: Session = Depends(get_db)):
    j = require_job(db, job_id)
    if j.status in ACTIVE:
        j.status = 'stopping' if j.run_id or j.status == 'submitting' else 'paused'
        j.message = '正在停止；已保存章纲保留' if j.status == 'stopping' else '已暂停，可继续剩余章纲'
        db.commit()
        if j.run_id:
            await cw.canvas('POST', f'/runs/{j.run_id}/cancel')
    return out(j)


@router.post('/plan-writing/jobs/{job_id}/resume')
def resume(job_id: str, db: Session = Depends(get_db)):
    j = require_job(db, job_id)
    plans.lock(db, j.book_id)
    ensure_idle(db, j.book_id, j.id)
    if j.status != 'paused' or j.config.get('submission_uncertain'):
        raise HTTPException(409, '该任务不能直接继续；请核对画布运行记录')
    if j.config.get('remote_failed'):
        raise HTTPException(409, '本章未通过或模型失败，请在画布核对后重新安排续写；已完成章纲保留')
    j.status = 'queued'; j.message = '继续剩余章纲'; j.run_id = None
    j.source, j.manifest, _ = job_snapshot(db, j)
    db.commit()
    return out(j)


def save_result(db, j, run):
    node = next((n for n in run['nodes'] if n['nodeId'] == j.config['output_node_id'] and n['status'] == 'SUCCEEDED'), None)
    raw = node.get('outputs', {}).get('text') if node else None
    if not isinstance(raw, str) or not raw.strip() or run.get('loop', {}).get('status') != 'passed':
        raise ValueError('条件节点未返回审核通过的章纲，未保存')
    j.raw_output = raw
    db.commit()  # Preserve the candidate even when a source conflict prevents saving.
    cleaned = raw.strip()
    if cleaned.startswith('```') and cleaned.endswith('```'):
        cleaned = cleaned.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        fields = json.loads(cleaned)
        if not isinstance(fields, dict) or set(fields) != {'title', 'volume', 'content'}:
            raise ValueError()
        fields = plans.PlanText(**fields, status='draft').model_dump()
    except (ValueError, TypeError) as exc:
        raise ValueError('章纲格式无效，需要单章 title、volume、content；原始输出已保留') from exc
    plans.lock(db, j.book_id)
    if j.status != 'running':
        return
    _, manifest, position = job_snapshot(db, j)
    if manifest != j.manifest:
        raise ValueError('生成期间大纲、章纲或资料已变化，候选章纲已保留，请核对后重新生成')
    p = next((p for p in plans.plans(db, j.book_id) if p.position == position), None)
    note = f'章纲{"重生成" if p else "续写"}任务 {j.id}；审核通过；画布运行 {j.run_id}'
    if p:
        plans.add_version(db, p, fields, source='workflow', note=note)
    else:
        p = plans.create_plan(db, j.book_id, position, fields, source='workflow', note=note)
    j.completed_count += 1
    j.config = {**j.config, 'items': [*j.config['items'], {'plan_id': p.id, 'position': position, 'title': fields['title'], 'run_id': j.run_id}]}
    j.run_id = None
    j.status = 'completed' if j.completed_count == j.target_count else 'queued'
    j.message = f'已完成 {j.completed_count}/{j.target_count} 份章纲'
    if j.status == 'queued':
        j.source, j.manifest, _ = job_snapshot(db, j)
    db.commit()  # Append and advance the checkpoint in the same transaction.


async def tick(job_id):
    with SessionLocal() as db:
        j = require_job(db, job_id)
        if j.status == 'queued':
            _, manifest, position = job_snapshot(db, j)
            if manifest != j.manifest:
                raise ValueError('资料已变化，请核对后重新安排续写')
            j.status = 'submitting'; j.message = f'正在生成第 {position} 章章纲'
            db.commit()
            run = (await cw.canvas('POST', '/runs', json={'workflowId': j.workflow_id, 'expectedVersion': j.workflow_etag,
                'inputOverrides': {key: {'bookId': str(j.book_id), 'sourceRequestId': 'plan:' + j.id} for key in j.config['input_nodes']}})).json()
            db.expire_all()
            j.run_id = run['id']
            if j.status == 'stopping':
                db.commit(); await cw.canvas('POST', f'/runs/{j.run_id}/cancel'); return
            j.status = 'running'; db.commit()
        elif j.status in ('running', 'stopping') and j.run_id:
            run = (await cw.canvas('GET', f'/runs/{j.run_id}')).json()
            db.expire_all()
            if j.status == 'stopping':
                if run['status'] not in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
                    await cw.canvas('POST', f'/runs/{j.run_id}/cancel')
                    return
                j.status = 'paused'; j.message = '已暂停，已完成章纲保留'; db.commit(); return
            j.config = {**j.config, 'round': run.get('loop', {}).get('round', 1)}
            if run['status'] == 'SUCCEEDED':
                save_result(db, j, run)
            elif run['status'] in ('FAILED', 'CANCELLED'):
                j.raw_output = json.dumps(run.get('loop', {}).get('history', []), ensure_ascii=False)
                j.config = {**j.config, 'remote_failed': True}
                j.status = 'paused'; j.message = '本章未完成：' + '；'.join(n['error']['message'] for n in run['nodes'] if n.get('error'))
                db.commit()
            else:
                j.message = f"已完成 {j.completed_count}/{j.target_count} 章，当前章第 {j.config['round']} 轮生成 / 审核"
                db.commit()


def recover_interrupted():
    with SessionLocal() as db:
        for j in db.scalars(select(PlanWritingJob).where(PlanWritingJob.status.in_(('submitting', 'queued', 'stopping')))):
            if j.status == 'stopping' and j.run_id:
                continue
            if j.status in ('submitting', 'stopping'):
                j.config = {**j.config, 'submission_uncertain': True}
                j.message = '提交结果未确认，请核对画布记录；不会自动重投'
            else:
                j.message = '服务已重启，可继续剩余章纲'
            j.status = 'paused'
        db.commit()


async def worker():
    while True:
        with SessionLocal() as db:
            ids = list(db.scalars(select(PlanWritingJob.id).where(PlanWritingJob.status.in_(ACTIVE))))
        for id in ids:
            try:
                await tick(id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with SessionLocal() as db:
                    j = db.get(PlanWritingJob, id)
                    if not j:
                        continue
                    if isinstance(exc, cw.CanvasUnavailable) and j.run_id:
                        j.message = '画布连接暂不可用，保留原运行并重查'
                    else:
                        if j.status in ('submitting', 'stopping') and not j.run_id:
                            j.config = {**j.config, 'submission_uncertain': True}
                        j.status = 'paused'
                        j.message = str(exc) if isinstance(exc, (ValueError, HTTPException)) else '章纲续写失败，请核对资料与画布记录'
                    db.commit()
        await asyncio.sleep(2)
