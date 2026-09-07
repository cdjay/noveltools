"""Convert segmented outlines without deleting or modifying their source rows."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Book, Outline, OutlineBlock, OutlineTextVersion


def legacy_outline_text(outline: Outline, blocks: list[OutlineBlock]) -> str:
    parts = [outline.title, outline.description]
    for block in blocks:
        fields = [block.title]
        if block.start_chapter is not None or block.end_chapter is not None:
            fields.append(f"章节范围：{block.start_chapter if block.start_chapter is not None else '未指定'} — {block.end_chapter if block.end_chapter is not None else '未指定'}")
        for name, label in [("summary", "内容"), ("objective", "目标"), ("conflict", "冲突"),
                            ("turning_point", "转折"), ("ending", "结尾")]:
            value = getattr(block, name)
            if value:
                fields.append(f"{label}：{value}")
        parts.append("\n".join(fields))
    return "\n\n".join(part for part in parts if part)


def migrate_book_outlines(db: Session, book_id: int) -> None:
    if db.scalar(select(OutlineTextVersion.id).where(OutlineTextVersion.book_id == book_id).limit(1)):
        return
    outlines = db.scalars(select(Outline).where(Outline.book_id == book_id)
                          .order_by(Outline.updated_at, Outline.id)).all()
    for number, outline in enumerate(outlines, 1):
        blocks = list(db.scalars(select(OutlineBlock).where(OutlineBlock.outline_id == outline.id)
                                 .order_by(OutlineBlock.order_index, OutlineBlock.id)))
        db.add(OutlineTextVersion(book_id=book_id, version_number=number,
                                 content=legacy_outline_text(outline, blocks), source="legacy",
                                 note=f"由旧大纲《{outline.title}》v{outline.version} 转为全文",
                                 created_at=outline.updated_at))
    db.flush()


def migrate_legacy_outlines() -> None:
    with SessionLocal.begin() as db:
        for book_id in db.scalars(select(Book.id)).all():
            migrate_book_outlines(db, book_id)
