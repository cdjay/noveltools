import asyncio
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import delete
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import CanvasWritingJob, Chapter, ChapterVersion, ChapterPlanUse
from app.routers import canvas_writing as cw, current_state

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup_jobs():
    yield
    with SessionLocal() as db:
        db.execute(delete(CanvasWritingJob))
        db.commit()


def fixture():
    bid = client.post('/api/books', json={'title': '画布隔离测试'}).json()['id']
    client.post(f'/api/books/{bid}/outline/versions', json={'content': '第一章找到退路，不提前逃出。', 'base_version_id': None})
    r = client.post(f'/api/books/{bid}/materials/writing-rules/versions', json={'title': '写作规则', 'content': '白话写作', 'base_version_id': None})
    assert r.status_code == 200, r.text
    plan = client.post(f'/api/books/{bid}/chapter-plans', json={'title': '初见', 'content': '找到退路', 'status': 'confirmed'}).json()
    r = client.post(f"/api/books/{bid}/chapter-plans/{plan['id']}/create-chapter", json={'base_version_id': plan['version']['id']})
    assert r.status_code == 200, r.text
    bound = r.json()
    return bid, plan, bound['chapter_id'], bound['chapter_version_id']


def graph(bid):
    return {'nodes': [
        {'id': 'book-source', 'type': 'chapter-context', 'inputs': {'bookId': str(bid)}},
        {'id': 'materials-source', 'type': 'material-context', 'inputs': {'bookId': str(bid), 'materialKeys': ['writing-rules']}},
        {'id': 'final-prose', 'type': 'llm', 'inputs': {'model': 'gemini-test'}},
    ], 'edges': []}


def fake_canvas(monkeypatch, bid, submitted):
    async def request(method, path, **kw):
        if method == 'GET' and path.startswith('/workflows/'):
            return httpx.Response(200, json=graph(bid), headers={'etag': '"'+'a'*64+'"'})
        if method == 'POST' and path == '/runs':
            submitted.append(kw['json'])
            return httpx.Response(201, json={'id': 'canvas-test-run'})
        if method == 'GET' and path.startswith('/runs/'):
            return httpx.Response(200, json={'id': 'canvas-test-run', 'status': 'SUCCEEDED', 'nodes': [
                {'nodeId': 'final-prose', 'status': 'SUCCEEDED', 'outputs': {'text': '第1章 初见\n他推开门。\n\n\n风从石缝里吹来。'}}]})
        raise AssertionError((method, path))
    monkeypatch.setattr(cw, 'canvas', request)


def test_create_blank_is_explicit_atomic_and_duplicate_safe():
    bid, p, cid, vid = fixture()
    body = client.get(f'/api/chapters/{cid}').json()
    assert body['title'] == '初见' and body['current_content'] == ''
    url = f"/api/books/{bid}/chapter-plans/{p['id']}/create-chapter"
    assert client.post(url, json={'base_version_id': p['version']['id']}).status_code == 409
    other = client.post(f'/api/books/{bid}/chapter-plans', json={'title': '初见', 'content': '另一个目标'}).json()
    assert client.post(f"/api/books/{bid}/chapter-plans/{other['id']}/create-chapter", json={'base_version_id': other['version']['id']}).status_code == 409
    assert len(client.get(f'/api/books/{bid}/chapters').json()) == 1
    with SessionLocal() as db:
        assert current_state.snapshot(db, bid)[1]['recent_chapters'] == []


def test_canvas_round_trip_saves_selected_chapter_and_version(monkeypatch):
    bid, p, cid, vid = fixture()
    submitted = []; fake_canvas(monkeypatch, bid, submitted)
    r = client.post(f'/api/chapters/{cid}/workflow-writing', json={'base_version_id': vid})
    assert r.status_code == 200, r.text
    jid = r.json()['id']
    assert client.post(f'/api/chapters/{cid}/workflow-writing', json={'base_version_id': vid}).status_code == 409
    asyncio.run(cw.tick(jid))
    assert len(submitted) == 1
    assert submitted[0]['expectedVersion'] == '"'+'a'*64+'"'
    assert submitted[0]['inputOverrides']['book-source']['sourceRequestId'] == jid
    frozen = client.get(f'/api/workflow-writing/jobs/{jid}/source').json()
    assert frozen['bookOutputs']['previousText'] == '无前文（第一章）'
    assert '找到退路' in frozen['bookOutputs']['planText']
    assert frozen['materials'][0]['content'] == '白话写作'
    asyncio.run(cw.tick(jid))
    body = client.get(f'/api/chapters/{cid}').json()
    assert body['current_content'] == '　　他推开门。\n\n　　风从石缝里吹来。'
    assert body['title'] == '初见' and body['current_version_id'] != vid
    assert len(client.get(f'/api/books/{bid}/chapters').json()) == 1
    with SessionLocal() as db:
        assert db.get(ChapterVersion, vid).content_text == ''
        assert db.query(ChapterPlanUse).filter_by(chapter_version_id=body['current_version_id']).count() == 1
    async def summary(*args, **kw): raise AssertionError('保存正文后不得生成梗概')
    async def state(*args, **kw): raise AssertionError('保存正文后不得生成速查')
    monkeypatch.setattr(cw, 'update_summary', summary); monkeypatch.setattr(current_state, 'generate_state', state)
    assert client.get(f'/api/workflow-writing/jobs/{jid}').json()['status'] == 'completed'
    asyncio.run(cw.tick(jid))
    assert client.get(f'/api/chapters/{cid}').json()['summary'] == ''


def test_changed_plan_preserves_raw_output_without_overwrite(monkeypatch):
    bid, p, cid, vid = fixture(); fake_canvas(monkeypatch, bid, [])
    jid = client.post(f'/api/chapters/{cid}/workflow-writing', json={'base_version_id': vid}).json()['id']
    asyncio.run(cw.tick(jid))
    client.post(f"/api/books/{bid}/chapter-plans/{p['id']}/versions", json={'base_version_id': p['version']['id'], 'title': '改纲', 'content': '改为守门', 'status': 'confirmed'})
    import pytest
    with pytest.raises(Exception, match='来源|变化'):
        asyncio.run(cw.tick(jid))
    assert client.get(f'/api/chapters/{cid}').json()['current_version_id'] == vid
    assert '他推开门' in client.get(f'/api/workflow-writing/jobs/{jid}/output').json()['text']


def test_missing_earlier_body_and_foreign_materials_block_before_run(monkeypatch):
    bid, p, cid, vid = fixture(); fake_canvas(monkeypatch, bid, [])
    second = client.post(f'/api/books/{bid}/chapter-plans', json={'title': '第二章', 'content': '接续', 'status': 'confirmed'}).json()
    blank = client.post(f"/api/books/{bid}/chapter-plans/{second['id']}/create-chapter", json={'base_version_id': second['version']['id']}).json()
    r = client.post(f"/api/chapters/{blank['chapter_id']}/workflow-writing", json={'base_version_id': blank['chapter_version_id']})
    assert r.status_code == 409 and '空白' in r.text
    import pytest
    with pytest.raises(Exception, match='书籍不同'):
        cw.validate_graph(graph(bid + 1), bid)
    g = graph(bid); g['nodes'][-1]['inputs']['model'] = 'k3'
    with pytest.raises(Exception, match='Gemini'):
        cw.validate_graph(g, bid)


def test_context_never_includes_future_body(monkeypatch):
    bid, p, cid, vid = fixture()
    client.post(f'/api/books/{bid}/chapters', json={'title': '未来章', 'content': '此事尚未发生'})
    with SessionLocal() as db:
        j = CanvasWritingJob(id='f'*32, book_id=bid, chapter_id=cid, base_version_id=vid, config={'material_keys':['writing-rules']})
        _, context, _, _, _ = cw.sources(db, j)
        assert context['recent_chapters'] == [] and context['all_summaries'] == []


def test_preflight_updates_edited_prior_memory_then_reuses_state(monkeypatch):
    from app.services import content_sha256
    bid, _, first, first_version = fixture()
    settings = client.get(f'/api/books/{bid}/current-state/settings').json()
    assert client.put(f'/api/books/{bid}/current-state/settings', json={
        'prompt': settings['prompt'], 'base_revision': settings['revision'],
        'writing_rule_keys': ['writing-rules']}).status_code == 200
    def edit_first(text, base):
        r = client.post(f'/api/chapters/{first}/versions', json={
            'base_version_id': base, 'title': '初见', 'content': text, 'source': 'manual'})
        assert r.status_code == 200, r.text
        return r.json()['id']
    first_version = edit_first('作者已经修订的第一章', first_version)
    plan = client.post(f'/api/books/{bid}/chapter-plans', json={
        'title': '再会', 'content': '第二章接续', 'status': 'confirmed'}).json()
    second = client.post(f"/api/books/{bid}/chapter-plans/{plan['id']}/create-chapter", json={
        'base_version_id': plan['version']['id']}).json()
    calls, submitted = [], []
    fake_canvas(monkeypatch, bid, submitted)
    async def summary(cid, db):
        assert cid == first  # Never summarize the target draft.
        c = db.get(Chapter, cid)
        text = db.get(ChapterVersion, c.current_version_id).content_text
        calls.append(('summary', text))
        c.summary = '已整理：' + text
        c.summary_source_version_id = c.current_version_id
        c.summary_content_sha256 = content_sha256(text)
        db.commit()
        return SimpleNamespace(updated=True)
    async def model(provider, prompt, context):
        assert len(context['all_summaries']) == 1
        assert context['all_summaries'][0]['is_stale'] is False
        text = context['recent_chapters'][0]['content']
        calls.append(('state', text))
        return '开始前状态：' + text
    async def fallback(chain, module, call):
        provider = SimpleNamespace(model='aux-test')
        return await call(provider), provider
    monkeypatch.setattr(cw, 'update_summary', summary)
    monkeypatch.setattr(cw, 'snapshot_chain', lambda *args: [])
    monkeypatch.setattr(cw, 'run_with_fallback', fallback)
    monkeypatch.setattr(current_state, 'call_model', model)
    def begin():
        r = client.post(f"/api/chapters/{second['chapter_id']}/workflow-writing", json={
            'base_version_id': second['chapter_version_id']})
        assert r.status_code == 200, r.text
        return r.json()['id']
    jid = begin()
    asyncio.run(cw.tick(jid))
    assert [c[0] for c in calls] == ['summary'] and not submitted
    asyncio.run(cw.tick(jid))
    assert [c[0] for c in calls] == ['summary', 'state'] and len(submitted) == 1
    assert client.get(f'/api/books/{bid}/current-state').json()['is_stale'] is False
    with SessionLocal() as db:
        db.get(CanvasWritingJob, jid).status = 'cancelled'; db.commit()
    jid = begin()
    asyncio.run(cw.tick(jid))
    assert len(calls) == 2 and len(submitted) == 2  # No needless model calls.
    with SessionLocal() as db:
        db.get(CanvasWritingJob, jid).status = 'cancelled'; db.commit()
    first_version = edit_first('再次修改后的第一章', first_version)
    jid = begin()
    asyncio.run(cw.tick(jid)); asyncio.run(cw.tick(jid))
    assert calls[-2:] == [('summary', '再次修改后的第一章'), ('state', '再次修改后的第一章')]
    frozen = client.get(f'/api/workflow-writing/jobs/{jid}/source').json()
    assert frozen['bookOutputs']['stateText'] == '开始前状态：再次修改后的第一章'
    count = len(calls)
    asyncio.run(cw.tick(jid)); asyncio.run(cw.tick(jid))
    assert len(calls) == count  # Saving prose never calls either memory model.
    assert client.get(f'/api/workflow-writing/jobs/{jid}').json()['status'] == 'completed'
    assert client.get(f"/api/chapters/{second['chapter_id']}").json()['summary'] == ''


def test_legacy_archiving_finishes_without_model_calls(monkeypatch):
    bid, _, cid, vid = fixture()
    with SessionLocal() as db:
        db.add(CanvasWritingJob(id='e'*32, book_id=bid, chapter_id=cid, base_version_id=vid,
            saved_version_id=vid, status='archiving', config={}, source={}, manifest={},
            workflow_id='legacy', workflow_etag='legacy', raw_output=''))
        db.commit()
    async def forbidden(*args, **kwargs): raise AssertionError('旧任务也不得继续生成记忆')
    monkeypatch.setattr(cw, 'update_summary', forbidden)
    monkeypatch.setattr(current_state, 'generate_state', forbidden)
    asyncio.run(cw.tick('e'*32))
    assert client.get('/api/workflow-writing/jobs/' + 'e'*32).json()['status'] == 'completed'


def test_custom_workflow_settings_node_ids_and_output_are_used(monkeypatch):
    bid, _, cid, vid = fixture()
    g = graph(bid)
    g['nodes'][0]['id'] = 'my-book'
    g['nodes'][1]['id'] = 'my-materials'
    g['nodes'][2]['id'] = 'my-gemini'
    g['nodes'].append({'id': 'my-output', 'type': 'text', 'label': '排版结果', 'inputs': {}})
    submitted = []
    async def remote(method, path, **kw):
        if path == '/workflows': return httpx.Response(200, json={'items':[{'id':'custom','name':'我的工作流'}]})
        if method == 'GET' and path.startswith('/workflows/'):
            return httpx.Response(200, json=g, headers={'etag':'"'+'b'*64+'"'})
        if path == '/runs':
            submitted.append(kw['json']); return httpx.Response(201, json={'id':'custom-run'})
        if path == '/runs/custom-run':
            return httpx.Response(200, json={'status':'SUCCEEDED','nodes':[
                {'nodeId':'my-gemini','status':'SUCCEEDED','outputs':{'text':'这不是选中的结果'}},
                {'nodeId':'my-output','status':'SUCCEEDED','outputs':{'text':'第1章 自选标题\n这才是选中的正文。'}}]})
        raise AssertionError(path)
    monkeypatch.setattr(cw, 'canvas', remote)
    assert client.get('/api/canvas-writing/workflows').json()[0]['name'] == '我的工作流'
    assert any(x['node_id']=='my-output' for x in client.get('/api/canvas-writing/workflows/custom/outputs').json())
    url = f'/api/books/{bid}/workflow-writing/settings'
    config = {'workflow_id':'custom','output_node_id':'my-output','output_port':'text','base_revision':0}
    assert client.put(url,json=config).status_code == 200
    assert client.put(url,json=config).status_code == 409
    assert client.get(url).json()['workflow_id'] == 'custom'
    jid=client.post(f'/api/chapters/{cid}/workflow-writing',json={'base_version_id':vid}).json()['id']
    asyncio.run(cw.tick(jid)); asyncio.run(cw.tick(jid))
    assert submitted[0]['workflowId'] == 'custom'
    assert set(submitted[0]['inputOverrides']) == {'my-book','my-materials'}
    assert client.get(f'/api/chapters/{cid}').json()['current_content'].strip() == '这才是选中的正文。'
    assert client.get(f'/api/workflow-writing/jobs/{jid}').json()['status']=='completed'


def test_standalone_context_does_not_leak_later_state(monkeypatch):
    from app.models import CurrentStateVersion
    bid, _, cid, vid = fixture()
    with SessionLocal() as db:
        db.add(CurrentStateVersion(book_id=bid,version_number=1,content='未来已经脱逃',source_manifest={'chapters':[{'chapter_number':9}]}))
        db.commit()
    result=client.get(f'/api/books/{bid}/chapters/1/writing-context').json()
    assert '找到退路' in result['planText']
    assert '开篇基准' in result['stateText'] and '未来已经脱逃' not in result['stateText']
