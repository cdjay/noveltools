import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.main import app
from app.database import SessionLocal
from app.models import CanvasWritingJob
from app.routers import canvas_writing as cw
from app.routers import chapter_review as cr

client = TestClient(app)


def test_revision_preserves_model_paragraph_spacing_without_global_reformatting():
    body = '　　第一段。\n\n\n　另一种原有缩进。\n\n第三段。  '
    assert cr.revision_text('第2章 当前章\n\n' + body) == ('当前章', body)
    assert cr.revision_text('```text\n第2章 当前章\n\n' + body + '\n```') == ('当前章', body)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with SessionLocal() as db:
        db.execute(delete(CanvasWritingJob)); db.commit()


def book_chapters():
    bid = client.post('/api/books', json={'title': '审核隔离书'}).json()['id']
    client.post(f'/api/books/{bid}/chapters', json={'title': '前文', 'content': '这是第一章的前文。'})
    c = client.post(f'/api/books/{bid}/chapters', json={'title': '当前章', 'content': '　　这是第二章的原稿。'}).json()
    client.post(f'/api/books/{bid}/chapters', json={'title': '未来', 'content': '这是第三章，不可作为本次原稿。'})
    return bid, c


def remote(monkeypatch, bid, outputs=None, assembled=False):
    submitted = []
    outputs = outputs or {}
    async def canvas(method, path, **kw):
        if path.startswith('/workflows/'):
            revision = path.endswith(cr.settings.revision_workflow_id)
            nodes = [{'id': 'book', 'type': 'chapter-context', 'inputs': {'bookId': str(bid),
                'bodyStart': 1, 'bodyEnd': 3, 'previousCount': 1, 'summaryMode': 'none', 'requireReader': False,
                'includeOutline': True, 'includeChapterPlans': True}}]
            if revision:
                nodes += [{'id': key, 'type': 'text', 'inputs': {'text': ''}} for key in ['editorial-opinions', 'accepted-decisions']]
            nodes += [{'id': 'revised-prose' if revision else 'final-review-llm', 'type': 'llm', 'inputs': {'model': 'test-only'}}]
            if revision and assembled:
                nodes += [{'id': 'revision-assembled', 'type': 'revision-apply', 'inputs': {'sourceText': '', 'edits': ''}}]
            return httpx.Response(200, json={'nodes': nodes, 'edges': []}, headers={'etag': '"fixture"'})
        if path == '/runs':
            submitted.append(kw['json'])
            return httpx.Response(201, json={'id': f'run-{len(submitted)}'})
        if path.startswith('/runs/'):
            return httpx.Response(200, json={'status': 'SUCCEEDED', 'nodes': [
                {'nodeId': 'final-review-llm', 'status': 'SUCCEEDED', 'outputs': {'text': outputs.get('review', '【主编意见】\n\n### M1\n确定问题：修改错字。\n\n### K1\n保留结果。')}},
                {'nodeId': 'revised-prose', 'status': 'SUCCEEDED', 'outputs': {'text': outputs.get('revision', '第2章 当前章\n　　这是第二章的修订稿。')}},
                {'nodeId': 'revision-check', 'status': 'SUCCEEDED', 'outputs': {'text': '这是复核报告，不是正文。'}},
                *([{'nodeId': 'revision-assembled', 'status': 'SUCCEEDED', 'outputs': {'text': outputs['assembled'], 'report': '局部替换完成'}}] if assembled else []),
            ]})
        raise AssertionError(path)
    monkeypatch.setattr(cw, 'canvas', canvas)
    return submitted


def finished_review(c):
    r = client.post(f"/api/chapters/{c['id']}/review", json={'base_version_id': c['current_version_id']})
    assert r.status_code == 200, r.text
    jid = r.json()['id']
    asyncio.run(cw.tick(jid)); asyncio.run(cw.tick(jid))
    return client.get(f'/api/chapter-review/jobs/{jid}').json()


def test_review_second_chapter_uses_a_to_a_and_does_not_change_body(monkeypatch):
    bid, c = book_chapters()
    submitted = remote(monkeypatch, bid)
    j = finished_review(c)
    overrides = submitted[0]['inputOverrides']['book']
    assert overrides['bodyStart'] == overrides['bodyEnd'] == 2
    frozen = client.get(f"/api/workflow-writing/jobs/{j['id']}/source").json()['bookOutputs']
    assert '第二章的原稿' in frozen['bodyText']
    assert '第一章的前文' not in frozen['bodyText'] and '第三章' not in frozen['bodyText']
    assert '第一章的前文' in frozen['previousText']
    assert j['status'] == 'completed' and 'M1' in j['opinions']
    assert client.get(f"/api/chapters/{c['id']}").json()['current_version_id'] == c['current_version_id']


def test_edited_opinions_and_default_acceptance_reach_revision_and_only_prose_is_saved(monkeypatch):
    bid, c = book_chapters(); submitted = remote(monkeypatch, bid)
    review = finished_review(c)
    r = client.post(f"/api/chapters/{c['id']}/revision", json={'base_version_id': c['current_version_id'],
        'review_job_id': review['id'], 'opinions': 'M1：作者编辑后的意见', 'decisions': ''})
    assert r.status_code == 200, r.text
    jid = r.json()['id']
    asyncio.run(cw.tick(jid)); asyncio.run(cw.tick(jid))
    override = submitted[1]['inputOverrides']
    assert override['editorial-opinions']['text'] == 'M1：作者编辑后的意见'
    assert '确定问题默认采纳' in override['accepted-decisions']['text']
    assert override['book']['bodyStart'] == override['book']['bodyEnd'] == 2
    updated = client.get(f"/api/chapters/{c['id']}").json()
    assert '修订稿' in updated['current_content'] and '复核报告' not in updated['current_content']
    assert updated['status'] == 'draft'
    history = client.get(f"/api/chapters/{c['id']}/versions").json()
    assert len(history) == 2 and '原稿' in history[1]['content']
    assert client.get(f'/api/chapter-review/jobs/{jid}').json()['status'] == 'completed'
    asyncio.run(cw.tick(jid))
    assert len(submitted) == 2  # No automatic second review or repeated revision.


def test_revision_apply_workflow_saves_assembled_prose_not_json_or_check(monkeypatch):
    bid, c = book_chapters()
    assembled = '第2章 当前章\n\n　　这是第二章的局部修订稿。\n\n\n　不改这段缩进。  '
    submitted = remote(monkeypatch, bid, {'revision': '{"version":1,"edits":[]}', 'assembled': assembled}, assembled=True)
    review = finished_review(c)
    r = client.post(f"/api/chapters/{c['id']}/revision", json={'base_version_id': c['current_version_id'],
        'review_job_id': review['id'], 'opinions': 'M1：修改确定问题'})
    assert r.status_code == 200, r.text
    jid = r.json()['id']
    with SessionLocal() as db:
        assert db.get(CanvasWritingJob, jid).config['output_node_id'] == 'revision-assembled'
    asyncio.run(cw.tick(jid)); asyncio.run(cw.tick(jid))
    assert submitted[-1]['workflowId'] == cr.settings.revision_workflow_id
    updated = client.get(f"/api/chapters/{c['id']}").json()
    assert updated['current_content'] == assembled.split('\n\n', 1)[1]
    assert updated['current_version_id'] != c['current_version_id']
    assert client.get(f'/api/chapter-review/jobs/{jid}').json()['check_report'] == '这是复核报告，不是正文。'


def test_revision_output_rejects_disabled_or_ambiguous_assembly_instead_of_json_fallback():
    from fastapi import HTTPException
    graph = {'nodes': [{'id': 'revised-prose', 'type': 'llm'},
                       {'id': 'assembly', 'type': 'revision-apply', 'enabled': False}]}
    with pytest.raises(HTTPException, match='正文合成'):
        cr.revision_output(graph)
    graph['nodes'][1]['enabled'] = True
    assert cr.revision_output(graph) == 'assembly'
    graph['nodes'].append({'id': 'second-assembly', 'type': 'revision-apply'})
    with pytest.raises(HTTPException, match='唯一'):
        cr.revision_output(graph)


def test_changed_body_cannot_use_stale_review_and_inflight_output_is_preserved(monkeypatch):
    bid, c = book_chapters(); remote(monkeypatch, bid)
    review = finished_review(c)
    revision = client.post(f"/api/chapters/{c['id']}/revision", json={'base_version_id': c['current_version_id'],
        'review_job_id': review['id'], 'opinions': 'M1：改字'}).json()
    asyncio.run(cw.tick(revision['id']))
    v = client.post(f"/api/chapters/{c['id']}/versions", json={'base_version_id': c['current_version_id'], 'content': '作者并发保存的新正文。'}).json()
    with pytest.raises(Exception, match='变化'):
        asyncio.run(cw.tick(revision['id']))
    assert client.get(f"/api/chapters/{c['id']}").json()['current_version_id'] == v['id']
    assert '修订稿' in client.get(f"/api/workflow-writing/jobs/{revision['id']}/output").json()['text']
    with SessionLocal() as db:
        db.get(CanvasWritingJob, revision['id']).status = 'conflict'; db.commit()
    r = client.post(f"/api/chapters/{c['id']}/revision", json={'base_version_id': v['id'], 'review_job_id': review['id'], 'opinions': 'M1：旧意见'})
    assert r.status_code == 409 and '变化' in r.text


def test_confirm_unifies_body_and_plan_review_and_new_save_resets_draft():
    bid, c = book_chapters()
    p = client.post(f'/api/books/{bid}/chapter-plans', json={'title': '当前章计划', 'content': '本章目标'}).json()
    client.post(f"/api/books/{bid}/chapter-plans/{p['id']}/binding", json={'base_version_id': p['version']['id'], 'chapter_id': c['id'], 'previous_chapter_id': None})
    url = f"/api/chapters/{c['id']}/confirm"
    assert client.post(url, json={'base_version_id': c['current_version_id'], 'plan_version_id': None}).status_code == 409
    r = client.post(url, json={'base_version_id': c['current_version_id'], 'plan_version_id': p['version']['id']})
    assert r.status_code == 200 and r.json()['status'] == 'approved'
    assert client.get(f'/api/books/{bid}/chapter-plans').json()['plans'][0]['needs_review'] is False
    client.post(f"/api/chapters/{c['id']}/versions", json={'base_version_id': c['current_version_id'], 'content': '新修订正文'})
    assert client.get(f"/api/chapters/{c['id']}").json()['status'] == 'draft'
    assert client.get(f'/api/books/{bid}/chapter-plans').json()['plans'][0]['needs_review'] is True


def test_blank_body_cannot_be_confirmed_or_reviewed(monkeypatch):
    bid = client.post('/api/books', json={'title': '空稿'}).json()['id']
    c = client.post(f'/api/books/{bid}/chapters', json={'title': '空章', 'content': ''}).json()
    remote(monkeypatch, bid)
    for suffix in ['confirm', 'review']:
        assert client.post(f"/api/chapters/{c['id']}/{suffix}", json={'base_version_id': c['current_version_id']}).status_code == 409
