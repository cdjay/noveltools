"""Persisted orchestration of the user's saved generation, review and revision canvases."""
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from ..automation_review import ensure_control, parse_decision, readable_report
from ..config import settings
from ..database import SessionLocal
from ..llm_failover import snapshot_chain
from ..models import Chapter, ChapterVersion, ChapterPlan, ChapterPlanVersion, CanvasWritingJob, WritingJob, WritingJobItem
from ..schemas import ChapterCreate
from ..services import read_content, content_sha256
from . import canvas_writing as cw, chapter_review as cr, chapter_plans as plans, current_state
from .core import add_chapter

ENGINE = 'canvas-v1'
ACTIVE = ('queued', 'running', 'cancelling')


class StartContinuous(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['count', 'through_plan'] = 'count'
    chapter_count: int | None = Field(default=None, ge=1, le=1000)
    target_plan_id: str | None = None
    start_chapter: int | None = Field(default=None, ge=1)
    replace_existing: bool = False
    entry: Literal['continuous', 'reader', 'chapter_plan'] = 'continuous'
    review_limit: int = Field(default=3, ge=0, le=5)
    auto_revise: bool = True

    @model_validator(mode='after')
    def range_choice(self):
        if self.mode == 'count' and (self.chapter_count is None or self.target_plan_id is not None):
            raise ValueError('按数量生成需要填写章数，不同时指定目标章纲。')
        if self.mode == 'through_plan' and (not self.target_plan_id or self.chapter_count is not None):
            raise ValueError('请选择结束章纲，不同时指定生成章数。')
        return self


def range_options(db, book_id, *, include_existing=False):
    plans.require_book(db, book_id)
    chapters = list(db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)))
    first = 1
    for c in chapters:
        if c.chapter_number != first or not read_content(db.get(ChapterVersion, c.current_version_id)).strip():
            break
        first += 1
    entries = []
    for p in plans.plans(db, book_id):
        if not include_existing and p.position < first:
            continue
        v = db.get(ChapterPlanVersion, p.current_version_id)
        c = db.get(Chapter, p.chapter_id) if p.chapter_id else None
        entries.append({'id': p.id, 'position': p.position, 'version_id': v.id, 'title': v.title,
            'volume': v.volume, 'confirmed': v.status == 'confirmed', 'chapter_id': p.chapter_id,
            'has_body': bool(c and read_content(db.get(ChapterVersion, c.current_version_id)).strip())})
    return {'start_chapter': first, 'plans': entries}


def selected_range(db, book_id, payload):
    options = range_options(db, book_id, include_existing=payload.start_chapter is not None or payload.replace_existing)
    first = payload.start_chapter or options['start_chapter']
    if first < 1:
        raise HTTPException(400, '起始章节必须大于零。')
    if payload.mode == 'through_plan':
        target = next((p for p in options['plans'] if p['id'] == payload.target_plan_id), None)
        if not target or target['position'] < first:
            raise HTTPException(409, '目标章纲不存在、属于其他书籍或早于起始章节，请重新选择。')
        last = target['position']
    else:
        last = first + payload.chapter_count - 1
    if last - first + 1 > 1000:
        raise HTTPException(400, '单次最多生成1000章，请分批推进。')
    chosen = [p for p in options['plans'] if first <= p['position'] <= last]
    if [p['position'] for p in chosen] != list(range(first, last + 1)):
        raise HTTPException(409, f'第{first}—{last}章的章纲不齐全，请先补齐；不会跳过缺章。')
    for p in chosen:
        if not p['confirmed']:
            raise HTTPException(409, f"第{p['position']}份章纲《{p['title']}》尚未确认，请先确认。")
        c = db.scalar(select(Chapter).where(Chapter.book_id == book_id, Chapter.chapter_number == p['position']))
        if c and p['chapter_id'] != c.id:
            raise HTTPException(409, f"第{p['position']}章尚未正确绑定章纲，请先核对关联。")
        if c and p['has_body'] and not payload.replace_existing:
            raise HTTPException(409, f"第{p['position']}章已有正文，请勾选“生成新版本并保留旧稿”后继续。")
        if p['chapter_id'] and (not c or c.id != p['chapter_id']):
            raise HTTPException(409, '章纲与正文顺序不一致，请先核对关联。')
    _, context = current_state.snapshot(db, book_id, before_chapter_number=first)
    if not context['outline'] or not context['outline']['content'].strip():
        raise HTTPException(409, '请先保存非空全书大纲。')
    return chosen


async def workflow_snapshots(db, book_id, review_limit):
    selection = cw.get_workflow_settings(book_id, db)
    result = {}
    responses = {}
    kinds = ['generate', 'review', 'revision'] if review_limit else ['generate']
    for kind in kinds:
        wid = selection['workflow_id'] if kind == 'generate' else settings.review_workflow_id if kind == 'review' else settings.revision_workflow_id
        response = await cw.canvas('GET', f'/workflows/{wid}')
        graph = response.json()
        output = selection['output_node_id'] if kind == 'generate' else 'final-review-llm' if kind == 'review' else cr.revision_output(graph)
        port = selection['output_port'] if kind == 'generate' else 'text'
        config = cw.validate_graph(graph, book_id, output, port, reader_required=kind == 'generate', gemini_only=kind == 'generate')
        if kind != 'generate':
            cr.validate_inputs(graph, config, kind)
        responses[kind] = response
        result[kind] = {'workflow_id': wid, 'name': graph.get('name', kind), 'etag': response.headers['etag'], 'config': config}
    for kind in ('review', 'revision'):
        if kind in responses:
            response = await ensure_control(cw.canvas, responses[kind], 'final-review-llm' if kind == 'review' else 'revision-check')
            result[kind]['etag'] = response.headers['etag']
    return result


def log(db, job, step, message, *, status=None):
    entries = job.providers.get('logs', [])
    if not entries or entries[-1]['text'] != message:
        entry = {'id': entries[-1]['id'] + 1 if entries else 1,
                 'time': datetime.now(timezone.utc).isoformat(), 'text': message}
        job.providers = {**job.providers, 'logs': [*entries, entry][-100:]}
    job.step, job.message = step, message
    if status:
        job.status = status
    db.commit()


def no_other_tasks(db, book_id, owner=None):
    from .plan_writing import ACTIVE as PLAN_ACTIVE
    from ..models import PlanWritingJob
    if db.scalar(select(PlanWritingJob.id).where(PlanWritingJob.book_id == book_id, PlanWritingJob.status.in_(PLAN_ACTIVE))):
        raise HTTPException(409, "本书正在续写章纲，请等待完成")
    if db.scalar(select(WritingJob.id).where(WritingJob.book_id == book_id, WritingJob.id != (owner or -1), WritingJob.status.in_(ACTIVE))):
        raise HTTPException(409, '本书已有持续生成任务运行。')
    for child in db.scalars(select(CanvasWritingJob).where(CanvasWritingJob.book_id == book_id, CanvasWritingJob.status.in_(cw.ACTIVE))):
        if owner is None or child.config.get('continuous_job_id') != owner:
            raise HTTPException(409, '本书已有正文、审核或修订工作流运行，请等待完成。')


async def start(book_id, payload, db):
    # Do not hold a database write lock during remote workflow reads/configuration.
    existing = db.scalar(select(WritingJob).where(WritingJob.book_id == book_id, WritingJob.status.in_(ACTIVE)))
    if existing:
        return existing
    chosen = selected_range(db, book_id, payload)
    no_other_tasks(db, book_id)
    memory = {key: snapshot_chain(db, key) for key in ('batch_summary', 'current_state')} if len(chosen) > 1 or chosen[0]['position'] > 1 else {}
    try:
        workflows = await workflow_snapshots(db, book_id, payload.review_limit)
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc
    plans.lock(db, book_id)
    no_other_tasks(db, book_id)
    if selected_range(db, book_id, payload) != chosen:
        raise HTTPException(409, '选择期间章纲或正文已变化，请重新选择生成范围。')
    workflows['generate']['config']['continuous_memory'] = memory
    job = WritingJob(book_id=book_id, target_count=len(chosen), providers={'engine': ENGINE,
        'mode': payload.mode, 'target_plan_id': chosen[-1]['id'], 'start_chapter': chosen[0]['position'],
        'end_chapter': chosen[-1]['position'], 'review_limit': payload.review_limit,
        'auto_revise': payload.auto_revise, 'replace_existing': payload.replace_existing,
        'entry': payload.entry, 'workflows': workflows,
        'authorization': '本次连续任务：仅自动修订有证据的确定问题，不采纳可选建议，不改大纲或章纲；需作者决定时暂停。'})
    db.add(job); db.flush()
    for i, p in enumerate(chosen, 1):
        chapter = db.scalar(select(Chapter).where(Chapter.book_id == book_id, Chapter.chapter_number == p['position']))
        db.add(WritingJobItem(job_id=job.id, sequence=i, title=p['title'], chapter_id=p['chapter_id'],
            execution={'plan_id': p['id'], 'plan_version_id': p['version_id'], 'position': p['position'],
                       # Rewrites must use the exact body that the author saw
                       # when the task was arranged; first-generation blanks
                       # receive their version after the blank chapter exists.
                       'base_version_id': chapter.current_version_id if chapter and p['has_body'] else None,
                       'phase': 'generate', 'review_count': 0, 'revision_count': 0, 'children': []}))
    log(db, job, 'preparing', f"已安排第{chosen[0]['position']}—{chosen[-1]['position']}章，共{len(chosen)}章；每章最多完整审核{payload.review_limit}次。")
    return job


def check_plan(db, job, item):
    state = item.execution
    p = db.get(ChapterPlan, state['plan_id'])
    if (not p or p.book_id != job.book_id or p.position != state['position'] or
            p.current_version_id != state['plan_version_id'] or p.chapter_id != item.chapter_id):
        raise ValueError('本次章纲版本、顺序或正文关联已变化，请核对后重新安排，不能静默换用其他章纲。')
    return p


def pause(db, job, reason):
    log(db, job, 'paused', reason, status='paused')
    return False


def done(db, job, item, reason):
    item.status = 'completed'
    item.execution = {**item.execution, 'phase': 'done', 'child_id': None, 'decision': reason}
    log(db, job, 'preparing', f"第{item.execution['position']}章完成自动流程：{reason}。保留草稿，下一章开始前更新梗概和速查。")


async def tick(job_id):
    with SessionLocal() as db:
        job = db.get(WritingJob, job_id)
        if not job or job.status not in ('running', 'cancelling'):
            return False
        item = db.scalar(select(WritingJobItem).where(WritingJobItem.job_id == job_id, WritingJobItem.status != 'completed').order_by(WritingJobItem.sequence))
        if not item:
            log(db, job, 'completed', '指定范围的正文与自动审核流程已完成，均保留为草稿；末章梗概与速查在下次续写前更新。', status='completed')
            return False
        state = dict(item.execution)
        child = db.get(CanvasWritingJob, state['child_id']) if state.get('child_id') else None
        if state.get('child_id') and not child:
            return pause(db, job, '子任务记录已删除，请人工核对；不会重新创建重复正文。')
        if job.status == 'cancelling':
            if child and child.status in ('queued', 'preparing'):
                cw.mark(db, child, 'cancelled', '持续生成已停止，未提交下一次画布运行。')
            elif child and child.status in cw.ACTIVE:
                log(db, job, 'waiting', '正在等待当前工作流结束后停止；不会启动下一步骤。')
                return True
            if child and child.status == 'completed' and child.saved_version_id:
                item.version_id, item.status = child.saved_version_id, 'generated'
            log(db, job, 'preparing', '已停止，已保存正文、意见和进度保留。', status='cancelled')
            return False
        p = check_plan(db, job, item)
        if child:
            if child.status in cw.ACTIVE:
                log(db, job, state['phase'], f"第{state['position']}章 · {child.message}")
                return True
            if child.status != 'completed':
                return pause(db, job, f"第{state['position']}章工作流未完成：{child.message}")
            c = db.get(Chapter, item.chapter_id)
            expected = child.saved_version_id if state['phase'] in ('generate', 'revision') else child.base_version_id
            if not c or c.current_version_id != expected:
                return pause(db, job, '工作流完成后的正文已被修改或删除，请先人工核对。')
            item.version_id = c.current_version_id
            item.status = 'generated'
            state['child_id'] = None
            if state['phase'] == 'generate':
                state['phase'] = 'review'
                item.execution = state
                if not job.providers['review_limit']:
                    done(db, job, item, '按本次设置仅生成，不审核')
                else:
                    log(db, job, 'review', f"第{state['position']}章初稿已保存，准备首次审核。")
                return True
            raw = child.raw_output if state['phase'] == 'review' else child.config.get('check_report', '')
            try:
                decision = parse_decision(raw, 'review' if state['phase'] == 'review' else 'check', child.base_version_id)
            except ValueError as exc:
                return pause(db, job, str(exc))
            state['decision'] = decision.reason
            item.execution = state
            if decision.needs_author or any(i.certainty == 'needs_author' for i in decision.issues) or decision.action == 'pause':
                return pause(db, job, '需要作者处理：' + decision.reason)
            if state['phase'] == 'review':
                state['review_job_id'] = child.id
                if decision.action == 'continue':
                    done(db, job, item, f"第{state['review_count']}次审核后无需修订：{decision.reason}")
                    return True
                fixes = [i for i in decision.issues if i.certainty == 'confirmed']
                if not job.providers['auto_revise'] or any(not i.auto_fixable for i in fixes):
                    return pause(db, job, '存在确定问题，需要人工修订：' + decision.reason)
                fingerprint = content_sha256(json.dumps(sorted(i.evidence.strip() for i in fixes), ensure_ascii=False))
                if fingerprint in state.get('issue_fingerprints', []):
                    return pause(db, job, '修订后仍重复报告同一证据问题，已停止循环，请人工处理。')
                if state['revision_count'] >= job.providers['review_limit']:
                    return pause(db, job, '已达到本章修订上限，请人工处理并确认本章后继续。')
                state.update(phase='revision', accepted_ids=[i.id for i in fixes],
                    issue_fingerprints=[*state.get('issue_fingerprints', []), fingerprint])
                item.execution = state
                log(db, job, 'revision', f"第{state['position']}章将局部修订确定问题：{'、'.join(state['accepted_ids'])}。{decision.reason}")
                return True
            before = read_content(db.get(ChapterVersion, child.base_version_id))
            after = read_content(db.get(ChapterVersion, child.saved_version_id))
            if before == after:
                return pause(db, job, '修订没有改变正文，无法确认确定问题已解决，已停止重复修订。')
            resolved = set(state.get('accepted_ids', [])) <= set(decision.resolved_ids)
            if decision.action == 'continue' and decision.impact != 'story' and resolved:
                done(db, job, item, '局部修改复核已解决问题，无需再次完整审核：' + decision.reason)
                return True
            if state['review_count'] >= job.providers['review_limit']:
                return pause(db, job, f"已用完{job.providers['review_limit']}次完整审核，仍需复审：{decision.reason}。请人工处理并确认本章后继续。")
            state['phase'] = 'review'
            item.execution = state
            log(db, job, 'review', f"第{state['position']}章需要第{state['review_count'] + 1}次完整审核：{decision.reason}")
            return True
        no_other_tasks(db, job.book_id, job.id)
        phase = state['phase']
        if phase == 'generate':
            plans.lock(db, job.book_id)
            p = check_plan(db, job, item)
            if not item.chapter_id:
                if db.scalar(select(Chapter.id).where(Chapter.book_id == job.book_id, Chapter.chapter_number == state['position'])):
                    return pause(db, job, '计划位置已存在未关联正文，请先人工绑定。')
                c = add_chapter(job.book_id, ChapterCreate(title=item.title, chapter_number=state['position'], content='', source='plan', note=f'连续工作流任务{job.id}'), db)
                p.chapter_id = item.chapter_id = c.id
                db.flush()
            else:
                c = db.get(Chapter, item.chapter_id)
                if not c:
                    return pause(db, job, '目标章节已被删除，不能继续生成。')
                existing = read_content(db.get(ChapterVersion, c.current_version_id)).strip()
                if existing and not job.providers.get('replace_existing'):
                    return pause(db, job, '目标章节已有正文；本次任务未授权生成新版本，不会自动覆盖。')
                if existing and state.get('base_version_id') and c.current_version_id != state['base_version_id']:
                    return pause(db, job, '重写基准正文已被修改，请重新安排任务；不会覆盖新的编辑。')
            base_version_id = state.get('base_version_id') or c.current_version_id
            result = await cw.start_job(c.id, cw.Start(base_version_id=base_version_id,
                replace_existing=bool(job.providers.get('replace_existing'))), db,
                frozen=job.providers['workflows']['generate'], owner_id=job.id, commit=False)
        else:
            c = db.get(Chapter, item.chapter_id)
            if not c or c.current_version_id != item.version_id:
                return pause(db, job, '正文在步骤之间发生变化，请人工核对后继续。')
            if phase == 'review':
                if state['review_count'] >= job.providers['review_limit']:
                    return pause(db, job, '本章完整审核次数已达到上限，请人工处理并确认本章后继续。')
                payload = cr.ReviewStart(base_version_id=c.current_version_id)
                state['review_count'] += 1
            else:
                review = db.get(CanvasWritingJob, state['review_job_id'])
                payload = cr.ReviseStart(base_version_id=c.current_version_id, review_job_id=review.id,
                    opinions=readable_report(review.raw_output), decisions='本次仅采纳确定问题 ' + '、'.join(state['accepted_ids']) +
                    ' 的必要局部修改。所有其他意见、可选建议、未裁定Q项均不采纳。不改大纲、章纲、未涉及段落及既有排版。不扩大到整章润色或重写。')
                state['revision_count'] += 1
            result = await cr.begin(c.id, payload, phase, db, frozen=job.providers['workflows'][phase], owner_id=job.id, commit=False)
        state['child_id'] = result['id']
        state['children'] = [*state['children'], result['id']]
        item.execution = state
        # Child creation, blank binding and the parent checkpoint commit together.
        log(db, job, phase, f"第{state['position']}章已安排{ {'generate':'生成正文','review':'完整审核','revision':'局部修订'}[phase]}工作流。")
        return True


async def resume(job, db):
    if job.status not in ('paused', 'failed', 'cancelled', 'interrupted'):
        raise HTTPException(409, '当前任务不能继续。')
    no_other_tasks(db, job.book_id, job.id)
    item = db.scalar(select(WritingJobItem).where(WritingJobItem.job_id == job.id, WritingJobItem.status != 'completed').order_by(WritingJobItem.sequence))
    if item:
        check_plan(db, job, item)
        state = dict(item.execution)
        c = db.get(Chapter, item.chapter_id) if item.chapter_id else None
        child = db.get(CanvasWritingJob, state['child_id']) if state.get('child_id') else None
        if c and c.status == 'approved' and c.word_count and not (child and child.status in cw.ACTIVE):
            item.version_id = c.current_version_id
            done(db, job, item, '作者已人工确认本章')
        elif job.status == 'paused' and (not child or child.status == 'completed'):
            raise HTTPException(409, '本章需要人工处理；请查看审核意见、修订并确认正文后继续。')
        elif child and child.status not in cw.ACTIVE and child.status != 'completed':
            if child.config.get('submission_uncertain') or (child.run_id and not child.config.get('remote_terminal')):
                raise HTTPException(409, '上次提交结果未知，请先核对画布运行，不能重复提交。')
            if state['phase'] == 'review':
                # Failed attempts do not consume a completed full review, but retries are explicit.
                state['review_count'] = max(0, state['review_count'] - 1)
            if state['phase'] == 'revision':
                state['revision_count'] = max(0, state['revision_count'] - 1)
            state['child_id'] = None
            item.execution = state
    log(db, job, 'preparing', '继续原任务，保留已完成步骤和审核次数。', status='queued')
    return job
