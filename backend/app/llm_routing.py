"""Explicit module assignments override the legacy single default provider."""
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .models import LLMModuleRoute, LLMProvider, LLMRouteFallback

MODULES = {
    "chapter_summary": ("单章梗概", "正文页生成或重新生成本章梗概。"),
    "batch_summary": ("批量梗概", "批量补齐或更新梗概；任务创建时固定模型，停止后继续时读取最新分流。"),
    "current_state": ("当前状态速查", "使用最新大纲、最后五章正文和全部梗概整理速查。"),
    "chapter_writing": ("正文生成", "持续生成下一章标题与正文；默认选择已保存的 Gemini 配置。"),
}


def default_provider(db: Session) -> LLMProvider | None:
    return db.scalar(select(LLMProvider).where(
        LLMProvider.provider_type == "openai_compatible", LLMProvider.api_key != "", LLMProvider.enabled.is_(True)
    ).order_by(LLMProvider.updated_at.desc(), LLMProvider.id.desc()).limit(1))


def default_for_module(db: Session, module: str) -> LLMProvider | None:
    if module == "chapter_writing":
        return db.scalar(select(LLMProvider).where(
            LLMProvider.provider_type == "openai_compatible", LLMProvider.api_key != "",
            func.lower(LLMProvider.model).contains("gemini")
        ).order_by(LLMProvider.enabled.desc(), LLMProvider.updated_at.desc(), LLMProvider.id.desc()).limit(1))
    return default_provider(db)


def resolve_provider(db: Session, module: str) -> LLMProvider | None:
    if module not in MODULES:
        raise HTTPException(404, "未知 LLM 调用模块")
    route = db.get(LLMModuleRoute, module)
    if not route or route.use_default:
        return default_for_module(db, module)
    provider = db.get(LLMProvider, route.provider_id) if route.provider_id else None
    if not provider or provider.provider_type != "openai_compatible" or not provider.api_key.strip():
        raise HTTPException(400, f"{MODULES[module][0]}指定的模型已删除或未设置密钥，请在 LLM 配置的模块分流中重新选择。")
    return provider


def resolve_chain(db: Session, module: str) -> list[LLMProvider]:
    primary = resolve_provider(db, module)
    if not primary:
        raise HTTPException(400, f"请为{MODULES[module][0]}设置可用的主模型。")
    providers = [primary]
    rows = db.scalars(select(LLMRouteFallback).where(LLMRouteFallback.module == module).order_by(LLMRouteFallback.position)).all()
    for row in rows:
        provider = db.get(LLMProvider, row.provider_id) if row.provider_id else None
        if not provider or provider.provider_type != 'openai_compatible' or not provider.api_key.strip():
            raise HTTPException(400, f"{MODULES[module][0]}备用 {row.position} 已删除或未设置密钥，请在模块分流中重新选择。")
        if provider.id not in [item.id for item in providers]:
            providers.append(provider)
    return providers
