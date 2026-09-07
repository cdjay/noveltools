import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .config import settings
from .models import (Analysis, AnalysisChange, Book, Chapter, ChapterVersion, Character, Event,
                     KnowledgeItem, LLMProvider, Outline, OutlineBlock, PlotThread, RelationState)


class LLMResponseError(ValueError):
    """A provider response error that still carries the original raw text."""

    def __init__(self, message: str, raw_response: str):
        super().__init__(message)
        self.raw_response = raw_response


def count_words(text: str) -> int:
    # 中文按字符计数，英文/数字按连续 token 计数，过滤空白。
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    western = sum(1 for token in text.split() if any(char.isascii() and char.isalnum() for char in token))
    return chinese + western


def content_root() -> Path:
    root = settings.data_dir / "books"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_content(book_id: int, chapter_id: int, version_number: int, content: str) -> str:
    path = content_root() / str(book_id) / "chapters" / str(chapter_id)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"v{version_number}.txt"
    target.write_text(content, encoding="utf-8")
    return str(target)


def read_content(version: ChapterVersion | None) -> str:
    if not version:
        return ""
    path = Path(version.content_path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def next_chapter_number(db: Session, book_id: int) -> int:
    last = db.scalar(select(Chapter.chapter_number).where(Chapter.book_id == book_id).order_by(Chapter.chapter_number.desc()))
    return (last or 0) + 1


def next_version_number(db: Session, chapter_id: int) -> int:
    last = db.scalar(select(ChapterVersion.version_number).where(ChapterVersion.chapter_id == chapter_id).order_by(ChapterVersion.version_number.desc()))
    return (last or 0) + 1


def find_analysis_providers(db: Session) -> tuple[list[LLMProvider], str]:
    """Return usable analysis providers in preference order.

    Explicitly enabled providers come first, most recently updated first. When
    there is exactly one usable
    provider and none is enabled, use it as a compatibility fallback so a newly
    added provider does not unexpectedly become a local mock analysis. Runtime-
    specific adapters live in the AI workbench; Noveltools only executes generic
    OpenAI-compatible providers with an API key. Multiple disabled providers
    remain ambiguous and are not guessed.
    """
    usable = and_(
        LLMProvider.provider_type == "openai_compatible",
        LLMProvider.api_key != "",
    )
    enabled = list(db.scalars(
        select(LLMProvider)
        .where(LLMProvider.enabled == True, usable)
        .order_by(LLMProvider.updated_at.desc(), LLMProvider.id.desc())
    ))
    if enabled:
        return enabled, "enabled"
    configured = db.scalars(
        select(LLMProvider).where(usable).order_by(LLMProvider.updated_at.desc(), LLMProvider.id.desc())
    ).all()
    if len(configured) == 1:
        return [configured[0]], "single_configured"
    return [], "none"


def find_analysis_provider(db: Session) -> tuple[LLMProvider | None, str]:
    """Select the preferred provider for single-provider analysis flows."""
    providers, selection = find_analysis_providers(db)
    return (providers[0] if providers else None), selection


def serialize_chapter(chapter: Chapter, db: Session) -> dict[str, Any]:
    version = db.get(ChapterVersion, chapter.current_version_id) if chapter.current_version_id else None
    return {**{column.name: getattr(chapter, column.name) for column in Chapter.__table__.columns}, "current_content": read_content(version)}


def serialize_book(book: Book, db: Session) -> dict[str, Any]:
    chapters = list(db.scalars(select(Chapter).where(Chapter.book_id == book.id)))
    return {**{column.name: getattr(book, column.name) for column in Book.__table__.columns},
            "chapter_count": len(chapters), "total_word_count": sum(ch.word_count for ch in chapters)}


def _chapter_summary(db: Session, chapter: Chapter) -> tuple[str, str]:
    """Return a compact, evidence-backed summary for prompt context.

    Chapter summaries are not a separate source of truth in the current data
    model. Prefer the latest completed analysis when one exists; otherwise use
    a short opening excerpt so prompt generation remains useful without making
    an LLM call or silently inventing plot details.
    """
    content = serialize_chapter(chapter, db)["current_content"]
    current_sha = content_sha256(content)
    if chapter.summary and chapter.summary_content_sha256 == current_sha:
        return chapter.summary, "固化梗概"

    latest = db.scalar(
        select(Analysis)
        .where(Analysis.chapter_id == chapter.id, Analysis.status == "completed")
        .order_by(Analysis.updated_at.desc(), Analysis.id.desc())
    )
    parts: list[str] = []
    if latest and isinstance(latest.result_json, dict):
        result = latest.result_json
        for key in ("facts", "events", "beliefs", "relation_changes"):
            for item in result.get(key, []) or []:
                if not isinstance(item, dict):
                    continue
                text = item.get("text") or item.get("description") or item.get("belief") or item.get("title")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                if len(parts) >= 3:
                    break
            if len(parts) >= 3:
                break
    if parts:
        return "；".join(parts)[:600], "分析记录"

    excerpt = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if excerpt:
        return excerpt[:280] + ("…" if len(excerpt) > 280 else ""), "正文开头摘录"
    return "暂无可用梗概", "未生成"


def _prompt_value(value: Any, fallback: str = "未记录") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, list):
        values = [_prompt_value(item, "") for item in value]
        return "、".join(item for item in values if item) or fallback
    if isinstance(value, dict):
        return "；".join(f"{key}：{_prompt_value(item, '')}" for key, item in value.items() if item not in (None, "")) or fallback
    return str(value)


def compose_prompt(context: dict[str, Any], extra_requirements: str = "", word_count: str = "") -> str:
    """Compose the copyable writing prompt from the selected target chapter."""
    book = context["book"]
    chapter = context["chapter"]
    target_number = chapter["number"]
    lines = [
        f"# 下一章写作上下文：{book['title']}",
        "",
        "## 写作目标",
        f"请在不违背已确认资料的前提下，生成第 {target_number} 章正文。",
        f"目标章节标题：{chapter['title']}",
        f"字数要求：{word_count or '由作者决定'}",
        "",
        "## 大纲简要",
    ]
    outlines = context.get("outlines", [])
    lines.extend(
        [f"- {item['title']}（{item.get('status') or '未标注'}）：{_prompt_value(item.get('description'))}" for item in outlines]
        or ["- 暂无大纲简要"]
    )

    lines.extend(["", f"## 第 {target_number} 章在大纲中的剧情"])
    outline_blocks = context.get("target_outline_blocks", [])
    if outline_blocks:
        for item in outline_blocks:
            details = [
                f"摘要：{_prompt_value(item.get('summary'))}",
                f"目标：{_prompt_value(item.get('objective'))}",
                f"冲突：{_prompt_value(item.get('conflict'))}",
                f"转折：{_prompt_value(item.get('turning_point'))}",
                f"结尾：{_prompt_value(item.get('ending'))}",
            ]
            lines.append(f"- {item['outline_title']} / {item['title']}（{'; '.join(details)}）")
    else:
        lines.append("- 大纲中暂无覆盖本章的剧情块")

    lines.extend(["", "## 前两章全文"])
    previous_full = context.get("previous_chapters_full", [])
    if previous_full:
        for item in previous_full:
            lines.extend([f"### 第 {item['number']} 章 {item['title']}", item["content"] or "（本章暂无正文）"])
    else:
        lines.append("暂无前置章节全文")

    lines.extend(["", "## 前所有章节梗概"])
    summaries = context.get("previous_chapter_summaries", [])
    if summaries:
        lines.extend([f"- 第 {item['number']} 章 {item['title']}（来源：{item['source']}）：{item['summary']}" for item in summaries])
    else:
        lines.append("暂无更早章节梗概")

    lines.extend(["", f"## 第 {target_number} 章涉及到的角色"])
    characters = context.get("involved_characters", [])
    if characters:
        for item in characters:
            identity = item.get("identity") or item.get("role") or "身份未记录"
            state = item.get("current_state") or "当前状态未记录"
            relation_note = item.get("relationships") or "角色卡未记录关系备注"
            lines.append(
                f"- {item['name']}（{identity}）：简介：{_prompt_value(item.get('summary'))}；"
                f"当前状态：{state}；资料状态：{item.get('status') or '未标注'}；"
                f"目标：{_prompt_value(item.get('goals'))}；关系备注：{relation_note}"
            )
    else:
        lines.append("- 尚未能从目标章节正文或对应大纲块中识别角色")

    lines.extend(["", f"## 第 {target_number} 章角色状态与角色之间的关系"])
    relations = context.get("relations", [])
    if relations:
        for item in relations:
            metrics = "；".join(
                f"{label}：{_prompt_value(item.get(key), '未记录')}"
                for key, label in (("liking", "好感"), ("trust", "信任"), ("fear", "恐惧"),
                                    ("dependence", "依赖"), ("alertness", "警觉"),
                                    ("hostility", "敌意"), ("cooperation", "合作"))
            )
            lines.append(
                f"- {item.get('from_name', item.get('from_character_id'))} ↔ "
                f"{item.get('to_name', item.get('to_character_id'))}：{metrics}；"
                f"当前关系说明：{_prompt_value(item.get('current_note'))}"
            )
    else:
        relationship_notes = [
            f"- {item['name']} 的角色卡关系备注：{item['relationships']}"
            for item in characters
            if item.get("relationships")
        ]
        lines.extend(relationship_notes or ["- 资料库暂无本章相关角色的结构化关系记录"])

    lines.extend([
        "",
        "## 写作限制",
        "- 不要替作者擅自揭示隐藏真相。",
        "- 不要覆盖或改写未确认的资料。",
        "- 对时间、地点、角色状态和角色认知保持可验证连续性。",
        "",
        "## 作者额外要求",
        extra_requirements or "无",
    ])
    return "\n".join(lines)


def build_context(db: Session, chapter: Chapter, request: dict[str, Any]) -> dict[str, Any]:
    book = db.get(Book, chapter.book_id)
    target_number = request.get("target_chapter_number")
    target_chapter = chapter
    if target_number is not None:
        target_chapter = db.scalar(select(Chapter).where(Chapter.book_id == book.id, Chapter.chapter_number == target_number))
    context: dict[str, Any] = {
        "analysis_type": request.get("analysis_type", "characters"),
        "book": {"id": book.id, "title": book.title, "author": book.author, "genre": book.genre, "description": book.description},
        "chapter": {
            "id": target_chapter.id if target_chapter else None,
            "number": target_number if target_number is not None else chapter.chapter_number,
            "title": target_chapter.title if target_chapter else f"第{target_number}章（待创作）",
            "content": serialize_chapter(target_chapter, db)["current_content"] if target_chapter else "",
        },
    }
    if request.get("include_outline"):
        outlines = list(db.scalars(select(Outline).where(Outline.book_id == book.id).order_by(Outline.updated_at.desc(), Outline.id)))
        context["outlines"] = [{"id": item.id, "title": item.title, "description": item.description, "status": item.status, "version": item.version} for item in outlines]
        outline_ids = [item.id for item in outlines]
        blocks = list(db.scalars(select(OutlineBlock).where(OutlineBlock.outline_id.in_(outline_ids)).order_by(OutlineBlock.order_index, OutlineBlock.id))) if outline_ids else []
        outline_titles = {item.id: item.title for item in outlines}
        context["outline_blocks"] = [{"id": item.id, "outline_id": item.outline_id, "outline_title": outline_titles[item.outline_id],
                                      "title": item.title, "summary": item.summary, "objective": item.objective,
                                      "conflict": item.conflict, "turning_point": item.turning_point, "ending": item.ending,
                                      "start_chapter": item.start_chapter, "end_chapter": item.end_chapter}
                                     for item in blocks]
        context["target_outline_blocks"] = [item for item in context["outline_blocks"]
                                             if (item["start_chapter"] is None or item["start_chapter"] <= context["chapter"]["number"])
                                             and (item["end_chapter"] is None or item["end_chapter"] >= context["chapter"]["number"])]
    if request.get("include_characters"):
        all_characters = list(db.scalars(select(Character).where(Character.book_id == book.id).order_by(Character.name)))
        context["characters"] = [{"id": c.id, "name": c.name, "aliases": c.aliases, "role": c.role, "age": c.age, "identity": c.identity,
                                   "summary": c.summary, "appearance": c.appearance, "personality": c.personality, "level": c.level,
                                   "inventory": c.inventory, "relationships": c.relationships, "goals": c.goals, "fears": c.fears,
                                   "abilities": c.abilities, "secrets": c.secrets, "current_state": c.current_state, "status": c.status}
                                  for c in all_characters]
        target_text = context["chapter"]["content"]
        target_text += " " + " ".join(
            str(item.get(key) or "")
            for item in context.get("target_outline_blocks", [])
            for key in ("title", "summary", "objective", "conflict", "turning_point", "ending")
        )
        involved_ids: set[int] = set()
        for character in all_characters:
            names = [character.name] + [alias.strip() for alias in (character.aliases or "").replace("，", ",").split(",") if alias.strip()]
            if any(name and name in target_text for name in names):
                involved_ids.add(character.id)

        target_id = context["chapter"]["id"]
        if target_id:
            latest_character_analysis = db.scalar(
                select(Analysis)
                .where(Analysis.chapter_id == target_id, Analysis.status == "completed")
                .order_by(Analysis.updated_at.desc(), Analysis.id.desc())
            )
            if latest_character_analysis and isinstance(latest_character_analysis.result_json, dict):
                analyzed_names = [item.get("name") for item in latest_character_analysis.result_json.get("characters", []) or [] if isinstance(item, dict)]
                for character in all_characters:
                    if character.name in analyzed_names:
                        involved_ids.add(character.id)
        context["involved_characters"] = [item for item in context["characters"] if item["id"] in involved_ids]
    if request.get("include_knowledge"):
        context["knowledge"] = [{"category": k.category, "name": k.name, "content": k.content, "status": k.status}
                                 for k in db.scalars(select(KnowledgeItem).where(KnowledgeItem.book_id == book.id, KnowledgeItem.status == "active"))]
    if request.get("include_events"):
        context["events"] = [{"title": e.title, "status": e.status, "track": e.track, "start_chapter": e.start_chapter, "end_chapter": e.end_chapter, "description": e.description}
                              for e in db.scalars(select(Event).where(Event.book_id == book.id))]
    if request.get("include_relations"):
        character_names = {item["id"]: item["name"] for item in context.get("involved_characters", [])}
        context["relations"] = [{"from_character_id": item.from_character_id, "to_character_id": item.to_character_id,
                                  "from_name": character_names.get(item.from_character_id, str(item.from_character_id)),
                                  "to_name": character_names.get(item.to_character_id, str(item.to_character_id)),
                                  "liking": item.liking, "trust": item.trust, "fear": item.fear,
                                  "dependence": item.dependence, "alertness": item.alertness,
                                  "hostility": item.hostility, "cooperation": item.cooperation,
                                  "current_note": item.current_note}
                                 for item in db.scalars(select(RelationState).where(RelationState.book_id == book.id))
                                 if character_names and item.from_character_id in character_names and item.to_character_id in character_names]
    if request.get("include_threads") or request.get("include_events"):
        context["threads"] = [{"id": item.id, "title": item.title, "description": item.description,
                                "thread_type": item.thread_type, "status": item.status,
                                "objective": item.objective, "unresolved_question": item.unresolved_question,
                                "payoff": item.payoff, "seed_chapter": item.seed_chapter,
                                "last_chapter": item.last_chapter, "evidence": item.evidence}
                               for item in db.scalars(select(PlotThread).where(PlotThread.book_id == book.id))]
    if request.get("include_previous_chapters"):
        previous = list(db.scalars(select(Chapter).where(Chapter.book_id == book.id, Chapter.chapter_number < context["chapter"]["number"]).order_by(Chapter.chapter_number)))
        recent = previous[-2:]
        context["previous_chapters"] = [{"number": item.chapter_number, "title": item.title, "content": serialize_chapter(item, db)["current_content"][-3000:]} for item in reversed(recent)]
        if request.get("include_previous_chapters_full"):
            context["previous_chapters_full"] = [{"number": item.chapter_number, "title": item.title, "content": serialize_chapter(item, db)["current_content"]} for item in recent]
        if request.get("include_previous_summaries") or request.get("include_summaries"):
            summaries = []
            for item in previous[:-2]:
                summary, source = _chapter_summary(db, item)
                summaries.append({"number": item.chapter_number, "title": item.title, "summary": summary, "source": source})
            context["previous_chapter_summaries"] = summaries
    return context


def mock_analysis(context: dict[str, Any]) -> dict[str, Any]:
    chapter = context["chapter"]
    content = chapter["content"]
    first_sentence = next((line.strip() for line in content.splitlines() if line.strip()), "本章暂无足够文本")
    return {
        "analysis_type": context.get("analysis_type", "characters"),
        "characters": [],
        "facts": [{"type": "fact", "text": first_sentence[:120], "fact_status": "confirmed", "chapter": chapter["number"], "position": "paragraph_1", "evidence": first_sentence[:240], "confidence": 0.72}] if content.strip() else [],
        "events": [{"title": f"第{chapter['number']}章主要行动", "event_type": "action", "participants": [], "location": "", "start_position": "paragraph_1", "end_position": "unknown", "status": "ongoing", "causal_relations": [], "chapter": chapter["number"], "evidence": first_sentence[:240], "confidence": 0.55}] if content.strip() else [],
        "beliefs": [],
        "relation_changes": [],
        "issues": [],
        "foreshadowing_candidates": [],
        "change_candidates": [],
        "notice": "当前未启用可用的 LLM 配置，本次使用本地保守分析占位结果；你可以在 LLM 设置中配置 OpenAI Compatible 接口后重新分析。"
    }


def mock_graphify_init(context: dict[str, Any]) -> dict[str, Any]:
    premise = str(context.get("premise") or "").strip()
    return {
        "analysis_type": "graphify_init",
        "characters": [],
        "knowledge": [{"category": "worldview", "name": "故事前提", "content": premise, "evidence": premise, "confidence": 1.0}] if premise else [],
        "threads": [],
        "events": [],
        "notice": "当前未启用可用的 LLM，本次只把作者输入的故事前提作为待确认知识项提案，不会自动写入资料库。",
    }


async def call_provider(provider: LLMProvider, context: dict[str, Any]) -> tuple[dict[str, Any], str, int]:
    analysis_type = context.get("analysis_type", "characters")
    issue_schema = {"type": "object", "properties": {"severity": {"type": "string", "enum": ["error", "warning", "info"]}, "category": {"type": "string"}, "message": {"type": "string"}, "current_text": {"type": "string"}, "reference": {"type": "string"}, "evidence": {"type": "string"}, "suggestion": {"type": "string"}, "confidence": {"type": "number"}}}
    if analysis_type == "characters":
        system_prompt = (
            "你是中文小说角色资料分析器。本次任务只分析当前章节中实际出现或被明确提及的角色。"
            "必须以角色姓名为单位输出结构化 JSON，不要输出事实、事件、伏笔或泛泛的剧情总结。"
            "只记录正文明确提供的信息；没有证据的字段必须留空，不能猜测。每个角色至少提供 evidence，"
            "没有明确姓名的路人不要单独创建角色。不要输出空角色对象。"
        )
        schema = {"type": "object", "required": ["analysis_type", "characters"], "properties": {
            "analysis_type": {"type": "string", "enum": ["characters"]},
            "characters": {"type": "array", "items": {"type": "object", "required": ["name", "evidence"], "properties": {
                "name": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}},
                "role": {"type": "string"}, "age": {"type": "string"}, "identity": {"type": "string"},
                "summary": {"type": "string"}, "appearance": {"type": "string"}, "personality": {"type": "string"},
                "level": {"type": "string"}, "inventory": {"type": "array", "items": {"type": "string"}},
                "relationships": {"type": "array", "items": {"type": "object", "properties": {
                    "character": {"type": "string"}, "relation": {"type": "string"}, "change": {"type": "string"},
                    "evidence": {"type": "string"}, "confidence": {"type": "number"}}}},
                "goals": {"type": "string"}, "fears": {"type": "string"}, "abilities": {"type": "string"},
                "secrets": {"type": "string"}, "current_state": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "object", "properties": {
                    "text": {"type": "string"}, "position": {"type": "string"}}}}, "confidence": {"type": "number"}
            }}}
        }}
    elif analysis_type == "chapter_summary":
        system_prompt = (
            "你是中文小说章节梗概整理器。请只根据章节正文，生成可长期保存、格式固定的章节梗概。"
            "不得添加正文没有明确给出的原因、真相或后续预测。只返回 JSON，不要 Markdown 代码围栏。"
            "summary 字段必须严格按以下六个栏目输出，栏目名称和顺序不能改变：\n"
            "【本章概述】用 2-4 句概括本章发生了什么。\n"
            "【关键人物】列出本章实际出现且有作用的人物及其本章状态；没有则写“无”。\n"
            "【关键事件】按发生顺序列出重要行动、冲突、发现或决定；没有则写“无”。\n"
            "【状态变化】记录人物、物品、地点、关系或事件状态的明确变化；没有则写“无”。\n"
            "【结尾状态】说明本章结束时人物处境、事件进度和地点；正文未说明的写“未说明”。\n"
            "【未决事项】只列正文明确留下但尚未解决的问题、伏笔候选或进行中事件；没有则写“无”。"
        )
        schema = {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}
    elif analysis_type == "graphify_init":
        system_prompt = (
            "你是中文小说资料库初始化器。请根据故事前提或已有章节，提出角色、世界观知识、情节线和事件的初始资料。"
            "characters 必须列出当前章节实际出现或被明确提及的所有有姓名角色，包括资料库中已经存在的角色；"
            "不能因为角色已存在而省略，只需在字段中记录本章有正文证据的新状态。没有明确姓名的群体或路人不要创建角色。"
            "所有内容都只是待作者确认的提案；只能使用输入中的证据，无法确定的字段留空，绝不补全隐藏真相。"
        )
        schema = {"type": "object", "required": ["analysis_type", "characters", "knowledge", "threads", "events"], "properties": {
            "analysis_type": {"type": "string", "enum": ["graphify_init"]},
            "characters": {"type": "array", "items": {"type": "object", "required": ["name", "evidence"], "properties": {
                "name": {"type": "string"}, "aliases": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"}, "appearance": {"type": "string"}, "personality": {"type": "string"},
                "level": {"type": "string"}, "identity": {"type": "string"}, "goals": {"type": "string"},
                "abilities": {"type": "string"}, "current_state": {"type": "string"},
                "evidence": {"type": "string"}, "confidence": {"type": "number"}}}},
            "knowledge": {"type": "array", "items": {"type": "object", "required": ["name", "content", "evidence"], "properties": {
                "category": {"type": "string"}, "name": {"type": "string"}, "content": {"type": "string"},
                "evidence": {"type": "string"}, "confidence": {"type": "number"}}}},
            "threads": {"type": "array", "items": {"type": "object", "required": ["title", "evidence"], "properties": {
                "title": {"type": "string"}, "description": {"type": "string"}, "thread_type": {"type": "string"},
                "objective": {"type": "string"}, "unresolved_question": {"type": "string"}, "payoff": {"type": "string"},
                "seed_chapter": {"type": ["integer", "null"]}, "evidence": {"type": "string"}, "confidence": {"type": "number"}}}},
            "events": {"type": "array", "items": {"type": "object", "required": ["title", "evidence"], "properties": {
                "title": {"type": "string"}, "description": {"type": "string"}, "event_type": {"type": "string"},
                "track": {"type": "string"}, "status": {"type": "string"}, "start_chapter": {"type": ["integer", "null"]},
                "end_chapter": {"type": ["integer", "null"]}, "evidence": {"type": "string"}, "confidence": {"type": "number"}}}}
        }}
    else:
        system_prompt = (
            "你是中文小说资料库审阅器。检查当前章节与已确认资料、章节历史和情节线的关系。"
            "只输出有正文或资料证据的问题、事实、事件、角色认知、关系变化、伏笔和变更提案。"
            "当前章节正文非空时，必须尽量提取至少一条事实、事件、角色认知或关系变化；没有硬伤不代表所有数组都必须为空。"
            "不得复述输入中的 schema、context 或请求内容，必须返回实际分析结果。"
            "审阅结果只是建议，不能假设作者意图，也不能未经确认覆盖资料库。"
        )
        schema = {"type": "object", "properties": {"facts": {"type": "array"}, "events": {"type": "array"}, "beliefs": {"type": "array"}, "relation_changes": {"type": "array"}, "issues": {"type": "array", "items": issue_schema}, "foreshadowing_candidates": {"type": "array"}, "change_candidates": {"type": "array"}}}
    if analysis_type in {"characters", "graphify_init"}:
        user_prompt = json.dumps({"schema": schema, "context": context}, ensure_ascii=False)
    elif analysis_type == "chapter_summary":
        user_prompt = (
            "请根据下面的章节正文生成固定格式章节梗概，只返回一个合法 JSON 对象，且只能包含 summary 字段。"
            "不要复述 schema、context，不要输出 Markdown 或解释文字。\n\n"
            + json.dumps({"chapter": context.get("chapter"), "book": context.get("book")}, ensure_ascii=False)
        )
    else:
        user_prompt = (
            "请分析下面的中文小说章节，只返回一个合法 JSON 对象。"
            "对象必须包含 facts、events、beliefs、relation_changes、issues、foreshadowing_candidates、change_candidates 七个数组；"
            "没有证据的单项可以为空，但正文非空时至少返回一条有正文证据的事实、事件、认知或关系变化。"
            "不要输出 Markdown、schema、context 或解释文字。\n\n"
            + json.dumps({"chapter": context.get("chapter"), "book": context.get("book"), "reference_material": context}, ensure_ascii=False)
        )
    url = provider.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    payload: dict[str, Any] = {
        "model": provider.model,
        # Analysis responses are structured records, not novel prose. A very
        # large provider setting can keep Graphify generating until the request
        # times out, so cap this analysis-only path at a generous JSON budget.
        "max_tokens": min(provider.max_tokens, 8192),
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "temperature": provider.temperature,
        "response_format": {"type": "json_object"},
    }
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=provider.timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
    elapsed = int((time.perf_counter() - started) * 1000)
    data = response.json()
    raw_value = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    raw = raw_value if isinstance(raw_value, str) else json.dumps(raw_value, ensure_ascii=False)
    return parse_json_response(raw), raw, elapsed


def parse_json_response(raw: str) -> dict[str, Any]:
    """Parse strict JSON and tolerate a fenced or object-only LLM reply."""
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.removeprefix("```").removeprefix("json").strip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        parsed = None
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
                break
            except json.JSONDecodeError:
                continue
        if parsed is None:
            for index, char in enumerate(candidate):
                if char != "{":
                    continue
                parsed = _parse_relaxed_summary_object(candidate[index:])
                if parsed is not None:
                    break
        if parsed is None:
            raise LLMResponseError("LLM 返回不是有效 JSON；原始响应已保存", raw)
    if not isinstance(parsed, dict):
        raise LLMResponseError("LLM 返回的 JSON 根节点不是对象；原始响应已保存", raw)
    return parsed


def _parse_relaxed_summary_object(candidate: str) -> dict[str, Any] | None:
    """Repair a known summary shape with section strings missing colons.

    Some chapter-summary responses look like
    ``{"summary": {"【本章概述】内容", "【关键人物】内容"}}``.
    This is not JSON, but it is unambiguous for the fixed six-section format.
    Keep this repair narrowly scoped so malformed analysis objects remain errors.
    """
    match = re.fullmatch(r'\{\s*"summary"\s*:\s*\{(?P<body>.*)\}\s*\}', candidate, re.DOTALL)
    if not match:
        return None

    decoder = json.JSONDecoder()
    body = match.group("body")
    fragments: list[str] = []
    index = 0
    while index < len(body):
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body):
            break
        if body[index] == ",":
            index += 1
            continue
        if body[index] != '"':
            return None
        try:
            fragment, end = decoder.raw_decode(body, index)
        except json.JSONDecodeError:
            return None
        if not isinstance(fragment, str):
            return None
        fragments.append(fragment)
        index = end

    labels = (
        "【本章概述】", "【关键人物】", "【关键事件】",
        "【状态变化】", "【结尾状态】", "【未决事项】",
    )
    sections: dict[str, str] = {}
    for fragment in fragments:
        label = next((item for item in labels if fragment.startswith(item)), None)
        if label is None:
            return None
        sections[label] = fragment[len(label):].strip()
    return {"summary": sections} if sections else None


def validate_analysis_result(result: Any, analysis_type: str) -> dict[str, Any]:
    """Reject provider echoes and malformed-but-valid JSON before displaying success."""
    if not isinstance(result, dict):
        raise ValueError("LLM 返回的根节点必须是对象；原始响应已保存")
    if analysis_type == "chapter_summary":
        summary = normalize_chapter_summary(result)
        if not summary:
            raise ValueError("LLM 返回的章节梗概为空或缺少 summary 字段")
        result["summary"] = summary
        return result
    if analysis_type == "graphify_init":
        required = ("characters", "knowledge", "threads", "events")
    else:
        required = ("characters",) if analysis_type == "characters" else (
        "facts", "events", "beliefs", "relation_changes", "issues", "foreshadowing_candidates", "change_candidates"
        )
    missing = [key for key in required if key not in result]
    if missing:
        raise ValueError(f"LLM 返回缺少分析字段：{', '.join(missing)}；原始响应已保存")
    invalid = [key for key in required if not isinstance(result.get(key), list)]
    if invalid:
        raise ValueError(f"LLM 分析字段必须是数组：{', '.join(invalid)}；原始响应已保存")
    return result


SUMMARY_SECTIONS = (
    ("【本章概述】", "未说明"),
    ("【关键人物】", "无"),
    ("【关键事件】", "无"),
    ("【状态变化】", "无"),
    ("【结尾状态】", "未说明"),
    ("【未决事项】", "无"),
)


def _summary_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(item for item in (_summary_value(entry) for entry in value) if item)
    if isinstance(value, dict):
        return "；".join(f"{key}：{text}" for key, item in value.items() if (text := _summary_value(item)))
    return str(value).strip()


def normalize_chapter_summary(result: dict[str, Any]) -> str:
    """Normalize both string and section-object replies into the fixed summary format."""
    raw = result.get("summary")
    if isinstance(raw, dict):
        sections = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ""
        if any(label in text for label, _ in SUMMARY_SECTIONS):
            return text
        sections = {"【本章概述】": text}
    else:
        sections = {label: result.get(label) for label, _ in SUMMARY_SECTIONS if label in result}
        if not any(_summary_value(value) for value in sections.values()):
            return ""
    if not any(_summary_value(value) for value in sections.values()):
        return ""
    return "\n".join(f"{label}{_summary_value(sections.get(label)) or fallback}" for label, fallback in SUMMARY_SECTIONS)


CHARACTER_ANALYSIS_FIELDS = (
    ("aliases", "别名"),
    ("role", "角色定位"),
    ("age", "年龄"),
    ("identity", "身份"),
    ("summary", "简介"),
    ("appearance", "外貌"),
    ("personality", "性格"),
    ("level", "等级 / 境界"),
    ("inventory", "背包 / 持有物"),
    ("relationships", "人物关系"),
    ("goals", "目标"),
    ("fears", "恐惧"),
    ("abilities", "能力"),
    ("secrets", "秘密"),
    ("current_state", "当前状态"),
    ("status", "状态"),
)


def _json_text(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _character_aliases(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").replace("，", ",").replace("、", ",").split(",") if part.strip()]


def _character_evidence(item: dict[str, Any]) -> str:
    value = item.get("evidence", "")
    if isinstance(value, list):
        pieces = []
        for entry in value:
            if isinstance(entry, dict):
                pieces.append(str(entry.get("text") or entry.get("quote") or "").strip())
            elif entry:
                pieces.append(str(entry).strip())
        return "；".join(piece for piece in pieces if piece)
    return str(value or "").strip()


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _optional_int(value: Any) -> int | None:
    """Accept only real integer identifiers from model-generated candidates."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _find_character(characters: list[Character], name: str, aliases: list[str]) -> Character | None:
    wanted = {name.strip().casefold(), *(alias.casefold() for alias in aliases)}
    for item in characters:
        known = {item.name.strip().casefold(), *(alias.casefold() for alias in _character_aliases(item.aliases))}
        if wanted & known:
            return item
    return None


def _merged_character_aliases(matched: Character, incoming_name: str, incoming_aliases: list[str]) -> list[str]:
    """Merge aliases without erasing confirmed names from earlier chapters."""
    candidates = [*_character_aliases(matched.aliases), *incoming_aliases]
    if incoming_name.strip().casefold() != matched.name.strip().casefold():
        candidates.append(incoming_name.strip())
    merged: list[str] = []
    seen = {matched.name.strip().casefold()}
    for alias in candidates:
        normalized = alias.strip()
        folded = normalized.casefold()
        if normalized and folded not in seen:
            seen.add(folded)
            merged.append(normalized)
    return merged


def prepare_character_analysis(db: Session, analysis: Analysis, result: dict[str, Any]) -> dict[str, Any]:
    """Normalize character extraction and create review-only character changes."""
    raw_items = result.get("characters", []) if isinstance(result, dict) else []
    existing = list(db.scalars(select(Character).where(Character.book_id == analysis.book_id)))
    normalized: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    source_keys: dict[str, tuple[str, ...]] = {
        "aliases": ("aliases", "alias"), "role": ("role", "character_role"), "age": ("age",),
        "identity": ("identity",), "summary": ("summary", "introduction", "description", "profile"),
        "appearance": ("appearance", "looks", "外貌"), "personality": ("personality",),
        "level": ("level", "realm", "cultivation"), "inventory": ("inventory", "backpack", "items"),
        "relationships": ("relationships", "relations"), "goals": ("goals",), "fears": ("fears",),
        "abilities": ("abilities",), "secrets": ("secrets",), "current_state": ("current_state", "state"),
        "status": ("status",),
    }
    for raw_item in raw_items if isinstance(raw_items, list) else []:
        item = {"name": raw_item} if isinstance(raw_item, str) else raw_item if isinstance(raw_item, dict) else {}
        name = str(_first_value(item, "name", "character", "人物")).strip()
        if not name:
            continue
        aliases = _character_aliases(_first_value(item, "aliases", "alias"))
        matched = _find_character(existing, name, aliases)
        if matched:
            aliases = _merged_character_aliases(matched, name, aliases)
        values = {field: _json_text(_first_value(item, *keys)) for field, keys in source_keys.items()}
        values["aliases"] = ", ".join(aliases)
        evidence = _character_evidence(item)
        confidence_value = item.get("confidence", 0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_value)))
        except (TypeError, ValueError):
            confidence = 0.0
        canonical = {"name": name, **values, "evidence": item.get("evidence", []), "confidence": confidence}
        if not matched:
            canonical.update({"match_status": "new", "matched_character_id": None, "existing": {}})
            proposed = {"name": name, **{key: value for key, value in values.items() if value}}
            proposed.setdefault("status", "active")
            candidates.append({
                "change_type": "character_add", "target_type": "character", "target_id": None, "target_name": name,
                "old_value": {}, "proposed_value": proposed,
                "reason": "本章明确出现了角色库中不存在的角色，建议新增角色卡。",
                "evidence": evidence, "source_chapter_id": analysis.chapter_id, "confidence": confidence, "conflict": False,
            })
            canonical["proposed_fields"] = proposed
            canonical["field_changes"] = [{"field": key, "label": dict(CHARACTER_ANALYSIS_FIELDS).get(key, key), "old_value": "", "new_value": value} for key, value in proposed.items() if key != "name"]
        else:
            old = {field: str(getattr(matched, field) or "") for field, _ in CHARACTER_ANALYSIS_FIELDS}
            changed = {field: value for field, value in values.items() if value and value != old.get(field, "")}
            canonical.update({"match_status": "existing", "matched_character_id": matched.id, "existing": old, "proposed_fields": changed})
            canonical["field_changes"] = [{"field": key, "label": dict(CHARACTER_ANALYSIS_FIELDS).get(key, key), "old_value": old.get(key, ""), "new_value": value} for key, value in changed.items()]
            if changed:
                candidates.append({
                    "change_type": "character_update", "target_type": "character", "target_id": matched.id, "target_name": matched.name,
                    "old_value": {key: old.get(key, "") for key in changed}, "proposed_value": changed,
                    "reason": "本章出现了与现有角色卡不同的新信息，建议作者确认是否更新。",
                    "evidence": evidence, "source_chapter_id": analysis.chapter_id, "confidence": confidence, "conflict": False,
                })
        normalized.append(canonical)

    result["analysis_type"] = "characters"
    result["characters"] = normalized
    result["character_changes"] = candidates
    result["change_candidates"] = candidates
    if not normalized:
        result["notice"] = "本章没有识别到带姓名且有明确证据的角色。"
    return result


def prepare_graphify_init(db: Session, book_id: int, result: dict[str, Any], source_chapter_id: int | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Turn init/update output into review-only proposals for all bible domains."""
    result = result if isinstance(result, dict) else {}
    proposals: list[dict[str, Any]] = []
    characters = list(result.get("characters") or []) if isinstance(result.get("characters"), list) else []
    knowledge = list(result.get("knowledge") or []) if isinstance(result.get("knowledge"), list) else []
    threads = list(result.get("threads") or []) if isinstance(result.get("threads"), list) else []
    events = list(result.get("events") or []) if isinstance(result.get("events"), list) else []
    existing_characters = list(db.scalars(select(Character).where(Character.book_id == book_id)))
    existing_knowledge = list(db.scalars(select(KnowledgeItem).where(KnowledgeItem.book_id == book_id)))
    existing_threads = list(db.scalars(select(PlotThread).where(PlotThread.book_id == book_id)))
    existing_events = list(db.scalars(select(Event).where(Event.book_id == book_id)))

    character_keys = {key for key, _ in CHARACTER_ANALYSIS_FIELDS}
    for raw in characters:
        item = raw if isinstance(raw, dict) else {"name": str(raw)}
        name = str(_first_value(item, "name", "character", "人物")).strip()
        if not name:
            continue
        aliases = _character_aliases(_first_value(item, "aliases", "alias"))
        matched = _find_character(existing_characters, name, aliases)
        if matched:
            aliases = _merged_character_aliases(matched, name, aliases)
        proposed = {key: _json_text(item.get(key)) for key in character_keys if item.get(key) not in (None, "", [], {})}
        proposed["name"] = name
        proposed["aliases"] = ", ".join(aliases)
        evidence = _character_evidence(item)
        confidence = _confidence(item.get("confidence"))
        if matched:
            old = {key: str(getattr(matched, key) or "") for key in character_keys}
            changed = {key: value for key, value in proposed.items() if key != "name" and value and value != old.get(key, "")}
            if changed:
                proposals.append({"change_type": "character_update", "target_type": "character", "target_id": matched.id,
                                  "target_name": matched.name, "old_value": {key: old[key] for key in changed},
                                  "proposed_value": changed, "reason": "初始化发现角色卡存在新的明确资料，等待作者确认。",
                                  "evidence": evidence, "source_chapter_id": source_chapter_id, "confidence": confidence, "conflict": False})
        else:
            proposals.append({"change_type": "character_add", "target_type": "character", "target_id": None,
                              "target_name": name, "old_value": {}, "proposed_value": proposed,
                              "reason": "初始化发现角色库中不存在的角色，等待作者确认新增。", "evidence": evidence,
                              "source_chapter_id": source_chapter_id, "confidence": confidence, "conflict": False})

    for raw in knowledge:
        item = raw if isinstance(raw, dict) else {"name": str(raw), "content": str(raw)}
        name = str(_first_value(item, "name", "title")).strip()
        content = _json_text(_first_value(item, "content", "description", "text"))
        if not name or not content:
            continue
        matched = next((entry for entry in existing_knowledge if entry.name.casefold() == name.casefold()), None)
        proposed = {"category": str(item.get("category") or "other"), "name": name, "content": content, "status": "active"}
        evidence = str(item.get("evidence") or "").strip()
        confidence = _confidence(item.get("confidence"))
        if matched:
            if content != matched.content:
                proposals.append({"change_type": "knowledge_update", "target_type": "knowledge", "target_id": matched.id,
                                  "target_name": matched.name, "old_value": {"content": matched.content},
                                  "proposed_value": {"content": content}, "reason": "初始化发现知识项内容存在新的明确表述，等待作者确认。",
                                  "evidence": evidence, "source_chapter_id": source_chapter_id, "confidence": confidence, "conflict": False})
        else:
            proposals.append({"change_type": "knowledge_add", "target_type": "knowledge", "target_id": None,
                              "target_name": name, "old_value": {}, "proposed_value": proposed,
                              "reason": "初始化发现新的世界观或知识项，等待作者确认新增。", "evidence": evidence,
                              "source_chapter_id": source_chapter_id, "confidence": confidence, "conflict": False})

    for raw in threads:
        item = raw if isinstance(raw, dict) else {"title": str(raw)}
        title = str(_first_value(item, "title", "name")).strip()
        if not title:
            continue
        proposed = {key: item.get(key) for key in ("title", "description", "thread_type", "status", "objective", "unresolved_question", "payoff", "seed_chapter", "last_chapter", "evidence") if item.get(key) not in (None, "", [], {})}
        proposed.setdefault("status", "open")
        proposed["title"] = title
        matched = next((entry for entry in existing_threads if entry.title.casefold() == title.casefold()), None)
        evidence = str(item.get("evidence") or "").strip()
        confidence = _confidence(item.get("confidence"))
        if matched:
            changed = {key: value for key, value in proposed.items() if key != "title" and value not in (None, "") and str(value) != str(getattr(matched, key, "") or "")}
            if changed:
                proposals.append({"change_type": "thread_update", "target_type": "thread", "target_id": matched.id, "target_name": matched.title,
                                  "old_value": {key: getattr(matched, key, "") for key in changed}, "proposed_value": changed,
                                  "reason": "初始化发现情节线存在新的明确信息，等待作者确认。", "evidence": evidence,
                                  "source_chapter_id": source_chapter_id, "confidence": confidence, "conflict": False})
        else:
            proposals.append({"change_type": "thread_add", "target_type": "thread", "target_id": None, "target_name": title,
                              "old_value": {}, "proposed_value": proposed, "reason": "初始化发现新的情节线，等待作者确认新增。",
                              "evidence": evidence, "source_chapter_id": source_chapter_id, "confidence": confidence, "conflict": False})

    for raw in events:
        item = raw if isinstance(raw, dict) else {"title": str(raw)}
        title = str(_first_value(item, "title", "name")).strip()
        if not title:
            continue
        proposed = {key: item.get(key) for key in ("title", "description", "event_type", "track", "status", "start_chapter", "end_chapter", "start_position", "end_position", "objective_fact", "hidden_truth") if item.get(key) not in (None, "", [], {})}
        proposed["title"] = title
        matched = next((entry for entry in existing_events if entry.title.casefold() == title.casefold()), None)
        evidence = str(item.get("evidence") or "").strip()
        confidence = _confidence(item.get("confidence"))
        if matched:
            changed = {key: value for key, value in proposed.items() if key != "title" and value not in (None, "") and str(value) != str(getattr(matched, key, "") or "")}
            if changed:
                proposals.append({"change_type": "event_update", "target_type": "event", "target_id": matched.id, "target_name": matched.title,
                                  "old_value": {key: getattr(matched, key, "") for key in changed}, "proposed_value": changed,
                                  "reason": "初始化发现事件状态或描述存在新的明确信息，等待作者确认。", "evidence": evidence,
                                  "source_chapter_id": source_chapter_id, "confidence": confidence, "conflict": False})
        else:
            proposed["created_from_chapter_id"] = source_chapter_id
            proposals.append({"change_type": "event_add", "target_type": "event", "target_id": None, "target_name": title,
                              "old_value": {}, "proposed_value": proposed, "reason": "初始化发现新的事件，等待作者确认加入事件轴。",
                              "evidence": evidence, "source_chapter_id": source_chapter_id, "confidence": confidence, "conflict": False})

    result["analysis_type"] = "graphify_init"
    result["proposals"] = proposals
    return result, proposals


def make_analysis_changes(db: Session, analysis: Analysis) -> int:
    result = analysis.result_json or {}
    candidates = result.get("change_candidates", []) if isinstance(result, dict) else []
    skipped = 0
    normalized_candidates = list(candidates or []) if isinstance(candidates, list) else []

    # Some compatible models return well-formed event observations in the
    # `events` array but omit a separate `change_candidates` entry. Treat an
    # explicitly titled event as an addressable graph update so automatic
    # ingestion can keep the event axis in sync with chapter reviews. Untitled
    # observations remain in the raw analysis only; we must not invent names.
    existing_events = list(db.scalars(select(Event).where(Event.book_id == analysis.book_id)))
    source_chapter = db.get(Chapter, analysis.chapter_id)
    source_chapter_number = source_chapter.chapter_number if source_chapter else None
    status_map = {"已完成": "completed", "进行中": "ongoing", "未解决": "unresolved", "计划": "planned",
                  "已计划": "planned", "中断": "interrupted", "失败": "failed", "已揭示": "revealed", "作废": "invalidated"}
    track_map = {"主线": "mainline", "支线": "sideline", "伏笔": "foreshadow", "人物线": "character", "世界线": "world"}
    event_type_map = {"行动": "action", "冲突": "conflict", "冲突事件": "conflict", "发现": "discovery",
                      "决定": "decision", "揭示": "reveal", "关系": "relationship", "状态变化": "state_change"}
    existing_signatures = set()
    generated_signatures = set()
    for item in db.scalars(select(AnalysisChange).where(AnalysisChange.analysis_id == analysis.id)):
        existing_signatures.add((item.target_type, item.target_name, json.dumps(item.proposed_value, ensure_ascii=False, sort_keys=True, default=str)))
    for raw_event in (result.get("events", []) if isinstance(result, dict) else []):
        if not isinstance(raw_event, dict):
            continue
        title = str(raw_event.get("title") or raw_event.get("name") or "").strip()
        if not title:
            continue
        matched = next((item for item in existing_events if item.title.casefold() == title.casefold()), None)
        proposed: dict[str, Any] = {
            "title": title,
            "description": str(raw_event.get("description") or raw_event.get("text") or ""),
            "event_type": event_type_map.get(str(raw_event.get("event_type") or ""), raw_event.get("event_type") or "other"),
            "track": track_map.get(str(raw_event.get("track") or ""), raw_event.get("track") or "mainline"),
            "status": status_map.get(str(raw_event.get("status") or ""), raw_event.get("status") or "unresolved"),
            "start_chapter": raw_event.get("start_chapter") if raw_event.get("start_chapter") is not None else source_chapter_number,
            "end_chapter": raw_event.get("end_chapter"),
            "start_position": str(raw_event.get("start_position") or ""),
            "end_position": str(raw_event.get("end_position") or ""),
            "created_from_chapter_id": analysis.chapter_id,
        }
        proposed = {key: value for key, value in proposed.items() if value not in (None, "") or key in {"title", "start_chapter", "created_from_chapter_id"}}
        if matched:
            changed = {key: value for key, value in proposed.items() if key != "title" and value not in (None, "") and str(value) != str(getattr(matched, key, "") or "")}
            if not changed:
                continue
            proposed = {"title": matched.title, **changed}
            old_value = {key: getattr(matched, key, "") for key in changed}
            target_id = matched.id
            change_type = "event_update"
            reason = "本章分析发现事件存在新的明确状态或范围信息，已自动入库并保留来源。"
        else:
            old_value = {}
            target_id = None
            change_type = "event_add"
            reason = "本章分析发现新的有标题事件，已自动加入事件轴并保留来源。"
        signature = ("event", title, json.dumps(proposed, ensure_ascii=False, sort_keys=True, default=str))
        if signature not in existing_signatures and signature not in generated_signatures:
            normalized_candidates.append({
                "change_type": change_type, "target_type": "event", "target_id": target_id, "target_name": title,
                "old_value": old_value, "proposed_value": proposed, "reason": reason,
                "evidence": str(raw_event.get("evidence") or raw_event.get("description") or ""),
                "source_chapter_id": analysis.chapter_id, "confidence": float(raw_event.get("confidence") or 0.8),
                "conflict": False,
            })
            generated_signatures.add(signature)

    for candidate in normalized_candidates:
        if not isinstance(candidate, dict):
            skipped += 1
            continue
        signature = (str(candidate.get("target_type") or "other"), str(candidate.get("target_name") or ""),
                     json.dumps(candidate.get("proposed_value"), ensure_ascii=False, sort_keys=True, default=str))
        if signature in existing_signatures:
            continue
        existing_signatures.add(signature)
        change = AnalysisChange(analysis_id=analysis.id, change_type=candidate.get("change_type", "other"),
                                target_type=candidate.get("target_type", "other"), target_id=_optional_int(candidate.get("target_id")),
                                target_name=candidate.get("target_name", ""), old_value=candidate.get("old_value"),
                                proposed_value=candidate.get("proposed_value"), reason=candidate.get("reason", ""),
                                evidence=candidate.get("evidence", ""), source_chapter_id=analysis.chapter_id,
                                confidence=float(candidate.get("confidence", 0)), conflict=bool(candidate.get("conflict", False)))
        db.add(change)
    return skipped
