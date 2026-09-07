from datetime import datetime

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Book, GraphifyRun, Analysis, Outline, OutlineBlock, OutlineTextVersion, LLMProvider
from app.outline_history import migrate_legacy_outlines

client = TestClient(app)


def new_book(title="全文大纲测试"):
    return client.post("/api/books", json={"title": title}).json()["id"]


def test_outline_history_is_immutable_and_scoped_to_book():
    book, other = new_book(), new_book("另一部小说")
    url = f"/api/books/{book}/outline/versions"
    assert client.get(url).json() == []
    content = "主角离开家乡。\n\n第二幕自由规划，不分大纲块。"
    first = client.post(url, json={"content": content, "base_version_id": None}).json()
    second = client.post(url, json={"content": "修订的完整大纲", "base_version_id": first["id"]}).json()
    assert second["version_number"] == 2
    stale = client.post(url, json={"content": "过时编辑不能覆盖", "base_version_id": first["id"]})
    assert stale.status_code == 409
    restored = client.post(f"{url}/{first['id']}/restore", json={"base_version_id": second["id"]}).json()
    assert restored["version_number"] == 3
    assert restored["content"] == content
    history = client.get(url).json()
    assert [v["content"] for v in history] == [content, "修订的完整大纲", content]
    assert client.get(f"/api/books/{other}/outline/versions").json() == []
    assert client.post(f"/api/books/{other}/outline/versions/{first['id']}/restore", json={"base_version_id": None}).status_code == 404
    assert client.get("/api/books/999999/outline/versions").status_code == 404
    blank = client.post(url, json={"content": "", "base_version_id": restored["id"]}).json()
    assert blank["content"] == ""
    assert blank["version_number"] == 4
    assert client.delete(f"/api/books/{book}").status_code == 200
    with SessionLocal() as db:
        assert db.scalars(select(OutlineTextVersion).where(OutlineTextVersion.book_id == book)).all() == []


def test_legacy_outlines_become_text_history_once_without_losing_sources():
    book = new_book("旧大纲兼容")
    with SessionLocal.begin() as db:
        older = Outline(book_id=book, title="初稿", description="早期构思", updated_at=datetime(2025, 1, 1))
        newer = Outline(book_id=book, title="修订稿", description="完整故事前提", version=3, updated_at=datetime(2025, 1, 2))
        db.add_all([older, newer]); db.flush()
        old_id, new_id = older.id, newer.id
        block = OutlineBlock(outline_id=newer.id, title="开篇", summary="相遇", objective="寻找证据", conflict="受到阻拦",
                             turning_point="盟友出现", ending="离开城市", start_chapter=1, end_chapter=5, order_index=0)
        db.add(block); db.flush()
        block_id = block.id
    migrate_legacy_outlines()
    migrate_legacy_outlines()
    versions = client.get(f"/api/books/{book}/outline/versions").json()
    assert len(versions) == 2
    assert versions[1]["content"] == "初稿\n\n早期构思"
    for text in ("完整故事前提", "开篇", "相遇", "寻找证据", "受到阻拦", "盟友出现", "离开城市", "1 — 5"):
        assert text in versions[0]["content"]
    client.post(f"/api/books/{book}/outline/versions", json={"content": "手动编辑后的新全文", "base_version_id": versions[0]["id"]})
    migrate_legacy_outlines()
    assert len(client.get(f"/api/books/{book}/outline/versions").json()) == 3
    with SessionLocal() as db:
        assert db.get(Outline, old_id).description == "早期构思"
        assert db.get(Outline, new_id).version == 3
        assert db.get(OutlineBlock, block_id).summary == "相遇"


def test_startup_and_body_writes_never_call_models_or_create_analysis(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("正文保存不得调用模型")
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    with SessionLocal.begin() as db:
        db.add(LLMProvider(provider_name="不可调用", base_url="https://example.invalid/v1", api_key="test-only", model="test", enabled=True))
    with TestClient(app) as active:
        book = active.post("/api/books", json={"title": "纯正文"}).json()["id"]
        chapter = active.post(f"/api/books/{book}/chapters", json={"content": "第一章正文"}).json()
        url = f"/api/chapters/{chapter['id']}/versions"
        saved = active.post(url, json={"content": "保存修改"}).json()
        restored = active.post(f"{url}/{chapter['current_version_id']}/restore")
        assert restored.status_code == 200
        assert restored.json()["version_number"] == 3
        assert restored.json()["content"] == "第一章正文"
        imported = active.post(f"/api/books/{book}/chapters/import", files={"file": ("第二章.md", "导入全文".encode(), "text/plain")})
        assert imported.status_code == 200
        with SessionLocal() as db:
            assert db.scalars(select(GraphifyRun).where(GraphifyRun.book_id == book)).all() == []
            assert db.scalars(select(Analysis).where(Analysis.book_id == book)).all() == []
    # Starting again must not reconcile existing content into analysis jobs.
    with TestClient(app):
        with SessionLocal() as db:
            assert db.scalars(select(GraphifyRun).where(GraphifyRun.book_id == book)).all() == []


def test_retired_features_are_removed_from_api():
    paths = client.get("/openapi.json").json()["paths"]
    for fragment in ("graphify", "characters", "knowledge", "events", "threads", "analysis", "summary", "summaries", "prompt", "blocks", "outlines", "change-logs"):
        assert not any(fragment in path for path in paths), fragment
