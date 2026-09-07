import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import (Analysis, AnalysisChange, Book, Chapter, ChapterVersion, Character, CharacterVersion,
                      ChangeLog, Event, EventRelation, GraphifyProposal, GraphifyRun, KnowledgeItem, KnowledgeVersion, LLMProvider,
                      Outline, OutlineBlock, PlotThread)
from ..schemas import (AnalysisOut, AnalysisRequest, BookCreate, BookOut, BookUpdate, ChapterCreate, ChapterOut,
                       ChapterReorder, ChapterSummaryBatchOut, ChapterSummaryOut, ChapterUpdate, ChapterVersionOut, CharacterCreate, CharacterOut, EventCreate, EventOut, EventRelationOut,
                       ChangeLogOut, GraphifyInitRequest, GraphifyLoreRequest, GraphifyPassageRequest, GraphifyPathRequest, GraphifyProposalOut, GraphifyQueryRequest, GraphifyRunOut,
                       IssueReviewRequest, KnowledgeCreate, KnowledgeOut, LLMProviderCreate, LLMProviderOut, OutlineBlockCreate,
                       OutlineBlockOut, OutlineCreate, OutlineOut, PromptRequest, ThreadCreate, ThreadOut, VersionCreate)
from ..services import (build_context, call_provider, compose_prompt, content_sha256, count_words, find_analysis_provider, find_analysis_providers, make_analysis_changes, mock_analysis, next_chapter_number,
                        mock_graphify_init, prepare_character_analysis, prepare_graphify_init,
                        next_version_number, read_content, save_content, serialize_book, serialize_chapter, validate_analysis_result)
from ..graphify import graph_path, graph_query, graph_status

router = APIRouter(prefix="/api")


class AutoGraphifyProvidersFailed(RuntimeError):
    """All configured providers failed during one automatic Graphify phase."""

    def __init__(self, failures: list[dict[str, str]], raw_response: str = ""):
        detail = "；".join(f"{item['model']}：{item['error']}" for item in failures)
        super().__init__(f"自动 Graphify 可用模型均失败：{detail}")
        self.failures = failures
        self.attempted_models = [item["model"] for item in failures]
        self.raw_response = raw_response


def require_book(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise HTTPException(404, "书籍不存在")
    return book


def _provider_failure_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return (str(exc).strip() or exc.__class__.__name__)[:240]


def _is_transient_provider_failure(exc: Exception) -> bool:
    return isinstance(exc, httpx.TimeoutException) or (
        isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500
    )


async def _call_auto_graphify_with_fallback(
    providers: list[LLMProvider],
    context: dict[str, Any],
    preferred: LLMProvider | None = None,
) -> tuple[dict[str, Any], str, int, LLMProvider, list[dict[str, str]]]:
    """Try enabled providers sequentially so one broken integration cannot stop auto extraction."""
    ordered = list(providers)
    if preferred:
        ordered = [preferred, *(item for item in ordered if item.id != preferred.id)]
    failures: list[dict[str, str]] = []
    last_raw = ""
    for provider in ordered:
        for attempt in range(2):
            try:
                result, raw, elapsed = await call_provider(provider, context)
                return result, raw, elapsed, provider, failures
            except Exception as exc:
                last_raw = getattr(exc, "raw_response", last_raw)
                if attempt == 0 and _is_transient_provider_failure(exc):
                    await asyncio.sleep(1)
                    continue
                failures.append({
                    "provider": provider.provider_name,
                    "model": provider.model,
                    "error": _provider_failure_message(exc),
                })
                break
    raise AutoGraphifyProvidersFailed(failures, last_raw)


def require_chapter(db: Session, chapter_id: int) -> Chapter:
    chapter = db.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    return chapter


def _summary_has_character_signal(summary: str) -> bool:
    """Return whether a saved summary says the chapter contains real characters."""
    marker = "【关键人物】"
    if marker not in summary:
        return False
    section = summary.split(marker, 1)[1]
    for next_marker in ("【关键事件】", "【状态变化】", "【结尾状态】", "【未决事项】"):
        section = section.split(next_marker, 1)[0]
    compact = section.strip().strip("。；;，,")
    if not compact:
        return False
    return compact not in {"无", "未说明", "未提取", "未使用模型提取"} and "未使用模型提取" not in compact


def _completed_run_needs_character_recovery(db: Session, chapter: Chapter, run: GraphifyRun) -> bool:
    """Allow one recovery pass for historical successful runs that missed characters."""
    result = run.result_json if isinstance(run.result_json, dict) else {}
    if result.get("characters") or result.get("character_extraction_attempted"):
        return False
    if not _summary_has_character_signal(chapter.summary or ""):
        return False
    if run.model != "local-safe-mock":
        return True
    provider, _ = find_analysis_provider(db)
    return bool(provider and provider.api_key)


def queue_auto_graphify_run(db: Session, chapter: Chapter, version: ChapterVersion, content: str, trigger: str) -> int | None:
    """Create one automatic Graphify pass for a unique chapter-content SHA.

    The queue record is created before the background task starts, so the UI can
    immediately show that a saved chapter is being processed. Failed runs are
    intentionally not treated as duplicates and can be retried by saving the
    chapter again.
    """
    if not content.strip():
        return
    sha = content_sha256(content)
    existing_runs = db.scalars(
        select(GraphifyRun)
        .where(
            GraphifyRun.book_id == chapter.book_id,
            GraphifyRun.operation == "auto_chapter",
            GraphifyRun.source_chapter_id == chapter.id,
            GraphifyRun.content_sha256 == sha,
            GraphifyRun.status.in_(("queued", "analyzing", "completed")),
        )
        .order_by(GraphifyRun.id.desc())
    ).all()
    character_recovery_only = False
    for existing in existing_runs:
        if existing.status in {"queued", "analyzing"}:
            return None
        if existing.status == "completed":
            if not _completed_run_needs_character_recovery(db, chapter, existing):
                return None
            character_recovery_only = True
    run = GraphifyRun(
        book_id=chapter.book_id,
        operation="auto_chapter",
        status="queued",
        source_chapter_id=chapter.id,
        source_version_id=version.id,
        content_sha256=sha,
        request_context={
            "analysis_type": "graphify_init",
            "source": "chapter_version",
            "chapter_id": chapter.id,
            "chapter_number": chapter.chapter_number,
            "version_id": version.id,
            "content_sha256": sha,
            "trigger": trigger,
            "character_recovery_only": character_recovery_only,
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run.id


def schedule_auto_graphify(db: Session, background_tasks: BackgroundTasks, chapter: Chapter, version: ChapterVersion, content: str) -> None:
    run_id = queue_auto_graphify_run(db, chapter, version, content, "chapter_write")
    if run_id:
        background_tasks.add_task(process_auto_graphify, run_id)


async def auto_graphify_existing_chapters() -> None:
    """Backfill saved chapters after startup without blocking application startup."""
    db = SessionLocal()
    run_ids: list[int] = []
    try:
        # Background tasks do not survive a process restart. Resume their audit
        # records explicitly instead of letting queued/analyzing rows block the
        # SHA reconciliation forever.
        orphaned = db.scalars(
            select(GraphifyRun)
            .where(
                GraphifyRun.operation == "auto_chapter",
                GraphifyRun.status.in_(("queued", "analyzing")),
            )
            .order_by(GraphifyRun.id)
        ).all()
        for run in orphaned:
            run.status = "queued"
            run_ids.append(run.id)
        if orphaned:
            db.commit()

        chapters = db.scalars(select(Chapter).order_by(Chapter.book_id, Chapter.chapter_number)).all()
        for chapter in chapters:
            version = db.get(ChapterVersion, chapter.current_version_id) if chapter.current_version_id else None
            if not version:
                continue
            run_id = queue_auto_graphify_run(db, chapter, version, read_content(version), "startup_reconcile")
            if run_id:
                run_ids.append(run_id)
    finally:
        db.close()
    # Keep calls sequential: a browser-backed conversation may not support
    # concurrent requests, and the audit order stays deterministic.
    for run_id in run_ids:
        await process_auto_graphify(run_id)


def _auto_graphify_character_context(
    db: Session,
    chapter: Chapter,
    context: dict[str, Any],
    version: ChapterVersion,
    run: GraphifyRun,
) -> dict[str, Any]:
    character_context = build_context(db, chapter, {
        "analysis_type": "characters",
        "include_characters": True,
    })
    character_context["chapter"] = dict(context["chapter"])
    character_context["intent"] = (
        "自动 Graphify 的角色补提取：列出当前章节实际出现或被明确提及的所有有姓名角色，"
        "包括角色库中已有的角色；没有明确姓名的群体或路人不要创建角色卡。"
    )
    character_context["source_version_id"] = version.id
    character_context["content_sha256"] = run.content_sha256
    return character_context


async def process_auto_graphify(run_id: int) -> None:
    """Analyze a saved chapter version and automatically ingest safe proposals."""
    db = SessionLocal()
    run: GraphifyRun | None = None
    raw = ""
    try:
        run = db.get(GraphifyRun, run_id)
        if not run:
            return
        chapter = db.get(Chapter, run.source_chapter_id) if run.source_chapter_id else None
        version = db.get(ChapterVersion, run.source_version_id) if run.source_version_id else None
        book = db.get(Book, run.book_id)
        if not chapter or not version or not book:
            raise ValueError("自动 Graphify 的来源章节或版本不存在")
        content = read_content(version)
        if content_sha256(content) != run.content_sha256:
            raise ValueError("自动 Graphify 来源正文 SHA 已变化，跳过旧任务")

        run.status = "analyzing"
        db.commit()
        context = build_context(db, chapter, {
            "analysis_type": "graphify_init",
            "include_outline": True,
            "include_characters": True,
            "include_knowledge": True,
            "include_events": True,
            "include_relations": True,
            "include_threads": True,
        })
        # The source is the version that caused this task, not necessarily the
        # chapter's current version if another edit was saved while queued.
        context["chapter"] = {
            "id": chapter.id,
            "number": chapter.chapter_number,
            "title": chapter.title,
            "content": content,
        }
        context["intent"] = "章节保存后自动提取角色、知识、事件和情节线；只使用正文证据，不猜测未出现的信息。"
        context["source_version_id"] = version.id
        context["content_sha256"] = run.content_sha256

        providers, _ = find_analysis_providers(db)
        request_context = run.request_context if isinstance(run.request_context, dict) else {}
        character_recovery_only = bool(request_context.get("character_recovery_only"))
        if not character_recovery_only:
            historical_runs = db.scalars(
                select(GraphifyRun).where(
                    GraphifyRun.id != run.id,
                    GraphifyRun.operation == "auto_chapter",
                    GraphifyRun.source_chapter_id == chapter.id,
                    GraphifyRun.content_sha256 == run.content_sha256,
                    GraphifyRun.status == "completed",
                )
            ).all()
            character_recovery_only = any(
                _completed_run_needs_character_recovery(db, chapter, historical)
                for historical in historical_runs
            )
            if character_recovery_only:
                run.request_context = {**request_context, "character_recovery_only": True}
        character_extraction_attempted = False
        provider_fallbacks: list[dict[str, str]] = []
        if providers:
            run.model = providers[0].model
            db.commit()
            if character_recovery_only:
                character_context = _auto_graphify_character_context(db, chapter, context, version, run)
                character_result, character_raw, character_elapsed, character_provider, failures = await _call_auto_graphify_with_fallback(
                    providers, character_context
                )
                provider_fallbacks.extend({**item, "phase": "characters"} for item in failures)
                character_result = validate_analysis_result(character_result, "characters")
                result = {
                    "analysis_type": "graphify_init",
                    "characters": character_result.get("characters", []),
                    "knowledge": [], "threads": [], "events": [],
                }
                raw, elapsed = character_raw, character_elapsed
                character_extraction_attempted = True
                run.model = character_provider.model
            else:
                result, raw, elapsed, provider, failures = await _call_auto_graphify_with_fallback(providers, context)
                provider_fallbacks.extend({**item, "phase": "graphify_init"} for item in failures)
                result = validate_analysis_result(result, "graphify_init")
                if not result.get("characters"):
                    character_context = _auto_graphify_character_context(db, chapter, context, version, run)
                    character_result, character_raw, character_elapsed, character_provider, failures = await _call_auto_graphify_with_fallback(
                        providers, character_context, preferred=provider
                    )
                    provider_fallbacks.extend({**item, "phase": "characters"} for item in failures)
                    character_result = validate_analysis_result(character_result, "characters")
                    result["characters"] = character_result.get("characters", [])
                    raw = json.dumps({"graphify_init": raw, "character_extraction": character_raw}, ensure_ascii=False)
                    elapsed += character_elapsed
                    character_extraction_attempted = True
                    if character_provider.id != provider.id:
                        run.model = f"{provider.model} / {character_provider.model}"
                    else:
                        run.model = provider.model
                else:
                    run.model = provider.model
            run.raw_response = raw
            run.elapsed_ms = elapsed
        else:
            result = mock_graphify_init(context)
            run.model = "local-safe-mock"
            run.raw_response = json.dumps(result, ensure_ascii=False)

        result["character_extraction_attempted"] = character_extraction_attempted
        result["character_recovery_only"] = character_recovery_only
        if provider_fallbacks:
            result["provider_fallbacks"] = provider_fallbacks

        result, proposals = prepare_graphify_init(db, book.id, result, chapter.id)
        for candidate in proposals:
            db.add(GraphifyProposal(run_id=run.id, **{
                key: candidate.get(key)
                for key in ("change_type", "target_type", "target_id", "target_name", "old_value", "proposed_value", "reason", "evidence", "source_chapter_id", "confidence", "conflict")
                if key in {column.name for column in GraphifyProposal.__table__.columns}
            }))
        db.flush()
        run.result_json = {
            **result,
            "notice": (
                "历史章节缺失角色已完成专用补提取并自动入库；详细变更请查看入库日志。"
                if character_recovery_only
                else "章节保存触发的 Graphify 已自动入库；详细变更请查看入库日志。"
            ),
            "source_chapter_id": chapter.id,
            "source_version_id": version.id,
            "content_sha256": run.content_sha256,
        }
        auto_apply_graphify_proposals(run, db)
        run.status = "completed"
        db.commit()
    except Exception as exc:
        db.rollback()
        raw = getattr(exc, "raw_response", raw)
        if run:
            failed = db.get(GraphifyRun, run.id)
            if failed:
                failed.status = "failed"
                failed.raw_response = raw
                failed.error_message = str(exc).strip() or exc.__class__.__name__
                attempted_models = getattr(exc, "attempted_models", None)
                if attempted_models:
                    failed.model = " / ".join(attempted_models)
                failed.result_json = {
                    "analysis_type": "graphify_init",
                    "characters": [], "knowledge": [], "threads": [], "events": [], "proposals": [],
                }
                db.commit()
    finally:
        db.close()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/books", response_model=list[BookOut])
def list_books(db: Session = Depends(get_db)):
    return [serialize_book(book, db) for book in db.scalars(select(Book).order_by(Book.updated_at.desc())).all()]


@router.post("/books", response_model=BookOut)
def create_book(payload: BookCreate, db: Session = Depends(get_db)):
    book = Book(**payload.model_dump())
    db.add(book)
    db.commit()
    db.refresh(book)
    return serialize_book(book, db)


@router.get("/books/{book_id}", response_model=BookOut)
def get_book(book_id: int, db: Session = Depends(get_db)):
    return serialize_book(require_book(db, book_id), db)


@router.put("/books/{book_id}", response_model=BookOut)
def update_book(book_id: int, payload: BookUpdate, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(book, key, value)
    db.commit()
    db.refresh(book)
    return serialize_book(book, db)


@router.delete("/books/{book_id}")
def delete_book(book_id: int, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    db.delete(book)
    db.commit()
    return {"ok": True}


@router.get("/books/{book_id}/chapters", response_model=list[ChapterOut])
def list_chapters(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return [serialize_chapter(chapter, db) for chapter in db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()]


@router.post("/books/{book_id}/chapters", response_model=ChapterOut)
def create_chapter(book_id: int, payload: ChapterCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    number = payload.chapter_number or next_chapter_number(db, book_id)
    chapter = Chapter(book_id=book_id, chapter_number=number, title=payload.title, status=payload.status, word_count=count_words(payload.content))
    db.add(chapter)
    db.flush()
    version_number = 1
    path = save_content(book_id, chapter.id, version_number, payload.content)
    version = ChapterVersion(chapter_id=chapter.id, version_number=version_number, content_path=path, word_count=count_words(payload.content), source=payload.source, note=payload.note)
    db.add(version)
    db.flush()
    chapter.current_version_id = version.id
    if not book.current_chapter_id:
        book.current_chapter_id = chapter.id
    db.commit()
    db.refresh(chapter)
    schedule_auto_graphify(db, background_tasks, chapter, version, payload.content)
    return serialize_chapter(chapter, db)


@router.post("/books/{book_id}/chapters/import", response_model=list[ChapterOut])
async def import_chapters(book_id: int, background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    require_book(db, book_id)
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    title = Path(file.filename or "导入章节").stem
    chapter = create_chapter(book_id, ChapterCreate(title=title, content=text, source="imported"), background_tasks, db)
    return [chapter]


@router.get("/chapters/{chapter_id}", response_model=ChapterOut)
def get_chapter(chapter_id: int, db: Session = Depends(get_db)):
    return serialize_chapter(require_chapter(db, chapter_id), db)


@router.put("/chapters/{chapter_id}", response_model=ChapterOut)
def update_chapter(chapter_id: int, payload: ChapterUpdate, db: Session = Depends(get_db)):
    chapter = require_chapter(db, chapter_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(chapter, key, value)
    if payload.chapter_number is not None:
        conflict = db.scalar(select(Chapter).where(Chapter.book_id == chapter.book_id, Chapter.chapter_number == payload.chapter_number, Chapter.id != chapter.id))
        if conflict:
            raise HTTPException(409, "同一本书内章节编号不能重复")
    db.commit()
    db.refresh(chapter)
    return serialize_chapter(chapter, db)


@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, confirm: bool = False, db: Session = Depends(get_db)):
    if not confirm:
        raise HTTPException(400, "删除章节需要确认")
    chapter = require_chapter(db, chapter_id)
    db.delete(chapter)
    db.commit()
    return {"ok": True}


@router.post("/books/{book_id}/chapters/reorder")
def reorder_chapters(book_id: int, payload: ChapterReorder, db: Session = Depends(get_db)):
    require_book(db, book_id)
    chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()
    known = {chapter.id: chapter for chapter in chapters}
    if set(payload.chapter_ids) != set(known) or len(payload.chapter_ids) != len(known):
        raise HTTPException(400, "必须提供当前书籍的全部章节，且不能重复")
    # Use temporary negative numbers to avoid unique-key collisions while swapping order.
    for index, chapter_id in enumerate(payload.chapter_ids, start=1):
        known[chapter_id].chapter_number = -index
    db.flush()
    for index, chapter_id in enumerate(payload.chapter_ids, start=1):
        known[chapter_id].chapter_number = index
    db.commit()
    return [serialize_chapter(known[chapter_id], db) for chapter_id in payload.chapter_ids]


@router.post("/chapters/{chapter_id}/versions", response_model=ChapterVersionOut)
def create_version(chapter_id: int, payload: VersionCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    chapter = require_chapter(db, chapter_id)
    number = next_version_number(db, chapter_id)
    path = save_content(chapter.book_id, chapter.id, number, payload.content)
    version = ChapterVersion(chapter_id=chapter_id, version_number=number, content_path=path, word_count=count_words(payload.content), source=payload.source, note=payload.note)
    db.add(version)
    db.flush()
    chapter.current_version_id = version.id
    chapter.word_count = version.word_count
    db.commit()
    db.refresh(version)
    schedule_auto_graphify(db, background_tasks, chapter, version, payload.content)
    return {**{column.name: getattr(version, column.name) for column in ChapterVersion.__table__.columns}, "content": payload.content}


@router.get("/chapters/{chapter_id}/versions", response_model=list[ChapterVersionOut])
def list_versions(chapter_id: int, db: Session = Depends(get_db)):
    require_chapter(db, chapter_id)
    versions = db.scalars(select(ChapterVersion).where(ChapterVersion.chapter_id == chapter_id).order_by(ChapterVersion.version_number.desc())).all()
    return [{**{column.name: getattr(item, column.name) for column in ChapterVersion.__table__.columns}, "content": read_content(item)} for item in versions]


@router.get("/chapters/{chapter_id}/diff")
def diff_versions(chapter_id: int, left: int, right: int, db: Session = Depends(get_db)):
    require_chapter(db, chapter_id)
    left_version = db.get(ChapterVersion, left)
    right_version = db.get(ChapterVersion, right)
    if not left_version or not right_version or left_version.chapter_id != chapter_id or right_version.chapter_id != chapter_id:
        raise HTTPException(404, "版本不存在或不属于当前章节")
    return {"left": {"version": left_version.version_number, "content": read_content(left_version)}, "right": {"version": right_version.version_number, "content": read_content(right_version)}}


@router.post("/chapters/{chapter_id}/versions/{version_id}/restore", response_model=ChapterVersionOut)
def restore_version(chapter_id: int, version_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    chapter = require_chapter(db, chapter_id)
    version = db.get(ChapterVersion, version_id)
    if not version or version.chapter_id != chapter_id:
        raise HTTPException(404, "版本不存在或不属于当前章节")
    restored = VersionCreate(content=read_content(version), source="revised", note=f"从 v{version.version_number} 恢复")
    return create_version(chapter_id, restored, background_tasks, db)


def _fallback_summary(content: str) -> str:
    paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
    if not paragraphs:
        return "【本章概述】本章暂无正文。\n【关键人物】无\n【关键事件】无\n【状态变化】无\n【结尾状态】未说明。\n【未决事项】无"
    summary = "；".join(paragraphs)
    excerpt = summary[:800] + ("……" if len(summary) > 800 else "")
    return f"【本章概述】{excerpt}\n【关键人物】未使用模型提取。\n【关键事件】未使用模型提取。\n【状态变化】未使用模型提取。\n【结尾状态】未使用模型提取。\n【未决事项】未使用模型提取。"


async def _update_chapter_summary(chapter_id: int, db: Session, *, skip_if_unchanged: bool = False):
    chapter = require_chapter(db, chapter_id)
    content = serialize_chapter(chapter, db)["current_content"]
    current_sha = content_sha256(content)
    if skip_if_unchanged and chapter.summary and chapter.summary_content_sha256 == current_sha:
        return {
            "chapter_id": chapter.id, "summary": chapter.summary, "content_sha256": current_sha,
            "source_version_id": chapter.summary_source_version_id, "model": chapter.summary_model,
            "status": "skipped", "updated": False, "skipped": True,
            "message": "正文 SHA 未变化，已有梗概，未重复生成。", "analysis_id": None,
        }

    analysis = Analysis(book_id=chapter.book_id, chapter_id=chapter.id, status="analyzing",
                        request_context={"analysis_type": "chapter_summary", "content_sha256": current_sha,
                                         "source_version_id": chapter.current_version_id})
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    raw = ""
    try:
        provider, _ = find_analysis_provider(db)
        if provider and provider.api_key:
            context = build_context(db, chapter, {"analysis_type": "chapter_summary"})
            result, raw, elapsed = await call_provider(provider, context)
            result = validate_analysis_result(result, "chapter_summary")
            summary = result["summary"]
            model = provider.model
        else:
            summary = _fallback_summary(content)
            raw = json.dumps({"summary": summary}, ensure_ascii=False)
            elapsed = 0
            model = "local-safe-mock"

        analysis.raw_response = raw
        analysis.elapsed_ms = elapsed
        analysis.model = model
        analysis.token_count = len(raw) // 4
        analysis.result_json = {"analysis_type": "chapter_summary", "summary": summary, "content_sha256": current_sha}
        analysis.status = "completed"
        chapter.summary = summary
        chapter.summary_content_sha256 = current_sha
        chapter.summary_source_version_id = chapter.current_version_id
        chapter.summary_model = model
        chapter.summary_updated_at = datetime.utcnow()
        db.commit()
        return {
            "chapter_id": chapter.id, "summary": summary, "content_sha256": current_sha,
            "source_version_id": chapter.current_version_id, "model": model,
            "status": "completed", "updated": True, "skipped": False,
            "message": "本章梗概已更新并固化。", "analysis_id": analysis.id,
        }
    except Exception as exc:
        raw = getattr(exc, "raw_response", raw)
        analysis.raw_response = raw
        analysis.status = "failed"
        analysis.error_message = str(exc).strip() or exc.__class__.__name__
        analysis.result_json = {"analysis_type": "chapter_summary", "summary": "", "content_sha256": current_sha}
        db.commit()
        return {
            "chapter_id": chapter.id, "summary": chapter.summary or "", "content_sha256": current_sha,
            "source_version_id": chapter.summary_source_version_id, "model": analysis.model,
            "status": "failed", "updated": False, "skipped": False,
            "message": analysis.error_message or "章节梗概生成失败。", "analysis_id": analysis.id,
        }


@router.post("/chapters/{chapter_id}/summary", response_model=ChapterSummaryOut)
async def update_chapter_summary(chapter_id: int, db: Session = Depends(get_db)):
    # A direct request is an explicit user instruction to regenerate the summary.
    return await _update_chapter_summary(chapter_id, db)


@router.post("/books/{book_id}/chapter-summaries", response_model=ChapterSummaryBatchOut)
async def update_book_summaries(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()
    results = [await _update_chapter_summary(chapter.id, db, skip_if_unchanged=True) for chapter in chapters]
    return {
        "book_id": book_id,
        "total": len(results),
        "updated": sum(1 for item in results if item.get("updated")),
        "skipped": sum(1 for item in results if item.get("skipped")),
        "failed": sum(1 for item in results if item.get("status") == "failed"),
        "results": results,
    }


def character_version(db: Session, item: Character, source: str = "manual", reason: str = "") -> None:
    structured = {key: getattr(item, key) for key in ("name", "aliases", "role", "age", "identity", "summary", "appearance", "personality", "level", "inventory", "relationships", "goals", "fears", "abilities", "secrets", "current_state", "status")}
    last = db.scalar(select(CharacterVersion.version_number).where(CharacterVersion.character_id == item.id).order_by(CharacterVersion.version_number.desc())) or 0
    version = CharacterVersion(character_id=item.id, version_number=last + 1, structured_json=structured, source=source, change_reason=reason)
    db.add(version)
    db.flush()
    item.current_version_id = version.id


@router.get("/books/{book_id}/characters", response_model=list[CharacterOut])
def list_characters(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return db.scalars(select(Character).where(Character.book_id == book_id).order_by(Character.name)).all()


@router.post("/books/{book_id}/characters", response_model=CharacterOut)
def create_character(book_id: int, payload: CharacterCreate, db: Session = Depends(get_db)):
    require_book(db, book_id)
    item = Character(book_id=book_id, **payload.model_dump())
    db.add(item)
    db.flush()
    character_version(db, item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/characters/{character_id}", response_model=CharacterOut)
def update_character(character_id: int, payload: CharacterCreate, db: Session = Depends(get_db)):
    item = db.get(Character, character_id)
    if not item:
        raise HTTPException(404, "角色不存在")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    character_version(db, item, reason="手动编辑角色卡")
    db.commit()
    db.refresh(item)
    return item


@router.delete("/characters/{character_id}")
def delete_character(character_id: int, confirm: bool = False, db: Session = Depends(get_db)):
    if not confirm:
        raise HTTPException(400, "删除角色需要确认")
    item = db.get(Character, character_id)
    if not item:
        raise HTTPException(404, "角色不存在")
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.get("/characters/{character_id}/versions")
def list_character_versions(character_id: int, db: Session = Depends(get_db)):
    if not db.get(Character, character_id):
        raise HTTPException(404, "角色不存在")
    return db.scalars(select(CharacterVersion).where(CharacterVersion.character_id == character_id).order_by(CharacterVersion.version_number.desc())).all()


def knowledge_version(db: Session, item: KnowledgeItem, source: str = "manual", reason: str = "") -> None:
    last = db.scalar(select(KnowledgeVersion.version_number).where(KnowledgeVersion.knowledge_item_id == item.id).order_by(KnowledgeVersion.version_number.desc())) or 0
    version = KnowledgeVersion(knowledge_item_id=item.id, version_number=last + 1, content=item.content, source=source, change_reason=reason)
    db.add(version)
    db.flush()
    item.current_version_id = version.id


@router.get("/books/{book_id}/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return db.scalars(select(KnowledgeItem).where(KnowledgeItem.book_id == book_id).order_by(KnowledgeItem.category, KnowledgeItem.name)).all()


@router.post("/books/{book_id}/knowledge", response_model=KnowledgeOut)
def create_knowledge(book_id: int, payload: KnowledgeCreate, db: Session = Depends(get_db)):
    require_book(db, book_id)
    item = KnowledgeItem(book_id=book_id, **payload.model_dump())
    db.add(item)
    db.flush()
    knowledge_version(db, item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/knowledge/{knowledge_id}", response_model=KnowledgeOut)
def update_knowledge(knowledge_id: int, payload: KnowledgeCreate, db: Session = Depends(get_db)):
    item = db.get(KnowledgeItem, knowledge_id)
    if not item:
        raise HTTPException(404, "知识项不存在")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    knowledge_version(db, item, reason="手动编辑知识项")
    db.commit()
    db.refresh(item)
    return item


@router.get("/knowledge/{knowledge_id}/versions")
def list_knowledge_versions(knowledge_id: int, db: Session = Depends(get_db)):
    if not db.get(KnowledgeItem, knowledge_id):
        raise HTTPException(404, "知识项不存在")
    return db.scalars(select(KnowledgeVersion).where(KnowledgeVersion.knowledge_item_id == knowledge_id).order_by(KnowledgeVersion.version_number.desc())).all()


@router.get("/books/{book_id}/outlines", response_model=list[OutlineOut])
def list_outlines(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return db.scalars(select(Outline).where(Outline.book_id == book_id).order_by(Outline.updated_at.desc())).all()


@router.post("/books/{book_id}/outlines", response_model=OutlineOut)
def create_outline(book_id: int, payload: OutlineCreate, db: Session = Depends(get_db)):
    require_book(db, book_id)
    item = Outline(book_id=book_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/outlines/{outline_id}", response_model=OutlineOut)
def update_outline(outline_id: int, payload: OutlineCreate, db: Session = Depends(get_db)):
    item = db.get(Outline, outline_id)
    if not item:
        raise HTTPException(404, "大纲不存在")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.version += 1
    db.commit()
    db.refresh(item)
    return item


@router.get("/outlines/{outline_id}/blocks", response_model=list[OutlineBlockOut])
def list_blocks(outline_id: int, db: Session = Depends(get_db)):
    if not db.get(Outline, outline_id):
        raise HTTPException(404, "大纲不存在")
    return db.scalars(select(OutlineBlock).where(OutlineBlock.outline_id == outline_id).order_by(OutlineBlock.order_index, OutlineBlock.id)).all()


@router.post("/outlines/{outline_id}/blocks", response_model=OutlineBlockOut)
def create_block(outline_id: int, payload: OutlineBlockCreate, db: Session = Depends(get_db)):
    if not db.get(Outline, outline_id):
        raise HTTPException(404, "大纲不存在")
    item = OutlineBlock(outline_id=outline_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/outline-blocks/{block_id}", response_model=OutlineBlockOut)
def update_block(block_id: int, payload: OutlineBlockCreate, db: Session = Depends(get_db)):
    item = db.get(OutlineBlock, block_id)
    if not item:
        raise HTTPException(404, "大纲块不存在")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.get("/books/{book_id}/events", response_model=list[EventOut])
def list_events(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return db.scalars(select(Event).where(Event.book_id == book_id).order_by(Event.start_chapter.nulls_last(), Event.id)).all()


@router.post("/books/{book_id}/events", response_model=EventOut)
def create_event(book_id: int, payload: EventCreate, db: Session = Depends(get_db)):
    require_book(db, book_id)
    item = Event(book_id=book_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/events/{event_id}", response_model=EventOut)
def update_event(event_id: int, payload: EventCreate, db: Session = Depends(get_db)):
    item = db.get(Event, event_id)
    if not item:
        raise HTTPException(404, "事件不存在")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.get("/events/{event_id}/relations", response_model=list[EventRelationOut])
def event_relations(event_id: int, db: Session = Depends(get_db)):
    if not db.get(Event, event_id):
        raise HTTPException(404, "事件不存在")
    return db.scalars(select(EventRelation).where((EventRelation.from_event_id == event_id) | (EventRelation.to_event_id == event_id))).all()


@router.get("/books/{book_id}/threads", response_model=list[ThreadOut])
def list_threads(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return db.scalars(select(PlotThread).where(PlotThread.book_id == book_id).order_by(PlotThread.updated_at.desc(), PlotThread.id)).all()


@router.post("/books/{book_id}/threads", response_model=ThreadOut)
def create_thread(book_id: int, payload: ThreadCreate, db: Session = Depends(get_db)):
    require_book(db, book_id)
    item = PlotThread(book_id=book_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/threads/{thread_id}", response_model=ThreadOut)
def update_thread(thread_id: int, payload: ThreadCreate, db: Session = Depends(get_db)):
    item = db.get(PlotThread, thread_id)
    if not item:
        raise HTTPException(404, "情节线不存在")
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.post("/threads/{thread_id}/resolve", response_model=ThreadOut)
def resolve_thread(thread_id: int, db: Session = Depends(get_db)):
    item = db.get(PlotThread, thread_id)
    if not item:
        raise HTTPException(404, "情节线不存在")
    item.status = "resolved"
    db.commit()
    db.refresh(item)
    return item


@router.get("/books/{book_id}/graph")
def get_graph(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    from ..graphify import build_graph
    return build_graph(db, book_id)


@router.get("/books/{book_id}/graphify/status")
def get_graphify_status(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return graph_status(db, book_id)


@router.post("/books/{book_id}/graphify/query")
def query_graphify(book_id: int, payload: GraphifyQueryRequest, db: Session = Depends(get_db)):
    require_book(db, book_id)
    if not payload.question.strip():
        raise HTTPException(400, "请输入要查询的问题或对象")
    return graph_query(db, book_id, payload.question, payload.dfs)


@router.post("/books/{book_id}/graphify/path")
def path_graphify(book_id: int, payload: GraphifyPathRequest, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return graph_path(db, book_id, payload.from_name, payload.to_name)


def graphify_run_with_proposals(run: GraphifyRun, db: Session) -> dict[str, Any]:
    data = {column.name: getattr(run, column.name) for column in GraphifyRun.__table__.columns}
    data["proposals"] = db.scalars(select(GraphifyProposal).where(GraphifyProposal.run_id == run.id).order_by(GraphifyProposal.id)).all()
    return data


def write_change_log(db: Session, *, book_id: int, source_type: str, source_id: int | None, operation: str,
                     action: str, target_type: str, target_id: int | None, target_name: str,
                     old_value: Any, new_value: Any, reason: str, evidence: str) -> None:
    db.add(ChangeLog(book_id=book_id, source_type=source_type, source_id=source_id, operation=operation,
                     action=action, target_type=target_type, target_id=target_id, target_name=target_name,
                     old_value=old_value, new_value=new_value, reason=reason, evidence=evidence))


@router.get("/books/{book_id}/change-logs", response_model=list[ChangeLogOut])
def list_change_logs(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    rows = db.scalars(select(ChangeLog).where(ChangeLog.book_id == book_id).order_by(ChangeLog.created_at.desc(), ChangeLog.id.desc())).all()
    result = []
    for item in rows:
        data = {column.name: getattr(item, column.name) for column in ChangeLog.__table__.columns}
        # Older analyses could store a model-provided name in target_id. Keep
        # the audit record visible instead of letting one legacy row break the
        # whole log response schema.
        if not isinstance(data.get("target_id"), int):
            data["target_id"] = None
        result.append(data)
    return result


@router.get("/books/{book_id}/graphify/runs", response_model=list[GraphifyRunOut])
def list_graphify_runs(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    return [graphify_run_with_proposals(item, db) for item in db.scalars(select(GraphifyRun).where(GraphifyRun.book_id == book_id).order_by(GraphifyRun.created_at.desc(), GraphifyRun.id.desc())).all()]


@router.get("/graphify/runs/{run_id}", response_model=GraphifyRunOut)
def get_graphify_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(GraphifyRun, run_id)
    if not run:
        raise HTTPException(404, "Graphify 运行记录不存在")
    return graphify_run_with_proposals(run, db)


@router.post("/graphify/runs/{run_id}/auto-apply", response_model=GraphifyRunOut)
def auto_apply_graphify_run(run_id: int, db: Session = Depends(get_db)):
    """将历史运行中仍处于待处理状态的非冲突提案自动写入资料库。

    这是一个幂等的恢复入口：只处理 pending 提案，已经自动入库、接受或拒绝的
    提案不会再次执行。用于升级到自动入库模式前创建的历史运行记录。
    """
    run = db.get(GraphifyRun, run_id)
    if not run:
        raise HTTPException(404, "Graphify 运行记录不存在")
    auto_apply_graphify_proposals(run, db)
    if isinstance(run.result_json, dict):
        run.result_json = {
            **run.result_json,
            "notice": "历史提案已按当前自动入库规则处理；详细写入记录请查看 Graphify 图谱中的入库日志。",
        }
    db.commit()
    return graphify_run_with_proposals(run, db)


@router.post("/books/{book_id}/graphify/init", response_model=GraphifyRunOut)
async def init_graphify(book_id: int, payload: GraphifyInitRequest, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    chapters = list(db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)))
    if payload.from_chapters and not chapters:
        raise HTTPException(400, "当前书籍还没有章节，不能从章节初始化")
    context: dict[str, Any] = {
        "analysis_type": "graphify_init", "premise": payload.premise, "intent": payload.intent,
        "batch": payload.batch, "book": {"id": book.id, "title": book.title, "author": book.author, "genre": book.genre, "description": book.description},
        "characters": [], "knowledge": [], "events": [], "threads": [],
    }
    if payload.from_chapters:
        context["chapters"] = [{"id": item.id, "number": item.chapter_number, "title": item.title,
                                 "content": serialize_chapter(item, db)["current_content"]} for item in chapters]
    provider, selection = find_analysis_provider(db)
    run = GraphifyRun(book_id=book_id, operation="init", status="analyzing", request_context=payload.model_dump())
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        if provider and provider.api_key:
            result, raw, elapsed = await call_provider(provider, context)
            run.model = provider.model
            run.raw_response = raw
            run.elapsed_ms = elapsed
        else:
            result = mock_graphify_init(context)
            run.model = "local-safe-mock"
            run.raw_response = json.dumps(result, ensure_ascii=False)
        result, proposals = prepare_graphify_init(db, book_id, result, chapters[0].id if chapters else None)
        run.result_json = result
        run.status = "completed"
        for candidate in proposals:
            db.add(GraphifyProposal(run_id=run.id, **{key: candidate.get(key) for key in ("change_type", "target_type", "target_id", "target_name", "old_value", "proposed_value", "reason", "evidence", "source_chapter_id", "confidence", "conflict") if key in {column.name for column in GraphifyProposal.__table__.columns}}))
        db.flush()
        auto_apply_graphify_proposals(run, db)
        result["notice"] = "初始化提案已自动入库；详细写入记录请查看 Graphify 图谱中的入库日志。"
        run.result_json = result
        db.commit()
    except Exception as exc:
        raw = getattr(exc, "raw_response", "")
        run.status = "failed"
        run.raw_response = raw
        run.error_message = str(exc).strip() or exc.__class__.__name__
        run.result_json = {"analysis_type": "graphify_init", "characters": [], "knowledge": [], "threads": [], "events": [], "proposals": []}
        db.commit()
    return graphify_run_with_proposals(run, db)


@router.post("/books/{book_id}/graphify/review-passage", response_model=GraphifyRunOut)
async def review_graphify_passage(book_id: int, payload: GraphifyPassageRequest, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    if not payload.passage.strip():
        raise HTTPException(400, "片段不能为空")
    context = {"analysis_type": "review", "intent": payload.intent, "book": {"id": book.id, "title": book.title, "author": book.author, "genre": book.genre, "description": book.description},
               "chapter": {"id": None, "number": None, "title": "临时片段", "content": payload.passage},
               "characters": [{"id": item.id, "name": item.name, "identity": item.identity, "summary": item.summary, "current_state": item.current_state} for item in db.scalars(select(Character).where(Character.book_id == book_id))],
               "knowledge": [{"name": item.name, "category": item.category, "content": item.content, "status": item.status} for item in db.scalars(select(KnowledgeItem).where(KnowledgeItem.book_id == book_id, KnowledgeItem.status == "active"))],
               "events": [{"title": item.title, "description": item.description, "status": item.status, "track": item.track} for item in db.scalars(select(Event).where(Event.book_id == book_id))],
               "threads": [{"title": item.title, "description": item.description, "status": item.status, "unresolved_question": item.unresolved_question} for item in db.scalars(select(PlotThread).where(PlotThread.book_id == book_id))]}
    provider, _ = find_analysis_provider(db)
    run = GraphifyRun(book_id=book_id, operation="review", status="analyzing", request_context=payload.model_dump())
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        if provider and provider.api_key:
            result, raw, elapsed = await call_provider(provider, context)
            run.model, run.raw_response, run.elapsed_ms = provider.model, raw, elapsed
            result = validate_analysis_result(result, "review")
        else:
            result = mock_analysis(context)
            run.model, run.raw_response = "local-safe-mock", json.dumps(result, ensure_ascii=False)
        run.result_json = result
        run.status = "completed"
        for candidate in (result.get("change_candidates", []) if isinstance(result, dict) else []):
            if not isinstance(candidate, dict) or not candidate.get("target_type"):
                continue
            db.add(GraphifyProposal(run_id=run.id, change_type=candidate.get("change_type", "other"), target_type=candidate.get("target_type", "other"), target_id=candidate.get("target_id"), target_name=candidate.get("target_name", ""), old_value=candidate.get("old_value"), proposed_value=candidate.get("proposed_value"), reason=candidate.get("reason", ""), evidence=candidate.get("evidence", ""), confidence=_safe_confidence(candidate.get("confidence")), conflict=bool(candidate.get("conflict", False))))
        db.flush()
        auto_apply_graphify_proposals(run, db)
        if isinstance(result, dict):
            result["notice"] = "片段审阅中的可写入变更已自动入库；详细写入记录请查看入库日志。"
            run.result_json = result
        db.commit()
    except Exception as exc:
        run.raw_response = getattr(exc, "raw_response", "")
        run.status, run.error_message = "failed", str(exc).strip() or exc.__class__.__name__
        run.result_json = {"analysis_type": "review", "facts": [], "events": [], "beliefs": [], "relation_changes": [], "issues": [], "foreshadowing_candidates": []}
        db.commit()
    return graphify_run_with_proposals(run, db)


@router.post("/books/{book_id}/graphify/lore", response_model=GraphifyRunOut)
def propose_graphify_lore(book_id: int, payload: GraphifyLoreRequest, db: Session = Depends(get_db)):
    require_book(db, book_id)
    run = GraphifyRun(book_id=book_id, operation="lore", status="completed", request_context=payload.model_dump(), model="manual")
    db.add(run)
    db.flush()
    result = {"analysis_type": "graphify_lore", "knowledge": [{"name": payload.name, "category": payload.category, "content": payload.content, "evidence": payload.evidence}], "proposals": []}
    matched = db.scalar(select(KnowledgeItem).where(KnowledgeItem.book_id == book_id, KnowledgeItem.name == payload.name))
    if matched:
        candidate = {"change_type": "knowledge_update", "target_type": "knowledge", "target_id": matched.id, "target_name": matched.name, "old_value": {"content": matched.content}, "proposed_value": {"content": payload.content}, "reason": "作者手动提出知识项更新，等待确认。", "evidence": payload.evidence, "confidence": 1.0, "conflict": False}
    else:
        candidate = {"change_type": "knowledge_add", "target_type": "knowledge", "target_id": None, "target_name": payload.name, "old_value": {}, "proposed_value": {"name": payload.name, "category": payload.category, "content": payload.content, "status": "active"}, "reason": "作者手动提出新的世界观或知识项，等待确认。", "evidence": payload.evidence, "confidence": 1.0, "conflict": False}
    result["proposals"] = [candidate]
    run.result_json, run.raw_response = result, json.dumps(result, ensure_ascii=False)
    db.add(GraphifyProposal(run_id=run.id, **candidate))
    db.flush()
    auto_apply_graphify_proposals(run, db)
    result["notice"] = "手动知识项已自动入库；详细写入记录请查看入库日志。"
    run.result_json = result
    db.commit()
    return graphify_run_with_proposals(run, db)


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def apply_graphify_proposal(proposal: GraphifyProposal, run: GraphifyRun, db: Session) -> None:
    value = proposal.proposed_value
    if not isinstance(value, dict) or proposal.conflict:
        return
    if proposal.target_type == "character":
        item = db.get(Character, proposal.target_id) if proposal.target_id else None
        if not item:
            item = Character(book_id=run.book_id, name=str(value.get("name") or proposal.target_name))
            db.add(item)
            db.flush()
        for key, proposed in value.items():
            if key in {"name", "aliases", "role", "age", "identity", "summary", "appearance", "personality", "level", "inventory", "relationships", "goals", "fears", "abilities", "secrets", "current_state", "status"}:
                setattr(item, key, str(proposed) if proposed is not None else "")
        character_version(db, item, source="extracted", reason=proposal.reason)
    elif proposal.target_type == "knowledge":
        item = db.get(KnowledgeItem, proposal.target_id) if proposal.target_id else None
        if not item:
            item = KnowledgeItem(book_id=run.book_id, name=str(value.get("name") or proposal.target_name), category=str(value.get("category") or "other"), content=str(value.get("content") or ""), status=str(value.get("status") or "active"))
            db.add(item)
            db.flush()
        else:
            for key in ("category", "content", "status"):
                if key in value:
                    setattr(item, key, value[key])
        knowledge_version(db, item, source="extracted", reason=proposal.reason)
    elif proposal.target_type == "event":
        item = db.get(Event, proposal.target_id) if proposal.target_id else None
        if not item:
            allowed = {key: value[key] for key in ("title", "description", "event_type", "track", "status", "start_chapter", "end_chapter", "start_position", "end_position", "objective_fact", "hidden_truth", "created_from_chapter_id") if key in value}
            item = Event(book_id=run.book_id, title=str(allowed.pop("title", proposal.target_name)), **allowed)
            db.add(item)
        else:
            for key, proposed in value.items():
                if key in {"title", "description", "event_type", "track", "status", "start_chapter", "end_chapter", "start_position", "end_position", "objective_fact", "hidden_truth"}:
                    setattr(item, key, proposed)
    elif proposal.target_type == "thread":
        item = db.get(PlotThread, proposal.target_id) if proposal.target_id else None
        if not item:
            allowed = {key: value[key] for key in ("title", "description", "thread_type", "status", "objective", "unresolved_question", "payoff", "seed_chapter", "last_chapter", "evidence") if key in value}
            item = PlotThread(book_id=run.book_id, title=str(allowed.pop("title", proposal.target_name)), **allowed)
            db.add(item)
        else:
            for key, proposed in value.items():
                if key in {"title", "description", "thread_type", "status", "objective", "unresolved_question", "payoff", "seed_chapter", "last_chapter", "evidence"}:
                    setattr(item, key, proposed)


def auto_apply_graphify_proposals(run: GraphifyRun, db: Session) -> None:
    """Apply every non-conflicting Graphify proposal immediately and audit it."""
    proposals = db.scalars(select(GraphifyProposal).where(GraphifyProposal.run_id == run.id, GraphifyProposal.status == "pending")).all()
    for proposal in proposals:
        action = "auto_skipped" if proposal.conflict or not isinstance(proposal.proposed_value, dict) else "auto_applied"
        if action == "auto_applied":
            apply_graphify_proposal(proposal, run, db)
            proposal.status = "auto_applied"
        else:
            proposal.status = "auto_skipped"
        write_change_log(db, book_id=run.book_id, source_type="graphify_run", source_id=run.id, operation=run.operation,
                         action=action, target_type=proposal.target_type, target_id=proposal.target_id,
                         target_name=proposal.target_name, old_value=proposal.old_value, new_value=proposal.proposed_value,
                         reason=proposal.reason, evidence=proposal.evidence)


def auto_apply_analysis_changes(analysis: Analysis, db: Session) -> None:
    """Apply every non-conflicting analysis change immediately and audit it."""
    changes = db.scalars(select(AnalysisChange).where(AnalysisChange.analysis_id == analysis.id, AnalysisChange.status == "pending")).all()
    for change in changes:
        action = "auto_skipped" if change.conflict or not isinstance(change.proposed_value, dict) else "auto_applied"
        if action == "auto_applied":
            apply_confirmed_change(change, db)
            change.status = "auto_applied"
        else:
            change.status = "auto_skipped"
        write_change_log(db, book_id=analysis.book_id, source_type="analysis", source_id=analysis.id,
                         operation=str((analysis.request_context or {}).get("analysis_type") or "analysis"),
                         action=action, target_type=change.target_type, target_id=change.target_id,
                         target_name=change.target_name, old_value=change.old_value, new_value=change.proposed_value,
                         reason=change.reason, evidence=change.evidence)


@router.post("/graphify/runs/{run_id}/proposals/{proposal_id}/accept")
def accept_graphify_proposal(run_id: int, proposal_id: int, db: Session = Depends(get_db)):
    run = db.get(GraphifyRun, run_id)
    proposal = db.get(GraphifyProposal, proposal_id)
    if not run or not proposal or proposal.run_id != run_id:
        raise HTTPException(404, "提案不存在")
    if proposal.status != "pending":
        raise HTTPException(409, "该提案已经处理")
    apply_graphify_proposal(proposal, run, db)
    proposal.status = "accepted"
    write_change_log(db, book_id=run.book_id, source_type="graphify_run", source_id=run.id, operation=run.operation,
                     action="accepted", target_type=proposal.target_type, target_id=proposal.target_id,
                     target_name=proposal.target_name, old_value=proposal.old_value, new_value=proposal.proposed_value,
                     reason=proposal.reason, evidence=proposal.evidence)
    db.commit()
    return {"ok": True, "status": proposal.status}


@router.post("/graphify/runs/{run_id}/proposals/{proposal_id}/reject")
def reject_graphify_proposal(run_id: int, proposal_id: int, db: Session = Depends(get_db)):
    proposal = db.get(GraphifyProposal, proposal_id)
    if not proposal or proposal.run_id != run_id:
        raise HTTPException(404, "提案不存在")
    proposal.status = "rejected"
    run = db.get(GraphifyRun, run_id)
    if run:
        write_change_log(db, book_id=run.book_id, source_type="graphify_run", source_id=run.id, operation=run.operation,
                         action="rejected", target_type=proposal.target_type, target_id=proposal.target_id,
                         target_name=proposal.target_name, old_value=proposal.old_value, new_value=proposal.proposed_value,
                         reason=proposal.reason, evidence=proposal.evidence)
    db.commit()
    return {"ok": True, "status": proposal.status}


@router.post("/graphify/runs/{run_id}/accept-all")
def accept_all_graphify_proposals(run_id: int, db: Session = Depends(get_db)):
    run = db.get(GraphifyRun, run_id)
    if not run:
        raise HTTPException(404, "Graphify 运行记录不存在")
    for proposal in db.scalars(select(GraphifyProposal).where(GraphifyProposal.run_id == run_id, GraphifyProposal.status == "pending")):
        apply_graphify_proposal(proposal, run, db)
        proposal.status = "accepted"
        write_change_log(db, book_id=run.book_id, source_type="graphify_run", source_id=run.id, operation=run.operation,
                         action="accepted", target_type=proposal.target_type, target_id=proposal.target_id,
                         target_name=proposal.target_name, old_value=proposal.old_value, new_value=proposal.proposed_value,
                         reason=proposal.reason, evidence=proposal.evidence)
    db.commit()
    return {"ok": True}


@router.post("/chapters/{chapter_id}/analyze", response_model=AnalysisOut)
async def analyze_chapter(chapter_id: int, payload: AnalysisRequest, db: Session = Depends(get_db)):
    chapter = require_chapter(db, chapter_id)
    context = build_context(db, chapter, payload.model_dump())
    analysis = Analysis(book_id=chapter.book_id, chapter_id=chapter.id, status="analyzing", request_context=payload.model_dump())
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    provider, provider_selection = find_analysis_provider(db)
    if provider:
        analysis.model = provider.model
    try:
        if provider and provider.api_key:
            result, raw, elapsed = await call_provider(provider, context)
            analysis.model = provider.model
            analysis.raw_response = raw
            analysis.elapsed_ms = elapsed
            result = validate_analysis_result(result, payload.analysis_type)
            if provider_selection == "single_configured":
                result.setdefault("notice", "当前只有一条已配置的 LLM，虽然未勾选启用，系统已使用它完成本次分析；配置多个模型时请明确启用其中一个。")
        else:
            result = mock_analysis(context)
            analysis.model = "local-safe-mock"
            analysis.raw_response = json.dumps(result, ensure_ascii=False)
        if payload.analysis_type == "characters":
            result = prepare_character_analysis(db, analysis, result if isinstance(result, dict) else {})
        analysis.result_json = result
        analysis.status = "completed"
        analysis.token_count = len(analysis.raw_response) // 4
        skipped_candidates = make_analysis_changes(db, analysis)
        db.flush()
        auto_apply_analysis_changes(analysis, db)
        if skipped_candidates and isinstance(result, dict):
            result.setdefault("notice", f"本次 LLM 返回了 {skipped_candidates} 条非结构化变更候选，已保留在原始结果中，但未生成不可验证的资料库变更预览。")
        if isinstance(result, dict):
            result["notice"] = (result.get("notice", "") + " 本次分析结果已自动入库；详细写入记录请查看 Graphify 图谱中的入库日志。").strip()
            analysis.result_json = result
        db.commit()
    except Exception as exc:
        analysis.raw_response = getattr(exc, "raw_response", analysis.raw_response)
        analysis.status = "failed"
        analysis.error_message = str(exc).strip() or exc.__class__.__name__
        analysis.result_json = {"facts": [], "events": [], "beliefs": [], "relation_changes": [], "issues": [], "foreshadowing_candidates": [], "change_candidates": [], "notice": "LLM 分析失败，请检查 LLM 设置、连接测试和原始错误信息。"}
        db.commit()
    return analysis_with_changes(analysis, db)


def analysis_with_changes(analysis: Analysis, db: Session) -> dict[str, Any]:
    data = {column.name: getattr(analysis, column.name) for column in Analysis.__table__.columns}
    data["changes"] = db.scalars(select(AnalysisChange).where(AnalysisChange.analysis_id == analysis.id).order_by(AnalysisChange.id)).all()
    return data


@router.get("/analysis/{analysis_id}", response_model=AnalysisOut)
def get_analysis(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(404, "分析记录不存在")
    return analysis_with_changes(analysis, db)


@router.get("/chapters/{chapter_id}/analysis", response_model=list[AnalysisOut])
def list_analysis(chapter_id: int, db: Session = Depends(get_db)):
    require_chapter(db, chapter_id)
    return [analysis_with_changes(item, db) for item in db.scalars(select(Analysis).where(Analysis.chapter_id == chapter_id).order_by(Analysis.created_at.desc())).all()]


@router.post("/analysis/{analysis_id}/issues/{issue_index}/review", response_model=AnalysisOut)
def review_analysis_issue(analysis_id: int, issue_index: int, payload: IssueReviewRequest, db: Session = Depends(get_db)):
    if payload.status not in {"pending", "confirmed", "dismissed", "deferred"}:
        raise HTTPException(400, "无效的问题确认状态")
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(404, "分析记录不存在")
    result = analysis.result_json or {}
    issues = result.get("issues", []) if isinstance(result, dict) else []
    if issue_index < 0 or issue_index >= len(issues):
        raise HTTPException(404, "问题不存在")
    issue = issues[issue_index]
    if isinstance(issue, str):
        issue = {"message": issue}
        issues[issue_index] = issue
    if not isinstance(issue, dict):
        raise HTTPException(400, "该问题结果不是可确认的结构化对象")
    issue["review_status"] = payload.status
    issue["review_note"] = payload.note
    issue["reviewed_by"] = "local_user"
    issue["reviewed_at"] = datetime.utcnow().isoformat()
    result["issues"] = issues
    analysis.result_json = result
    flag_modified(analysis, "result_json")
    db.commit()
    db.refresh(analysis)
    return analysis_with_changes(analysis, db)


@router.post("/analysis/{analysis_id}/changes/{change_id}/accept")
def accept_change(analysis_id: int, change_id: int, db: Session = Depends(get_db)):
    analysis = db.get(Analysis, analysis_id)
    change = db.get(AnalysisChange, change_id)
    if not analysis or not change or change.analysis_id != analysis_id:
        raise HTTPException(404, "变更不存在")
    if change.status != "pending":
        raise HTTPException(409, "该变更已经处理")
    apply_confirmed_change(change, db)
    change.status = "accepted"
    write_change_log(db, book_id=analysis.book_id, source_type="analysis", source_id=analysis.id,
                     operation=str((analysis.request_context or {}).get("analysis_type") or "analysis"), action="accepted",
                     target_type=change.target_type, target_id=change.target_id, target_name=change.target_name,
                     old_value=change.old_value, new_value=change.proposed_value, reason=change.reason, evidence=change.evidence)
    db.commit()
    return {"ok": True, "status": change.status, "message": "已记录接受；资料库实体的自动合并将在对应对象确认后创建新版本。"}


@router.post("/analysis/{analysis_id}/changes/{change_id}/reject")
def reject_change(analysis_id: int, change_id: int, db: Session = Depends(get_db)):
    change = db.get(AnalysisChange, change_id)
    if not change or change.analysis_id != analysis_id:
        raise HTTPException(404, "变更不存在")
    analysis = db.get(Analysis, analysis_id)
    if change.status != "pending":
        raise HTTPException(409, "该变更已经处理")
    change.status = "rejected"
    if analysis:
        write_change_log(db, book_id=analysis.book_id, source_type="analysis", source_id=analysis.id,
                         operation=str((analysis.request_context or {}).get("analysis_type") or "analysis"), action="rejected",
                         target_type=change.target_type, target_id=change.target_id, target_name=change.target_name,
                         old_value=change.old_value, new_value=change.proposed_value, reason=change.reason, evidence=change.evidence)
    db.commit()
    return {"ok": True, "status": change.status}


@router.post("/analysis/{analysis_id}/accept-all")
def accept_all(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(404, "分析记录不存在")
    for change in db.scalars(select(AnalysisChange).where(AnalysisChange.analysis_id == analysis_id, AnalysisChange.status == "pending")):
        apply_confirmed_change(change, db)
        change.status = "accepted"
        write_change_log(db, book_id=analysis.book_id, source_type="analysis", source_id=analysis.id,
                         operation=str((analysis.request_context or {}).get("analysis_type") or "analysis"), action="accepted",
                         target_type=change.target_type, target_id=change.target_id, target_name=change.target_name,
                         old_value=change.old_value, new_value=change.proposed_value, reason=change.reason, evidence=change.evidence)
    db.commit()
    return {"ok": True}


def apply_confirmed_change(change: AnalysisChange, db: Session) -> None:
    """Apply only explicitly addressable proposals; unknown targets remain audit-only."""
    value = change.proposed_value
    if not isinstance(value, dict) or change.conflict:
        return
    if change.target_type == "character":
        item = db.get(Character, change.target_id) if change.target_id else None
        if not item:
            analysis = db.get(Analysis, change.analysis_id)
            if not analysis or not value.get("name"):
                return
            item = Character(book_id=analysis.book_id, name=str(value.get("name")))
            db.add(item)
            db.flush()
        for key, proposed in value.items():
            if key in {"name", "aliases", "role", "age", "identity", "summary", "appearance", "personality", "level", "inventory", "relationships", "goals", "fears", "abilities", "secrets", "current_state", "status"}:
                setattr(item, key, str(proposed) if proposed is not None else "")
        character_version(db, item, source="extracted", reason=change.reason)
    elif change.target_type == "knowledge":
        analysis = db.get(Analysis, change.analysis_id)
        item = db.get(KnowledgeItem, change.target_id) if change.target_id else None
        if not item and analysis:
            item = KnowledgeItem(book_id=analysis.book_id, name=str(value.get("name") or change.target_name), category=str(value.get("category") or "other"), content=str(value.get("content") or ""), status=str(value.get("status") or "active"))
            db.add(item)
            db.flush()
        if item:
            if "content" in value:
                item.content = str(value["content"])
            if "status" in value and value["status"] in {"active", "deprecated", "disputed"}:
                item.status = value["status"]
            if "category" in value:
                item.category = str(value["category"])
            knowledge_version(db, item, source="extracted", reason=change.reason)
    elif change.target_type == "event":
        analysis = db.get(Analysis, change.analysis_id)
        item = db.get(Event, change.target_id) if change.target_id else None
        if not item and analysis:
            allowed = {key: value[key] for key in {"description", "event_type", "track", "status", "start_chapter", "end_chapter", "start_position", "end_position", "objective_fact", "hidden_truth", "created_from_chapter_id"} if key in value}
            item = Event(book_id=analysis.book_id, title=str(value.get("title") or change.target_name), **allowed)
            db.add(item)
        if item:
            for key, proposed in value.items():
                if key in {"title", "description", "event_type", "track", "status", "start_chapter", "end_chapter", "start_position", "end_position", "objective_fact", "hidden_truth"}:
                    setattr(item, key, proposed)
    elif change.target_type == "thread":
        analysis = db.get(Analysis, change.analysis_id)
        item = db.get(PlotThread, change.target_id) if change.target_id else None
        if not item and analysis:
            allowed = {key: value[key] for key in {"description", "thread_type", "status", "objective", "unresolved_question", "payoff", "seed_chapter", "last_chapter", "evidence"} if key in value}
            item = PlotThread(book_id=analysis.book_id, title=str(value.get("title") or change.target_name), **allowed)
            db.add(item)
        if item:
            for key, proposed in value.items():
                if key in {"title", "description", "thread_type", "status", "objective", "unresolved_question", "payoff", "seed_chapter", "last_chapter", "evidence"}:
                    setattr(item, key, proposed)


@router.get("/llm/providers", response_model=list[LLMProviderOut])
def list_providers(db: Session = Depends(get_db)):
    providers = db.scalars(
        select(LLMProvider)
        .where(LLMProvider.provider_type == "openai_compatible")
        .order_by(LLMProvider.updated_at.desc())
    ).all()
    return [_serialize_provider(item) for item in providers]


def _serialize_provider(item: LLMProvider) -> dict[str, Any]:
    """Expose only the generic provider contract, never legacy runtime fields."""
    return {
        "id": item.id,
        "provider_name": item.provider_name,
        "provider_type": item.provider_type,
        "base_url": item.base_url,
        "model": item.model,
        "temperature": item.temperature,
        "max_tokens": item.max_tokens,
        "timeout": item.timeout,
        "enabled": item.enabled,
        "has_api_key": bool(item.api_key),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.post("/llm/providers", response_model=LLMProviderOut)
def create_provider(payload: LLMProviderCreate, db: Session = Depends(get_db)):
    item = LLMProvider(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize_provider(item)


@router.put("/llm/providers/{provider_id}", response_model=LLMProviderOut)
def update_provider(provider_id: int, payload: LLMProviderCreate, db: Session = Depends(get_db)):
    item = db.get(LLMProvider, provider_id)
    if not item or item.provider_type != "openai_compatible":
        raise HTTPException(404, "LLM 配置不存在")
    values = payload.model_dump()
    if not values["api_key"] and item.api_key:
        values["api_key"] = item.api_key
    for key, value in values.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return _serialize_provider(item)


@router.post("/llm/providers/{provider_id}/test")
async def test_provider(provider_id: int, db: Session = Depends(get_db)):
    item = db.get(LLMProvider, provider_id)
    if not item or item.provider_type != "openai_compatible":
        raise HTTPException(404, "LLM 配置不存在")
    if not item.api_key:
        return {"ok": False, "message": "未填写 API Key，当前只能使用本地安全占位分析。"}
    try:
        async with __import__("httpx").AsyncClient(timeout=item.timeout) as client:
            response = await client.post(item.base_url.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {item.api_key}"}, json={"model": item.model, "messages": [{"role": "user", "content": "请只回复：连接成功"}], "max_tokens": 10})
            response.raise_for_status()
        return {"ok": True, "message": "连接成功"}
    except Exception as exc:
        if isinstance(exc, httpx.ConnectError):
            parsed = urlparse(item.base_url)
            host = parsed.hostname or item.base_url
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            return {"ok": False, "message": f"无法连接 LLM 接口：{host}:{port}。请确认 Base URL 为 {item.base_url}。"}
        if isinstance(exc, httpx.TimeoutException):
            return {"ok": False, "message": f"连接 LLM 接口超时（{item.timeout} 秒）。请检查服务状态或 Base URL。"}
        if isinstance(exc, httpx.HTTPStatusError):
            return {"ok": False, "message": f"LLM 接口返回 HTTP {exc.response.status_code}。请检查服务状态和接口地址 {item.base_url}。"}
        return {"ok": False, "message": str(exc) or exc.__class__.__name__}


@router.post("/chapters/{chapter_id}/prompt")
def generate_prompt(chapter_id: int, payload: PromptRequest, db: Session = Depends(get_db)):
    chapter = require_chapter(db, chapter_id)
    max_chapter = db.scalar(select(Chapter.chapter_number).where(Chapter.book_id == chapter.book_id).order_by(Chapter.chapter_number.desc())) or 0
    target_number = payload.target_chapter_number or chapter.chapter_number + 1
    if target_number > max_chapter + 1:
        raise HTTPException(400, f"目标章节最多只能选择第 {max_chapter + 1} 章")
    context = build_context(db, chapter, {
        "target_chapter_number": target_number,
        "include_outline": True,
        "include_previous_chapters": True,
        "include_previous_chapters_full": True,
        "include_previous_summaries": True,
        "include_characters": True,
        "include_knowledge": True,
        "include_events": True,
        "include_relations": True,
    })
    return {"prompt": compose_prompt(context, payload.extra_requirements, payload.word_count), "context": context}


@router.get("/books/{book_id}/export.json")
def export_book(book_id: int, db: Session = Depends(get_db)):
    book = require_book(db, book_id)
    data = serialize_book(book, db)
    data["chapters"] = []
    for chapter in db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)):
        versions = db.scalars(select(ChapterVersion).where(ChapterVersion.chapter_id == chapter.id).order_by(ChapterVersion.version_number)).all()
        data["chapters"].append({**serialize_chapter(chapter, db), "versions": [{**{column.name: getattr(v, column.name) for column in ChapterVersion.__table__.columns}, "content": read_content(v)} for v in versions]})
    data["characters"] = [item.__dict__ for item in db.scalars(select(Character).where(Character.book_id == book_id))]
    data["knowledge"] = [item.__dict__ for item in db.scalars(select(KnowledgeItem).where(KnowledgeItem.book_id == book_id))]
    return data


@router.get("/books/{book_id}/export.txt", response_class=PlainTextResponse)
def export_text(book_id: int, db: Session = Depends(get_db)):
    require_book(db, book_id)
    chapters = db.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number)).all()
    return "\n\n".join([f"第{item.chapter_number}章 {item.title}\n{serialize_chapter(item, db)['current_content']}" for item in chapters])
