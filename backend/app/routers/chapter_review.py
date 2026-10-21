"""Single-chapter review and explicit revision through the saved canvas workflows."""
import json
import re
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import CanvasWritingJob, Chapter, ChapterVersion, ChapterPlan, ChapterPlanVersion, WritingJob
from ..services import read_content, serialize_chapter, content_sha256, count_words, next_version_number
from . import canvas_writing as cw, chapter_plans, current_state, materials

router = APIRouter(prefix='/api', tags=['Chapter review'])
DEFAULT_DECISIONS = '''作者通过正文页明确点击修订，同意按以下默认规则执行当前可编辑主编意见：
确定问题默认采纳；可选建议仅在收益明确且不损伤保护项时采纳，否则保留原文。
Q项没有明确裁定时不擅自决定；K项只作为保护边界。不扩写剧情或新增事实。
未填写额外意见不表示拒绝全部修改。当前原稿与审稿来源版本已由应用核对。
遵守当前工作流的输出协议：全文修订工作流结合原稿、审核意见、采纳项和上下文重新输出本章完整候选正文，可调整叙述与分段以解决已采纳问题，保留章纲目标、关键事实、回报、伏笔及前后衔接；如用户另选局部编辑工作流，则按其JSON替换协议执行。完整正文首行为章节名，不把复核报告写入正文，不宣称已人工确认。'''


def revision_text(raw):
    from .writing_jobs import parse_chapter_text
    title, _ = parse_chapter_text(raw)
    # Validate the title/body contract, but do not reformat every untouched paragraph.
    text = raw.lstrip('\ufeff \t\r\n').rstrip('\r\n')
    fenced = re.fullmatch(r'```(?:json|text|plaintext|markdown|md)?[ \t]*\r?\n([\s\S]*?)\r?\n```', text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    content = json.loads(text)['content'] if text.startswith('{') else text.split('\n', 1)[1]
    return title, content.strip('\r\n')


class ReviewStart(BaseModel):
    base_version_id: int


class ReviseStart(ReviewStart):
    review_job_id: str
    opinions: str = Field(min_length=1, max_length=500000)
    decisions: str = Field(default='', max_length=100000)


class ConfirmBody(ReviewStart):
    plan_version_id: int | None = None


@router.post('/chapters/{chapter_id}/confirm')
def confirm_body(chapter_id: int, payload: ConfirmBody, db: Session = Depends(get_db)):
    c = db.get(Chapter, chapter_id)
    if not c:
        raise HTTPException(404, '章节不存在')
    chapter_plans.lock(db, c.book_id)
    if c.current_version_id != payload.base_version_id:
        raise HTTPException(409, '正文已变化，请重新读取后确认。')
    if not read_content(db.get(ChapterVersion, c.current_version_id)).strip():
        raise HTTPException(409, '空白正文不能确认为正式稿。')
    p = db.scalar(select(ChapterPlan).where(ChapterPlan.chapter_id == c.id, ChapterPlan.book_id == c.book_id))
    if (p.current_version_id if p else None) != payload.plan_version_id:
        raise HTTPException(409, '本章章纲或关联已变化，请重新读取后确认。')
    c.status = 'approved'
    if p:
        p.reviewed_plan_version_id, p.reviewed_chapter_version_id = p.current_version_id, c.current_version_id
    db.commit()
    return serialize_chapter(c, db)


def review_out(j):
    from ..automation_review import readable_report
    return cw.job_out(j) | {'kind': j.config.get('kind'), 'base_version_id': j.base_version_id,
        'opinions': readable_report(j.raw_output) if j.config.get('kind') == 'review' else j.config.get('opinions', ''),
        'check_report': readable_report(j.config.get('check_report', ''))}


def capture(db, chapter, config):
    """Freeze current A..A, preceding context and saved materials; never run memory models."""
    manifest, ctx = current_state.snapshot(db, chapter.book_id, before_chapter_number=chapter.chapter_number)
    raw = read_content(db.get(ChapterVersion, chapter.current_version_id))
    if not raw.strip():
        raise HTTPException(409, '本章正文为空，请先生成或保存正文再审核。')
    chosen = []
    for key in config['material_keys']:
        m = materials.latest(db, chapter.book_id, key)
        if not m or not m.content.strip():
            raise HTTPException(409, f'工作流选中的资料缺失或为空：{key}')
        chosen.append({'material_key': key, 'title': m.title, 'content': m.content, 'version_id': m.id})
    p = db.scalar(select(ChapterPlan).where(ChapterPlan.chapter_id == chapter.id, ChapterPlan.book_id == chapter.book_id))
    v = db.get(ChapterPlanVersion, p.current_version_id) if p else None
    g = chapter_plans.guidance(db, chapter.book_id)
    manifest.update(target={'id': chapter.id, 'number': chapter.chapter_number, 'title': chapter.title,
        'version': chapter.current_version_id, 'sha256': content_sha256(raw)}, plan={'id': p.id, 'version': v.id} if p else None,
        guidance=g, materials=[{'key': m['material_key'], 'version': m['version_id']} for m in chosen])
    # Respect the review canvas's context choices while always binding bodyStart == bodyEnd.
    opts = config.get('book_inputs', {})
    preceding = list(db.scalars(select(Chapter).where(Chapter.book_id == chapter.book_id,
        Chapter.chapter_number < chapter.chapter_number).order_by(Chapter.chapter_number)))
    previous_count = max(0, min(20, int(opts.get('previousCount') or 0)))
    previous = preceding[-previous_count:] if previous_count else []
    prev_text = '\n\n'.join(f'第{x.chapter_number}章 {x.title}\n{read_content(db.get(ChapterVersion, x.current_version_id))}' for x in previous)
    manifest['review_previous'] = [{'id': x.id, 'version': x.current_version_id} for x in previous]
    mode = str(opts.get('summaryMode') or 'none')
    summary_rows = [x for x in ctx['all_summaries'] if mode == 'all' or (mode.isdigit() and x['chapter_number'] <= int(mode))]
    summary_text = '\n\n'.join(f"第{x['chapter_number']}章 {x['title']}\n{x['summary']}" for x in summary_rows)
    plan_text = f'第{chapter.chapter_number}章 {v.title}（章纲：{v.status}）\n{v.content}' if v else '本章未绑定章纲'
    if g:
        plan_text += '\n\n共同执行说明：\n' + g['content']
    context = cw.standalone_context(chapter.book_id, chapter.chapter_number, db) if opts.get('includeCurrentState') else {}
    body = f'第{chapter.chapter_number}章 {chapter.title}\n\n{raw}'
    outputs = {'bookInfo': f"书名：{ctx['book_title']}\n本次仅审核第{chapter.chapter_number}章，正文范围{chapter.chapter_number}—{chapter.chapter_number}",
        'chapterTitle': f'第{chapter.chapter_number}章 {chapter.title}', 'bodyText': body,
        'previousText': prev_text or '未提取前文', 'summaryText': summary_text or '未提取梗概',
        'outlineText': (ctx['outline']['content'] if ctx['outline'] else '暂无大纲') if opts.get('includeOutline') else '未提取大纲',
        'planText': plan_text if opts.get('includeChapterPlans') else '未提取章纲',
        'stateText': context.get('stateText', '未提取开始前速查')}
    outputs['text'] = '\n\n'.join(outputs.values())
    source = {'book_id': chapter.book_id, 'materials': chosen, 'bookOutputs': outputs}
    return json.loads(json.dumps(manifest, ensure_ascii=False)), source


def validate_inputs(graph, config, kind):
    nodes = {n['id']: n for n in graph['nodes'] if n.get('enabled') is not False}
    books = [n for n in nodes.values() if n['type'] == 'chapter-context']
    if len(books) != 1:
        raise HTTPException(409, '审核和修订工作流需要一个书籍提取节点，以明确本章资料范围。')
    config['book_inputs'] = books[0]['inputs']
    config['book_nodes'] = [books[0]['id']]
    if kind == 'revision':
        for key in ('editorial-opinions', 'accepted-decisions'):
            if key not in nodes or nodes[key]['type'] != 'text':
                raise HTTPException(409, f'正文修订工作流缺少意见输入节点 {key}，请恢复后重试。')
    input_ports = {n: ['bookId', 'sourceRequestId'] for n in config['input_nodes']}
    input_ports[books[0]['id']] += ['bodyStart', 'bodyEnd']
    if kind == 'revision':
        input_ports.update({'editorial-opinions': ['text'], 'accepted-decisions': ['text']})
        if nodes.get('author-requirements', {}).get('type') == 'text':
            config['author_input'] = 'author-requirements'
            input_ports['author-requirements'] = ['text']
    for edge in graph.get('edges', []):
        if edge['targetHandle'] in input_ports.get(edge['targetNodeId'], []):
            raise HTTPException(409, '工作流的正文页输入已被连线占用，请移除冲突连线后重试。')


def revision_output(graph):
    # New canvases produce edits at revised-prose and complete prose at revision-apply.
    # Never fall back to saving the edit plan when an assembler is disabled or ambiguous.
    assemblies = [n for n in graph['nodes'] if n['type'] == 'revision-apply']
    if assemblies:
        enabled = [n for n in assemblies if n.get('enabled') is not False]
        if len(enabled) != 1:
            raise HTTPException(409, '正文修订工作流需要唯一启用的正文合成节点，请检查后重试；任务尚未启动。')
        return enabled[0]['id']
    if any(n['id'] == 'revision-assembled' for n in graph['nodes']):
        raise HTTPException(409, '正文合成节点类型已变化，请恢复后重试；任务尚未启动。')
    return 'revised-prose'


async def begin(chapter_id, payload, kind, db, *, frozen=None, owner_id=None, commit=True):
    c = db.get(Chapter, chapter_id)
    if not c:
        raise HTTPException(404, '章节不存在')
    workflow_id = frozen['workflow_id'] if frozen else settings.review_workflow_id if kind == 'review' else settings.revision_workflow_id
    if frozen:
        config = dict(frozen['config'])
        etag = frozen['etag']
    else:
        try:
            response = await cw.canvas('GET', f'/workflows/{workflow_id}')
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from exc
        graph = response.json()
        output_id = 'final-review-llm' if kind == 'review' else revision_output(graph)
        config = cw.validate_graph(graph, c.book_id, output_id, reader_required=False, gemini_only=False)
        validate_inputs(graph, config, kind)
        etag = response.headers['etag']
    config['kind'] = kind
    chapter_plans.lock(db, c.book_id)
    if c.current_version_id != payload.base_version_id:
        raise HTTPException(409, '正文版本已变化，请重新读取并审核当前版本。')
    from ..models import PlanWritingJob
    if db.scalar(select(PlanWritingJob.id).where(PlanWritingJob.book_id == c.book_id, PlanWritingJob.status.in_(('queued', 'submitting', 'running', 'stopping')))):
        raise HTTPException(409, '本书正在续写章纲，请等待完成')
    if db.scalar(select(CanvasWritingJob.id).where(CanvasWritingJob.book_id == c.book_id, CanvasWritingJob.status.in_(cw.ACTIVE))) or db.scalar(
        select(WritingJob.id).where(WritingJob.book_id == c.book_id, WritingJob.status.in_(['queued', 'running', 'cancelling']), WritingJob.id != (owner_id or -1))):
        raise HTTPException(409, '本书已有工作流任务运行，请等待完成或停止后重试。')
    if kind == 'revision':
        parent = cw.require_job(db, payload.review_job_id)
        if parent.chapter_id != c.id or parent.book_id != c.book_id or parent.config.get('kind') != 'review' or parent.status != 'completed':
            raise HTTPException(409, '请选择本章已完成的主编审核。')
        if parent.base_version_id != c.current_version_id or capture(db, c, parent.config)[0] != parent.manifest:
            raise HTTPException(409, '审稿后正文或参考资料已变化，请重新审核；旧意见不会应用到新稿。')
        if not payload.opinions.strip():
            raise HTTPException(400, '主编意见不能为空。')
        config.update(review_job_id=parent.id, opinions=payload.opinions,
            author_requirements=payload.decisions,
            decisions=DEFAULT_DECISIONS + ('\n\n作者补充（优先适用）：\n' + payload.decisions if payload.decisions.strip() else ''))
        if owner_id:
            config['decisions'] = payload.decisions
    manifest, source = capture(db, c, config)
    if owner_id:
        config['continuous_job_id'] = owner_id
    j = CanvasWritingJob(id=uuid4().hex, book_id=c.book_id, chapter_id=c.id, base_version_id=c.current_version_id,
        workflow_id=workflow_id, workflow_etag=etag, config=config,
        status='queued', message='等待审核工作流' if kind == 'review' else '等待修订工作流',
        source=source, manifest=manifest, raw_output='')
    db.add(j); db.flush()
    if commit:
        db.commit()
    return review_out(j)


@router.post('/chapters/{chapter_id}/review')
async def start_review(chapter_id: int, payload: ReviewStart, db: Session = Depends(get_db)):
    return await begin(chapter_id, payload, 'review', db)


@router.post('/chapters/{chapter_id}/revision')
async def start_revision(chapter_id: int, payload: ReviseStart, db: Session = Depends(get_db)):
    return await begin(chapter_id, payload, 'revision', db)


@router.get('/chapters/{chapter_id}/review/latest')
def latest_review(chapter_id: int, db: Session = Depends(get_db)):
    jobs = db.scalars(select(CanvasWritingJob).where(CanvasWritingJob.chapter_id == chapter_id).order_by(CanvasWritingJob.created_at.desc()))
    j = next((j for j in jobs if j.config.get('kind') in ('review', 'revision')), None)
    return review_out(j) if j else None


@router.get('/chapter-review/jobs/{job_id}')
def review_detail(job_id: str, db: Session = Depends(get_db)):
    return review_out(cw.require_job(db, job_id))


async def tick_review(db, j):
    if j.status not in cw.ACTIVE:
        return
    # Bound service outages and a stalled remote queue; never submit a second run automatically.
    if (datetime.utcnow() - j.created_at).total_seconds() > 7200:
        cw.mark(db, j, 'failed', '工作流超过两小时仍未结束，请查看画布运行记录；未重复提交或覆盖正文。')
        return
    if j.status == 'queued':
        c = db.get(Chapter, j.chapter_id)
        if capture(db, c, j.config)[0] != j.manifest:
            raise HTTPException(409, '排队期间正文或参考资料已变化，请重新审核。')
        overrides = {key: {'bookId': str(j.book_id), 'sourceRequestId': j.id} for key in j.config['input_nodes']}
        for key in j.config['book_nodes']:
            overrides[key].update(bodyStart=c.chapter_number, bodyEnd=c.chapter_number)
        if j.config['kind'] == 'revision':
            overrides.update({'editorial-opinions': {'text': j.config['opinions']},
                              'accepted-decisions': {'text': j.config['decisions']}})
            if j.config.get('author_input'):
                overrides[j.config['author_input']] = {'text': j.config.get('author_requirements', '')}
        if j.config.get('continuous_job_id'):
            from ..automation_review import CONTROL_NODE, decision_prompt
            overrides[CONTROL_NODE] = {'text': decision_prompt('review' if j.config['kind'] == 'review' else 'check', j.base_version_id)}
        cw.mark(db, j, 'submitting', '正在提交' + ('审稿' if j.config['kind'] == 'review' else '修订'))
        run = (await cw.canvas('POST', '/runs', json={'workflowId': j.workflow_id,
            'expectedVersion': j.workflow_etag, 'inputOverrides': overrides})).json()
        db.expire_all()
        j.run_id = run['id']
        if j.status == 'cancelled':
            db.commit(); await cw.canvas('POST', f'/runs/{j.run_id}/cancel'); return
        cw.mark(db, j, 'running', '正在审核本章' if j.config['kind'] == 'review' else '正在根据主编意见修订')
        return
    if j.status != 'running':
        return
    run = (await cw.canvas('GET', f'/runs/{j.run_id}')).json()
    db.expire_all()
    if j.status != 'running':
        return
    progress = [{'label': j.config['node_labels'].get(n['nodeId'], n['nodeId']), 'status': n['status']} for n in run['nodes']]
    j.config = {**j.config, 'node_progress': progress}
    db.commit()
    if run['status'] in ('FAILED', 'CANCELLED'):
        j.config = {**j.config, 'remote_terminal': True}
        db.commit()
        candidate = next((n for n in run['nodes'] if n['nodeId'] == j.config['output_node_id'] and n['status'] == 'SUCCEEDED'), None)
        candidate_text = (candidate.get('outputs') or {}).get('text') if candidate else None
        if isinstance(candidate_text, str):
            j.raw_output = candidate_text
            db.commit()
        errors = [n['error'].get('message', '') for n in run['nodes'] if n.get('error')]
        raise ValueError('工作流未完成：' + ('；'.join(errors) or run['status']))
    if run['status'] != 'SUCCEEDED':
        return
    node = next((n for n in run['nodes'] if n['nodeId'] == j.config['output_node_id'] and n['status'] == 'SUCCEEDED'), None)
    raw = node.get('outputs', {}).get('text') if node else None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('所选工作流输出为空，请检查主编或修订输出节点。')
    j.raw_output = raw
    check = next((n for n in run['nodes'] if n['nodeId'] == 'revision-check'), None)
    j.config = {**j.config, 'check_report': (check.get('outputs') or {}).get('text', '') if check else ''}
    db.commit()
    chapter_plans.lock(db, j.book_id)
    if j.status != 'running' or j.saved_version_id:
        return
    c = db.get(Chapter, j.chapter_id)
    if capture(db, c, j.config)[0] != j.manifest:
        raise HTTPException(409, '执行期间正文或参考资料已变化，输出已保留，未覆盖正文。')
    if j.config['kind'] == 'revision':
        title, content = revision_text(raw)
        version = ChapterVersion(chapter_id=c.id, version_number=next_version_number(db, c.id), content_path='',
            content_text=content, word_count=count_words(content), source='review_revision', note='根据主编意见修订')
        db.add(version); db.flush()
        c.title, c.current_version_id, c.word_count, c.status = title, version.id, version.word_count, 'draft'
        j.saved_version_id = version.id
        cw.mark(db, j, 'completed', '修订已保存为新版本。本轮结束，可确认正文或再次点击审核。')
    else:
        cw.mark(db, j, 'completed', '审核完成，可编辑主编意见后点击修订。')
