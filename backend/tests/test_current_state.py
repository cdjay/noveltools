import json
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.main import app
from app.database import SessionLocal
from app.models import Chapter, LLMProvider
from app.services import content_sha256
from app.routers import current_state

client = TestClient(app)


def setup_book(count=7):
    with SessionLocal.begin() as db:
        db.execute(delete(LLMProvider))
    client.post('/api/llm/providers', json={'base_url': 'https://state.invalid/v1', 'model': 'state-model', 'api_key': 'test-only', 'enabled': True})
    book = client.post('/api/books', json={'title': '状态速查测试'}).json()
    chapters = []
    for index in range(count):
        chapter = client.post(f"/api/books/{book['id']}/chapters", json={'title': f'章节{index + 1}', 'content': f'正文{index + 1}'}).json()
        chapters.append(chapter)
        with SessionLocal.begin() as db:
            row = db.get(Chapter, chapter['id'])
            row.summary = f'梗概{index + 1}'
            row.summary_source_version_id = chapter['current_version_id']
            row.summary_content_sha256 = content_sha256(chapter['current_content'])
    return book, chapters, f"/api/books/{book['id']}/current-state"


def test_exact_llm_context_prompt_and_read_interfaces(monkeypatch):
    book, chapters, url = setup_book()
    other = client.post('/api/books', json={'title': '另一本'}).json()
    client.post(f"/api/books/{other['id']}/chapters", json={'content': '不能泄入本书'})
    prompt = '使用 Markdown 整理我的状态资料'
    assert client.put(url + '/settings', json={'prompt': prompt, 'base_revision': 0}).status_code == 200
    assert client.get(f"/api/books/{other['id']}/current-state/settings").json()['revision'] == 0
    captured = []
    original = httpx.AsyncClient
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '## 当前进度\n\n第七章结束。'}}]})
    monkeypatch.setattr(current_state.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    generated = client.post(url + '/generate', json={'base_version_id': None})
    assert generated.status_code == 200, generated.text
    state = generated.json()
    assert captured[0]['model'] == 'state-model'
    assert captured[0]['messages'][0]['content'] == prompt
    payload = json.loads(captured[0]['messages'][1]['content'])
    assert [c['content'] for c in payload['recent_chapters']] == [f'正文{i}' for i in range(3, 8)]
    assert [c['summary'] for c in payload['all_summaries']] == [f'梗概{i}' for i in range(1, 8)]
    assert client.get(url).json()['is_stale'] is False
    assert client.get(url + '/export.md').text == state['content']
    assert 'api_key' not in client.get(url).text
    assert client.get(f"/api/books/{other['id']}/current-state/versions/{state['id']}").status_code == 404
    assert client.get(url + '/context').json()['summary_count'] == 7
    assert client.put(url + '/settings', json={'prompt': 'stale', 'base_revision': 0}).status_code == 409
    # Even an older chapter's summary change invalidates the state sheet.
    with SessionLocal.begin() as db:
        db.get(Chapter, chapters[0]['id']).summary = '新的梗概'
    assert client.get(url).json()['is_stale'] is True


def test_failed_and_racing_generation_preserves_previous_state(monkeypatch):
    book, chapters, url = setup_book(2)
    saved = client.post(url + '/versions', json={'content': '作者旧速查', 'base_version_id': None}).json()
    async def race(*args):
        client.post(f"/api/chapters/{chapters[-1]['id']}/versions", json={'content': '生成途中改正文'})
        return '过时结果'
    monkeypatch.setattr(current_state, 'call_model', race)
    result = client.post(url + '/generate', json={'base_version_id': saved['id']})
    assert result.status_code == 409
    assert client.get(url).json()['version']['content'] == '作者旧速查'
    assert not client.get(url).json()['generating']
    async def failed(*args):
        raise httpx.ConnectError('test')
    monkeypatch.setattr(current_state, 'call_model', failed)
    assert client.post(url + '/generate', json={'base_version_id': saved['id']}).status_code == 502
    assert len(client.get(url + '/versions').json()) == 1
    assert client.post(url + '/versions', json={'content': '过时编辑', 'base_version_id': None}).status_code == 409
    newer = client.post(url + '/versions', json={'content': '新速查', 'base_version_id': saved['id']}).json()
    restored = client.post(url + f"/versions/{saved['id']}/restore", json={'base_version_id': newer['id']}).json()
    assert restored['content'] == '作者旧速查' and restored['version_number'] == 3
    assert client.get(url).json()['is_stale'] is True


def test_missing_summaries_and_duplicate_generation(monkeypatch):
    book, chapters, url = setup_book(1)
    with SessionLocal.begin() as db:
        db.get(Chapter, chapters[0]['id']).summary = ''
    assert client.get(url + '/context').json()['missing_summary_chapter_ids'] == [chapters[0]['id']]
    async def nested(provider, prompt, context):
        assert len(context['recent_chapters']) == 1 and context['all_summaries'] == []
        assert client.post(url + '/generate', json={'base_version_id': None}).status_code == 409
        return '## 当前进度\n资料待补充'
    monkeypatch.setattr(current_state, 'call_model', nested)
    assert client.post(url + '/generate', json={'base_version_id': None}).status_code == 200
