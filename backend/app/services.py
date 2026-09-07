import hashlib
from pathlib import Path
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import settings
from .models import Book, Chapter, ChapterVersion


def count_words(text: str) -> int:
    # 中文按字符计数，英文/数字按连续 token 计数，过滤空白。
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    western = sum(1 for token in text.split() if any(char.isascii() and char.isalnum() for char in token))
    return chinese + western


def content_root() -> Path:
    root = settings.data_dir / "books"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_content(book_id: int, chapter_id: int, version_number: int, content: str) -> str:
    path = content_root() / str(book_id) / "chapters" / str(chapter_id)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"v{version_number}.txt"
    target.write_text(content, encoding="utf-8")
    return str(target)


def read_content(version: ChapterVersion | None) -> str:
    if not version:
        return ""
    path = Path(version.content_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def next_chapter_number(db: Session, book_id: int) -> int:
    last = db.scalar(select(Chapter.chapter_number).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number.desc()))
    return (last or 0) + 1


def next_version_number(db: Session, chapter_id: int) -> int:
    last = db.scalar(select(ChapterVersion.version_number).where(ChapterVersion.chapter_id == chapter_id).order_by(ChapterVersion.version_number.desc()))
    return (last or 0) + 1


def serialize_chapter(chapter: Chapter, db: Session) -> dict[str, Any]:
    version = db.get(ChapterVersion, chapter.current_version_id) if chapter.current_version_id else None
    content = read_content(version)
    return {**{column.name: getattr(chapter, column.name) for column in Chapter.__table__.columns},
            "current_content": content,
            "summary_is_stale": bool(chapter.summary and chapter.summary_content_sha256 != content_sha256(content))}


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def serialize_book(book: Book, db: Session) -> dict[str, Any]:
    chapters = list(db.scalars(select(Chapter).where(Chapter.book_id == book.id)))
    return {**{column.name: getattr(book, column.name) for column in Book.__table__.columns},
            "chapter_count": len(chapters), "total_word_count": sum(ch.word_count for ch in chapters)}
