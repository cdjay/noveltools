from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update, select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..llm_routing import MODULES, resolve_provider, default_for_module
from ..models import LLMModuleRoute, LLMProvider, LLMRouteFallback, LLMProviderCooldown

router = APIRouter(prefix="/api/llm/routes", tags=["LLM routing"])


class RouteSave(BaseModel):
    provider_id: int | None
    base_revision: int = Field(ge=0)
    fallback_provider_ids: list[int] | None = Field(default=None, max_length=3)


def route_out(db, module):
    row = db.get(LLMModuleRoute, module)
    error = None
    try:
        provider = resolve_provider(db, module)
        if not provider:
            error = "请保存 Gemini 配置，或为正文生成指定模型" if module == "chapter_writing" else "尚未设置可用的默认模型"
    except HTTPException as exc:
        provider, error = None, exc.detail
    def cooldown_out(item):
        if not item:
            return None
        from ..llm_failover import fingerprint
        from datetime import datetime
        cooldown = db.get(LLMProviderCooldown, item.id)
        if cooldown and cooldown.fingerprint == fingerprint(item) and cooldown.until > datetime.utcnow():
            return {"until": cooldown.until.isoformat() + 'Z', "reason": cooldown.reason}
        return None
    fallbacks = []
    for slot in db.scalars(select(LLMRouteFallback).where(LLMRouteFallback.module == module).order_by(LLMRouteFallback.position)):
        item = db.get(LLMProvider, slot.provider_id) if slot.provider_id else None
        fallbacks.append({"position": slot.position, "provider_id": slot.provider_id,
            "provider_name": item.provider_name if item else None, "model": item.model if item else None,
            "error": None if item and item.api_key.strip() else "备用配置已删除或未设置密钥", "cooldown": cooldown_out(item)})
    return {"module": module, "label": MODULES[module][0], "description": MODULES[module][1],
            "provider_id": row.provider_id if row else None, "use_default": row.use_default if row else True,
            "revision": row.revision if row else 0, "error": error, "fallbacks": fallbacks,
            "effective_provider": {"id": provider.id, "provider_name": provider.provider_name, "model": provider.model, "cooldown": cooldown_out(provider)} if provider else None}


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
    if payload.fallback_provider_ids is not None:
        if len(set(payload.fallback_provider_ids)) != len(payload.fallback_provider_ids) or payload.provider_id in payload.fallback_provider_ids:
            raise HTTPException(400, "主模型与备用模型不能重复，备用模型之间也不能重复。")
        for provider_id in payload.fallback_provider_ids:
            item = db.get(LLMProvider, provider_id)
            if not item or item.provider_type != 'openai_compatible' or not item.api_key.strip():
                raise HTTPException(400, "备用模型必须是已保存且设置 API Key 的配置。")
        if payload.provider_id is None:
            primary = default_for_module(db, module)
            if primary and primary.id in payload.fallback_provider_ids:
                raise HTTPException(400, "备用模型不能与当前默认主模型重复。")
    row = db.get(LLMModuleRoute, module, populate_existing=True)
    if payload.base_revision != (row.revision if row else 0):
        raise HTTPException(409, "此模块的模型分流已在其他窗口修改，请重新读取后再保存。")
    if row:
        row.provider_id, row.use_default, row.revision = payload.provider_id, payload.provider_id is None, row.revision + 1
    else:
        db.add(LLMModuleRoute(module=module, provider_id=payload.provider_id, use_default=payload.provider_id is None))
    if payload.fallback_provider_ids is not None:
        db.flush()
        db.execute(delete(LLMRouteFallback).where(LLMRouteFallback.module == module))
        db.add_all([LLMRouteFallback(module=module, position=index + 1, provider_id=provider_id)
                    for index, provider_id in enumerate(payload.fallback_provider_ids)])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "模型配置或分流已变化，请重新读取后再保存。")
    return route_out(db, module)
