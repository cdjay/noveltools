import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import WritingJob, ChapterPlan, ChapterPlanUse, ChapterPlanVersion
from app.routers import writing_jobs as writing

client = TestClient(app)


def new_book():
    return client.post('/api/books', json={'title': '独立章纲测试'}).json()['id']


def base(bid):
    return f'/api/books/{bid}/chapter-plans'


def create(bid, title='初见', status='draft'):
    r = client.post(base(bid), json={'title': title, 'volume': '第一卷', 'content': '目标：找到退路\n冲突：守卫封门', 'status': status})
    assert r.status_code == 200, r.text
    return r.json()


def save(bid, p, **fields):
    return client.post(f"{base(bid)}/{p['id']}/versions", json={
        **{k: p['version'][k] for k in ('title', 'volume', 'content', 'status')},
        'base_version_id': p['version']['id'], **fields})


def test_plans_do_not_create_body_and_versions_cannot_overwrite():
    bid = new_book(); p = create(bid)
    assert client.get(f'/api/books/{bid}').json()['chapter_count'] == 0
    updated = save(bid, p, title='改名', status='confirmed').json()
    assert updated['id'] == p['id'] and updated['version']['version_number'] == 2
    assert save(bid, p, content='过期修改').status_code == 409
    restored = client.post(f"{base(bid)}/{p['id']}/versions/{p['version']['id']}/restore",
        json={'base_version_id': updated['version']['id']}).json()
    assert restored['version']['version_number'] == 3 and restored['version']['status'] == 'draft'
    assert restored['version']['title'] == '初见'
    other = new_book()
    assert client.get(f"{base(other)}/{p['id']}").status_code == 404


def test_binding_survives_reorder_rename_and_changes_need_review():
    bid = new_book(); p = create(bid); second = create(bid, '后续')
    chapter = client.post(f'/api/books/{bid}/chapters', json={'title': '正文原名', 'content': '已写正文'}).json()
    endpoint = f"{base(bid)}/{p['id']}"
    bound = client.post(endpoint+'/binding', json={'base_version_id': p['version']['id'], 'previous_chapter_id': None, 'chapter_id': chapter['id']})
    assert bound.status_code == 200 and bound.json()['needs_review']
    checked = client.post(endpoint+'/review', json={'base_version_id': p['version']['id'], 'chapter_version_id': chapter['current_version_id']})
    assert checked.status_code == 200 and not checked.json()['needs_review']
    reorder = {'plan_ids': [second['id'], p['id']], 'previous_order': [p['id'], second['id']]}
    assert client.post(base(bid)+'/reorder', json=reorder).status_code == 200
    assert client.post(base(bid)+'/reorder', json=reorder).status_code == 409
    latest = save(bid, p, title='章纲新名').json()
    assert latest['chapter_id'] == chapter['id'] and latest['needs_review']
    duplicate = client.post(f"{base(bid)}/{second['id']}/binding", json={'base_version_id': second['version']['id'], 'previous_chapter_id': None, 'chapter_id': chapter['id']})
    assert duplicate.status_code == 409
    other = new_book()
    foreign = client.post(f'/api/books/{other}/chapters', json={'title': '他书', 'content': '正文'}).json()
    assert client.post(endpoint+'/binding', json={'base_version_id': latest['version']['id'], 'previous_chapter_id': chapter['id'], 'chapter_id': foreign['id']}).status_code == 400
    client.delete(f"/api/chapters/{chapter['id']}?confirm=true")
    assert client.get(endpoint).json()['chapter_id'] is None


def test_outline_move_is_atomic_preserves_history_and_shared_notes():
    bid = new_book()
    text = '# 全书\n主线不可丢\n\n# 前十章\n作者说明\n\n### 第 1 章：初见\n目标一\n\n### 第 2 章：离开\n目标二\n\n## 执行勘误与待落实项\n不能听出杀意。'
    v = client.post(f'/api/books/{bid}/outline/versions', json={'content': text, 'base_version_id': None}).json()
    payload = {'base_version_id': v['id'], 'section_heading': '# 前十章'}
    r = client.post(base(bid)+'/import-outline/move', json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data['plans']) == 2 and all(p['version']['status'] == 'draft' for p in data['plans'])
    assert '不能听出杀意' in data['guidance']['content']
    history = client.get(f'/api/books/{bid}/outline/versions').json()
    assert history[1]['content'] == text and '主线不可丢' in history[0]['content']
    assert '目标一' not in history[0]['content']
    assert client.post(base(bid)+'/import-outline/move', json=payload).status_code == 409
    assert client.get(f'/api/books/{bid}').json()['chapter_count'] == 0
    # Abort after a partial in-transaction parse: no plans/material/outline survive.
    bad = new_book()
    v = client.post(f'/api/books/{bad}/outline/versions', json={'content': text.replace('目标二', ''), 'base_version_id': None}).json()
    assert client.post(base(bad)+'/import-outline/move', json={**payload, 'base_version_id': v['id']}).status_code == 400
    assert client.get(base(bad)).json() == {'plans': [], 'guidance': None}
    assert len(client.get(f'/api/books/{bad}/outline/versions').json()) == 1


def configure():
    from sqlalchemy import delete
    from app.models import LLMProvider, LLMModuleRoute
    with SessionLocal.begin() as db:
        db.execute(delete(LLMModuleRoute)); db.execute(delete(LLMProvider))
    client.post('/api/llm/providers', json={'provider_name': '整理', 'model': 'organizer', 'base_url': 'https://organizer.invalid/v1', 'api_key': 'secret', 'enabled': True})
    client.post('/api/llm/providers', json={'provider_name': '写作', 'model': 'gemini-test', 'base_url': 'https://gemini.invalid/v1', 'api_key': 'secret', 'enabled': False})


def start_book():
    configure(); bid = new_book()
    client.post(f'/api/books/{bid}/outline/versions', json={'content': '只按本章计划写作', 'base_version_id': None})
    return bid


def mock(monkeypatch, callback=None):
    original = httpx.AsyncClient
    calls = []
    def respond(request):
        body = json.loads(request.content); ctx = json.loads(body['messages'][1]['content'])
        calls.append(ctx)
        is_writing = body['model'] == 'gemini-test'
        if is_writing and callback:
            callback(ctx)
        text = '第1章 初见\n新的完整正文。' if is_writing else '## 状态\n来源已核对。'
        return httpx.Response(200, json={'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(writing.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    return calls


def run(job):
    with SessionLocal.begin() as db:
        db.get(WritingJob, job['id']).status = 'running'
    asyncio.run(writing.run_job(job['id']))
    return client.get(f"/api/writing-jobs/{job['id']}").json()


def test_confirmed_plan_can_generate_first_body_and_pin_source(monkeypatch):
    bid = start_book(); p = create(bid)
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 1}).status_code == 409
    p = save(bid, p, status='confirmed').json()
    calls = mock(monkeypatch)
    job = client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 1}).json()
    done = run(job)
    assert done['status'] == 'completed', done
    assert calls[0]['chapter_plan']['plan_id'] == p['id']
    assert calls[0]['chapter_plan']['id'] == p['version']['id']
    assert calls[0]['recent_chapters'] == []
    linked = client.get(f"{base(bid)}/{p['id']}").json()
    assert linked['chapter_id'] and linked['uses'][0]['plan_version_id'] == p['version']['id']
    assert linked['needs_review']  # Generation alone is not a semantic audit.
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 1}).status_code == 409
    # Archive includes stable associations, immutable source versions, and old versions.
    save(bid, linked, content='修订章纲')
    archive = client.get(f'/api/books/{bid}/backup')
    assert archive.status_code == 200
    restored = client.post('/api/backups/restore', files={'file': ('backup.json', archive.content)})
    assert restored.status_code == 200, restored.text
    copied = client.get(base(restored.json()['id'])).json()['plans'][0]
    assert copied['id'] != p['id'] and copied['chapter_id'] != linked['chapter_id']
    assert copied['uses'][0]['plan_version_id'] != p['version']['id']
    assert len(client.get(f"{base(restored.json()['id'])}/{copied['id']}/versions").json()) == 3


def test_changed_plan_during_generation_never_saves_stale_body(monkeypatch):
    bid = start_book(); p = create(bid, status='confirmed')
    mock(monkeypatch, lambda ctx: save(bid, p, content='生成期间已修改'))
    job = client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 1}).json()
    assert run(job)['status'] == 'failed'
    assert client.get(f'/api/books/{bid}').json()['chapter_count'] == 0


def test_draft_is_not_skipped_and_batch_requires_contiguous_confirmations():
    bid = start_book(); create(bid, status='confirmed'); create(bid, title='草稿'); create(bid, title='第三', status='confirmed')
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 3}).status_code == 409


def test_review_detects_later_body_edit_without_modifying_plan():
    bid = new_book(); p = create(bid, status='confirmed')
    chapter = client.post(f'/api/books/{bid}/chapters', json={'title': '正文', 'content': '旧稿'}).json()
    path = f"{base(bid)}/{p['id']}"
    client.post(path+'/binding', json={'base_version_id': p['version']['id'], 'chapter_id': chapter['id'], 'previous_chapter_id': None})
    payload = {'base_version_id': p['version']['id'], 'chapter_version_id': chapter['current_version_id']}
    assert not client.post(path+'/review', json=payload).json()['needs_review']
    client.post(f"/api/chapters/{chapter['id']}/versions", json={'content': '新正文', 'base_version_id': chapter['current_version_id']})
    after = client.get(path).json()
    assert after['needs_review'] and after['version'] == p['version']
    assert client.post(path+'/review', json=payload).status_code == 409
