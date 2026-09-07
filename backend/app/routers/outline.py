from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Book, OutlineTextVersion
from .core import require_book

router = APIRouter(prefix="/api/books/{book_id}/outline")


class OutlineSave(BaseModel):
    content: str
    base_version_id: int | None


class OutlineRestore(BaseModel):
    base_version_id: int | None


class OutlineVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    book_id: int
    version_number: int
    content: str
    source: str
    note: str
    created_at: datetime


def latest_version(db: Session, book_id: int) -> OutlineTextVersion | None:
    return db.scalar(select(OutlineTextVersion).where(OutlineTextVersion.book_id == book_id)
                     .order_by(OutlineTextVersion.version_number.desc()).limit(1))


def append_version(db: Session, book: Book, content: str, base_version_id: int | None,
                   source: str = "manual", note: str = "") -> OutlineTextVersion:
    latest = latest_version(db, book.id)
    if base_version_id != (latest.id if latest else None):
        raise HTTPException(409, "大纲已在其他窗口更新。请先复制当前修改，再刷新查看最新版本。")
    version = OutlineTextVersion(book_id=book.id, version_number=latest.version_number + 1 if latest else 1,
                                 content=content, source=source, note=note)
    db.add(version)
    book.updated_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "大纲同时发生更新，请刷新后重试。")
    db.refresh(version)
    return version


@router.get("/versions", response_model=list[OutlineVersionOut])
def list_versions(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return db.scalars(select(OutlineTextVersion).where(OutlineTextVersion.book_id == book_id)
                      .order_by(OutlineTextVersion.version_number.desc())).all()


@router.post("/versions", response_model=OutlineVersionOut)
def save_version(book_id: int, payload: OutlineSave, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    return append_version(db, book, payload.content, payload.base_version_id)


@router.post("/versions/{version_id}/restore", response_model=OutlineVersionOut)
def restore_version(book_id: int, version_id: int, payload: OutlineRestore, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    version = db.get(OutlineTextVersion, version_id)
    if not version or version.book_id != book_id:
        raise HTTPException(404, "大纲版本不存在或不属于当前书籍")
    return append_version(db, book, version.content, payload.base_version_id,
                          source="restored", note=f"从 v{version.version_number} 恢复")
