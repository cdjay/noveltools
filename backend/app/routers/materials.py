"""Versioned, author-maintained book documents; reads never create or generate content."""
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, defer

from ..database import get_db
from ..models import Book, BookMaterialVersion
from .core import require_book

router = APIRouter(prefix="/api/books/{book_id}/materials", tags=["Book materials"])
RULES_KEY = "writing-rules"
RULES_TITLE = "写作规则"
RULES_TEMPLATE = """# 写作规则

## 叙事视角与时态
待填写。

## 文风与节奏
待填写。

## 必须遵守的要求
待填写。

## 禁用词句与表达
待填写。

## 建议与偏好
待填写。

## 章节交付要求
待填写。
"""


class MaterialBase(BaseModel):
    base_version_id: int | None


class MaterialCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = ""

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value):
        if not value.strip():
            raise ValueError("资料标题不能为空")
        return value.strip()


class MaterialSave(MaterialCreate, MaterialBase):
    pass


class MaterialMeta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    book_id: int
    material_key: str
    version_number: int
    title: str
    source: str
    note: str
    created_at: datetime


class MaterialVersionOut(MaterialMeta):
    content: str


class MaterialOut(BaseModel):
    schema_version: int = 1
    book_id: int
    material_key: str
    title: str
    format: str = "markdown"
    version: MaterialVersionOut | None
    template: str = ""


def latest(db, book_id, key):
    return db.scalar(select(BookMaterialVersion).where(BookMaterialVersion.book_id == book_id,
        BookMaterialVersion.material_key == key).order_by(BookMaterialVersion.version_number.desc()).limit(1))


def lock_book(db, book_id):
    db.execute(update(Book).where(Book.id == book_id).values(id=Book.id))
    require_book(db, book_id)
    db.expire_all()


def require_material(db, book_id, key):
    require_book(db, book_id)
    version = latest(db, book_id, key)
    if version is None and key != RULES_KEY:
        raise HTTPException(404, "资料不存在或不属于本书")
    return version


def append(db, book_id, key, payload, *, creating=False, source="manual", note=""):
    lock_book(db, book_id)
    current = latest(db, book_id, key)
    if current is None and key != RULES_KEY and not creating:
        raise HTTPException(404, "资料不存在或不属于本书")
    if payload.base_version_id != (current.id if current else None):
        raise HTTPException(409, "这份资料已在其他窗口更新。当前草稿保留，请复制后重新读取并核对。")
    if key == RULES_KEY and payload.title != RULES_TITLE:
        raise HTTPException(400, "写作规则使用固定标题；其他资料可自由命名。")
    version = BookMaterialVersion(book_id=book_id, material_key=key,
        version_number=current.version_number + 1 if current else 1,
        title=payload.title, content=payload.content, source=source, note=note)
    db.add(version)
    db.commit()
    return version


@router.get("")
def list_materials(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    newest = select(BookMaterialVersion.material_key, func.max(BookMaterialVersion.version_number).label("number")).where(
        BookMaterialVersion.book_id == book_id).group_by(BookMaterialVersion.material_key).subquery()
    rows = db.scalars(select(BookMaterialVersion).join(newest,
        (BookMaterialVersion.material_key == newest.c.material_key) & (BookMaterialVersion.version_number == newest.c.number))
        .where(BookMaterialVersion.book_id == book_id).options(defer(BookMaterialVersion.content))
        .order_by(BookMaterialVersion.created_at.desc(), BookMaterialVersion.id.desc())).all()
    result = [{"material_key": v.material_key, "title": v.title, "version_id": v.id,
               "version_number": v.version_number, "updated_at": v.created_at} for v in rows]
    rules = next((v for v in result if v["material_key"] == RULES_KEY), None)
    return [rules or {"material_key": RULES_KEY, "title": RULES_TITLE, "version_id": None,
                      "version_number": 0, "updated_at": None}] + [v for v in result if v["material_key"] != RULES_KEY]


@router.post("", response_model=MaterialVersionOut)
def create_material(book_id: int, payload: MaterialCreate, db: Session = Depends(get_db)):
    return append(db, book_id, "note-" + uuid4().hex, MaterialSave(**payload.model_dump(), base_version_id=None), creating=True)


@router.get("/{material_key}", response_model=MaterialOut)
def read_material(book_id: int, material_key: str, db: Session = Depends(get_db)):
    version = require_material(db, book_id, material_key)
    return {"book_id": book_id, "material_key": material_key, "title": version.title if version else RULES_TITLE,
            "version": version, "template": RULES_TEMPLATE if material_key == RULES_KEY else ""}


@router.get("/{material_key}/export.md")
def export_material(book_id: int, material_key: str, db: Session = Depends(get_db)):
    version = require_material(db, book_id, material_key)
    if version is None:
        raise HTTPException(404, "这份资料尚未保存")
    return Response(version.content, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="material-{book_id}-{material_key}.md"',
        "X-Material-Version": str(version.version_number)})


@router.get("/{material_key}/versions", response_model=list[MaterialMeta])
def list_versions(book_id: int, material_key: str, db: Session = Depends(get_db)):
    require_material(db, book_id, material_key)
    return db.scalars(select(BookMaterialVersion).where(BookMaterialVersion.book_id == book_id,
        BookMaterialVersion.material_key == material_key).options(defer(BookMaterialVersion.content))
        .order_by(BookMaterialVersion.version_number.desc())).all()


@router.get("/{material_key}/versions/{version_id}", response_model=MaterialVersionOut)
def read_version(book_id: int, material_key: str, version_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    version = db.get(BookMaterialVersion, version_id)
    if not version or version.book_id != book_id or version.material_key != material_key:
        raise HTTPException(404, "资料版本不存在或不属于本书的这份资料")
    return version


@router.post("/{material_key}/versions", response_model=MaterialVersionOut)
def save_version(book_id: int, material_key: str, payload: MaterialSave, db: Session = Depends(get_db)):
    return append(db, book_id, material_key, payload)


@router.post("/{material_key}/versions/{version_id}/restore", response_model=MaterialVersionOut)
def restore_version(book_id: int, material_key: str, version_id: int, payload: MaterialBase, db: Session = Depends(get_db)):
    lock_book(db, book_id)
    saved = read_version(book_id, material_key, version_id, db)
    return append(db, book_id, material_key, MaterialSave(title=saved.title, content=saved.content,
        base_version_id=payload.base_version_id), source="restored", note=f"从 v{saved.version_number} 恢复")
