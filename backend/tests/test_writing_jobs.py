import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.database import SessionLocal
from app.main import app
from app.models import LLMModuleRoute, LLMProvider, WritingJob, CurrentStateLease
from app.routers import writing_jobs as writing

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate():
    with SessionLocal.begin() as db:
        db.execute(delete(WritingJob)); db.execute(delete(LLMModuleRoute)); db.execute(delete(LLMProvider))
    yield
    with SessionLocal.begin() as db:
        db.execute(delete(WritingJob)); db.execute(delete(LLMModuleRoute))


def setup(count=1):
    client.post('/api/llm/providers', json={'provider_name': '整理', 'base_url': 'https://organizer.invalid/v1', 'model': 'organizer', 'api_key': 'secret', 'enabled': True})
    gemini = client.post('/api/llm/providers', json={'provider_name': '写作', 'base_url': 'https://gemini.invalid/v1', 'model': 'gemini-test', 'api_key': 'secret-gemini', 'enabled': False, 'timeout': 3600}).json()
    book = client.post('/api/books', json={'title': '连续写作测试'}).json()
    bid = book['id']
    chapter = client.post(f'/api/books/{bid}/chapters', json={'title': '开端', 'content': '第一章已发生的正文'}).json()
    outline = client.post(f'/api/books/{bid}/outline/versions', json={'content': '未来大纲全文', 'base_version_id': None}).json()
    response = client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': count})
    assert response.status_code == 200, response.text
    return bid, chapter, outline, response.json(), gemini


def mock_models(monkeypatch, before=None):
    original = httpx.AsyncClient
    calls = []
    def respond(request):
        body = json.loads(request.content)
        context = json.loads(body['messages'][1]['content'])
        kind = 'writing' if body['model'] == 'gemini-test' else 'state' if 'recent_chapters' in context else 'summary'
        calls.append((kind, context))
        if before:
            result = before(kind, context)
            if result is not None:
                return result
        if kind == 'writing':
            assert request.url.host == 'gemini.invalid'
            assert request.extensions['timeout']['read'] == 3600
            assert set(context) == {'book_title', 'next_chapter_number', 'current_state', 'outline'}
            assert context['outline']['content'] == '未来大纲全文'
            text = json.dumps({'title': f"新的相遇{context['next_chapter_number']}", 'content': '完整正文\n角色开始新的行动。'}, ensure_ascii=False)
        else:
            text = '## 当前进度\n已核对正文的资料'
        return httpx.Response(200, json={'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(writing.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    return calls


def run(job):
    with SessionLocal.begin() as db:
        db.get(WritingJob, job['id']).status = 'running'
    asyncio.run(writing.run_job(job['id']))
    return client.get(f"/api/writing-jobs/{job['id']}").json()


def test_full_loop_repairs_sources_saves_separate_title_and_finishes_final_archive(monkeypatch):
    bid, _, _, job, gemini = setup(3)
    routes = client.get('/api/llm/routes').json()
    assert next(r for r in routes if r['module'] == 'chapter_writing')['effective_provider']['id'] == gemini['id']
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 100}).json()['id'] == job['id']
    calls = mock_models(monkeypatch)
    result = run(job)
    assert result['status'] == 'completed', result
    assert result['completed_count'] == result['saved_count'] == 3
    assert [kind for kind, _ in calls] == ['summary', 'state', 'writing', 'summary', 'state', 'writing', 'summary', 'state', 'writing', 'summary', 'state']
    chapters = client.get(f'/api/books/{bid}/chapters').json()
    assert len(chapters) == 4
    assert [c['chapter_number'] for c in chapters] == [1, 2, 3, 4]
    assert chapters[-1]['title'] == '新的相遇4'
    assert chapters[-1]['current_content'] == '完整正文\n角色开始新的行动。'
    assert all(c['summary'] and not c['summary_is_stale'] for c in chapters)
    assert not client.get(f'/api/books/{bid}/current-state').json()['is_stale']
    asyncio.run(writing.run_job(job['id']))
    assert len(calls) == 11
    assert client.post(f"/api/writing-jobs/{job['id']}/resume").status_code == 409
    assert 'secret' not in client.get(f"/api/writing-jobs/{job['id']}").text
    with SessionLocal() as db:
        assert 'api_key' not in json.dumps(db.get(WritingJob, job['id']).providers)


def test_stop_after_writing_and_resume_only_archives_without_duplicate_chapter(monkeypatch):
    bid, _, _, job, _ = setup()
    def stop(kind, context):
        if kind == 'writing':
            client.post(f"/api/writing-jobs/{job['id']}/cancel")
    calls = mock_models(monkeypatch, stop)
    stopped = run(job)
    assert stopped['status'] == 'cancelled' and stopped['saved_count'] == 1 and stopped['completed_count'] == 0
    assert client.post(f"/api/writing-jobs/{job['id']}/resume").status_code == 200
    complete = run(job)
    assert complete['status'] == 'completed' and complete['completed_count'] == 1
    assert sum(kind == 'writing' for kind, _ in calls) == 1
    assert len(client.get(f'/api/books/{bid}/chapters').json()) == 2


def test_summary_failure_after_save_resume_and_restart_keep_checkpoint(monkeypatch):
    bid, _, _, job, _ = setup()
    fail = {'enabled': True}
    def respond(kind, context):
        if kind == 'summary' and context['title'].startswith('新的相遇') and fail['enabled']:
            return httpx.Response(429, json={'error': {'message': 'rate limit exceeded'}})
    calls = mock_models(monkeypatch, respond)
    failed = run(job)
    assert failed['status'] == 'failed' and failed['saved_count'] == 1 and failed['completed_count'] == 0
    assert len(client.get(f'/api/books/{bid}/chapters').json()) == 2
    assert client.post(f"/api/writing-jobs/{job['id']}/resume").status_code == 200
    writing.recover_interrupted()
    assert client.get(f"/api/writing-jobs/{job['id']}").json()['status'] == 'interrupted'
    fail['enabled'] = False
    assert client.post(f"/api/writing-jobs/{job['id']}/resume").status_code == 200
    assert run(job)['status'] == 'completed'
    assert sum(kind == 'writing' for kind, _ in calls) == 1


@pytest.mark.parametrize('invalid', ['bad-json', 'empty-title', 'truncated', 'source-race'])
def test_invalid_output_or_changed_sources_never_create_chapter(monkeypatch, invalid):
    bid, chapter, outline, job, _ = setup()
    def bad(kind, context):
        if kind != 'writing':
            return
        if invalid == 'source-race':
            client.post(f'/api/books/{bid}/outline/versions', json={'content': '生成期间新的大纲', 'base_version_id': outline['id']})
            return
        text = '只有文字，没有结构' if invalid == 'bad-json' else json.dumps({'title': '', 'content': '正文'})
        return httpx.Response(200, json={'choices': [{'message': {'content': text}, 'finish_reason': 'length' if invalid == 'truncated' else 'stop'}]})
    mock_models(monkeypatch, bad)
    failed = run(job)
    assert failed['status'] == 'failed' and failed['saved_count'] == 0
    assert len(client.get(f'/api/books/{bid}/chapters').json()) == 1
    assert client.get(f"/api/chapters/{chapter['id']}").json()['current_content'] == chapter['current_content']


def test_waits_for_existing_generation_can_stop_and_validates_count(monkeypatch):
    bid, _, _, job, _ = setup()
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 2}).status_code == 422
    calls = mock_models(monkeypatch)
    with SessionLocal.begin() as db:
        db.get(WritingJob, job['id']).status = 'running'
        db.add(CurrentStateLease(book_id=bid))
    assert asyncio.run(writing.tick(job['id']))
    assert client.get(f"/api/writing-jobs/{job['id']}").json()['step'] == 'waiting'
    assert not calls
    client.post(f"/api/writing-jobs/{job['id']}/cancel")
    assert not asyncio.run(writing.tick(job['id']))
    assert client.get(f"/api/writing-jobs/{job['id']}").json()['status'] == 'cancelled'
    with SessionLocal.begin() as db:
        db.execute(delete(CurrentStateLease).where(CurrentStateLease.book_id == bid))
