import asyncio
import os
from pathlib import Path

import httpx

os.environ["DATABASE_URL"] = "sqlite:///./test_noveltools.db"
os.environ["DATA_DIR"] = "./test_data"

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.main import app
from app.database import SessionLocal
from app.models import Analysis, Chapter, ChapterVersion, GraphifyRun, LLMProvider, RelationState
from app import services


client = TestClient(app)


def test_book_chapter_versions_and_isolation():
    first = client.post("/api/books", json={"title": "第一本", "genre": "奇幻"}).json()
    second = client.post("/api/books", json={"title": "第二本", "genre": "悬疑"}).json()
    chapter = client.post(f"/api/books/{first['id']}/chapters", json={"title": "开端", "content": "陈渡走进城门。"}).json()
    assert chapter["word_count"] >= 6
    version = client.post(f"/api/chapters/{chapter['id']}/versions", json={"content": "陈渡走进城门，听见钟声。", "note": "扩写"}).json()
    assert version["version_number"] == 2
    versions = client.get(f"/api/chapters/{chapter['id']}/versions").json()
    assert len(versions) == 2
    assert client.get(f"/api/books/{second['id']}/chapters").json() == []


def test_analysis_and_prompt():
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()
    book = client.post("/api/books", json={"title": "分析测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "主角发现一扇没有锁的门。"}).json()
    analysis = client.post(f"/api/chapters/{chapter['id']}/analyze", json={}).json()
    assert analysis["status"] == "completed"
    assert "facts" in analysis["result_json"]
    prompt = client.post(f"/api/chapters/{chapter['id']}/prompt", json={"word_count": "2000字"}).json()
    assert "下一章写作上下文" in prompt["prompt"]


def test_single_chapter_summary_always_regenerates(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    calls = []

    async def fake_call_provider(provider, context):
        if context.get("analysis_type") == "chapter_summary":
            calls.append(context["chapter"]["content"])
        return {"summary": f"固化梗概：{context['chapter']['content']}"}, '{"summary":"ok"}', 11

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "梗概缓存测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡走进火窟。"}).json()
    client.post("/api/llm/providers", json={"provider_name": "梗概模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "summary-test", "enabled": True})

    first = client.post(f"/api/chapters/{chapter['id']}/summary")
    assert first.status_code == 200
    assert first.json()["updated"] is True
    second = client.post(f"/api/chapters/{chapter['id']}/summary")
    assert second.status_code == 200
    assert second.json()["updated"] is True
    assert second.json()["skipped"] is False
    assert len(calls) == 2

    client.post(f"/api/chapters/{chapter['id']}/versions", json={"content": "陈渡走进火窟，发现一枚古印。"})
    third = client.post(f"/api/chapters/{chapter['id']}/summary")
    assert third.status_code == 200
    assert third.json()["updated"] is True
    assert len(calls) == 3
    stored = client.get(f"/api/chapters/{chapter['id']}").json()
    assert stored["summary_content_sha256"] == third.json()["content_sha256"]


def test_batch_chapter_summaries_only_updates_missing_or_changed(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    calls = []

    async def fake_call_provider(provider, context):
        if context.get("analysis_type") == "chapter_summary":
            calls.append(context["chapter"]["number"])
        return {"summary": f"【本章概述】第{context['chapter']['number']}章\n【关键人物】无\n【关键事件】无\n【状态变化】无\n【结尾状态】未说明\n【未决事项】无"}, '{"summary":"ok"}', 8

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "批量梗概测试"}).json()
    first = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "第一章正文"}).json()
    client.post(f"/api/books/{book['id']}/chapters", json={"title": "第二章", "content": "第二章正文"})
    client.post("/api/llm/providers", json={"provider_name": "批量梗概模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "summary-batch-test", "enabled": True})

    initial = client.post(f"/api/books/{book['id']}/chapter-summaries").json()
    assert (initial["updated"], initial["skipped"], initial["failed"]) == (2, 0, 0)
    cached = client.post(f"/api/books/{book['id']}/chapter-summaries").json()
    assert (cached["updated"], cached["skipped"], cached["failed"]) == (0, 2, 0)

    client.post(f"/api/chapters/{first['id']}/versions", json={"content": "第一章正文已修改"})
    changed = client.post(f"/api/books/{book['id']}/chapter-summaries").json()
    assert (changed["updated"], changed["skipped"], changed["failed"]) == (1, 1, 0)
    assert calls == [1, 2, 1]


def test_saved_chapter_automatically_graphifies_and_deduplicates_same_sha(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    calls = []

    async def fake_call_provider(provider, context):
        assert context["analysis_type"] == "graphify_init"
        calls.append(context["source_version_id"])
        return {
            "analysis_type": "graphify_init",
            "characters": [{"name": "陈渡", "summary": "本章主角", "evidence": "陈渡走进城门", "confidence": 0.95}],
            "knowledge": [], "threads": [], "events": [],
        }, '{"analysis_type":"graphify_init"}', 12

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "自动图谱测试"}).json()
    client.post("/api/llm/providers", json={"provider_name": "自动图谱模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "graphify-test", "enabled": True})
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡走进城门。"}).json()
    same = client.post(f"/api/chapters/{chapter['id']}/versions", json={"content": "陈渡走进城门。"}).json()
    changed = client.post(f"/api/chapters/{chapter['id']}/versions", json={"content": "陈渡走进城门，发现古印。"}).json()

    runs = [item for item in client.get(f"/api/books/{book['id']}/graphify/runs").json() if item["operation"] == "auto_chapter"]
    assert len(runs) == 2
    assert all(item["status"] == "completed" for item in runs)
    assert len(calls) == 2
    assert same["version_number"] == 2
    assert changed["version_number"] == 3
    characters = client.get(f"/api/books/{book['id']}/characters").json()
    assert [item["name"] for item in characters] == ["陈渡"]


def test_auto_graphify_runs_focused_character_pass_when_general_result_is_empty(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    calls = []

    async def fake_call_provider(provider, context):
        calls.append(context["analysis_type"])
        if context["analysis_type"] == "characters":
            return {
                "analysis_type": "characters",
                "characters": [{
                    "name": "陈渡", "summary": "本章主角",
                    "evidence": [{"text": "陈渡走进城门", "position": "paragraph_1"}],
                    "confidence": 0.96,
                }],
            }, '{"analysis_type":"characters"}', 6
        return {
            "analysis_type": "graphify_init",
            "characters": [], "knowledge": [], "threads": [], "events": [],
        }, '{"analysis_type":"graphify_init","characters":[]}', 9

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "自动角色补提取测试"}).json()
    client.post("/api/llm/providers", json={"provider_name": "自动角色模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "character-recovery-test", "enabled": True})

    client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡走进城门。"})

    assert calls == ["graphify_init", "characters"]
    characters = client.get(f"/api/books/{book['id']}/characters").json()
    assert [item["name"] for item in characters] == ["陈渡"]
    run = next(item for item in client.get(f"/api/books/{book['id']}/graphify/runs").json() if item["operation"] == "auto_chapter")
    assert run["status"] == "completed"
    assert run["result_json"]["character_extraction_attempted"] is True
    assert [item["name"] for item in run["result_json"]["characters"]] == ["陈渡"]


def test_auto_graphify_falls_back_to_another_enabled_provider(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    calls = []

    async def fake_call_provider(provider, context):
        calls.append(provider.model)
        if provider.model == "primary-unavailable":
            raise RuntimeError("首选模型暂时不可用")
        return {
            "analysis_type": "graphify_init",
            "characters": [{"name": "陈渡", "evidence": "陈渡走进城门", "confidence": 0.95}],
            "knowledge": [], "threads": [], "events": [],
        }, '{"analysis_type":"graphify_init"}', 12

    def fake_find_analysis_providers(db):
        providers = list(db.scalars(select(LLMProvider).order_by(LLMProvider.id.desc())))
        return providers, "enabled"

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    monkeypatch.setattr("app.routers.core.find_analysis_providers", fake_find_analysis_providers)
    book = client.post("/api/books", json={"title": "自动图谱模型回退测试"}).json()
    client.post("/api/llm/providers", json={"provider_name": "备用模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "fallback-works", "enabled": True})
    client.post("/api/llm/providers", json={"provider_name": "首选模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "primary-unavailable", "enabled": True})

    client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡走进城门。"})

    assert calls == ["primary-unavailable", "fallback-works"]
    run = next(item for item in client.get(f"/api/books/{book['id']}/graphify/runs").json() if item["operation"] == "auto_chapter")
    assert run["status"] == "completed"
    assert run["model"] == "fallback-works"
    assert run["result_json"]["provider_fallbacks"][0]["model"] == "primary-unavailable"
    assert [item["name"] for item in client.get(f"/api/books/{book['id']}/characters").json()] == ["陈渡"]


def test_auto_graphify_retries_transient_provider_failure(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    calls = []

    async def fake_call_provider(provider, context):
        calls.append(provider.model)
        if len(calls) == 1:
            raise httpx.ReadTimeout("模型生成超时")
        return {
            "analysis_type": "graphify_init",
            "characters": [{"name": "陈渡", "evidence": "陈渡走进城门", "confidence": 0.95}],
            "knowledge": [], "threads": [], "events": [],
        }, '{"analysis_type":"graphify_init"}', 12

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "自动图谱瞬时故障重试测试"}).json()
    client.post("/api/llm/providers", json={"provider_name": "可重试模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "retry-provider", "enabled": True})

    client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡走进城门。"})

    assert calls == ["retry-provider", "retry-provider"]
    run = next(item for item in client.get(f"/api/books/{book['id']}/graphify/runs").json() if item["operation"] == "auto_chapter")
    assert run["status"] == "completed"


def test_auto_graphify_does_not_send_previous_chapter_bodies(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    contexts = []

    async def fake_call_provider(provider, context):
        contexts.append(context)
        return {
            "analysis_type": "graphify_init",
            "characters": [{"name": "陈渡", "evidence": "当前章证据", "confidence": 0.95}],
            "knowledge": [], "threads": [], "events": [],
        }, '{"analysis_type":"graphify_init"}', 12

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "自动图谱上下文测试"}).json()
    client.post("/api/llm/providers", json={"provider_name": "上下文模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "context-provider", "enabled": True})
    client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "第一章旧正文。"})
    contexts.clear()

    client.post(f"/api/books/{book['id']}/chapters", json={"title": "第二章", "content": "第二章当前正文。"})

    assert len(contexts) == 1
    assert contexts[0]["chapter"]["content"] == "第二章当前正文。"
    assert "previous_chapters" not in contexts[0]
    assert "previous_chapters_full" not in contexts[0]


def test_auto_graphify_requeues_historical_empty_character_result_once(monkeypatch):
    from app.routers import core

    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    book = client.post("/api/books", json={"title": "历史角色漏提取测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡走进城门。"}).json()
    run = next(item for item in client.get(f"/api/books/{book['id']}/graphify/runs").json() if item["operation"] == "auto_chapter")

    with SessionLocal() as db:
        stored_chapter = db.get(Chapter, chapter["id"])
        version = db.get(ChapterVersion, stored_chapter.current_version_id)
        historical_run = db.get(GraphifyRun, run["id"])
        stored_chapter.summary = "【本章概述】陈渡进城。\n【关键人物】陈渡：本章主角。\n【关键事件】进城。"
        historical_run.model = "historical-provider"
        historical_run.result_json = {
            "analysis_type": "graphify_init",
            "characters": [], "knowledge": [], "threads": [], "events": [], "proposals": [],
        }
        db.commit()

        retry_id = core.queue_auto_graphify_run(db, stored_chapter, version, services.read_content(version), "startup_reconcile")

    assert retry_id is not None
    calls = []

    async def fake_call_provider(provider, context):
        calls.append(context["analysis_type"])
        return {
            "analysis_type": "characters",
            "characters": [{"name": "陈渡", "evidence": "陈渡走进城门", "confidence": 0.95}],
        }, '{"analysis_type":"characters"}', 8

    monkeypatch.setattr(core, "call_provider", fake_call_provider)
    client.post("/api/llm/providers", json={"provider_name": "历史角色恢复模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "recovery-test", "enabled": True})
    asyncio.run(core.process_auto_graphify(retry_id))

    assert calls == ["characters"]
    recovered = client.get(f"/api/graphify/runs/{retry_id}").json()
    assert recovered["status"] == "completed"
    assert recovered["result_json"]["character_recovery_only"] is True


def test_startup_graphify_resumes_orphaned_queued_run(monkeypatch):
    from app.routers import core

    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    book = client.post("/api/books", json={"title": "启动恢复排队任务测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡走进城门。"}).json()
    run = next(item for item in client.get(f"/api/books/{book['id']}/graphify/runs").json() if item["operation"] == "auto_chapter")
    with SessionLocal() as db:
        stored = db.get(GraphifyRun, run["id"])
        stored.status = "queued"
        db.commit()

    processed = []

    async def fake_process_auto_graphify(run_id):
        processed.append(run_id)

    monkeypatch.setattr(core, "process_auto_graphify", fake_process_auto_graphify)
    asyncio.run(core.auto_graphify_existing_chapters())

    assert run["id"] in processed


def test_chapter_summary_normalizes_structured_summary_object(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    async def fake_call_provider(provider, context):
        return {"summary": {
            "【本章概述】": "陈渡发现异常。",
            "【关键人物】": "陈渡：保持警惕。",
            "【关键事件】": "发现异常火纹。",
            "【状态变化】": "警觉提高。",
            "【结尾状态】": "返回散舍。",
            "【未决事项】": "异常来源未明。",
        }}, '{"summary":{}}', 7

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "结构化梗概测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡发现异常。"}).json()
    client.post("/api/llm/providers", json={"provider_name": "结构化梗概模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "summary-object-test", "enabled": True})

    result = client.post(f"/api/chapters/{chapter['id']}/summary")
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "completed"
    assert data["summary"].startswith("【本章概述】陈渡发现异常。")
    assert "【未决事项】异常来源未明。" in data["summary"]


def test_chapter_summary_repairs_section_strings_returned_as_invalid_object():
    raw = (
        '{"summary": {"【本章概述】陈渡发现异常。", '
        '"【关键人物】陈渡", "【关键事件】发现异常火纹。", '
        '"【状态变化】警觉提高。", "【结尾状态】返回散舍。", '
        '"【未决事项】异常来源未明。"}}'
    )

    parsed = services.parse_json_response(raw)

    assert parsed["summary"]["【本章概述】"] == "陈渡发现异常。"
    assert parsed["summary"]["【未决事项】"] == "异常来源未明。"


def test_failed_chapter_summary_preserves_invalid_raw_response(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    raw = "模型没有返回 JSON"

    async def fake_call_provider(provider, context):
        raise services.LLMResponseError("LLM 返回不是有效 JSON；原始响应已保存", raw)

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "梗概原文保存测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡发现异常。"}).json()
    client.post("/api/llm/providers", json={"provider_name": "原文保存模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "raw-test", "enabled": True})

    result = client.post(f"/api/chapters/{chapter['id']}/summary").json()

    assert result["status"] == "failed"
    assert result["message"] == "LLM 返回不是有效 JSON；原始响应已保存"
    with SessionLocal() as db:
        analysis = db.get(Analysis, result["analysis_id"])
        assert analysis.raw_response == raw


def test_prompt_can_target_uncreated_chapter_and_split_full_text_from_summaries():
    book = client.post("/api/books", json={"title": "目标章节提示词测试"}).json()
    chapters = [
        client.post(f"/api/books/{book['id']}/chapters", json={"title": f"第{number}章", "content": f"第{number}章的正文内容，包含第{number}章独有信息。"}).json()
        for number in range(1, 12)
    ]
    outline = client.post(f"/api/books/{book['id']}/outlines", json={"title": "第三幕", "description": "主角面对最终选择"}).json()
    client.post(f"/api/outlines/{outline['id']}/blocks", json={
        "title": "最终选择",
        "summary": "陈渡与苏晚在古印和城门之间做出最终选择。",
        "objective": "完成抉择",
        "conflict": "救人还是保住古印",
        "start_chapter": 12,
        "end_chapter": 12,
    })
    first = client.post(f"/api/books/{book['id']}/characters", json={"name": "陈渡", "role": "主角", "current_state": "受伤但仍在坚持", "relationships": "与苏晚互相信任"}).json()
    second = client.post(f"/api/books/{book['id']}/characters", json={"name": "苏晚", "role": "同伴", "current_state": "守在城门外"}).json()
    with SessionLocal() as db:
        db.add(RelationState(book_id=book["id"], from_character_id=first["id"], to_character_id=second["id"], trust=0.8, cooperation=0.6, current_note="共同经历过火窟事件"))
        db.commit()

    response = client.post(f"/api/chapters/{chapters[-1]['id']}/prompt", json={"target_chapter_number": 12})
    assert response.status_code == 200
    data = response.json()
    assert data["context"]["chapter"]["number"] == 12
    assert data["context"]["chapter"]["id"] is None
    assert [item["number"] for item in data["context"]["previous_chapters_full"]] == [10, 11]
    assert [item["number"] for item in data["context"]["previous_chapter_summaries"]] == list(range(1, 10))
    assert {item["name"] for item in data["context"]["involved_characters"]} == {"陈渡", "苏晚"}
    assert len(data["context"]["relations"]) == 1
    assert data["context"]["relations"][0]["from_name"] == "陈渡"
    assert "最终选择" in data["prompt"]
    assert "第10章的正文内容" in data["prompt"]
    assert "第11章的正文内容" in data["prompt"]
    assert "第1章独有信息" in data["prompt"]
    assert "陈渡" in data["prompt"]


def test_restore_outline_and_knowledge_version():
    book = client.post("/api/books", json={"title": "版本测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一版", "content": "旧内容"}).json()
    client.post(f"/api/chapters/{chapter['id']}/versions", json={"content": "新内容"})
    versions = client.get(f"/api/chapters/{chapter['id']}/versions").json()
    restored = client.post(f"/api/chapters/{chapter['id']}/versions/{versions[-1]['id']}/restore").json()
    assert restored["version_number"] == 3
    assert restored["content"] == "旧内容"
    outline = client.post(f"/api/books/{book['id']}/outlines", json={"title": "第一幕"}).json()
    block = client.post(f"/api/outlines/{outline['id']}/blocks", json={"title": "开端", "start_chapter": 1}).json()
    assert block["title"] == "开端"
    item = client.post(f"/api/books/{book['id']}/knowledge", json={"name": "火窟", "content": "危险地点"}).json()
    client.put(f"/api/knowledge/{item['id']}", json={"name": "火窟", "content": "高温危险地点", "category": "location", "status": "active"})
    assert len(client.get(f"/api/knowledge/{item['id']}/versions").json()) == 2


def test_analysis_uses_single_configured_provider_when_not_ticked_enabled(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    calls = []

    async def fake_call_provider(provider, context):
        calls.append(provider.model)
        return {"facts": [{"text": "来自真实 provider"}], "events": [], "beliefs": [], "relation_changes": [], "issues": [{"severity": "warning", "category": "continuity", "message": "测试疑点", "current_text": "当前文本", "reference": "第 1 章依据", "suggestion": "请作者判断", "confidence": 0.8}], "foreshadowing_candidates": [], "change_candidates": ["模型返回的非结构化候选"]}, "{\"facts\": [{\"text\": \"来自真实 provider\"}]}", 12

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "Provider 回归测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "正文"}).json()
    client.post("/api/llm/providers", json={"provider_name": "已添加但忘记勾选", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "configured-model", "enabled": False})
    result = client.post(f"/api/chapters/{chapter['id']}/analyze", json={"analysis_type": "update"}).json()
    assert result["status"] == "completed"
    assert result["model"] == "configured-model"
    assert calls == ["configured-model"]
    assert result["result_json"]["facts"][0]["text"] == "来自真实 provider"
    reviewed = client.post(f"/api/analysis/{result['id']}/issues/0/review", json={"status": "confirmed", "note": "作者确认"}).json()
    assert reviewed["result_json"]["issues"][0]["review_status"] == "confirmed"


def test_review_rejects_provider_prompt_echo_and_preserves_raw_response(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    raw = '{"schema": {"properties": {"facts": []}}, "context": {"chapter": {"content": ""}}}'

    async def fake_call_provider(provider, context):
        return {"schema": {"properties": {"facts": []}}, "context": {"chapter": {"content": ""}}}, raw, 9

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "回显响应测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡走进火窟。"}).json()
    client.post("/api/llm/providers", json={"provider_name": "回显测试模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "echo-test", "enabled": True})

    result = client.post(f"/api/chapters/{chapter['id']}/analyze", json={"analysis_type": "review"}).json()

    assert result["status"] == "failed"
    assert "缺少分析字段" in result["error_message"]
    assert result["raw_response"] == raw


def test_character_analysis_matches_existing_and_proposes_new_character(monkeypatch):
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    async def fake_call_provider(provider, context):
        return {
            "analysis_type": "characters",
            "characters": [
                {"name": "陈渡", "appearance": "右臂缠着旧护腕", "level": "练气一层", "evidence": [{"text": "陈渡抬起右臂", "position": "paragraph_2"}], "confidence": 0.94},
                {"name": "老周", "role": "火窟杂役", "evidence": [{"text": "老周守在炉边", "position": "paragraph_5"}], "confidence": 0.82},
            ],
        }, '{"analysis_type":"characters"}', 20

    monkeypatch.setattr("app.routers.core.call_provider", fake_call_provider)
    book = client.post("/api/books", json={"title": "角色分析测试"}).json()
    existing = client.post(f"/api/books/{book['id']}/characters", json={"name": "陈渡", "role": "杂役"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "第一章", "content": "陈渡抬起右臂。老周守在炉边。"}).json()
    client.post(f"/api/llm/providers", json={"provider_name": "角色分析测试模型", "base_url": "http://127.0.0.1:9/v1", "api_key": "test-key", "model": "character-test", "enabled": True})

    result = client.post(f"/api/chapters/{chapter['id']}/analyze", json={"analysis_type": "characters"}).json()

    assert result["status"] == "completed"
    assert result["result_json"]["analysis_type"] == "characters"
    assert [item["name"] for item in result["result_json"]["characters"]] == ["陈渡", "老周"]
    assert len(result["changes"]) == 2
    update = next(item for item in result["changes"] if item["target_id"] == existing["id"])
    add = next(item for item in result["changes"] if item["target_id"] is None)
    assert update["proposed_value"]["level"] == "练气一层"
    assert add["target_name"] == "老周"

    assert add["status"] == "auto_applied"
    characters = client.get(f"/api/books/{book['id']}/characters").json()
    assert {item["name"] for item in characters} == {"陈渡", "老周"}
    logs = client.get(f"/api/books/{book['id']}/change-logs").json()
    assert any(item["action"] == "auto_applied" and item["target_name"] == "老周" for item in logs)


def test_graphify_init_status_query_path_and_thread_confirmation():
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    book = client.post("/api/books", json={"title": "Graphify 全功能测试"}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={"title": "火窟", "content": "陈渡走进火窟，发现一枚古印。"}).json()
    character = client.post(f"/api/books/{book['id']}/characters", json={"name": "陈渡", "summary": "主角"}).json()
    knowledge = client.post(f"/api/books/{book['id']}/knowledge", json={"name": "火窟", "category": "location", "content": "高温地点"}).json()
    thread = client.post(f"/api/books/{book['id']}/threads", json={"title": "古印来历", "description": "古印的来源尚未揭示"}).json()
    assert thread["status"] == "open"

    status = client.get(f"/api/books/{book['id']}/graphify/status").json()
    assert len(status["open_threads"]) == 1
    assert status["node_count"] >= 3
    graph = client.get(f"/api/books/{book['id']}/graph").json()
    assert any(node["label"] == "陈渡" for node in graph["nodes"])
    query = client.post(f"/api/books/{book['id']}/graphify/query", json={"question": "陈渡", "dfs": True}).json()
    assert query["matched"]
    path = client.post(f"/api/books/{book['id']}/graphify/path", json={"from_name": "陈渡", "to_name": "火窟"}).json()
    assert path["found"] is True

    run = client.post(f"/api/books/{book['id']}/graphify/init", json={"premise": "陈渡在火窟寻找古印", "from_chapters": False}).json()
    assert run["status"] == "completed"
    assert run["proposals"]
    proposal = run["proposals"][0]
    assert proposal["status"] == "auto_applied"
    refreshed = client.get(f"/api/graphify/runs/{run['id']}").json()
    assert refreshed["proposals"][0]["status"] == "auto_applied"

    passage = client.post(f"/api/books/{book['id']}/graphify/review-passage", json={"passage": "陈渡握紧古印。", "intent": "确认他已经拿到古印"}).json()
    assert passage["operation"] == "review"
    lore = client.post(f"/api/books/{book['id']}/graphify/lore", json={"name": "古印", "content": "一枚尚未解释来历的印记", "category": "item"}).json()
    assert lore["proposals"][0]["target_type"] == "knowledge"
    assert lore["proposals"][0]["status"] == "auto_applied"
    assert any(item["name"] == "古印" for item in client.get(f"/api/books/{book['id']}/knowledge").json())

    resolved = client.post(f"/api/threads/{thread['id']}/resolve").json()
    assert resolved["status"] == "resolved"


def test_graphify_character_alias_match_preserves_old_aliases_and_adds_new_name():
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    book = client.post("/api/books", json={"title": "角色别名归并测试"}).json()
    existing = client.post(
        f"/api/books/{book['id']}/characters",
        json={"name": "许丹师", "aliases": "许师叔, 老丹师", "identity": "丹师"},
    ).json()

    with SessionLocal() as db:
        _, proposals = services.prepare_graphify_init(db, book["id"], {
            "analysis_type": "graphify_init",
            "characters": [{"name": "许乘风", "aliases": ["许师叔"], "evidence": "许乘风下令开炉"}],
            "knowledge": [], "threads": [], "events": [],
        })

    update = next(item for item in proposals if item["target_id"] == existing["id"])
    aliases = {item.strip() for item in update["proposed_value"]["aliases"].split(",")}
    assert aliases == {"许师叔", "老丹师", "许乘风"}


def test_runtime_presets_and_openapi_exclude_moved_ai_services():
    response = client.get("/api/llm/providers/presets")
    assert response.status_code in {404, 405}

    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/llm/providers/presets" not in paths
    assert all(not path.startswith("/api/xingyue/") for path in paths)


def test_moved_runtime_rows_remain_history_but_are_not_analysis_adapters():
    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.add_all([
            LLMProvider(provider_name="历史 WebAI", provider_type="webai", base_url="http://127.0.0.1:6969/openai/v1", api_key="legacy-key", model="legacy", enabled=True),
            LLMProvider(provider_name="历史星月", provider_type="xingyue_browser_session", base_url="https://example.invalid", api_key="legacy-key", model="legacy", enabled=True),
            LLMProvider(provider_name="普通兼容服务", provider_type="openai_compatible", base_url="https://example.invalid/v1", api_key="test-key", model="generic", enabled=False),
        ])
        db.commit()

        providers, selection = services.find_analysis_providers(db)

        assert [item.provider_name for item in providers] == ["普通兼容服务"]
        assert selection == "single_configured"
        assert db.query(LLMProvider).count() == 3

    visible = client.get("/api/llm/providers").json()
    assert [item["provider_name"] for item in visible] == ["普通兼容服务"]


def test_moved_runtime_provider_types_cannot_be_created():
    for provider_type in ("webai", "xingyue_browser_session"):
        response = client.post("/api/llm/providers", json={
            "provider_name": "已迁移运行时",
            "provider_type": provider_type,
            "base_url": "https://example.invalid/v1",
            "model": "legacy",
        })
        assert response.status_code == 422


def test_provider_api_hides_and_ignores_legacy_conversation_id():
    openapi = client.get("/openapi.json").json()
    create_properties = openapi["components"]["schemas"]["LLMProviderCreate"]["properties"]
    output_properties = openapi["components"]["schemas"]["LLMProviderOut"]["properties"]
    assert "conversation_id" not in create_properties
    assert "conversation_id" not in output_properties

    with SessionLocal() as db:
        db.execute(delete(LLMProvider))
        db.commit()

    created = client.post("/api/llm/providers", json={
        "provider_name": "普通兼容服务",
        "provider_type": "openai_compatible",
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
        "model": "generic",
        "conversation_id": "create-attempt",
    })
    assert created.status_code == 200
    assert "conversation_id" not in created.json()

    provider_id = created.json()["id"]
    with SessionLocal() as db:
        provider = db.get(LLMProvider, provider_id)
        provider.conversation_id = "historical-value"
        db.commit()

    updated = client.put(f"/api/llm/providers/{provider_id}", json={
        "provider_name": "普通兼容服务",
        "provider_type": "openai_compatible",
        "base_url": "https://example.invalid/v1",
        "api_key": "",
        "model": "generic-updated",
        "conversation_id": "update-attempt",
    })
    assert updated.status_code == 200
    assert "conversation_id" not in updated.json()
    assert all("conversation_id" not in item for item in client.get("/api/llm/providers").json())

    with SessionLocal() as db:
        assert db.get(LLMProvider, provider_id).conversation_id == "historical-value"
