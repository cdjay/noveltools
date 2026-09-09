"""Portable book archives, restored as a new book in one transaction."""
from datetime import datetime
from copy import deepcopy
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Book, Chapter, ChapterVersion, OutlineTextVersion, CurrentStateVersion, CurrentStateSettings, BookMaterialVersion
from ..models import ChapterPlan, ChapterPlanVersion, ChapterPlanUse
from ..schemas import BookCreate, BookOut
from ..services import count_words, read_content, serialize_book
from .core import require_book

router = APIRouter(prefix="/api")


class SavedVersion(BaseModel):
    id: int
    version_number: int = Field(ge=1)
    content: str
    source: str
    note: str
    created_at: datetime


class SavedChapter(BaseModel):
    id: int
    chapter_number: int = Field(ge=1)
    title: str
    status: str
    current_version_id: int | None
    reading_position: int = Field(ge=0)
    summary: str
    summary_content_sha256: str
    summary_source_version_id: int | None
    summary_model: str
    summary_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    versions: list[SavedVersion]


class SavedStateVersion(SavedVersion):
    model: str = ""
    prompt: str = ""
    source_manifest: dict = Field(default_factory=dict)


class SavedMaterialVersion(SavedVersion):
    material_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    title: str = Field(min_length=1, max_length=120)


class SavedPlanVersion(SavedVersion):
    title: str = Field(min_length=1, max_length=200)
    volume: str = Field(min_length=1, max_length=200)
    status: Literal['draft', 'confirmed']


class SavedPlan(BaseModel):
    id: str
    position: int = Field(ge=1)
    current_version_id: int
    chapter_id: int | None = None
    reviewed_plan_version_id: int | None = None
    reviewed_chapter_version_id: int | None = None
    created_at: datetime
    versions: list[SavedPlanVersion]


class SavedPlanUse(BaseModel):
    plan_version_id: int
    chapter_version_id: int
    created_at: datetime


class BookArchive(BaseModel):
    format: Literal["noveltools-book"] = "noveltools-book"
    schema_version: Literal[1, 2, 3, 4] = 4
    book: BookCreate
    created_at: datetime
    updated_at: datetime
    current_chapter_id: int | None
    chapters: list[SavedChapter] = Field(max_length=10000)
    outlines: list[SavedVersion]
    current_state_versions: list[SavedStateVersion] = Field(default_factory=list)
    current_state_prompt: str | None = None
    writing_rule_keys: list[str] = Field(default_factory=list, max_length=30)
    material_versions: list[SavedMaterialVersion] = Field(default_factory=list)
    chapter_plans: list[SavedPlan] = Field(default_factory=list)
    chapter_plan_uses: list[SavedPlanUse] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_references(self):
        def unique(values):
            if len(values) != len(set(values)):
                raise ValueError("备份包含重复编号")
        unique([c.id for c in self.chapters])
        unique([c.chapter_number for c in self.chapters])
        unique([o.version_number for o in self.outlines])
        unique([s.version_number for s in self.current_state_versions])
        unique([(m.material_key, m.version_number) for m in self.material_versions])
        unique(self.writing_rule_keys)
        unique([p.id for p in self.chapter_plans])
        unique([p.position for p in self.chapter_plans])
        unique([p.chapter_id for p in self.chapter_plans if p.chapter_id is not None])
        unique([v.id for p in self.chapter_plans for v in p.versions])
        unique([u.chapter_version_id for u in self.chapter_plan_uses])
        chapters = {c.id: c for c in self.chapters}
        plan_versions = {v.id for p in self.chapter_plans for v in p.versions}
        body_versions = {v.id for c in self.chapters for v in c.versions}
        for p in self.chapter_plans:
            ids = {v.id for v in p.versions}
            unique([v.version_number for v in p.versions])
            if p.current_version_id not in ids or (p.reviewed_plan_version_id is not None and p.reviewed_plan_version_id not in ids):
                raise ValueError('章纲版本引用无效')
            if p.chapter_id is not None and p.chapter_id not in chapters:
                raise ValueError('章纲关联正文不存在')
            if p.reviewed_chapter_version_id is not None and p.chapter_id is not None and p.reviewed_chapter_version_id not in {v.id for v in chapters[p.chapter_id].versions}:
                raise ValueError('章纲审核引用无效')
        for use in self.chapter_plan_uses:
            if use.plan_version_id not in plan_versions or use.chapter_version_id not in body_versions:
                raise ValueError('章纲生成来源引用无效')
        if not set(self.writing_rule_keys) <= {m.material_key for m in self.material_versions}:
            raise ValueError("写作规则资料引用无效")
        for material in self.material_versions:
            if not material.title.strip() or (material.material_key == 'writing-rules' and material.title != '写作规则'):
                raise ValueError("资料标题无效")
        if self.current_chapter_id is not None and self.current_chapter_id not in {c.id for c in self.chapters}:
            raise ValueError("当前章节引用无效")
        for chapter in self.chapters:
            unique([v.id for v in chapter.versions])
            unique([v.version_number for v in chapter.versions])
            ids = {v.id for v in chapter.versions}
            if chapter.current_version_id not in ids:
                raise ValueError("当前正文版本引用无效")
            if chapter.summary_source_version_id is not None and chapter.summary_source_version_id not in ids:
                raise ValueError("梗概来源版本引用无效")
        return self


def version_out(version, content):
    return {key: getattr(version, key) for key in ('id', 'version_number', 'source', 'note', 'created_at')} | {'content': content}


@router.get("/books/{book_id}/backup")
def export_backup(book_id: int, db: Session = Depends(get_db)):
    # A consistent snapshot includes all version pointers and text, even during edits.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    book = require_book(db, book_id)
    chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()
    saved = []
    for chapter in chapters:
        versions = db.scalars(select(ChapterVersion).where(ChapterVersion.chapter_id == chapter.id).order_by(ChapterVersion.version_number)).all()
        saved.append({key: getattr(chapter, key) for key in SavedChapter.model_fields if key != 'versions'} |
                     {'versions': [version_out(v, read_content(v)) for v in versions]})
    outlines = db.scalars(select(OutlineTextVersion).where(OutlineTextVersion.book_id == book_id).order_by(OutlineTextVersion.version_number)).all()
    states = db.scalars(select(CurrentStateVersion).where(CurrentStateVersion.book_id == book_id).order_by(CurrentStateVersion.version_number)).all()
    state_settings = db.get(CurrentStateSettings, book_id)
    materials = db.scalars(select(BookMaterialVersion).where(BookMaterialVersion.book_id == book_id)
                          .order_by(BookMaterialVersion.material_key, BookMaterialVersion.version_number)).all()
    saved_plans = []
    for p in db.scalars(select(ChapterPlan).where(ChapterPlan.book_id == book_id).order_by(ChapterPlan.position)):
        versions = db.scalars(select(ChapterPlanVersion).where(ChapterPlanVersion.plan_id == p.id).order_by(ChapterPlanVersion.version_number)).all()
        saved_plans.append({key: getattr(p, key) for key in SavedPlan.model_fields if key != 'versions'} |
            {'versions': [version_out(v, v.content) | {'title': v.title, 'volume': v.volume, 'status': v.status} for v in versions]})
    uses = db.scalars(select(ChapterPlanUse).join(ChapterPlanVersion, ChapterPlanVersion.id == ChapterPlanUse.plan_version_id)
                     .join(ChapterPlan, ChapterPlan.id == ChapterPlanVersion.plan_id).where(ChapterPlan.book_id == book_id)).all()
    try:
        archive = BookArchive(book=BookCreate.model_validate({key: getattr(book, key) for key in BookCreate.model_fields}),
            created_at=book.created_at, updated_at=book.updated_at,
            current_chapter_id=book.current_chapter_id if book.current_chapter_id in {c.id for c in chapters} else None,
            chapters=saved, outlines=[version_out(o, o.content) for o in outlines],
            current_state_versions=[version_out(s, s.content) | {"model": s.model, "prompt": s.prompt, "source_manifest": s.source_manifest} for s in states],
            current_state_prompt=state_settings.prompt if state_settings else None,
            writing_rule_keys=state_settings.writing_rule_keys if state_settings else [],
            material_versions=[version_out(m, m.content) | {"material_key": m.material_key, "title": m.title} for m in materials],
            chapter_plans=saved_plans, chapter_plan_uses=[{k: getattr(u, k) for k in SavedPlanUse.model_fields} for u in uses])
    except ValidationError:
        raise HTTPException(409, "书籍的版本引用不完整，请先修复数据后备份。")
    return Response(archive.model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="noveltools-book-{book_id}.json"'})


async def parse_archive(file):
    raw = await file.read(200 * 1024 * 1024 + 1)
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(400, "备份超过 200 MB，无法在界面恢复。")
    try:
        return BookArchive.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise HTTPException(400, "备份格式、版本或内容引用无效。未写入任何书籍内容。")


@router.post("/backups/preview")
async def preview_backup(file: UploadFile = File(...)):
    archive = await parse_archive(file)
    return {"title": archive.book.title, "chapters": len(archive.chapters),
            "versions": sum(len(c.versions) for c in archive.chapters), "outlines": len(archive.outlines),
            "current_states": len(archive.current_state_versions),
            "materials": len({m.material_key for m in archive.material_versions}), "material_versions": len(archive.material_versions),
            "chapter_plans": len(archive.chapter_plans)}


@router.post("/backups/restore", response_model=BookOut)
async def restore_backup(file: UploadFile = File(...), db: Session = Depends(get_db)):
    archive = await parse_archive(file)
    book = Book(**archive.book.model_dump(), created_at=archive.created_at, updated_at=archive.updated_at)
    book.title += "（恢复副本）"
    db.add(book)
    db.flush()
    chapter_ids, version_ids = {}, {}
    for saved in archive.chapters:
        values = saved.model_dump(exclude={'id', 'versions', 'current_version_id', 'summary_source_version_id'})
        chapter = Chapter(book_id=book.id, **values)
        db.add(chapter)
        db.flush()
        chapter_ids[saved.id] = chapter.id
        ids, counts = {}, {}
        for version in saved.versions:
            fields = version.model_dump(exclude={'id', 'content'})
            restored = ChapterVersion(chapter_id=chapter.id, content_path="", content_text=version.content,
                                      word_count=count_words(version.content), **fields)
            db.add(restored)
            db.flush()
            ids[version.id], counts[version.id] = restored.id, restored.word_count
            version_ids[version.id] = restored.id
        chapter.current_version_id = ids[saved.current_version_id]
        chapter.word_count = counts[saved.current_version_id]
        chapter.summary_source_version_id = ids.get(saved.summary_source_version_id)
        if saved.id == archive.current_chapter_id:
            book.current_chapter_id = chapter.id
    for outline in archive.outlines:
        db.add(OutlineTextVersion(book_id=book.id, **outline.model_dump(exclude={'id'})))
    for state in archive.current_state_versions:
        fields = state.model_dump(exclude={'id', 'source_manifest'})
        manifest = remap_manifest(state.source_manifest, chapter_ids, version_ids, book.title)
        db.add(CurrentStateVersion(book_id=book.id, source_manifest=manifest, **fields))
    if archive.current_state_prompt is not None:
        db.add(CurrentStateSettings(book_id=book.id, prompt=archive.current_state_prompt, writing_rule_keys=archive.writing_rule_keys))
    for material in archive.material_versions:
        db.add(BookMaterialVersion(book_id=book.id, **material.model_dump(exclude={'id'})))
    plan_version_ids = {}
    for saved in archive.chapter_plans:
        p = ChapterPlan(id=uuid4().hex, book_id=book.id, position=saved.position,
                        created_at=saved.created_at, chapter_id=chapter_ids.get(saved.chapter_id))
        db.add(p); db.flush()
        for v in saved.versions:
            restored = ChapterPlanVersion(plan_id=p.id, **v.model_dump(exclude={'id'}))
            db.add(restored); db.flush()
            plan_version_ids[v.id] = restored.id
        p.current_version_id = plan_version_ids[saved.current_version_id]
        p.reviewed_plan_version_id = plan_version_ids.get(saved.reviewed_plan_version_id)
        p.reviewed_chapter_version_id = version_ids.get(saved.reviewed_chapter_version_id) if p.chapter_id else None
    for use in archive.chapter_plan_uses:
        db.add(ChapterPlanUse(plan_version_id=plan_version_ids[use.plan_version_id],
            chapter_version_id=version_ids[use.chapter_version_id], created_at=use.created_at))
    db.commit()
    return serialize_book(book, db)


def remap_manifest(source, chapter_ids, version_ids, title):
    manifest = deepcopy(source)
    if not manifest:
        return manifest
    # Deleted historical sources remain unresolved rather than pointing at unrelated new IDs.
    try:
        manifest['book_title'] = title
        for chapter in manifest['chapters']:
            chapter['chapter_id'] = chapter_ids.get(chapter['chapter_id'])
            chapter['version_id'] = version_ids.get(chapter['version_id'])
            chapter['summary_source_version_id'] = version_ids.get(chapter['summary_source_version_id'])
        for key in ('body_chapter_ids', 'missing_summary_chapter_ids', 'stale_summary_chapter_ids'):
            manifest[key] = [chapter_ids.get(value) for value in manifest[key]]
    except (KeyError, TypeError, AttributeError):
        raise HTTPException(400, "状态速查的来源记录无效，未恢复任何内容。")
    return manifest
