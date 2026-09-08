import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.main import app
from app.database import SessionLocal
from app.models import LLMProvider, LLMModuleRoute, LLMProviderCooldown, WritingJob, SummaryJob
from app.llm_failover import fallback_reason, generation_http_error, cooldown_deadline
from app.routers import summary_jobs, writing_jobs

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate():
    with SessionLocal.begin() as db:
        db.execute(delete(WritingJob)); db.execute(delete(LLMModuleRoute)); db.execute(delete(LLMProvider))
    yield
    with SessionLocal.begin() as db:
        db.execute(delete(WritingJob)); db.execute(delete(LLMModuleRoute))


def provider(model, default=False):
    return client.post('/api/llm/providers', json={'provider_name': model, 'model': model,
        'base_url': 'https://' + model + '.invalid/v1', 'api_key': 'secret-' + model,
        'enabled': default, 'temperature': 1, 'max_tokens': 16000, 'timeout': 3600}).json()


def route(module, main, *fallbacks, revision=0):
    return client.put('/api/llm/routes/' + module, json={'provider_id': main['id'],
        'fallback_provider_ids': [p['id'] for p in fallbacks], 'base_revision': revision})


def book():
    b = client.post('/api/books', json={'title': '独立备用验收'}).json()
    c = client.post(f"/api/books/{b['id']}/chapters", json={'title': '第一章', 'content': '完整正文，不截断。'}).json()
    client.post(f"/api/books/{b['id']}/outline/versions", json={'content': '未来大纲', 'base_version_id': None})
    return b['id'], c['id']


def mock_http(monkeypatch, responder):
    original = httpx.AsyncClient
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append((payload, request.headers['Authorization'], str(request.url), request.extensions['timeout']['read']))
        return responder(payload)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    return calls


def text_response(text='已根据正文整理'):
    return httpx.Response(200, json={'choices': [{'message': {'content': text}}]})


def test_ordered_fallback_preserves_messages_uses_each_config_and_cools_down(monkeypatch):
    main, backup, last = provider('k3', True), provider('backup'), provider('last')
    assert route('chapter_summary', main, backup, last).status_code == 200
    calls = mock_http(monkeypatch, lambda p: httpx.Response(429 if p['model'] == 'k3' else 403, json={'error': {'message': 'limited'}},
        headers={'Retry-After': '18000'}) if p['model'] != 'last' else text_response())
    bid, cid = book()
    result = client.post(f'/api/chapters/{cid}/summary').json()
    assert result['updated'] and result['chapter']['summary_model'] == 'last'
    assert [c[0]['model'] for c in calls] == ['k3', 'backup', 'last']
    assert all(c[0]['messages'] == calls[0][0]['messages'] for c in calls)
    assert all(key == 'Bearer secret-' + p['model'] and url == f"https://{p['model']}.invalid/v1/chat/completions" and timeout == 3600
               for p, key, url, timeout in calls)
    assert client.post(f'/api/chapters/{cid}/summary').json()['updated']
    assert [c[0]['model'] for c in calls] == ['k3', 'backup', 'last', 'last']
    with SessionLocal() as db:
        assert db.get(LLMProviderCooldown, main['id']).until > datetime.utcnow() + timedelta(hours=4)
    rows = client.get('/api/llm/routes').json()
    assert 'secret-' not in json.dumps(rows)
    assert next(r for r in rows if r['module'] == 'chapter_summary')['effective_provider']['cooldown']


@pytest.mark.parametrize('status,payload', [(400, {'error': {'message': '5-hour usage limit reached'}}),
    (402, {'error': {'code': 'insufficient_quota'}}), (200, {'error': {'code': 'usage_limit_reached'}})])
def test_window_errors_and_error_envelopes_fail_over(monkeypatch, status, payload):
    main, backup = provider('k3', True), provider('backup')
    route('current_state', main, backup)
    calls = mock_http(monkeypatch, lambda p: httpx.Response(status, json=payload) if p['model'] == 'k3' else text_response())
    bid, _ = book()
    result = client.post(f'/api/books/{bid}/current-state/generate', json={'base_version_id': None})
    assert result.status_code == 200, result.text
    assert result.json()['model'] == 'backup'
    assert [c[0]['model'] for c in calls] == ['k3', 'backup']
    assert calls[0][0]['messages'] == calls[1][0]['messages']


def test_no_fallback_stops_and_never_borrows_another_modules_backup(monkeypatch):
    main, backup = provider('k3', True), provider('backup')
    gemini = provider('gemini-test')
    route('chapter_summary', main, backup)
    route('current_state', main)
    calls = mock_http(monkeypatch, lambda p: httpx.Response(429, json={'error': {'message': 'limited'}}))
    bid, _ = book()
    assert client.post(f'/api/books/{bid}/current-state/generate', json={'base_version_id': None}).status_code == 502
    job = client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 1}).json()
    with SessionLocal() as db:
        saved = db.get(WritingJob, job['id']).providers
        assert saved['chapter_writing']['id'] == gemini['id'] and saved['chapter_writing']['fallbacks'] == []
        assert saved['batch_summary']['fallbacks'] == []
    assert [c[0]['model'] for c in calls] == ['k3']


@pytest.mark.parametrize('response', [httpx.Response(400, json={'error': {'message': 'invalid temperature'}}),
    httpx.Response(401, json={'error': {'message': 'bad key'}}), httpx.Response(502, json={'error': {'message': 'gateway error'}}),
    httpx.Response(200, json={'choices': []})])
def test_other_errors_do_not_switch_or_overwrite(monkeypatch, response):
    main, backup = provider('k3', True), provider('backup')
    route('current_state', main, backup)
    calls = mock_http(monkeypatch, lambda p: response)
    bid, _ = book()
    previous = client.post(f'/api/books/{bid}/current-state/versions', json={'base_version_id': None, 'content': '已有速查'}).json()
    result = client.post(f'/api/books/{bid}/current-state/generate', json={'base_version_id': previous['id']})
    assert result.status_code == 502
    assert [c[0]['model'] for c in calls] == ['k3']
    assert client.get(f'/api/books/{bid}/current-state').json()['version']['id'] == previous['id']


def test_all_backups_exhausted_stop_without_loop_or_write(monkeypatch):
    main, backup = provider('k3', True), provider('backup')
    route('chapter_summary', main, backup)
    calls = mock_http(monkeypatch, lambda p: httpx.Response(403, json={'error': {'message': 'secret-upstream-payload'}}))
    _, cid = book()
    result = client.post(f'/api/chapters/{cid}/summary').json()
    assert not result['updated'] and '均受限' in result['message']
    assert 'secret' not in result['message'] and result['chapter']['summary'] == ''
    assert len(calls) == 2
    assert not client.post(f'/api/chapters/{cid}/summary').json()['updated']
    assert len(calls) == 2


def test_batch_chain_frozen_and_deleted_backup_stays_invalid():
    main, backup = provider('k3', True), provider('backup')
    route('batch_summary', main, backup)
    bid, _ = book()
    job = client.post(f'/api/books/{bid}/summary-jobs').json()
    assert route('batch_summary', main, revision=1).status_code == 200
    with SessionLocal() as db:
        chain = db.get(SummaryJob, job['id']).provider_config['_chain']
        assert chain['fallbacks'][0]['id'] == backup['id']
        assert 'secret' not in json.dumps(chain) and 'api_key' not in json.dumps(chain)
    route('chapter_summary', main, backup)
    client.delete(f"/api/llm/providers/{backup['id']}")
    provider('replacement')
    row = next(r for r in client.get('/api/llm/routes').json() if r['module'] == 'chapter_summary')
    assert row['fallbacks'][0]['provider_id'] is None and row['fallbacks'][0]['error']


def test_route_validation_and_legacy_clients_preserve_backups():
    main, backup = provider('k3', True), provider('backup')
    assert route('chapter_summary', main, main).status_code == 400
    assert route('chapter_summary', main, backup, backup).status_code == 400
    assert route('chapter_summary', main, backup).status_code == 200
    assert route('chapter_summary', main).status_code == 409
    legacy = client.put('/api/llm/routes/chapter_summary', json={'provider_id': main['id'], 'base_revision': 1})
    assert legacy.json()['fallbacks'][0]['provider_id'] == backup['id']
    assert route('chapter_summary', main, revision=2).json()['fallbacks'] == []


def test_writing_workflow_uses_module_backups_but_keeps_gemini_and_checkpoint(monkeypatch):
    main, summaries, states = provider('k3', True), provider('summaries'), provider('states')
    gemini = provider('gemini-test')
    route('batch_summary', main, summaries)
    route('current_state', main, states)
    fail_gemini = [True]
    def response(payload):
        if payload['model'] == 'k3' or (payload['model'] == 'gemini-test' and fail_gemini[0]):
            return httpx.Response(429, json={'error': {'message': 'usage limit'}})
        if payload['model'] == 'gemini-test':
            return text_response(json.dumps({'title': '新章标题', 'content': '新章完整正文'}))
        return text_response()
    calls = mock_http(monkeypatch, response)
    bid, _ = book()
    job = client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 1}).json()
    def run():
        with SessionLocal.begin() as db:
            db.get(WritingJob, job['id']).status = 'running'
        asyncio.run(writing_jobs.run_job(job['id']))
        return client.get(f"/api/writing-jobs/{job['id']}").json()
    failed = run()
    assert failed['status'] == 'failed' and failed['step'] == 'writing' and failed['saved_count'] == 0
    assert [c[0]['model'] for c in calls] == ['k3', 'summaries', 'states', 'gemini-test']
    assert any('summaries' in event['text'] for event in failed['failover_events'])
    assert 'secret' not in json.dumps(failed)
    fail_gemini[0] = False
    assert client.post(f"/api/writing-jobs/{job['id']}/resume").status_code == 200
    complete = run()
    assert complete['status'] == 'completed' and complete['completed_count'] == complete['saved_count'] == 1
    chapters = client.get(f'/api/books/{bid}/chapters').json()
    assert len(chapters) == 2 and chapters[-1]['summary_model'] == 'summaries'
    assert client.get(f'/api/books/{bid}/current-state').json()['version']['model'] == 'states'
    assert chapters[-1]['current_content'] == '　　新章完整正文'


def test_gemini_output_limit_explained_without_exposing_error_body():
    response = httpx.Response(400, json={'error': {'message': 'maxOutputTokens value of 1000000 but the supported range is from 1 (inclusive) to 65537 (exclusive). secret'}},
        request=httpx.Request('POST', 'https://test.invalid'))
    exc = httpx.HTTPStatusError('failed', request=response.request, response=response)
    message = generation_http_error(exc, 'gemini')
    assert '65,536' in message and '不是输入上下文' in message and 'secret' not in message
    assert fallback_reason(response) is None
    assert fallback_reason(text_response('小说角色讨论5-hour usage limit reached')) is None
    assert cooldown_deadline(httpx.Response(429, headers={'Retry-After': 'not-a-date'})) > datetime.utcnow()
