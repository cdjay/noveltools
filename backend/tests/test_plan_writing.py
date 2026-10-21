import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.database import SessionLocal
from app.models import Book, OutlineTextVersion, PlanWritingJob, ChapterPlanVersion
from app.routers import plan_writing as pw, chapter_plans as plans

client = TestClient(app)


@pytest.fixture
def scenario(monkeypatch):
    with SessionLocal() as db:
        book = Book(title='章纲测试', description='测试用书')
        db.add(book); db.flush()
        db.add(OutlineTextVersion(book_id=book.id, version_number=1, content='全书大纲：寻找失踪的师兄', source='manual', note=''))
        plans.create_plan(db, book.id, 1, {'title': '开端', 'volume': '第一卷', 'content': '主角收到线索', 'status': 'confirmed'})
        db.commit(); bid = book.id
    graph = {'id': 'test-loop', 'nodes': [
        {'id': 'book', 'type': 'chapter-context', 'inputs': {}},
        {'id': 'end', 'type': 'review-condition', 'inputs': {}},
    ], 'edges': []}
    calls = []
    remote = {}
    async def canvas(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == 'GET' and path.startswith('/workflows/'):
            return httpx.Response(200, json=graph, headers={'etag': 'v1'})
        if method == 'POST' and path == '/runs':
            run = uuid4().hex
            remote[run] = {'id': run, 'status': 'RUNNING', 'nodes': []}
            return httpx.Response(200, json={'id': run})
        if method == 'POST' and path.endswith('/cancel'):
            remote[path.split('/')[2]]['status'] = 'CANCELLED'
            return httpx.Response(200, json={})
        return httpx.Response(200, json=remote[path.split('/')[2]])
    monkeypatch.setattr(pw.cw, 'canvas', canvas)
    return bid, calls, remote


def start(bid, count=1):
    result = client.post(f'/api/books/{bid}/plan-writing', json={'chapter_count': count, 'workflow_id': 'test-loop'})
    assert result.status_code == 200, result.text
    return result.json()['id']


def finish(job_id, remote, title='新章', content='主角主动追查，得到具体线索'):
    with SessionLocal() as db:
        run_id = db.get(PlanWritingJob, job_id).run_id
    remote[run_id] = {'id': run_id, 'status': 'SUCCEEDED', 'loop': {'status': 'passed', 'round': 2},
        'nodes': [{'nodeId': 'end', 'status': 'SUCCEEDED', 'outputs': {'text': json.dumps({'title': title, 'volume': '第一卷', 'content': content}, ensure_ascii=False)}}]}
    asyncio.run(pw.tick(job_id))


def test_two_plans_append_sequentially_and_next_receives_prior_result(scenario):
    bid, calls, remote = scenario
    id = start(bid, 2)
    assert client.post(f'/api/books/{bid}/plan-writing', json={'workflow_id': 'test-loop'}).status_code == 409
    asyncio.run(pw.tick(id))
    assert calls[-1][2]['json']['inputOverrides']['book']['sourceRequestId'] == 'plan:' + id
    finish(id, remote, title='第二章计划', content='发现密室的钥匙')
    with SessionLocal() as db:
        job = db.get(PlanWritingJob, id)
        assert job.completed_count == 1 and job.status == 'queued'
        assert '发现密室的钥匙' in job.source['bookOutputs']['planText']
        assert '计划，尚不代表已发生' in job.source['bookOutputs']['planText']
        assert '第3章' in job.source['bookOutputs']['chapterTitle']
    asyncio.run(pw.tick(id)); finish(id, remote, title='第三章计划')
    asyncio.run(pw.tick(id))  # Repeated ticks do not duplicate saved plans.
    with SessionLocal() as db:
        job = db.get(PlanWritingJob, id)
        assert job.status == 'completed' and job.completed_count == 2
        rows = plans.plans(db, bid)
        assert len(rows) == 3
        assert db.get(ChapterPlanVersion, rows[-1].current_version_id).status == 'draft'
        assert len(job.config['items']) == 2


def test_source_conflict_preserves_candidate_without_appending(scenario):
    bid, _, remote = scenario
    id = start(bid); asyncio.run(pw.tick(id))
    with SessionLocal() as db:
        db.add(OutlineTextVersion(book_id=bid, version_number=2, content='作者修改的大纲', source='manual', note=''))
        db.commit()
    with pytest.raises(ValueError, match='资料已变化'):
        finish(id, remote)
    with SessionLocal() as db:
        assert len(plans.plans(db, bid)) == 1
        assert '新章' in db.get(PlanWritingJob, id).raw_output


def test_pause_resume_and_restart_dont_duplicate_submissions(scenario):
    bid, calls, remote = scenario
    id = start(bid, 2)
    assert client.post(f'/api/plan-writing/jobs/{id}/cancel').json()['status'] == 'paused'
    assert client.post(f'/api/plan-writing/jobs/{id}/resume').json()['status'] == 'queued'
    asyncio.run(pw.tick(id))
    pw.recover_interrupted()
    asyncio.run(pw.tick(id))
    assert len([c for c in calls if c[:2] == ('POST', '/runs')]) == 1
    assert client.post(f'/api/plan-writing/jobs/{id}/cancel').json()['status'] == 'stopping'
    asyncio.run(pw.tick(id))
    assert client.get(f'/api/plan-writing/jobs/{id}').json()['status'] == 'paused'


def test_unpassed_loop_never_saves_and_rejects_invalid_counts(scenario):
    bid, _, remote = scenario
    for count in (0, 101, 1.5):
        assert client.post(f'/api/books/{bid}/plan-writing', json={'chapter_count': count}).status_code == 422
    id = start(bid); asyncio.run(pw.tick(id))
    with SessionLocal() as db:
        run = db.get(PlanWritingJob, id).run_id
    remote[run] = {'status': 'FAILED', 'loop': {'history': [{'draft': '未通过草稿'}]}, 'nodes': [{'error': {'message': '修改达到上限'}}]}
    asyncio.run(pw.tick(id))
    response = client.get(f'/api/plan-writing/jobs/{id}').json()
    assert response['status'] == 'paused' and not response['can_resume']
    with SessionLocal() as db:
        assert len(plans.plans(db, bid)) == 1
    assert '未通过草稿' in client.get(f'/api/plan-writing/jobs/{id}/output').json()['text']


def test_default_template_creates_independent_workflow_with_both_roles(scenario, monkeypatch):
    bid, _, _ = scenario
    saved = []
    role = {'id': 'final-review-llm', 'type': 'llm', 'config': {'credentialId': 'saved-service'}, 'inputs': {'model': 'configured-model', 'prompt': '原始提示词', 'system': ''}}
    async def canvas(method, path, **kwargs):
        if method == 'GET':
            return httpx.Response(200, json={'nodes': [role]})
        assert (method, path) == ('POST', '/workflows')
        saved.append(kwargs['json'])
        return httpx.Response(200, json=kwargs['json'])
    monkeypatch.setattr(pw.cw, 'canvas', canvas)
    with SessionLocal() as db:
        wid = asyncio.run(pw.default_workflow(db, bid))
        assert asyncio.run(pw.default_workflow(db, bid)) == wid
    assert len(saved) == 1
    graph = saved[0]
    assert len([n for n in graph['nodes'] if n['type'] == 'llm']) == 2
    assert next(n for n in graph['nodes'] if n['id'] == 'old-plan')['inputs']['text'] == ''
    assert next(n for n in graph['nodes'] if n['type'] == 'review-condition')['inputs']['targetNodeId'] == 'old-plan'
    assert role['inputs']['prompt'] == '原始提示词'


def test_regeneration_versions_bound_plan_then_appends_without_losing_history(scenario):
    from app.models import Chapter, ChapterVersion
    from app.routers.core import add_chapter
    from app.schemas import ChapterCreate
    bid, calls, remote = scenario
    with SessionLocal() as db:
        p = plans.plans(db, bid)[0]
        old_id, pid = p.current_version_id, p.id
        chapter = add_chapter(bid, ChapterCreate(title='开端', content='已有正文中的具体行动', chapter_number=1), db)
        p.chapter_id = chapter.id
        chapter.status = 'approved'
        cid, body_id = chapter.id, chapter.current_version_id
        db.commit()
    response = client.post(f'/api/books/{bid}/plan-writing', json={'workflow_id': 'test-loop', 'start_position': 1, 'chapter_count': 2})
    assert response.status_code == 200, response.text
    jid = response.json()['id']
    source = client.get(f'/api/plan-writing/jobs/{jid}/source').json()
    assert '重新生成第1章' in source['bookOutputs']['chapterTitle']
    assert '已有正文中的具体行动' in source['bookOutputs']['bodyText']
    asyncio.run(pw.tick(jid)); finish(jid, remote, '重写开端', '按新大纲明确行动')
    with SessionLocal() as db:
        p = plans.plans(db, bid)[0]
        assert p.id == pid and p.chapter_id == cid
        assert p.current_version_id != old_id
        assert db.get(ChapterPlanVersion, old_id).content == '主角收到线索'
        assert db.get(Chapter, cid).current_version_id == body_id
        assert db.get(Chapter, cid).status == 'draft'
        job = db.get(PlanWritingJob, jid)
        assert '第2章' in job.source['bookOutputs']['chapterTitle']
        assert '按新大纲明确行动' in job.source['bookOutputs']['planText']
    asyncio.run(pw.tick(jid)); finish(jid, remote, '新第二章')
    asyncio.run(pw.tick(jid))
    assert client.get(f'/api/plan-writing/jobs/{jid}').json()['completed_count'] == 2
    with SessionLocal() as db:
        assert len(plans.plans(db, bid)) == 2


def test_regeneration_ignores_unwritten_old_and_future_plans_and_rejects_gap(scenario):
    bid, _, remote = scenario
    with SessionLocal() as db:
        plans.create_plan(db, bid, 2, {'title': '未来旧计划', 'volume': '第一卷', 'content': '不应被新第一章引用', 'status': 'confirmed'})
        db.commit()
    assert client.post(f'/api/books/{bid}/plan-writing', json={'workflow_id': 'test-loop', 'start_position': 4}).status_code == 409
    assert client.post(f'/api/books/{bid}/plan-writing', json={'workflow_id': 'test-loop', 'start_position': 0}).status_code == 422
    result = client.post(f'/api/books/{bid}/plan-writing', json={'workflow_id': 'test-loop', 'start_position': 1, 'chapter_count': 2})
    jid = result.json()['id']
    source = client.get(f'/api/plan-writing/jobs/{jid}/source').json()
    assert '不应被新第一章引用' not in source['bookOutputs']['planText']
    assert '主角收到线索' not in source['bookOutputs']['planText']
    asyncio.run(pw.tick(jid)); finish(jid, remote, content='新第一章有效计划')
    assert client.post(f'/api/plan-writing/jobs/{jid}/cancel').json()['status'] == 'paused'
    assert client.post(f'/api/plan-writing/jobs/{jid}/resume').json()['status'] == 'queued'
    source = client.get(f'/api/plan-writing/jobs/{jid}/source').json()
    assert '第2章' in source['bookOutputs']['chapterTitle']
    assert '新第一章有效计划' in source['bookOutputs']['planText']
    assert '不应被新第一章引用' not in source['bookOutputs']['planText']
