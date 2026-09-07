import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.main import app
from app.database import SessionLocal
from app.models import LLMProvider
from app.routers.summary import summary_provider

client = TestClient(app)


def test_active_model_and_key_lifecycle(monkeypatch):
    with SessionLocal.begin() as db:
        db.execute(delete(LLMProvider))
    payload = {'base_url': 'https://test.invalid/v1', 'api_key': 'secret-test', 'model': 'Model-ID', 'enabled': True}
    first = client.post('/api/llm/providers', json=payload).json()
    second = client.post('/api/llm/providers', json=payload).json()
    assert [p['id'] for p in client.get('/api/llm/providers').json() if p['enabled']] == [second['id']]
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'data': [{'id': 'DeepSeek-V4-Flash'}, {'id': 'Model-ID'}]}))))
    assert client.get(f"/api/llm/providers/{second['id']}/models").json() == ['DeepSeek-V4-Flash', 'Model-ID']
    cleared = client.delete(f"/api/llm/providers/{second['id']}/key").json()
    assert not cleared['has_api_key'] and not cleared['enabled']
    client.delete(f"/api/llm/providers/{second['id']}")
    with SessionLocal() as db:
        assert summary_provider(db) is None  # A sole disabled provider must remain disabled.
