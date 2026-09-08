from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .llm_timeout import DEFAULT_LLM_TIMEOUT


def now() -> datetime:
    return datetime.utcnow()


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    author: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    cover_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    current_chapter_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("book_id", "chapter_number", name="uq_book_chapter_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="未命名章节")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    current_version_id: Mapped[int | None] = mapped_column(nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    reading_position: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_content_sha256: Mapped[str] = mapped_column(String(64), default="")
    summary_source_version_id: Mapped[int | None] = mapped_column(nullable=True)
    summary_model: Mapped[str] = mapped_column(String(120), default="")
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class ChapterVersion(Base):
    __tablename__ = "chapter_versions"
    __table_args__ = (UniqueConstraint("chapter_id", "version_number", name="uq_chapter_version_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    content_path: Mapped[str] = mapped_column(String(800))
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SummaryJob(Base):
    __tablename__ = "summary_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    model: Mapped[str] = mapped_column(String(120), default="")
    provider_id: Mapped[int | None] = mapped_column(nullable=True)
    provider_config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class SummaryJobItem(Base):
    __tablename__ = "summary_job_items"
    __table_args__ = (UniqueConstraint("job_id", "chapter_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("summary_jobs.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    message: Mapped[str] = mapped_column(Text, default="")


class SummaryLease(Base):
    __tablename__ = "summary_leases"
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True)


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(30), default="other")
    name: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="active")
    current_version_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_item_id: Mapped[int] = mapped_column(ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="")
    change_reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(30), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    aliases: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(120), default="")
    age: Mapped[str] = mapped_column(String(60), default="")
    identity: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(120), default="")
    inventory: Mapped[str] = mapped_column(Text, default="")
    relationships: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[str] = mapped_column(Text, default="")
    fears: Mapped[str] = mapped_column(Text, default="")
    abilities: Mapped[str] = mapped_column(Text, default="")
    secrets: Mapped[str] = mapped_column(Text, default="")
    current_state: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="active")
    current_version_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CharacterVersion(Base):
    __tablename__ = "character_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    structured_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    change_reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(30), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class BookMaterialVersion(Base):
    __tablename__ = "book_material_versions"
    __table_args__ = (UniqueConstraint("book_id", "material_key", "version_number", name="uq_book_material_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    material_key: Mapped[str] = mapped_column(String(80))
    version_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CurrentStateSettings(Base):
    __tablename__ = "current_state_settings"
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    prompt: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class CurrentStateVersion(Base):
    __tablename__ = "current_state_versions"
    __table_args__ = (UniqueConstraint("book_id", "version_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    note: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    source_manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CurrentStateLease(Base):
    __tablename__ = "current_state_leases"
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)


class OutlineTextVersion(Base):
    """Immutable whole-outline snapshots; legacy tables remain intact."""
    __tablename__ = "outline_text_versions"
    __table_args__ = (UniqueConstraint("book_id", "version_number", name="uq_book_outline_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(30), default="manual")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# Historical models are retained for existing database compatibility.
class Outline(Base):
    __tablename__ = "outlines"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class OutlineBlock(Base):
    __tablename__ = "outline_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    outline_id: Mapped[int] = mapped_column(ForeignKey("outlines.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(nullable=True)
    block_type: Mapped[str] = mapped_column(String(30), default="chapter_plan")
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    conflict: Mapped[str] = mapped_column(Text, default="")
    turning_point: Mapped[str] = mapped_column(Text, default="")
    ending: Mapped[str] = mapped_column(Text, default="")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    start_chapter: Mapped[int | None] = mapped_column(nullable=True)
    end_chapter: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    event_type: Mapped[str] = mapped_column(String(30), default="other")
    track: Mapped[str] = mapped_column(String(30), default="mainline")
    status: Mapped[str] = mapped_column(String(30), default="planned")
    start_chapter: Mapped[int | None] = mapped_column(nullable=True)
    start_position: Mapped[str] = mapped_column(String(80), default="")
    start_label: Mapped[str] = mapped_column(String(80), default="")
    end_chapter: Mapped[int | None] = mapped_column(nullable=True)
    end_position: Mapped[str] = mapped_column(String(80), default="")
    end_label: Mapped[str] = mapped_column(String(80), default="")
    start_certainty: Mapped[float] = mapped_column(Float, default=0.0)
    end_certainty: Mapped[float] = mapped_column(Float, default=0.0)
    objective_fact: Mapped[str] = mapped_column(Text, default="")
    hidden_truth: Mapped[str] = mapped_column(Text, default="")
    created_from_chapter_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class EventRelation(Base):
    __tablename__ = "event_relations"
    id: Mapped[int] = mapped_column(primary_key=True)
    from_event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    to_event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text, default="")


class Belief(Base):
    __tablename__ = "beliefs"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"), nullable=True)
    subject_type: Mapped[str] = mapped_column(String(30), default="other")
    subject_id: Mapped[int | None] = mapped_column(nullable=True)
    belief_text: Mapped[str] = mapped_column(Text)
    belief_type: Mapped[str] = mapped_column(String(30), default="unknown")
    certainty: Mapped[float] = mapped_column(Float, default=0.0)
    source_chapter_id: Mapped[int | None] = mapped_column(nullable=True)
    valid_from_chapter: Mapped[int | None] = mapped_column(nullable=True)
    valid_to_chapter: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active")


class RelationState(Base):
    __tablename__ = "relation_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    from_character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"))
    to_character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"))
    liking: Mapped[float] = mapped_column(Float, default=0)
    trust: Mapped[float] = mapped_column(Float, default=0)
    fear: Mapped[float] = mapped_column(Float, default=0)
    dependence: Mapped[float] = mapped_column(Float, default=0)
    alertness: Mapped[float] = mapped_column(Float, default=0)
    hostility: Mapped[float] = mapped_column(Float, default=0)
    cooperation: Mapped[float] = mapped_column(Float, default=0)
    current_note: Mapped[str] = mapped_column(Text, default="")
    current_version_id: Mapped[int | None] = mapped_column(nullable=True)


class RelationDelta(Base):
    __tablename__ = "relation_deltas"
    id: Mapped[int] = mapped_column(primary_key=True)
    relation_state_id: Mapped[int | None] = mapped_column(ForeignKey("relation_states.id"), nullable=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    field_name: Mapped[str] = mapped_column(String(60))
    old_value: Mapped[str] = mapped_column(String(120), default="")
    proposed_value: Mapped[str] = mapped_column(String(120), default="")
    delta: Mapped[str] = mapped_column(String(120), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    certainty: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default="pending")


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    request_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="mock")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AnalysisChange(Base):
    __tablename__ = "analysis_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    change_type: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[int | None] = mapped_column(nullable=True)
    target_name: Mapped[str] = mapped_column(String(200), default="")
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    proposed_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    source_chapter_id: Mapped[int | None] = mapped_column(nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PlotThread(Base):
    """A durable plot thread, separate from the derived relationship graph."""

    __tablename__ = "plot_threads"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    thread_type: Mapped[str] = mapped_column(String(30), default="plot")
    status: Mapped[str] = mapped_column(String(30), default="open")
    objective: Mapped[str] = mapped_column(Text, default="")
    unresolved_question: Mapped[str] = mapped_column(Text, default="")
    payoff: Mapped[str] = mapped_column(Text, default="")
    seed_chapter: Mapped[int | None] = mapped_column(nullable=True)
    last_chapter: Mapped[int | None] = mapped_column(nullable=True)
    evidence: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class GraphifyRun(Base):
    """Persisted init/query/review runs so raw responses and proposals remain auditable."""

    __tablename__ = "graphify_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    request_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_response: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="local-safe-mock")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    source_chapter_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    source_version_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    content_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class GraphifyProposal(Base):
    __tablename__ = "graphify_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("graphify_runs.id", ondelete="CASCADE"), index=True)
    change_type: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[int | None] = mapped_column(nullable=True)
    target_name: Mapped[str] = mapped_column(String(200), default="")
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    proposed_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    source_chapter_id: Mapped[int | None] = mapped_column(nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ChangeLog(Base):
    """Audit trail for every automatic or manual write into the story bible."""

    __tablename__ = "change_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[int | None] = mapped_column(nullable=True)
    operation: Mapped[str] = mapped_column(String(40), default="analysis")
    action: Mapped[str] = mapped_column(String(30), default="auto_applied")
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[int | None] = mapped_column(nullable=True)
    target_name: Mapped[str] = mapped_column(String(200), default="")
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="local_user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LLMModuleRoute(Base):
    __tablename__ = "llm_module_routes"
    module: Mapped[str] = mapped_column(String(60), primary_key=True)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("llm_providers.id", ondelete="SET NULL"), nullable=True)
    use_default: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class LLMProvider(Base):
    __tablename__ = "llm_providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(120))
    provider_type: Mapped[str] = mapped_column(String(40), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(500))
    api_key: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120))
    conversation_id: Mapped[str] = mapped_column(String(300), default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, default=3000)
    timeout: Mapped[int] = mapped_column(Integer, default=DEFAULT_LLM_TIMEOUT)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
