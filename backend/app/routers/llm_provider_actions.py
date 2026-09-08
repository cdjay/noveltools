"""Copy saved connection settings without sending credentials to the browser."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import LLMProvider
from ..schemas import LLMProviderOut
from .core import _serialize_provider

router = APIRouter(prefix="/api/llm/providers", tags=["LLM providers"])


@router.post("/{provider_id}/copy", response_model=LLMProviderOut)
def copy_provider(provider_id: int, db: Session = Depends(get_db)):
    source = db.get(LLMProvider, provider_id)
    if not source or source.provider_type != "openai_compatible":
        raise HTTPException(404, "LLM 配置不存在")
    copied = LLMProvider(
        provider_name=source.provider_name[:117] + " 副本",
        provider_type="openai_compatible", enabled=False,
        **{key: getattr(source, key) for key in ("base_url", "api_key", "model", "temperature", "max_tokens", "timeout")},
    )
    db.add(copied)
    db.commit()
    db.refresh(copied)
    return _serialize_provider(copied)
