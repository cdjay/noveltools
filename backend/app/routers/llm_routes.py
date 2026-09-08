from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..llm_routing import MODULES, resolve_provider
from ..models import LLMModuleRoute, LLMProvider

router = APIRouter(prefix="/api/llm/routes", tags=["LLM routing"])


class RouteSave(BaseModel):
    provider_id: int | None
    base_revision: int = Field(ge=0)


def route_out(db, module):
    row = db.get(LLMModuleRoute, module)
    error = None
    try:
        provider = resolve_provider(db, module)
        if not provider:
            error = "请保存 Gemini 配置，或为正文生成指定模型" if module == "chapter_writing" else "尚未设置可用的默认模型"
    except HTTPException as exc:
        provider, error = None, exc.detail
    return {"module": module, "label": MODULES[module][0], "description": MODULES[module][1],
            "provider_id": row.provider_id if row else None, "use_default": row.use_default if row else True,
            "revision": row.revision if row else 0, "error": error,
            "effective_provider": {"id": provider.id, "provider_name": provider.provider_name, "model": provider.model} if provider else None}


@router.get("")
def list_routes(db: Session = Depends(get_db)):
    return [route_out(db, module) for module in MODULES]


@router.put("/{module}")
def save_route(module: str, payload: RouteSave, db: Session = Depends(get_db)):
    if module not in MODULES:
        raise HTTPException(404, "未知 LLM 调用模块")
    # Acquire the SQLite write lock before validating provider deletion or revision.
    db.execute(update(LLMModuleRoute).where(LLMModuleRoute.module == module).values(module=module))
    if payload.provider_id is not None:
        provider = db.get(LLMProvider, payload.provider_id)
        if not provider or provider.provider_type != "openai_compatible" or not provider.api_key.strip():
            raise HTTPException(400, "请选择已保存且设置 API Key 的模型。")
    row = db.get(LLMModuleRoute, module, populate_existing=True)
    if payload.base_revision != (row.revision if row else 0):
        raise HTTPException(409, "此模块的模型分流已在其他窗口修改，请重新读取后再保存。")
    if row:
        row.provider_id, row.use_default, row.revision = payload.provider_id, payload.provider_id is None, row.revision + 1
    else:
        db.add(LLMModuleRoute(module=module, provider_id=payload.provider_id, use_default=payload.provider_id is None))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "模型配置或分流已变化，请重新读取后再保存。")
    return route_out(db, module)
