"""Durable user-started jobs. Restarted work waits for an explicit resume."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, delete, select, update
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import Book, Chapter, SummaryJob, SummaryJobItem, SummaryLease, CurrentStateLease
from .core import require_book
from .summary import summary_provider, update_summary

router = APIRouter(prefix="/api")
ACTIVE = ("queued", "running", "cancelling")


def job_out(job, db):
    items = db.scalars(select(SummaryJobItem).where(SummaryJobItem.job_id == job.id).order_by(SummaryJobItem.id)).all()
    return {"id": job.id, "book_id": job.book_id, "status": job.status, "model": job.model,
            "total": len(items), "updated": sum(i.status == "completed" for i in items),
            "skipped": sum(i.status == "skipped" for i in items), "failed": sum(i.status == "failed" for i in items),
            "items": [{"chapter_id": i.chapter_id, "title": i.title, "status": i.status, "message": i.message} for i in items]}


def require_job(job_id, db):
    job = db.get(SummaryJob, job_id)
    if not job:
        raise HTTPException(404, "梗概任务不存在")
    return job


def lock_book(book_id, db):
    # Serializes job creation/resume, including requests from different browser windows.
    db.execute(update(Book).where(Book.id == book_id).values(id=Book.id))
    require_book(db, book_id)


@router.post("/books/{book_id}/summary-jobs")
def create_job(book_id: int, db: Session = Depends(get_db)):
    lock_book(book_id, db)
    active = db.scalar(select(SummaryJob).where(SummaryJob.book_id == book_id, SummaryJob.status.in_(ACTIVE)))
    if active:
        return job_out(active, db)
    provider = summary_provider(db, "batch_summary")
    if not provider:
        raise HTTPException(400, "请设置可用的默认模型或批量梗概分流。")
    job = SummaryJob(book_id=book_id, provider_id=provider.id, model=provider.model, provider_config=provider_config(provider))
    db.add(job)
    db.flush()
    chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()
    for chapter in chapters:
        db.add(SummaryJobItem(job_id=job.id, chapter_id=chapter.id, title=chapter.title))
    db.commit()
    return job_out(job, db)


@router.get("/books/{book_id}/summary-jobs/latest")
def latest_job(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    job = db.scalar(select(SummaryJob).where(SummaryJob.book_id == book_id).order_by(SummaryJob.updated_at.desc(), SummaryJob.id.desc()))
    return job_out(job, db) if job else None


@router.get("/summary-jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    return job_out(require_job(job_id, db), db)


@router.post("/summary-jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = require_job(job_id, db)
    db.execute(update(SummaryJob).where(SummaryJob.id == job_id, SummaryJob.status.in_(ACTIVE))
               .values(status=case((SummaryJob.status == "queued", "cancelled"), else_="cancelling")))
    db.commit()
    db.refresh(job)
    return job_out(job, db)


@router.post("/summary-jobs/{job_id}/resume")
def resume_job(job_id: int, db: Session = Depends(get_db)):
    job = require_job(job_id, db)
    lock_book(job.book_id, db)
    db.refresh(job)
    active = db.scalar(select(SummaryJob).where(SummaryJob.book_id == job.book_id, SummaryJob.status.in_(ACTIVE)))
    if active:
        raise HTTPException(409, "这本书已有任务运行，请等待完成。")
    provider = summary_provider(db, "batch_summary")
    if not provider:
        raise HTTPException(400, "请设置可用的默认模型或批量梗概分流。")
    db.execute(update(SummaryJobItem).where(SummaryJobItem.job_id == job_id, SummaryJobItem.status.in_(("failed", "running", "pending")))
               .values(status="pending", message=""))
    job.status = "queued"
    job.provider_id = provider.id
    job.model = provider.model
    job.provider_config = provider_config(provider)
    db.commit()
    return job_out(job, db)


def recover_interrupted():
    with SessionLocal.begin() as db:
        db.execute(update(SummaryJob).where(SummaryJob.status.in_(ACTIVE)).values(status="interrupted"))
        db.execute(update(SummaryJobItem).where(SummaryJobItem.status == "running").values(status="pending"))
        db.execute(delete(SummaryLease))
        db.execute(delete(CurrentStateLease))


def provider_config(provider):
    return {key: getattr(provider, key) for key in ("model", "base_url", "temperature", "max_tokens", "timeout")}


async def run_job(job_id):
    while True:
        with SessionLocal() as db:
            job = db.get(SummaryJob, job_id)
            if not job:
                return
            if job.status == "cancelling":
                job.status = "cancelled"
                db.commit()
                return
            if job.status != "running":
                return
            item = db.scalar(select(SummaryJobItem).where(SummaryJobItem.job_id == job_id, SummaryJobItem.status == "pending").order_by(SummaryJobItem.id))
            if not item:
                has_failure = db.scalar(select(SummaryJobItem.id).where(SummaryJobItem.job_id == job_id, SummaryJobItem.status == "failed"))
                job.status = "failed" if has_failure else "completed"
                db.commit()
                return
            item.status = "running"
            chapter_id, item_id, provider_id, config = item.chapter_id, item.id, job.provider_id, job.provider_config
            db.commit()
        try:
            with SessionLocal() as db:
                if not db.get(Chapter, chapter_id):
                    status, message = "skipped", "章节已删除，跳过。"
                else:
                    result = await update_summary(chapter_id, db, skip_unchanged=True, provider_id=provider_id, config=config)
                    status, message = result.status, result.message
        except HTTPException as exc:
            status, message = "failed", str(exc.detail)
        except Exception:
            status, message = "failed", "处理失败，已有梗概保留。请检查服务后重试。"
        with SessionLocal.begin() as db:
            db.execute(update(SummaryJobItem).where(SummaryJobItem.id == item_id).values(status=status, message=message))


async def worker():
    while True:
        with SessionLocal.begin() as db:
            db.execute(update(SummaryJob).where(SummaryJob.status == "cancelling").values(status="cancelled"))
            job = db.scalar(select(SummaryJob).where(SummaryJob.status == "queued").order_by(SummaryJob.id))
            job_id = job.id if job else None
            if job:
                changed = db.execute(update(SummaryJob).where(SummaryJob.id == job_id, SummaryJob.status == "queued").values(status="running"))
                if not changed.rowcount:
                    job_id = None
        if job_id is not None:
            await run_job(job_id)
        else:
            await asyncio.sleep(0.5)
