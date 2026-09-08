from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .llm_timeout import DEFAULT_LLM_TIMEOUT


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BookCreate(BaseModel):
    title: str
    author: str = ""
    description: str = ""
    genre: str = ""
    status: str = "draft"


class BookUpdate(BaseModel):
    title: str | None = None
    author: str | None = None
    description: str | None = None
    genre: str | None = None
    status: str | None = None
    current_chapter_id: int | None = None


class BookOut(ORMModel):
    id: int
    title: str
    author: str
    description: str
    genre: str
    status: str
    cover_path: str | None
    current_chapter_id: int | None
    created_at: datetime
    updated_at: datetime
    chapter_count: int = 0
    total_word_count: int = 0


class ChapterCreate(BaseModel):
    chapter_number: int | None = None
    title: str = "未命名章节"
    content: str = ""
    status: str = "draft"
    source: str = "manual"
    note: str = ""


class ChapterUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    reading_position: int | None = None
    chapter_number: int | None = None


class VersionCreate(BaseModel):
    content: str
    base_version_id: int | None = None
    title: str | None = None
    source: str = "manual"
    note: str = ""


class ChapterReorder(BaseModel):
    chapter_ids: list[int]


class ChapterImport(BaseModel):
    chapters: list[ChapterCreate] = Field(min_length=1, max_length=10000)


class ChapterVersionOut(ORMModel):
    id: int
    chapter_id: int
    version_number: int
    content_path: str
    word_count: int
    source: str
    note: str
    created_at: datetime
    content: str = ""


class ChapterOut(ORMModel):
    id: int
    book_id: int
    chapter_number: int
    title: str
    status: str
    current_version_id: int | None
    word_count: int
    reading_position: int
    created_at: datetime
    updated_at: datetime
    current_content: str = ""
    summary: str = ""
    summary_content_sha256: str = ""
    summary_source_version_id: int | None = None
    summary_model: str = ""
    summary_updated_at: datetime | None = None
    summary_is_stale: bool = False


class LLMProviderCreate(BaseModel):
    provider_name: str = "OpenAI Compatible"
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(3000, ge=100)
    timeout: int = Field(DEFAULT_LLM_TIMEOUT, ge=5)
    enabled: bool = False


class LLMProviderOut(ORMModel):
    id: int
    provider_name: str
    provider_type: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    enabled: bool
    has_api_key: bool = False
    created_at: datetime
    updated_at: datetime
