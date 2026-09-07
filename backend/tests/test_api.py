from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.main import app
from app.database import SessionLocal
from app.models import LLMProvider

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


def test_runtime_presets_and_openapi_exclude_moved_ai_services():
    response = client.get("/api/llm/providers/presets")
    assert response.status_code in {404, 405}

    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/llm/providers/presets" not in paths
    assert all(not path.startswith("/api/xingyue/") for path in paths)


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
