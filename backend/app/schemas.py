from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    source: str = "manual"
    note: str = ""


class ChapterReorder(BaseModel):
    chapter_ids: list[int]


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
    summary: str = ""
    summary_content_sha256: str = ""
    summary_source_version_id: int | None = None
    summary_model: str = ""
    summary_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    current_content: str = ""


class ChapterSummaryOut(BaseModel):
    chapter_id: int
    summary: str
    content_sha256: str
    source_version_id: int | None = None
    model: str = ""
    status: str
    updated: bool
    skipped: bool
    message: str = ""
    analysis_id: int | None = None


class ChapterSummaryBatchOut(BaseModel):
    book_id: int
    total: int
    updated: int
    skipped: int
    failed: int
    results: list[ChapterSummaryOut] = []


class CharacterCreate(BaseModel):
    name: str
    aliases: str = ""
    role: str = ""
    age: str = ""
    identity: str = ""
    summary: str = ""
    appearance: str = ""
    personality: str = ""
    level: str = ""
    inventory: str = ""
    relationships: str = ""
    goals: str = ""
    fears: str = ""
    abilities: str = ""
    secrets: str = ""
    current_state: str = ""
    status: str = "active"


class CharacterOut(CharacterCreate, ORMModel):
    id: int
    book_id: int
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime


class CharacterVersionOut(ORMModel):
    id: int
    character_id: int
    version_number: int
    structured_json: dict[str, Any]
    change_reason: str
    source: str
    created_at: datetime


class KnowledgeCreate(BaseModel):
    category: str = "other"
    name: str
    content: str = ""
    status: str = "active"


class KnowledgeOut(KnowledgeCreate, ORMModel):
    id: int
    book_id: int
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime


class KnowledgeVersionOut(ORMModel):
    id: int
    knowledge_item_id: int
    version_number: int
    content: str
    change_reason: str
    source: str
    created_at: datetime


class OutlineCreate(BaseModel):
    title: str
    description: str = ""
    status: str = "draft"


class OutlineBlockCreate(BaseModel):
    parent_id: int | None = None
    block_type: str = "chapter_plan"
    title: str
    summary: str = ""
    objective: str = ""
    conflict: str = ""
    turning_point: str = ""
    ending: str = ""
    order_index: int = 0
    start_chapter: int | None = None
    end_chapter: int | None = None


class OutlineOut(OutlineCreate, ORMModel):
    id: int
    book_id: int
    version: int
    created_at: datetime
    updated_at: datetime


class OutlineBlockOut(OutlineBlockCreate, ORMModel):
    id: int
    outline_id: int
    created_at: datetime
    updated_at: datetime


class EventCreate(BaseModel):
    title: str
    description: str = ""
    event_type: str = "other"
    track: str = "mainline"
    status: str = "planned"
    start_chapter: int | None = None
    start_position: str = ""
    start_label: str = ""
    end_chapter: int | None = None
    end_position: str = ""
    end_label: str = ""
    start_certainty: float = Field(0, ge=0, le=1)
    end_certainty: float = Field(0, ge=0, le=1)
    objective_fact: str = ""
    hidden_truth: str = ""
    created_from_chapter_id: int | None = None


class EventOut(EventCreate, ORMModel):
    id: int
    book_id: int
    created_at: datetime
    updated_at: datetime


class EventRelationOut(ORMModel):
    id: int
    from_event_id: int
    to_event_id: int
    relation_type: str
    note: str


class ThreadCreate(BaseModel):
    title: str
    description: str = ""
    thread_type: str = "plot"
    status: str = "open"
    objective: str = ""
    unresolved_question: str = ""
    payoff: str = ""
    seed_chapter: int | None = None
    last_chapter: int | None = None
    evidence: str = ""


class ThreadOut(ThreadCreate, ORMModel):
    id: int
    book_id: int
    created_at: datetime
    updated_at: datetime


class AnalysisRequest(BaseModel):
    analysis_type: str = "characters"
    include_outline: bool = True
    include_previous_chapters: bool = True
    include_summaries: bool = True
    include_characters: bool = True
    include_knowledge: bool = True
    include_events: bool = True
    include_relations: bool = True


class IssueReviewRequest(BaseModel):
    status: str
    note: str = ""


class AnalysisChangeOut(ORMModel):
    id: int
    analysis_id: int
    change_type: str
    target_type: str
    target_id: int | None
    target_name: str
    old_value: Any | None
    proposed_value: Any | None
    reason: str
    evidence: str
    source_chapter_id: int | None
    confidence: float
    conflict: bool
    status: str
    created_at: datetime


class AnalysisOut(ORMModel):
    id: int
    book_id: int
    chapter_id: int
    status: str
    request_context: dict[str, Any]
    result_json: dict[str, Any] | None
    raw_response: str
    model: str
    token_count: int
    elapsed_ms: int
    error_message: str
    created_at: datetime
    updated_at: datetime
    changes: list[AnalysisChangeOut] = []


class LLMProviderCreate(BaseModel):
    provider_name: str = "OpenAI Compatible"
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(3000, ge=100)
    timeout: int = Field(60, ge=5)
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


class PromptRequest(BaseModel):
    extra_requirements: str = ""
    word_count: str = ""
    target_chapter_number: int | None = Field(default=None, ge=1)


class GraphifyInitRequest(BaseModel):
    premise: str = ""
    from_chapters: bool = False
    batch: int = Field(5, ge=1, le=20)
    intent: str = ""


class GraphifyPassageRequest(BaseModel):
    passage: str
    intent: str = ""


class GraphifyLoreRequest(BaseModel):
    name: str
    content: str
    category: str = "worldview"
    evidence: str = "作者手动输入"


class GraphifyQueryRequest(BaseModel):
    question: str
    dfs: bool = False


class GraphifyPathRequest(BaseModel):
    from_name: str
    to_name: str


class GraphifyProposalOut(ORMModel):
    id: int
    run_id: int
    change_type: str
    target_type: str
    target_id: int | None
    target_name: str
    old_value: Any | None
    proposed_value: Any | None
    reason: str
    evidence: str
    source_chapter_id: int | None
    confidence: float
    conflict: bool
    status: str
    created_at: datetime


class GraphifyRunOut(ORMModel):
    id: int
    book_id: int
    operation: str
    status: str
    request_context: dict[str, Any]
    result_json: dict[str, Any] | None
    raw_response: str
    model: str
    elapsed_ms: int
    error_message: str
    source_chapter_id: int | None = None
    source_version_id: int | None = None
    content_sha256: str = ""
    created_at: datetime
    updated_at: datetime
    proposals: list[GraphifyProposalOut] = []


class ChangeLogOut(ORMModel):
    id: int
    book_id: int
    source_type: str
    source_id: int | None
    operation: str
    action: str
    target_type: str
    target_id: int | None
    target_name: str
    old_value: Any | None
    new_value: Any | None
    reason: str
    evidence: str
    actor: str
    created_at: datetime
