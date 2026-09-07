from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, defer

from ..database import get_db
from ..llm_errors import llm_http_error
from ..text_import import decode_text, split_chapters
from ..models import Book, Chapter, ChapterVersion, LLMProvider
from ..schemas import (BookCreate, BookOut, BookUpdate, ChapterCreate, ChapterOut,
                       ChapterReorder, ChapterUpdate, ChapterVersionOut,
                       LLMProviderCreate, LLMProviderOut, VersionCreate, ChapterImport)
from ..services import (count_words, next_chapter_number, next_version_number,
                        read_content, serialize_book, serialize_chapter)

router = APIRouter(prefix="/api")


def require_book(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    return book


def require_chapter(db: Session, chapter_id: int) -> Chapter:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    return chapter


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/books", response_model=list[BookOut])
def list_books(db: Session = Depends(get_db)):
    return [serialize_book(book, db) for book in db.scalars(select(Book).order_by(Book.updated_at.desc())).all()]


@router.post("/books", response_model=BookOut)
def create_book(payload: BookCreate, db: Session = Depends(get_db)):
    book = Book(**payload.model_dump())
    db.add(book)
    db.commit()
    db.refresh(book)
    return serialize_book(book, db)


@router.get("/books/{book_id}", response_model=BookOut)
def get_book(book_id: int, db: Session = Depends(get_db)):
    return serialize_book(require_book(db, book_id), db)


@router.put("/books/{book_id}", response_model=BookOut)
def update_book(book_id: int, payload: BookUpdate, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    if payload.current_chapter_id is not None:
        chapter = db.get(Chapter, payload.current_chapter_id)
        if not chapter or chapter.book_id != book_id:
            raise HTTPException(400, "阅读章节不属于当前书籍")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(book, key, value)
    db.commit()
    db.refresh(book)
    return serialize_book(book, db)


@router.delete("/books/{book_id}")
def delete_book(book_id: int, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    db.delete(book)
    db.commit()
    return {"ok": True}


@router.get("/books/{book_id}/chapters", response_model=list[ChapterOut])
def list_chapters(book_id: int, include_content: bool = True, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return [serialize_chapter(chapter, db, include_content) for chapter in db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()]


@router.post("/books/{book_id}/chapters", response_model=ChapterOut)
def create_chapter(book_id: int, payload: ChapterCreate, db: Session = Depends(get_db)):
    db.execute(update(Book).where(Book.id == book_id).values(id=Book.id))
    chapter = add_chapter(book_id, payload, db)
    db.commit()
    db.refresh(chapter)
    return serialize_chapter(chapter, db)


def add_chapter(book_id: int, payload: ChapterCreate, db: Session):
    book = require_book(db, book_id)
    number = payload.chapter_number or next_chapter_number(db, book_id)
    chapter = Chapter(book_id=book_id, chapter_number=number, title=payload.title, status=payload.status, word_count=count_words(payload.content))
    db.add(chapter)
    db.flush()
    version_number = 1
    version = ChapterVersion(chapter_id=chapter.id, version_number=version_number, content_path="", content_text=payload.content, word_count=count_words(payload.content), source=payload.source, note=payload.note)
    db.add(version)
    db.flush()
    chapter.current_version_id = version.id
    if not book.current_chapter_id:
        book.current_chapter_id = chapter.id
    db.flush()
    return chapter


@router.post("/books/{book_id}/chapters/import", response_model=list[ChapterOut])
async def import_chapters(book_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    require_book(db, book_id)
    raw = await file.read(30 * 1024 * 1024 + 1)
    text, _ = decode_text(raw)
    title = Path(file.filename or "导入章节").stem
    return confirm_import(book_id, ChapterImport(chapters=split_chapters(text, title)), db)


@router.post("/books/{book_id}/chapters/import/preview")
async def preview_import(book_id: int, split: bool = True, file: UploadFile = File(...), db: Session = Depends(get_db)):
    require_book(db, book_id)
    text, encoding = decode_text(await file.read(30 * 1024 * 1024 + 1))
    return {"encoding": encoding, "chapters": split_chapters(text, Path(file.filename or "导入章节").stem, split)}


@router.post("/books/{book_id}/chapters/import/confirm", response_model=list[ChapterOut])
def confirm_import(book_id: int, payload: ChapterImport, db: Session = Depends(get_db)):
    db.execute(update(Book).where(Book.id == book_id).values(id=Book.id))
    require_book(db, book_id)
    chapters = [add_chapter(book_id, ChapterCreate(title=item.title, content=item.content, source="imported"), db) for item in payload.chapters]
    db.commit()
    return [serialize_chapter(chapter, db, False) for chapter in chapters]


@router.get("/chapters/{chapter_id}", response_model=ChapterOut)
def get_chapter(chapter_id: int, db: Session = Depends(get_db)):
    return serialize_chapter(require_chapter(db, chapter_id), db)


@router.put("/chapters/{chapter_id}", response_model=ChapterOut)
def update_chapter(chapter_id: int, payload: ChapterUpdate, db: Session = Depends(get_db)):
    chapter = require_chapter(db, chapter_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(chapter, key, value)
    if payload.chapter_number is not None:
        conflict = db.scalar(select(Chapter).where(Chapter.book_id == chapter.book_id, Chapter.chapter_number == payload.chapter_number, Chapter.id != chapter.id))
        if conflict:
            raise HTTPException(409, "同一本书内章节编号不能重复")
    db.commit()
    db.refresh(chapter)
    return serialize_chapter(chapter, db)


@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, confirm: bool = False, db: Session = Depends(get_db)):
    if not confirm:
        raise HTTPException(400, "删除章节需要确认")
    chapter = require_chapter(db, chapter_id)
    db.delete(chapter)
    db.commit()
    return {"ok": True}


@router.post("/books/{book_id}/chapters/reorder")
def reorder_chapters(book_id: int, payload: ChapterReorder, db: Session = Depends(get_db)):
    db.execute(update(Book).where(Book.id == book_id).values(id=Book.id))
    require_book(db, book_id)
    chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()
    known = {chapter.id: chapter for chapter in chapters}
    if set(payload.chapter_ids) != set(known) or len(payload.chapter_ids) != len(known):
        raise HTTPException(400, "必须提供当前书籍的全部章节，且不能重复")
    # Use temporary negative numbers to avoid unique-key collisions while swapping order.
    for index, chapter_id in enumerate(payload.chapter_ids, start=1):
        known[chapter_id].chapter_number = -index
    db.flush()
    for index, chapter_id in enumerate(payload.chapter_ids, start=1):
        known[chapter_id].chapter_number = index
    db.commit()
    return [serialize_chapter(known[chapter_id], db, False) for chapter_id in payload.chapter_ids]


@router.post("/chapters/{chapter_id}/versions", response_model=ChapterVersionOut)
def create_version(chapter_id: int, payload: VersionCreate, db: Session = Depends(get_db)):
    chapter = require_chapter(db, chapter_id)
    book = require_book(db, chapter.book_id)
    if book.current_chapter_id == chapter_id:
        book.current_chapter_id = db.scalar(select(Chapter.id).where(Chapter.book_id == book.id, Chapter.id != chapter_id).order_by(Chapter.chapter_number))
    # Acquire the database write lock and check the source revision before allocating a number.
    base = payload.base_version_id if "base_version_id" in payload.model_fields_set else chapter.current_version_id
    changed = db.execute(update(Chapter).where(Chapter.id == chapter_id, Chapter.current_version_id == base)
                         .values(word_count=count_words(payload.content)))
    if not changed.rowcount:
        db.rollback()
        raise HTTPException(409, "本章已在其他窗口更新。当前草稿已保留，请重新加载最新版本后再保存。")
    number = next_version_number(db, chapter_id)
    version = ChapterVersion(chapter_id=chapter_id, version_number=number, content_path="", content_text=payload.content, word_count=count_words(payload.content), source=payload.source, note=payload.note)
    db.add(version)
    db.flush()
    chapter.current_version_id = version.id
    chapter.word_count = version.word_count
    if payload.title is not None:
        chapter.title = payload.title
    db.commit()
    db.refresh(version)
    return {**{column.name: getattr(version, column.name) for column in ChapterVersion.__table__.columns}, "content": payload.content}


@router.get("/chapters/{chapter_id}/versions", response_model=list[ChapterVersionOut])
def list_versions(chapter_id: int, include_content: bool = True, db: Session = Depends(get_db)):
    require_chapter(db, chapter_id)
    query = select(ChapterVersion).where(ChapterVersion.chapter_id == chapter_id).order_by(ChapterVersion.version_number.desc())
    if not include_content:
        query = query.options(defer(ChapterVersion.content_text))
    versions = db.scalars(query).all()
    return [{**{column.name: getattr(item, column.name) for column in ChapterVersion.__table__.columns if column.name != "content_text"}, "content": read_content(item) if include_content else ""} for item in versions]


@router.get("/chapters/{chapter_id}/diff")
def diff_versions(chapter_id: int, left: int, right: int, db: Session = Depends(get_db)):
    require_chapter(db, chapter_id)
    left_version = db.get(ChapterVersion, left)
    right_version = db.get(ChapterVersion, right)
    if not left_version or not right_version or left_version.chapter_id != chapter_id or right_version.chapter_id != chapter_id:
        raise HTTPException(404, "版本不存在或不属于当前章节")
    return {"left": {"version": left_version.version_number, "content": read_content(left_version)}, "right": {"version": right_version.version_number, "content": read_content(right_version)}}


@router.post("/chapters/{chapter_id}/versions/{version_id}/restore", response_model=ChapterVersionOut)
def restore_version(chapter_id: int, version_id: int, base_version_id: int | None = None, db: Session = Depends(get_db)):
    chapter = require_chapter(db, chapter_id)
    version = db.get(ChapterVersion, version_id)
    if not version or version.chapter_id != chapter_id:
        raise HTTPException(404, "版本不存在或不属于当前章节")
    restored = VersionCreate(content=read_content(version), base_version_id=base_version_id if base_version_id is not None else chapter.current_version_id, source="revised", note=f"从 v{version.version_number} 恢复")
    return create_version(chapter_id, restored, db)


@router.get("/llm/providers", response_model=list[LLMProviderOut])
def list_providers(db: Session = Depends(get_db)):
    providers = db.scalars(
        select(LLMProvider)
        .where(LLMProvider.provider_type == "openai_compatible")
        .order_by(LLMProvider.updated_at.desc())
    ).all()
    return [_serialize_provider(item) for item in providers]


def _serialize_provider(item: LLMProvider) -> dict[str, Any]:
    """Expose only the generic provider contract, never legacy runtime fields."""
    return {
        "id": item.id,
        "provider_name": item.provider_name,
        "provider_type": item.provider_type,
        "base_url": item.base_url,
        "model": item.model,
        "temperature": item.temperature,
        "max_tokens": item.max_tokens,
        "timeout": item.timeout,
        "enabled": item.enabled,
        "has_api_key": bool(item.api_key),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.post("/llm/providers", response_model=LLMProviderOut)
def create_provider(payload: LLMProviderCreate, db: Session = Depends(get_db)):
    if payload.enabled:
        db.execute(update(LLMProvider).where(LLMProvider.provider_type == "openai_compatible").values(enabled=False))
    item = LLMProvider(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize_provider(item)


@router.put("/llm/providers/{provider_id}", response_model=LLMProviderOut)
def update_provider(provider_id: int, payload: LLMProviderCreate, db: Session = Depends(get_db)):
    item = db.get(LLMProvider, provider_id)
    if not item or item.provider_type != "openai_compatible":
        raise HTTPException(404, "LLM 配置不存在")
    if payload.enabled:
        db.execute(update(LLMProvider).where(LLMProvider.provider_type == "openai_compatible", LLMProvider.id != provider_id).values(enabled=False))
    values = payload.model_dump()
    if not values["api_key"] and item.api_key:
        values["api_key"] = item.api_key
    for key, value in values.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return _serialize_provider(item)


@router.delete("/llm/providers/{provider_id}")
def delete_provider(provider_id: int, db: Session = Depends(get_db)):
    item = db.get(LLMProvider, provider_id)
    if not item or item.provider_type != "openai_compatible":
        raise HTTPException(404, "LLM 配置不存在")
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.delete("/llm/providers/{provider_id}/key")
def clear_provider_key(provider_id: int, db: Session = Depends(get_db)):
    item = db.get(LLMProvider, provider_id)
    if not item or item.provider_type != "openai_compatible":
        raise HTTPException(404, "LLM 配置不存在")
    item.api_key = ""
    item.enabled = False
    db.commit()
    return _serialize_provider(item)


@router.get("/llm/providers/{provider_id}/models")
async def provider_models(provider_id: int, db: Session = Depends(get_db)):
    item = db.get(LLMProvider, provider_id)
    if not item or item.provider_type != "openai_compatible":
        raise HTTPException(404, "LLM 配置不存在")
    if not item.api_key:
        raise HTTPException(400, "请先保存 API Key。")
    try:
        async with httpx.AsyncClient(timeout=item.timeout) as client:
            response = await client.get(item.base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {item.api_key}"})
            response.raise_for_status()
        values = response.json()["data"]
        return sorted({entry["id"] for entry in values if isinstance(entry, dict) and isinstance(entry.get("id"), str)})
    except httpx.HTTPStatusError as exc:
        raise HTTPException(400, llm_http_error(exc, item.model))
    except (httpx.RequestError, ValueError, KeyError, TypeError):
        raise HTTPException(400, "无法读取模型列表，请检查服务地址；也可手动填写模型 ID。")


@router.post("/llm/providers/{provider_id}/test")
async def test_provider(provider_id: int, db: Session = Depends(get_db)):
    item = db.get(LLMProvider, provider_id)
    if not item or item.provider_type != "openai_compatible":
        raise HTTPException(404, "LLM 配置不存在")
    if not item.api_key:
        return {"ok": False, "message": "未填写 API Key，请先保存密钥再测试连接。"}
    try:
        async with __import__("httpx").AsyncClient(timeout=item.timeout) as client:
            response = await client.post(item.base_url.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {item.api_key}"}, json={"model": item.model, "messages": [{"role": "user", "content": "请只回复：连接成功"}], "max_tokens": 10})
            response.raise_for_status()
        return {"ok": True, "message": "连接成功"}
    except Exception as exc:
        if isinstance(exc, httpx.ConnectError):
            parsed = urlparse(item.base_url)
            host = parsed.hostname or item.base_url
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            return {"ok": False, "message": f"无法连接 LLM 接口：{host}:{port}。请确认 Base URL 为 {item.base_url}。"}
        if isinstance(exc, httpx.TimeoutException):
            return {"ok": False, "message": f"连接 LLM 接口超时（{item.timeout} 秒）。请检查服务状态或 Base URL。"}
        if isinstance(exc, httpx.HTTPStatusError):
            return {"ok": False, "message": llm_http_error(exc, item.model)}
        return {"ok": False, "message": str(exc) or exc.__class__.__name__}


@router.get("/books/{book_id}/export.txt", response_class=PlainTextResponse)
def export_text(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()
    return "\n\n".join([f"第{item.chapter_number}章 {item.title}\n{serialize_chapter(item, db)['current_content']}" for item in chapters])
