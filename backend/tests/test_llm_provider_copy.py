from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import LLMProvider

client = TestClient(app)


def test_copy_preserves_secret_and_parameters_without_changing_default_or_routes():
    source = client.post('/api/llm/providers', json={
        'provider_name': 'sub2api', 'base_url': 'https://copy-test.invalid/v1', 'model': 'original-model',
        'api_key': 'copy-test-secret', 'temperature': 0.7, 'max_tokens': 12000, 'timeout': 3600, 'enabled': True,
    }).json()
    routes_before = client.get('/api/llm/routes').json()
    result = client.post(f"/api/llm/providers/{source['id']}/copy")
    assert result.status_code == 200
    copied = result.json()
    assert copied['id'] != source['id'] and copied['provider_name'] == 'sub2api 副本'
    assert not copied['enabled'] and copied['has_api_key']
    assert 'copy-test-secret' not in result.text and 'api_key' not in copied
    for field in ('base_url', 'model', 'temperature', 'max_tokens', 'timeout'):
        assert copied[field] == source[field]
    assert client.get('/api/llm/routes').json() == routes_before
    edited = client.put(f"/api/llm/providers/{copied['id']}", json={
        **{k: copied[k] for k in ('provider_name', 'provider_type', 'base_url', 'temperature', 'max_tokens', 'timeout', 'enabled')},
        'model': 'another-model', 'api_key': '',
    })
    assert edited.status_code == 200 and edited.json()['has_api_key']
    with SessionLocal() as db:
        original, clone = db.get(LLMProvider, source['id']), db.get(LLMProvider, copied['id'])
        assert original.enabled and not clone.enabled
        assert original.model == 'original-model' and clone.model == 'another-model'
        assert original.api_key == clone.api_key == 'copy-test-secret'
    client.delete(f"/api/llm/providers/{copied['id']}/key")
    with SessionLocal() as db:
        assert db.get(LLMProvider, source['id']).api_key == 'copy-test-secret'


def test_copy_missing_provider_and_empty_key():
    assert client.post('/api/llm/providers/999999/copy').status_code == 404
    source = client.post('/api/llm/providers', json={
        'provider_name': '无密钥配置', 'base_url': 'https://copy-test.invalid/v1', 'model': 'blank', 'enabled': False,
    }).json()
    copied = client.post(f"/api/llm/providers/{source['id']}/copy").json()
    assert not copied['has_api_key'] and not copied['enabled']
