import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.main import app
from app.database import SessionLocal
from app.models import Analysis, GraphifyRun, LLMProvider
from app.routers import summary

client = TestClient(app)


@pytest.fixture
def chapter():
    with SessionLocal.begin() as db:
        db.execute(delete(LLMProvider))
    book = client.post("/api/books", json={"title": "梗概验收"}).json()
    return client.post(f"/api/books/{book['id']}/chapters", json={"title": "风雪夜", "content": "沈舟在城门口遇见旧友，决定一同寻找失踪的师父。"}).json()


def configure():
    return client.post('/api/llm/providers', json={"provider_name": "测试模型", "base_url": "https://summary.invalid/v1",
                       "api_key": "test-only", "model": "summary-model", "temperature": 0.4, "max_tokens": 900, "enabled": True}).json()


def test_single_summary_uses_configured_model_and_preserves_body(monkeypatch, chapter):
    configure()
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "【本章概述】沈舟与旧友重逢，决定寻找师父。"}}]})

    monkeypatch.setattr(summary.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    url = f"/api/chapters/{chapter['id']}/summary"
    for _ in range(2):
        response = client.post(url)
        assert response.status_code == 200
        assert response.json()["updated"]
    assert len(requests) == 2  # explicit single-chapter generation always regenerates
    assert requests[0]["model"] == "summary-model"
    assert requests[0]["temperature"] == 0.4
    assert requests[0]["max_tokens"] == 900
    assert json.loads(requests[0]["messages"][1]["content"])["content"] == chapter["current_content"]
    stored = client.get(f"/api/chapters/{chapter['id']}").json()
    assert stored["summary"] == "【本章概述】沈舟与旧友重逢，决定寻找师父。"
    assert stored["summary_model"] == "summary-model"
    assert stored["summary_source_version_id"] == chapter["current_version_id"]
    assert not stored["summary_is_stale"]
    assert stored["current_content"] == chapter["current_content"]
    assert len(client.get(f"/api/chapters/{chapter['id']}/versions").json()) == 1
    with SessionLocal() as db:
        assert db.scalars(select(Analysis).where(Analysis.book_id == chapter["book_id"])).all() == []
        assert db.scalars(select(GraphifyRun).where(GraphifyRun.book_id == chapter["book_id"])).all() == []


def test_batch_only_updates_changed_or_missing_summaries(monkeypatch, chapter):
    configure()
    calls = []

    async def fake(provider, title, content):
        calls.append(content)
        return f"【本章概述】{content}"

    monkeypatch.setattr(summary, "generate_summary", fake)
    other = client.post('/api/books', json={"title": "隔离小说"}).json()
    client.post(f"/api/books/{other['id']}/chapters", json={"content": "不应发送到模型的另一本书"})
    client.post(f"/api/books/{chapter['book_id']}/chapters", json={"content": ""})
    url = f"/api/books/{chapter['book_id']}/chapter-summaries"
    first = client.post(url).json()
    assert (first["total"], first["updated"], first["skipped"]) == (2, 1, 1)
    again = client.post(url).json()
    assert (again["updated"], again["skipped"]) == (0, 2)
    client.post(f"/api/chapters/{chapter['id']}/versions", json={"content": "师父留下了一封书信。"})
    assert client.get(f"/api/chapters/{chapter['id']}").json()["summary_is_stale"]
    changed = client.post(url).json()
    assert (changed["updated"], changed["skipped"]) == (1, 1)
    assert calls == [chapter["current_content"], "师父留下了一封书信。"]


def test_missing_config_and_failed_provider_preserve_old_summary(monkeypatch, chapter):
    url = f"/api/chapters/{chapter['id']}/summary"
    assert client.post(url).json()["status"] == "failed"
    configure()

    async def good(*args): return "已保存的梗概"
    monkeypatch.setattr(summary, "generate_summary", good)
    client.post(url)

    async def bad(*args): raise httpx.ConnectError("test failure")
    monkeypatch.setattr(summary, "generate_summary", bad)
    failed = client.post(url).json()
    assert failed["status"] == "failed"
    assert failed["chapter"]["summary"] == "已保存的梗概"
    assert client.get(f"/api/chapters/{chapter['id']}").json()["summary"] == "已保存的梗概"


def test_newer_body_is_not_assigned_outdated_summary(monkeypatch, chapter):
    configure()

    async def racing(*args):
        assert client.post(f"/api/chapters/{chapter['id']}/versions", json={"content": "生成途中另存的新正文"}).status_code == 200
        return "对应旧正文的梗概"
    monkeypatch.setattr(summary, "generate_summary", racing)
    result = client.post(f"/api/chapters/{chapter['id']}/summary").json()
    assert result["status"] == "failed"
    assert result["chapter"]["summary"] == ""
    assert result["chapter"]["current_content"] == "生成途中另存的新正文"


def test_batch_stops_after_one_chapter_fails(monkeypatch, chapter):
    configure()
    second = client.post(f"/api/books/{chapter['book_id']}/chapters", json={"title": "第二章", "content": "新的线索"}).json()

    async def fake(provider, title, content):
        if title == "风雪夜": raise ValueError("模型输出为空")
        return "第二章梗概"
    monkeypatch.setattr(summary, "generate_summary", fake)
    result = client.post(f"/api/books/{chapter['book_id']}/chapter-summaries").json()
    assert (result["updated"], result["failed"]) == (0, 1)
    assert client.get(f"/api/chapters/{second['id']}").json()["summary"] == ""


def test_summary_response_formats():
    assert summary.normalize_summary('```json\n{"summary":"全文梗概"}\n```') == "全文梗概"
    assert summary.normalize_summary('{"summary":{"本章概述":"重逢","关键事件":["相遇","出发"]}}') == "【本章概述】重逢\n【关键事件】相遇；出发"
    for raw in ('', '{}', '{"summary":""}', '{broken'):
        with pytest.raises(ValueError): summary.normalize_summary(raw)
