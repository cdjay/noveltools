"""Durable, bounded chapter generation with source checks and atomic checkpoints."""
import asyncio
import json
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..llm_failover import generation_http_error as llm_http_error
from ..llm_failover import writing_job_context
from ..models import Book, Chapter, CurrentStateLease, SummaryJob, SummaryLease, WritingJob, WritingJobItem, LLMFailoverEvent
from .core import require_book

router = APIRouter(prefix="/api", tags=["Writing workflow"])
ACTIVE = ("queued", "running", "cancelling")
from .continuous_writing import StartContinuous as StartWriting


def lock_book(db, book_id):
    db.execute(update(Book).where(Book.id == book_id).values(id=Book.id))
    require_book(db, book_id)
    db.expire_all()


def job_out(job, db):
    items = db.scalars(select(WritingJobItem).where(WritingJobItem.job_id == job.id).order_by(WritingJobItem.sequence)).all()
    events = db.scalars(select(LLMFailoverEvent).where(LLMFailoverEvent.writing_job_id == job.id).order_by(LLMFailoverEvent.id.desc()).limit(100)).all()
    if job.providers.get('engine') == 'canvas-v1':
        config = job.providers
        return {'id': job.id, 'book_id': job.book_id, 'status': job.status, 'step': job.step, 'message': job.message,
            'engine': config['engine'], 'mode': config['mode'], 'target_count': job.target_count,
            'start_chapter': config['start_chapter'], 'end_chapter': config['end_chapter'],
            'review_limit': config['review_limit'], 'auto_revise': config['auto_revise'],
            'replace_existing': config.get('replace_existing', False), 'entry': config.get('entry', 'continuous'),
            'completed_count': sum(i.status == 'completed' for i in items),
            'saved_count': sum(i.version_id is not None for i in items), 'model': '',
            'logs': config.get('logs', []),
            'failover_events': [{'id': event.id, 'time': event.created_at.isoformat() + 'Z', 'text': event.message} for event in reversed(events)],
            'workflows': {k: {'id': v['workflow_id'], 'name': v['name']} for k, v in config['workflows'].items()},
            'items': [{'sequence': i.sequence, 'chapter_id': i.chapter_id, 'version_id': i.version_id,
                'title': i.title, 'status': i.status, **{k: i.execution.get(k) for k in
                    ('position', 'base_version_id', 'phase', 'review_count', 'revision_count', 'decision', 'child_id')}} for i in items]}
    return {"failover_events": [{"id": event.id, "time": event.created_at.isoformat() + 'Z', "text": event.message} for event in reversed(events)],
            "id": job.id, "book_id": job.book_id, "status": job.status, "step": job.step, "message": job.message,
            "target_count": job.target_count, "completed_count": sum(i.status == "completed" for i in items),
            "saved_count": sum(i.status != "pending" for i in items),
            "model": job.providers.get("chapter_writing", {}).get("config", {}).get("model", ""),
            "items": [{"sequence": i.sequence, "chapter_id": i.chapter_id, "version_id": i.version_id,
                       "title": i.title, "status": i.status} for i in items]}


def require_job(db, job_id):
    job = db.get(WritingJob, job_id)
    if not job:
        raise HTTPException(404, "正文生成任务不存在")
    return job


@router.get("/books/{book_id}/writing-jobs/options")
def options(book_id: int, db: Session = Depends(get_db)):
    from .continuous_writing import range_options
    return range_options(db, book_id)


@router.get("/books/{book_id}/writing-jobs/generation-options")
def generation_options(book_id: int, db: Session = Depends(get_db)):
    """Return every chapter plan so a reader or plan can choose a rewrite start."""
    from .continuous_writing import range_options
    return range_options(db, book_id, include_existing=True)


@router.post("/books/{book_id}/writing-jobs")
async def start(book_id: int, payload: StartWriting, db: Session = Depends(get_db)):
    from .continuous_writing import start as start_continuous
    return job_out(await start_continuous(book_id, payload, db), db)


@router.get("/books/{book_id}/writing-jobs/latest")
def latest(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    job = db.scalar(select(WritingJob).where(WritingJob.book_id == book_id).order_by(WritingJob.id.desc()).limit(1))
    return job_out(job, db) if job else None


@router.get("/writing-jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    return job_out(require_job(db, job_id), db)


@router.post("/writing-jobs/{job_id}/cancel")
def cancel(job_id: int, db: Session = Depends(get_db)):
    job = require_job(db, job_id)
    lock_book(db, job.book_id)
    if job.status in ACTIVE:
        job.status = "cancelled" if job.status == "queued" else "cancelling"
        db.commit()
    return job_out(job, db)


@router.post("/writing-jobs/{job_id}/resume")
async def resume(job_id: int, db: Session = Depends(get_db)):
    from .continuous_writing import ENGINE, resume as resume_continuous
    job = require_job(db, job_id)
    lock_book(db, job.book_id)
    if job.providers.get('engine') != ENGINE:
        raise HTTPException(409, '旧版持续任务已停用，请按已确认章纲重新安排工作流任务；已保存正文保留。')
    try:
        return job_out(await resume_continuous(job, db), db)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def other_generation(db, book_id):
    return bool(db.get(CurrentStateLease, book_id) or
        db.scalar(select(SummaryLease.chapter_id).join(Chapter, Chapter.id == SummaryLease.chapter_id).where(Chapter.book_id == book_id).limit(1)) or
        db.scalar(select(SummaryJob.id).where(SummaryJob.book_id == book_id, SummaryJob.status.in_(ACTIVE)).limit(1)))


def parse_chapter_text(raw):
    if not isinstance(raw, str):
        raise ValueError("正文模型没有返回文本，未创建新章节。")
    text = raw.lstrip('\ufeff \t\r\n').strip()
    fenced = re.fullmatch(r'```(?:json|text|plaintext|markdown|md)?[ \t]*\r?\n([\s\S]*?)\r?\n```', text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith(("资料冲突：", "资料冲突:")):
        raise ValueError("正文模型报告资料冲突，未创建新章节：" + text[:1200])
    try:
        # Keep older already-generated title/content responses readable, without requesting that format.
        if text.startswith('{'):
            parsed = json.loads(text)
            title, content = parsed['title'], parsed['content']
        else:
            title, content = text.split('\n', 1)
        if not isinstance(title, str) or not isinstance(content, str):
            raise TypeError()
        title = re.sub(r'^#{1,6}\s+', '', title.strip())
        for marker in ('**', '__'):
            if title.startswith(marker) and title.endswith(marker):
                title = title[len(marker):-len(marker)].strip()
        # The database allocates the chapter number; retain only the name to avoid a duplicate prefix.
        title = re.sub(r'^第\s*[0-9０-９零〇一二三四五六七八九十百千万两]+\s*[章回节]\s*[：:、.．—-]*\s*', '', title).strip()
        if not title or len(title) > 200 or '\r' in title or '\n' in title or title.startswith('```') or not content.strip():
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("正文格式无效：第一行须为章节名称，后续须有正文；未创建新章节。") from None
    # Plain novel text uses each non-empty line as a paragraph. Normalize saved text,
    # rather than relying on CSS, so editing, copying and exporting share the format.
    content = '\n\n'.join('　　' + line.strip() for line in content.splitlines() if line.strip())
    return title, content


async def tick(job_id):
    from .continuous_writing import ENGINE, tick as tick_continuous
    with SessionLocal.begin() as db:
        job = db.get(WritingJob, job_id)
        if not job:
            return False
        if job.providers.get('engine') != ENGINE:
            if job.status in ACTIVE:
                job.status, job.message = 'interrupted', '旧版持续任务已停用，请重新安排工作流任务。'
            return False
    return await tick_continuous(job_id)


async def run_job(job_id):
    token = writing_job_context.set(job_id)
    try:
        while await tick(job_id):
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if isinstance(exc, HTTPException):
            message = str(exc.detail)
        elif isinstance(exc, httpx.HTTPStatusError):
            message = llm_http_error(exc, "正文模型")
        elif isinstance(exc, httpx.RequestError):
            message = "正文模型连接失败或超时，请检查配置后继续。"
        elif isinstance(exc, ValueError):
            message = str(exc)
        else:
            message = "任务处理失败，已保存章节保留，请核对后继续。"
        with SessionLocal() as db:
            job = db.get(WritingJob, job_id)
            if job:
                if job.providers.get('engine') == 'canvas-v1':
                    from .continuous_writing import log
                    log(db, job, job.step, message, status='failed')
                else:
                    job.status, job.message = "failed", message
                    db.commit()
    finally:
        writing_job_context.reset(token)


def recover_interrupted():
    with SessionLocal.begin() as db:
        db.execute(update(WritingJob).where(WritingJob.status.in_(ACTIVE)).values(status="interrupted", message="服务已重启，已保存进度保留，请手动继续。"))


async def worker():
    while True:
        with SessionLocal.begin() as db:
            job = db.scalar(select(WritingJob).where(WritingJob.status == "queued").order_by(WritingJob.id).limit(1))
            job_id = job.id if job else None
            if job:
                changed = db.execute(update(WritingJob).where(WritingJob.id == job_id, WritingJob.status == "queued").values(status="running"))
                if not changed.rowcount:
                    job_id = None
        if job_id is not None:
            await run_job(job_id)
        else:
            await asyncio.sleep(0.5)
