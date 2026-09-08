import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.database import SessionLocal
from app.main import app
from app.models import LLMModuleRoute, LLMProvider, SummaryJob
from app.routers import current_state, summary_jobs

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_routes():
    with SessionLocal.begin() as db:
        db.execute(delete(LLMModuleRoute))
        db.execute(delete(LLMProvider))
    yield
    with SessionLocal.begin() as db:
        db.execute(delete(LLMModuleRoute))


def provider(model, enabled=False):
    result = client.post('/api/llm/providers', json={
        'provider_name': model, 'base_url': f'https://{model}.invalid/v1', 'model': model,
        'api_key': 'secret-' + model, 'enabled': enabled, 'timeout': 3600,
    })
    assert result.status_code == 200
    return result.json()


def assign(module, model, revision=0):
    return client.put('/api/llm/routes/' + module, json={'provider_id': model['id'] if model else None, 'base_revision': revision})


def book():
    b = client.post('/api/books', json={'title': '分流测试'}).json()
    c = client.post(f"/api/books/{b['id']}/chapters", json={'content': '测试正文'}).json()
    return b['id'], c['id']


def test_all_generation_paths_use_their_assigned_models_and_batch_snapshot(monkeypatch):
    default = provider('default', True)
    single, batch, state = provider('single'), provider('batch'), provider('state')
    routes = client.get('/api/llm/routes').json()
    assert len(routes) == 4
    assert all(r['effective_provider']['id'] == default['id'] for r in routes if r['module'] != 'chapter_writing')
    assert next(r for r in routes if r['module'] == 'chapter_writing')['effective_provider'] is None
    for module, model in [('chapter_summary', single), ('batch_summary', batch), ('current_state', state)]:
        assert assign(module, model).status_code == 200
    assert 'secret-' not in client.get('/api/llm/routes').text
    calls = []
    original = httpx.AsyncClient
    def respond(request):
        payload = json.loads(request.content)
        calls.append((payload['model'], request.url.host, request.headers['Authorization'], request.extensions['timeout']['read']))
        return httpx.Response(200, json={'choices': [{'message': {'content': '根据正文生成的资料'}}]})
    monkeypatch.setattr(current_state.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    bid, cid = book()
    assert client.post(f'/api/chapters/{cid}/summary').json()['updated']
    assert client.post(f'/api/books/{bid}/current-state/generate', json={'base_version_id': None}).status_code == 200
    assert client.get(f'/api/books/{bid}/current-state/context').json()['model'] == 'state'
    bid2, _ = book()
    assert client.post(f'/api/books/{bid2}/chapter-summaries').json()['updated'] == 1
    bid3, _ = book()
    job = client.post(f'/api/books/{bid3}/summary-jobs').json()
    assert job['model'] == 'batch'
    assert assign('batch_summary', single, 1).status_code == 200
    with SessionLocal.begin() as db:
        db.get(SummaryJob, job['id']).status = 'running'
    asyncio.run(summary_jobs.run_job(job['id']))
    assert client.get(f"/api/summary-jobs/{job['id']}").json()['updated'] == 1
    assert [c[0] for c in calls] == ['single', 'state', 'batch', 'batch']
    assert all(host == model + '.invalid' and key == 'Bearer secret-' + model and timeout == 3600 for model, host, key, timeout in calls)
    with SessionLocal() as db:
        assert 'api_key' not in db.get(SummaryJob, job['id']).provider_config
    resumed = client.post(f"/api/summary-jobs/{job['id']}/resume").json()
    assert resumed['model'] == 'single'
    client.post(f"/api/summary-jobs/{job['id']}/cancel")
    assert assign('current_state', None, 1).status_code == 200
    assert client.get(f'/api/books/{bid}/current-state/context').json()['model'] == 'default'


def test_invalid_routes_conflicts_and_removed_provider_never_fall_back(monkeypatch):
    provider('default', True)
    chosen = provider('chosen')
    assert assign('current_state', chosen).status_code == 200
    assert assign('current_state', None).status_code == 409
    assert assign('unknown', chosen).status_code == 404
    assert client.put('/api/llm/routes/chapter_summary', json={'provider_id': 999999, 'base_revision': 0}).status_code == 400
    assert client.put('/api/llm/routes/chapter_summary', json={'provider_id': None}).status_code == 422
    bid, _ = book()
    url = f'/api/books/{bid}/current-state'
    saved = client.post(url + '/versions', json={'content': '已有速查', 'base_version_id': None}).json()
    async def unexpected(*args):
        pytest.fail('Must not call an unrelated provider')
    monkeypatch.setattr(current_state, 'call_model', unexpected)
    assert client.delete(f"/api/llm/providers/{chosen['id']}").status_code == 200
    provider('replacement')  # A reused SQLite ID must not resurrect the old assignment.
    row = next(r for r in client.get('/api/llm/routes').json() if r['module'] == 'current_state')
    assert row['provider_id'] is None and not row['use_default'] and row['error']
    context = client.get(url + '/context').json()
    assert context['model'] is None and context['provider_error']
    assert client.post(url + '/generate', json={'base_version_id': saved['id']}).status_code == 400
    assert client.get(url).json()['version']['id'] == saved['id']
    assert assign('current_state', None, 1).status_code == 200


def test_cleared_key_blocks_explicit_single_and_batch_routes():
    chosen = provider('chosen')
    for module in ('chapter_summary', 'batch_summary'):
        assert assign(module, chosen).status_code == 200
    client.delete(f"/api/llm/providers/{chosen['id']}/key")
    assert assign('current_state', chosen).status_code == 400
    bid, cid = book()
    result = client.post(f'/api/chapters/{cid}/summary').json()
    assert not result['updated'] and '模块分流' in result['message']
    assert client.post(f'/api/books/{bid}/summary-jobs').status_code == 400
    assert client.post(f'/api/books/{bid}/chapter-summaries').status_code == 400
