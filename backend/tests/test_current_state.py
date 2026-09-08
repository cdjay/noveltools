import json
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.main import app
from app.database import SessionLocal
from app.models import Chapter, LLMProvider
from app.services import content_sha256
from app.routers import current_state

client = TestClient(app)


@pytest.mark.parametrize('timeout', [None, 3600])
def test_long_generation_timeout_is_used_by_state_and_summary(monkeypatch, timeout):
    _, chapters, url = setup_book(1)
    config = {'base_url': 'https://long-task.invalid/v1', 'model': 'long-task', 'api_key': 'test-only', 'enabled': True}
    if timeout is not None:
        config['timeout'] = timeout
    provider = client.post('/api/llm/providers', json=config).json()
    expected = timeout or 1800
    assert provider['timeout'] == expected
    original = httpx.AsyncClient
    timeouts = []
    def respond(request):
        timeouts.append(request.extensions['timeout'])
        return httpx.Response(200, json={'choices': [{'message': {'content': '当前进度：待续'}}]})
    monkeypatch.setattr(current_state.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    assert client.post(url + '/generate', json={'base_version_id': None}).status_code == 200
    assert client.post(f"/api/chapters/{chapters[0]['id']}/summary").json()['updated']
    assert len(timeouts) == 2
    assert all(t['read'] == expected and t['write'] == expected and t['connect'] == 30 for t in timeouts)


def test_rate_limit_explains_wait_and_preserves_state(monkeypatch):
    _, _, url = setup_book(1)
    saved = client.post(url + '/versions', json={'content': '已有速查', 'base_version_id': None}).json()
    original = httpx.AsyncClient
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '30'}, json={
            'error': {'type': 'rate_limit_exceeded', 'message': 'Too many requests'}})
    monkeypatch.setattr(current_state.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    result = client.post(url + '/generate', json={'base_version_id': saved['id']})
    assert '限流' in result.json()['detail'], result.text
    assert '30' in result.json()['detail']
    assert len(calls) == 1
    assert client.get(url).json()['version']['id'] == saved['id']
    assert not client.get(url).json()['generating']


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
    outline_url = f"/api/books/{book['id']}/outline/versions"
    first = client.post(outline_url, json={'content': '旧大纲', 'base_version_id': None}).json()
    outline_text = '# 世界规则与未来规划\n' + '未来计划并非已发生事实。' * 5000
    client.post(outline_url, json={'content': outline_text, 'base_version_id': first['id']})
    other = client.post('/api/books', json={'title': '另一本'}).json()
    client.post(f"/api/books/{other['id']}/chapters", json={'content': '不能泄入本书'})
    client.post(f"/api/books/{other['id']}/outline/versions", json={'content': '别书大纲不可泄入', 'base_version_id': None})
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
    assert captured[0]['messages'][0]['content'] == prompt + '\n\n' + current_state.OUTLINE_GUIDANCE
    assert state['prompt'] == captured[0]['messages'][0]['content']
    assert client.get(url + '/settings').json()['prompt'] == prompt
    payload = json.loads(captured[0]['messages'][1]['content'])
    assert payload['outline'] == {'version_number': 2, 'content': outline_text}
    assert state['source_manifest']['outline'] == {'version_number': 2, 'content_sha256': content_sha256(outline_text)}
    context = client.get(url + '/context').json()
    assert context['outline'] == {'version_number': 2, 'characters': len(outline_text), 'is_empty': False}
    assert context['input_characters'] == len(json.dumps(payload, ensure_ascii=False))
    assert [c['content'] for c in payload['recent_chapters']] == [f'正文{i}' for i in range(3, 8)]
    assert [c['summary'] for c in payload['all_summaries']] == [f'梗概{i}' for i in range(1, 8)]
    assert client.get(url).json()['is_stale'] is False
    assert client.get(url + '/export.md').text == state['content']
    assert 'api_key' not in client.get(url).text
    assert client.get(f"/api/books/{other['id']}/current-state/versions/{state['id']}").status_code == 404
    assert client.get(url + '/context').json()['summary_count'] == 7
    assert client.get(url + '/context').json()['max_output_tokens'] == captured[0]['max_tokens']
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
        assert context['outline'] is None
        assert client.post(url + '/generate', json={'base_version_id': None}).status_code == 409
        return '## 当前进度\n资料待补充'
    monkeypatch.setattr(current_state, 'call_model', nested)
    assert client.post(url + '/generate', json={'base_version_id': None}).status_code == 200


def test_backup_roundtrip_and_legacy_archive():
    book, chapters, url = setup_book(2)
    client.post(f"/api/books/{book['id']}/outline/versions", json={'content': '备份大纲', 'base_version_id': None})
    client.put(url + '/settings', json={'prompt': '本书专用提示词', 'base_revision': 0})
    client.post(url + '/versions', json={'content': '## 当前进度\n待续', 'base_version_id': None})
    backup = client.get(f"/api/books/{book['id']}/backup")
    assert backup.json()['schema_version'] == 3
    copied = client.post('/api/backups/restore', files={'file': ('state.json', backup.content)}).json()
    copied_url = f"/api/books/{copied['id']}/current-state"
    state = client.get(copied_url).json()
    assert state['version']['content'] == '## 当前进度\n待续'
    assert state['is_stale'] is False
    assert state['version']['source_manifest']['outline'] == {'version_number': 1, 'content_sha256': content_sha256('备份大纲')}
    assert state['version']['source_manifest']['body_chapter_ids'] != [c['id'] for c in chapters]
    assert client.get(copied_url + '/settings').json()['prompt'] == '本书专用提示词'
    old = backup.json()
    old['schema_version'] = 1
    del old['current_state_versions'], old['current_state_prompt']
    restored = client.post('/api/backups/restore', files={'file': ('old.json', json.dumps(old).encode())})
    assert restored.status_code == 200
    assert client.get(f"/api/books/{restored.json()['id']}/current-state").json()['version'] is None


def test_empty_truncated_and_summary_race_do_not_save(monkeypatch):
    book, chapters, url = setup_book(1)
    original = httpx.AsyncClient
    for output in ({'choices': [{'message': {'content': ''}}]},
                   {'choices': [{'message': {'content': '未完稿'}, 'finish_reason': 'length'}]}):
        with monkeypatch.context() as patch:
            patch.setattr(current_state.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=output))))
            assert client.post(url + '/generate', json={'base_version_id': None}).status_code == 502
            assert client.get(url).json()['version'] is None
    async def race(provider, prompt, context):
        with SessionLocal.begin() as db:
            db.get(Chapter, chapters[0]['id']).summary = '生成期间更新梗概'
        return '旧梗概对应的速查'
    monkeypatch.setattr(current_state, 'call_model', race)
    assert client.post(url + '/generate', json={'base_version_id': None}).status_code == 409
    assert client.get(url).json()['version'] is None


def test_outline_changes_invalidate_state_and_reject_inflight_result(monkeypatch):
    book, _, url = setup_book(1)
    outline_url = f"/api/books/{book['id']}/outline/versions"
    saved = client.post(url + '/versions', json={'content': '原速查', 'base_version_id': None}).json()
    assert not client.get(url).json()['is_stale']
    outline = client.post(outline_url, json={'content': '未来计划', 'base_version_id': None}).json()
    assert client.get(url).json()['is_stale']
    async def race(provider, prompt, context):
        assert context['outline']['content'] == '未来计划'
        assert client.post(outline_url, json={'content': '', 'base_version_id': outline['id']}).status_code == 200
        return '基于旧大纲的结果'
    monkeypatch.setattr(current_state, 'call_model', race)
    result = client.post(url + '/generate', json={'base_version_id': saved['id']})
    assert result.status_code == 409 and '大纲' in result.json()['detail']
    assert client.get(url).json()['version']['id'] == saved['id']
    assert not client.get(url).json()['generating']
    assert client.get(url + '/context').json()['outline'] == {'version_number': 2, 'characters': 0, 'is_empty': True}
    async def blank(provider, prompt, context):
        assert context['outline'] == {'version_number': 2, 'content': ''}
        return '空大纲，按正文整理'
    monkeypatch.setattr(current_state, 'call_model', blank)
    assert client.post(url + '/generate', json={'base_version_id': saved['id']}).status_code == 200
    assert not client.get(url).json()['is_stale']


def test_selected_rules_provenance_isolated_validated_and_roundtrips(monkeypatch):
    book, _, url = setup_book(2)
    bid = book['id']
    rule = client.post(f'/api/books/{bid}/materials', json={'title': 'AI写作禁令skill', 'content': '规则全文' * 2000}).json()
    key = rule['material_key']
    other = client.post('/api/books', json={'title': '另一本规则'}).json()
    alien = client.post(f"/api/books/{other['id']}/materials", json={'title': '不能送出', 'content': '别书规则'}).json()
    assert client.put(url + '/settings', json={'prompt': '只记事实', 'base_revision': 0, 'writing_rule_keys': [alien['material_key']]}).status_code == 400
    assert client.put(url + '/settings', json={'prompt': '只记事实', 'base_revision': 0, 'writing_rule_keys': [key, key]}).status_code == 400
    assert client.put(url + '/settings', json={'prompt': '只记事实', 'base_revision': 0, 'writing_rule_keys': [key]}).status_code == 200
    captured = []
    original = httpx.AsyncClient
    def respond(request):
        captured.append(json.loads(json.loads(request.content)['messages'][1]['content']))
        return httpx.Response(200, json={'choices': [{'message': {'content': '## 当前进度\n正文事实'}}]})
    monkeypatch.setattr(current_state.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    generated = client.post(url + '/generate', json={'base_version_id': None})
    assert generated.status_code == 200, generated.text
    assert captured[0]['writing_rules'] == [{'material_key': key, 'title': rule['title'], 'version_number': 1, 'content': '规则全文' * 2000}]
    assert not client.get(url).json()['is_stale']
    backup = client.get(f'/api/books/{bid}/backup')
    restored = client.post('/api/backups/restore', files={'file': ('book.json', backup.content, 'application/json')})
    assert restored.status_code == 200, restored.text
    restored_url = f"/api/books/{restored.json()['id']}/current-state"
    assert client.get(restored_url + '/settings').json()['writing_rule_keys'] == [key]
    assert not client.get(restored_url).json()['is_stale']
    changed = client.post(f'/api/books/{bid}/materials/{key}/versions', json={'title': '改名仍选中', 'content': '修改规则', 'base_version_id': rule['id']})
    assert changed.status_code == 200, changed.text
    assert client.get(url).json()['is_stale']
    # Old callers that only edit the prompt preserve selection.
    settings = client.put(url + '/settings', json={'prompt': '修改提示词', 'base_revision': 1}).json()
    assert settings['writing_rule_keys'] == [key]


def test_prompt_edit_during_state_request_keeps_old_state(monkeypatch):
    _, _, url = setup_book(1)
    saved = client.post(url + '/versions', json={'base_version_id': None, 'content': '旧稿'}).json()
    original = httpx.AsyncClient
    def respond(request):
        assert client.put(url + '/settings', json={'prompt': '新要求', 'base_revision': 0}).status_code == 200
        return httpx.Response(200, json={'choices': [{'message': {'content': '旧要求生成的稿件'}}]})
    monkeypatch.setattr(current_state.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    response = client.post(url + '/generate', json={'base_version_id': saved['id']})
    assert response.status_code == 409
    assert client.get(url).json()['version']['id'] == saved['id']
