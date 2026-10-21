"""Independent versioned plans with explicit chapter creation and stable bindings."""
import re
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (Book, Chapter, ChapterVersion, ChapterPlan, ChapterPlanVersion,
                      ChapterPlanUse, OutlineTextVersion, BookMaterialVersion)
from .core import require_book

router = APIRouter(prefix="/api/books/{book_id}/chapter-plans", tags=["Chapter plans"])
GUIDANCE_KEY = "chapter-plan-guidance"


class PlanText(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    volume: str = Field(default="第一卷", min_length=1, max_length=200)
    content: str = Field(min_length=1)
    status: Literal["draft", "confirmed"] = "draft"

    @field_validator("title", "volume", "content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("内容不能为空")
        return value


class PlanSave(PlanText):
    base_version_id: int


class PlanBase(BaseModel):
    base_version_id: int


@router.post("/{plan_id}/create-chapter")
def create_blank_chapter(book_id: int, plan_id: str, payload: PlanBase, db: Session = Depends(get_db)):
    from .core import add_chapter
    from ..schemas import ChapterCreate
    lock(db, book_id)
    p = require_plan(db, book_id, plan_id, payload.base_version_id)
    v = db.get(ChapterPlanVersion, p.current_version_id)
    if p.chapter_id:
        return plan_out(db, p)
    existing = db.scalar(select(Chapter).where(Chapter.book_id == book_id,
        (Chapter.chapter_number == p.position) | (Chapter.title == v.title)))
    if existing:
        raise HTTPException(409, f"第 {existing.chapter_number} 章《{existing.title}》已存在，请在正文关联中手动绑定，未创建重复章节。")
    chapter = add_chapter(book_id, ChapterCreate(title=v.title, chapter_number=p.position,
        content="", source="plan", note=f"由章纲 {p.id} v{v.version_number} 创建空白正文"), db)
    p.chapter_id = chapter.id
    db.commit()
    return plan_out(db, p)


class Bind(PlanBase):
    chapter_id: int | None
    previous_chapter_id: int | None


class Review(PlanBase):
    chapter_version_id: int


class Reorder(BaseModel):
    plan_ids: list[str]
    previous_order: list[str]


def lock(db, book_id):
    db.execute(update(Book).where(Book.id == book_id).values(id=Book.id))
    require_book(db, book_id)
    db.expire_all()


def plans(db, book_id):
    return db.scalars(select(ChapterPlan).where(ChapterPlan.book_id == book_id)
                      .order_by(ChapterPlan.position, ChapterPlan.id)).all()


def require_plan(db, book_id, plan_id, base=None):
    p = db.get(ChapterPlan, plan_id)
    if not p or p.book_id != book_id:
        raise HTTPException(404, "章纲不存在或不属于本书")
    if base is not None and p.current_version_id != base:
        raise HTTPException(409, "章纲已在其他窗口更新，请保留草稿并重新读取。")
    return p


def version_out(v):
    return {c.name: getattr(v, c.name) for c in ChapterPlanVersion.__table__.columns}


def plan_out(db, p):
    chapter = db.get(Chapter, p.chapter_id) if p.chapter_id else None
    v = db.get(ChapterPlanVersion, p.current_version_id)
    uses = db.execute(select(ChapterPlanUse, ChapterVersion).join(ChapterVersion,
        ChapterVersion.id == ChapterPlanUse.chapter_version_id).join(ChapterPlanVersion,
        ChapterPlanVersion.id == ChapterPlanUse.plan_version_id).where(ChapterPlanVersion.plan_id == p.id)
        .order_by(ChapterPlanUse.id.desc())).all()
    return {"id": p.id, "book_id": p.book_id, "position": p.position, "version": version_out(v),
            "chapter_id": p.chapter_id, "chapter_title": chapter.title if chapter else None,
            "chapter_number": chapter.chapter_number if chapter else None,
            "chapter_version_id": chapter.current_version_id if chapter else None,
            "needs_review": bool(chapter and (p.reviewed_plan_version_id != p.current_version_id or
                                             p.reviewed_chapter_version_id != chapter.current_version_id)),
            "uses": [{"plan_version_id": u.plan_version_id, "chapter_version_id": c.id,
                      "chapter_id": c.chapter_id, "body_version_number": c.version_number} for u, c in uses]}


def guidance(db, book_id):
    v = db.scalar(select(BookMaterialVersion).where(BookMaterialVersion.book_id == book_id,
        BookMaterialVersion.material_key == GUIDANCE_KEY).order_by(BookMaterialVersion.version_number.desc()))
    return {"id": v.id, "version_number": v.version_number, "content": v.content, "material_key": GUIDANCE_KEY} if v else None


def snapshot(db, book_id):
    """Ordering, edits, bindings and shared guidance are all generation sources."""
    rows = plans(db, book_id)
    g = guidance(db, book_id)
    return {"plans": [(p.id, p.position, p.current_version_id, p.chapter_id) for p in rows],
            "guidance_id": g['id'] if g else None}


def next_plan(db, book_id):
    rows = plans(db, book_id)
    if not rows:
        return None  # Legacy books can continue using their whole outline.
    p = next((p for p in rows if p.chapter_id is None), None)
    if p is None:
        raise HTTPException(409, "已用完章纲，请先新增并确认下一章章纲。")
    if db.get(ChapterPlanVersion, p.current_version_id).status != 'confirmed':
        raise HTTPException(409, "下一份章纲尚未确认，请先审阅并确认，生成不会跳过草稿。")
    return p


def add_version(db, p, fields, source='manual', note=''):
    old = db.get(ChapterPlanVersion, p.current_version_id) if p.current_version_id else None
    v = ChapterPlanVersion(plan_id=p.id, version_number=old.version_number + 1 if old else 1,
                           source=source, note=note, **fields)
    db.add(v); db.flush()
    p.current_version_id = v.id
    if p.chapter_id:
        chapter = db.get(Chapter, p.chapter_id)
        if chapter:
            chapter.status = 'draft'
    db.flush()
    return v


def create_plan(db, book_id, position, fields, source='manual', note=''):
    p = ChapterPlan(id=uuid4().hex, book_id=book_id, position=position)
    db.add(p); db.flush()
    add_version(db, p, fields, source, note)
    return p


def record_generation(db, p, chapter):
    p.chapter_id = chapter.id
    # Generation is provenance, not a claim of semantic review passing.
    db.add(ChapterPlanUse(plan_version_id=p.current_version_id, chapter_version_id=chapter.current_version_id))
    db.flush()


@router.get("")
def listing(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return {"plans": [plan_out(db, p) for p in plans(db, book_id)], "guidance": guidance(db, book_id)}


@router.post("")
def create(book_id: int, payload: PlanText, db: Session = Depends(get_db)):
    lock(db, book_id)
    p = create_plan(db, book_id, len(plans(db, book_id)) + 1, payload.model_dump())
    db.commit()
    return plan_out(db, p)


@router.post("/reorder")
def reorder(book_id: int, payload: Reorder, db: Session = Depends(get_db)):
    lock(db, book_id)
    rows = plans(db, book_id)
    ids = [p.id for p in rows]
    if payload.previous_order != ids:
        raise HTTPException(409, "章纲顺序已变化，请重新读取。")
    if len(payload.plan_ids) != len(ids) or set(payload.plan_ids) != set(ids):
        raise HTTPException(400, "请提供本书全部章纲且不得重复")
    lookup = {p.id: p for p in rows}
    for i, pid in enumerate(payload.plan_ids, 1):
        lookup[pid].position = i
    db.commit()
    return listing(book_id, db)


@router.get("/{plan_id}/versions")
def history(book_id: int, plan_id: str, db: Session = Depends(get_db)):
    require_plan(db, book_id, plan_id)
    return [version_out(v) for v in db.scalars(select(ChapterPlanVersion).where(ChapterPlanVersion.plan_id == plan_id)
                                             .order_by(ChapterPlanVersion.version_number.desc()))]


@router.get("/{plan_id}/export.md")
def export(book_id: int, plan_id: str, db: Session = Depends(get_db)):
    p = require_plan(db, book_id, plan_id)
    v = db.get(ChapterPlanVersion, p.current_version_id)
    g = guidance(db, book_id)
    return Response(f"# {v.volume} · {v.title}\n\n{v.content}" + (f"\n\n## 共同执行说明\n\n{g['content']}" if g else ''),
                    media_type='text/markdown; charset=utf-8')


@router.get("/{plan_id}")
def detail(book_id: int, plan_id: str, db: Session = Depends(get_db)):
    return plan_out(db, require_plan(db, book_id, plan_id))


@router.post("/{plan_id}/versions")
def save(book_id: int, plan_id: str, payload: PlanSave, db: Session = Depends(get_db)):
    lock(db, book_id)
    p = require_plan(db, book_id, plan_id, payload.base_version_id)
    add_version(db, p, payload.model_dump(exclude={'base_version_id'}))
    db.commit()
    return plan_out(db, p)


@router.post("/{plan_id}/versions/{version_id}/restore")
def restore(book_id: int, plan_id: str, version_id: int, payload: PlanBase, db: Session = Depends(get_db)):
    lock(db, book_id)
    p = require_plan(db, book_id, plan_id, payload.base_version_id)
    v = db.get(ChapterPlanVersion, version_id)
    if not v or v.plan_id != p.id:
        raise HTTPException(404, "历史版本不属于此章纲")
    # A restored old approval cannot authorize generation against today's facts.
    add_version(db, p, {'title': v.title, 'volume': v.volume, 'content': v.content, 'status': 'draft'},
                'restored', f'恢复自 v{v.version_number}，请重新确认')
    db.commit()
    return plan_out(db, p)


@router.post("/{plan_id}/binding")
def bind(book_id: int, plan_id: str, payload: Bind, db: Session = Depends(get_db)):
    lock(db, book_id)
    p = require_plan(db, book_id, plan_id, payload.base_version_id)
    if p.chapter_id != payload.previous_chapter_id:
        raise HTTPException(409, "正文关联已变化，请重新读取")
    if payload.chapter_id is not None:
        chapter = db.get(Chapter, payload.chapter_id)
        if not chapter or chapter.book_id != book_id:
            raise HTTPException(400, "只能关联本书已有章节")
        if db.scalar(select(ChapterPlan.id).where(ChapterPlan.chapter_id == chapter.id, ChapterPlan.id != p.id)):
            raise HTTPException(409, "此正文已关联另一份章纲")
    p.chapter_id = payload.chapter_id
    p.reviewed_plan_version_id = p.reviewed_chapter_version_id = None
    db.commit()
    return plan_out(db, p)


@router.post("/{plan_id}/review")
def review(book_id: int, plan_id: str, payload: Review, db: Session = Depends(get_db)):
    lock(db, book_id)
    p = require_plan(db, book_id, plan_id, payload.base_version_id)
    chapter = db.get(Chapter, p.chapter_id) if p.chapter_id else None
    if not chapter or chapter.current_version_id != payload.chapter_version_id:
        raise HTTPException(409, "正文版本已变化或尚未关联，请重新核对")
    p.reviewed_plan_version_id = p.current_version_id
    p.reviewed_chapter_version_id = chapter.current_version_id
    if not chapter.word_count:
        raise HTTPException(409, "本章正文为空，请先生成或保存正文。")
    chapter.status = 'approved'
    db.commit()
    return plan_out(db, p)


class ImportOutline(BaseModel):
    base_version_id: int
    section_heading: str = Field(min_length=1)
    volume: str = Field(default="第一卷：地火微光", min_length=1, max_length=200)


@router.post("/import-outline/move")
def move_outline(book_id: int, payload: ImportOutline, db: Session = Depends(get_db)):
    """Explicit move of a terminal chapter-plan section; one transaction, history retained."""
    lock(db, book_id)
    latest = db.scalar(select(OutlineTextVersion).where(OutlineTextVersion.book_id == book_id)
                       .order_by(OutlineTextVersion.version_number.desc()))
    if not latest or latest.id != payload.base_version_id:
        raise HTTPException(409, "大纲版本已变化，未迁移")
    if plans(db, book_id):
        raise HTTPException(409, "本书已有章纲，禁止重复迁移")
    marker = payload.section_heading.strip()
    hits = list(re.finditer(r'^' + re.escape(marker) + r'\s*$', latest.content, re.M))
    if len(hits) != 1:
        raise HTTPException(400, "请指定唯一的章纲章节标题")
    start = hits[0].start()
    section = latest.content[hits[0].end():].strip()
    matches = list(re.finditer(r'^### 第\s*(\d+)\s*章[：:]\s*(.+)$', section, re.M))
    if not matches or [int(m[1]) for m in matches] != list(range(1, len(matches) + 1)):
        raise HTTPException(400, "章纲须从第1章起连续编号；未迁移")
    # Only an explicit shared-notes section can follow the last plan; never consume another outline section.
    tail_match = re.search(r'^## 执行勘误与待落实项.*$', section, re.M)
    tail_at = tail_match.start() if tail_match else len(section)
    if tail_at < matches[-1].start() or re.search(r'^#{1,2} ', section[matches[0].start():tail_at], re.M):
        raise HTTPException(400, "章纲后含其他大纲内容，请先明确迁移边界")
    old_guide = guidance(db, book_id)
    if old_guide:
        raise HTTPException(409, "已有章纲共同说明，未覆盖或迁移")
    shared = section[:matches[0].start()].strip() + '\n\n' + section[tail_at:].strip()
    shared += f'\n\n迁移来源：大纲 v{latest.version_number}（ID {latest.id}）。原文保留在大纲历史中。章纲以草稿状态迁入，确认前请落实执行勘误。'
    db.add(BookMaterialVersion(book_id=book_id, material_key=GUIDANCE_KEY, version_number=1,
        title='章纲共同执行说明', content=shared.strip(), source='outline_migration', note=f'来自大纲 {latest.id}'))
    for i, m in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else tail_at
        text = section[m.end():end].strip()
        if not text:
            raise HTTPException(400, "存在空章纲，未迁移")
        create_plan(db, book_id, i+1, {'title': m[2].strip(), 'volume': payload.volume,
                    'content': text, 'status': 'draft'}, 'outline_migration', f'来自大纲 v{latest.version_number} / 第{i+1}章')
    remaining = latest.content[:start].rstrip()
    remaining += f'\n\n## 章纲入口\n\n前{len(matches)}章细纲已迁移至[独立章纲页面](/books/{book_id}/chapter-plans)，此处不再保留第二份有效副本。共同勘误在章纲页查看。原稿完整保留于大纲 v{latest.version_number} 历史中。章纲仍为未来计划，迁移未创建正文。\n'
    db.add(OutlineTextVersion(book_id=book_id, version_number=latest.version_number+1, content=remaining,
        source='chapter_plan_migration', note=f'按用户授权迁移{len(matches)}份章纲；保留原版 v{latest.version_number}'))
    db.commit()
    return listing(book_id, db)
