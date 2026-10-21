"""Continuous writing uses saved canvases, real checkpoints and a bounded decision loop."""
import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from app.main import app
from app.database import SessionLocal
from app.models import WritingJob, WritingJobItem, CanvasWritingJob, Chapter, ChapterVersion
from app.routers import writing_jobs as writing, canvas_writing as cw, chapter_review as cr, continuous_writing as cont
from app.automation_review import CONTROL_NODE, OPEN, CLOSE, DIMENSIONS, parse_decision, ensure_control

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with SessionLocal.begin() as db:
        db.execute(delete(CanvasWritingJob)); db.execute(delete(WritingJobItem)); db.execute(delete(WritingJob))


def setup(total=1, existing=0):
    bid = client.post('/api/books', json={'title': '连续工作流测试'}).json()['id']
    client.post(f'/api/books/{bid}/outline/versions', json={'content': '依次执行本章目标，不提前执行未来计划', 'base_version_id': None})
    plans = []
    for n in range(1, total + 1):
        plans.append(client.post(f'/api/books/{bid}/chapter-plans', json={'title': f'目标{n}', 'content': f'第{n}章的目标', 'status': 'confirmed'}).json())
        if n <= existing:
            client.post(f'/api/books/{bid}/chapters', json={'title': f'已写{n}', 'content': f'已发生的第{n}章正文'})
    return bid, plans


def report(stage, version, action='continue', **extra):
    value = dict(version=1, stage=stage, source_version_id=version, action=action, reason='已按正文证据核对',
                 needs_author=False, impact='none', checked_dimensions=sorted(DIMENSIONS), issues=[], resolved_ids=[])
    value.update(extra)
    return '主编意见 M1：有依据的判断。' + OPEN + json.dumps(value, ensure_ascii=False) + CLOSE


def issue(n=1):
    return dict(id=f'M{n}', certainty='confirmed', blocking=True, auto_fixable=True, evidence=f'第{n}处的原句', description='局部确定问题')


def remote(monkeypatch, bid, decisions=None, model_response=None):
    submissions, graphs, memory = [], {}, []
    decisions = decisions or []
    with SessionLocal() as db:
        generate_id = cw.get_workflow_settings(bid, db)['workflow_id']
    for kind, wid in [('generate', generate_id), ('review', cr.settings.review_workflow_id), ('revision', cr.settings.revision_workflow_id)]:
        nodes = [{'id': 'book', 'type': 'chapter-context', 'inputs': {'bookId': str(bid), 'requireReader': kind == 'generate', 'previousCount': 5, 'includeOutline': True, 'includeChapterPlans': True}}]
        if kind == 'revision':
            nodes += [{'id': key, 'type': 'text', 'inputs': {'text': ''}} for key in ['editorial-opinions', 'accepted-decisions']]
            nodes += [{'id': 'revision-assembled', 'type': 'revision-apply', 'inputs': {}}]
        nodes += [{'id': 'final-prose' if kind == 'generate' else 'final-review-llm' if kind == 'review' else 'revision-check', 'type': 'llm', 'inputs': {'model': 'gemini-test', 'prompt': {'kind': 'prompt-template', 'parts': [{'type': 'text', 'text': '原工作流提示词'}]}}}]
        graphs[wid] = dict(id=wid, name=kind, nodes=nodes, edges=[])
    async def canvas(method, path, **kw):
        if path.startswith('/workflows/'):
            wid = path.split('/')[-1]
            if method == 'PUT':
                assert kw['headers']['If-Match'] == '"original"'
                graphs[wid] = kw['json']
            return httpx.Response(200, json=graphs[wid], headers={'etag': '"frozen"' if any(n['id'] == CONTROL_NODE for n in graphs[wid]['nodes']) else '"original"'})
        if path == '/runs':
            submissions.append(kw['json'])
            return httpx.Response(201, json={'id': f'run-{len(submissions)}'})
        if path.startswith('/runs/'):
            submitted = submissions[int(path.split('-')[-1]) - 1]
            jid = submitted['inputOverrides']['book']['sourceRequestId']
            with SessionLocal() as db:
                child = db.get(CanvasWritingJob, jid)
                kind = child.config.get('kind', 'generate')
                n = db.get(Chapter, child.chapter_id).chapter_number
                version = child.base_version_id
            if kind == 'generate':
                nodes = [{'nodeId': 'final-prose', 'status': 'SUCCEEDED', 'outputs': {'text': f'第{n}章 目标{n}\n初稿第{n}章，主角推门。'}}]
            else:
                assert CONTROL_NODE in submitted['inputOverrides']
                stage = 'review' if kind == 'review' else 'check'
                chosen = decisions.pop(0) if decisions else {}
                raw = chosen if isinstance(chosen, str) else report(stage, version, **chosen)
                nodes = [{'nodeId': 'final-review-llm' if kind == 'review' else 'revision-check', 'status': 'SUCCEEDED', 'outputs': {'text': raw}}]
                if kind == 'revision':
                    assert '可选建议仅在收益明确' not in submitted['inputOverrides']['accepted-decisions']['text']
                    nodes += [{'nodeId': 'revision-assembled', 'status': 'SUCCEEDED', 'outputs': {'text': f'第{n}章 目标{n}\n修订稿{len(submissions)}，主角推开门。'}}]
            return httpx.Response(200, json={'status': 'SUCCEEDED', 'nodes': nodes})
        raise AssertionError((method, path))
    monkeypatch.setattr(cw, 'canvas', canvas)
    # The context refresh still uses its normal persistence/provenance checks, with model I/O stubbed.
    client.post('/api/llm/providers', json={'provider_name': '测试整理', 'model': 'memory-test', 'base_url': 'https://memory.invalid/v1', 'api_key': 'test', 'enabled': True})
    original = httpx.AsyncClient
    def model(request):
        payload = json.loads(request.content)
        memory.append(payload)
        if model_response:
            return model_response(payload)
        return httpx.Response(200, json={'choices': [{'message': {'content': '已依据最终正文整理的梗概与当前状态'}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(model)))
    return submissions, graphs, memory


def start(bid, **payload):
    r = client.post(f'/api/books/{bid}/writing-jobs', json=payload or {'chapter_count': 1})
    assert r.status_code == 200, r.text
    with SessionLocal.begin() as db:
        db.get(WritingJob, r.json()['id']).status = 'running'
    return r.json()['id']


def status(jid):
    return client.get(f'/api/writing-jobs/{jid}').json()


def pump(jid, steps=100):
    async def run():
        for _ in range(steps):
            more = await writing.tick(jid)
            with SessionLocal() as db:
                ids = list(db.scalars(select(CanvasWritingJob.id).where(CanvasWritingJob.config['continuous_job_id'].as_integer() == jid, CanvasWritingJob.status.in_(cw.ACTIVE))))
            for child_id in ids:
                await cw.tick(child_id)
            if not more:
                break
    asyncio.run(run())
    return status(jid)


def test_first_pass_stops_at_one_review_and_uses_saved_workflows(monkeypatch):
    bid, plans = setup(); calls, graphs, memory = remote(monkeypatch, bid)
    jid = start(bid)
    done = pump(jid)
    assert done['status'] == 'completed', done
    assert done['items'][0]['review_count'] == 1 and done['items'][0]['revision_count'] == 0
    assert [x['workflowId'] for x in calls] == [done['workflows']['generate']['id'], cr.settings.review_workflow_id]
    assert not memory  # No first-chapter memory, no premature final archive.
    assert client.get(f"/api/chapters/{done['items'][0]['chapter_id']}").json()['status'] == 'draft'
    assert client.get(f'/api/books/{bid}/chapter-plans').json()['plans'][0]['version']['id'] == plans[0]['version']['id']
    assert pump(jid)['completed_count'] == 1 and len(calls) == 2
    assert 'api_key' not in json.dumps(done) and done['logs']


@pytest.mark.parametrize('impact,expected', [('wording', 1), ('story', 2)])
def test_revision_check_dynamically_selects_rereview(monkeypatch, impact, expected):
    bid, _ = setup()
    calls, _, _ = remote(monkeypatch, bid, [dict(action='revise', issues=[issue()]),
        dict(action='continue' if impact == 'wording' else 'review', impact=impact, resolved_ids=['M1'])])
    done = pump(start(bid))
    assert done['status'] == 'completed', done
    assert done['items'][0]['review_count'] == expected
    assert done['items'][0]['revision_count'] == 1
    assert sum(c['workflowId'] == cr.settings.revision_workflow_id for c in calls) == 1
    assert all(c['expectedVersion'] == '"frozen"' for c in calls[1:])


def test_three_review_cap_pauses_and_resume_requires_author_confirmation(monkeypatch):
    bid, plans = setup(2)
    decisions = []
    for n in range(1, 4):
        decisions += [dict(action='revise', issues=[issue(n)]), dict(action='review', impact='story', resolved_ids=[f'M{n}'])]
    calls, _, _ = remote(monkeypatch, bid, decisions)
    jid = start(bid, chapter_count=2)
    paused = pump(jid)
    assert paused['status'] == 'paused' and '3次' in paused['message'], paused
    assert paused['items'][0]['review_count'] == paused['items'][0]['revision_count'] == 3
    assert len(client.get(f'/api/books/{bid}/chapters').json()) == 1
    assert client.post(f'/api/writing-jobs/{jid}/resume').status_code == 409
    c = client.get(f"/api/chapters/{paused['items'][0]['chapter_id']}").json()
    assert client.post(f"/api/chapters/{c['id']}/confirm", json={'base_version_id': c['current_version_id'], 'plan_version_id': plans[0]['version']['id']}).status_code == 200
    assert client.post(f'/api/writing-jobs/{jid}/resume').status_code == 200
    with SessionLocal.begin() as db:
        db.get(WritingJob, jid).status = 'running'
    done = pump(jid)
    assert done['status'] == 'completed' and done['completed_count'] == 2, done


def test_target_twenty_after_four_means_sixteen_and_range_is_frozen(monkeypatch):
    bid, plans = setup(20, 4); calls, _, _ = remote(monkeypatch, bid)
    options = client.get(f'/api/books/{bid}/writing-jobs/options').json()
    assert options['start_chapter'] == 5 and len(options['plans']) == 16
    jid = start(bid, mode='through_plan', target_plan_id=plans[19]['id'])
    j = status(jid)
    assert (j['start_chapter'], j['end_chapter'], j['target_count'], j['review_limit']) == (5, 20, 16, 3)
    assert [i['position'] for i in j['items']] == list(range(5, 21))
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 100}).json()['id'] == jid
    assert not calls


def test_multiple_chapters_refresh_memory_from_final_revision_before_next(monkeypatch):
    bid, _ = setup(2)
    calls, _, memory = remote(monkeypatch, bid, [dict(action='revise', issues=[issue()]), dict(action='continue', impact='wording', resolved_ids=['M1'])])
    done = pump(start(bid, chapter_count=2))
    assert done['status'] == 'completed', done
    assert len(calls) == 5 and memory
    chapters = client.get(f'/api/books/{bid}/chapters').json()
    assert chapters[0]['summary_source_version_id'] == chapters[0]['current_version_id']
    assert not chapters[1]['summary']
    assert '修订稿' in json.dumps(memory, ensure_ascii=False)


@pytest.mark.parametrize('decision', ['没有机器结论，但文字写着通过', dict(action='pause', needs_author=True), dict(action='revise', issues=[{**issue(), 'auto_fixable': False}])])
def test_invalid_or_author_decisions_never_advance(monkeypatch, decision):
    bid, _ = setup(2); calls, _, _ = remote(monkeypatch, bid, [decision])
    jid = start(bid, chapter_count=2)
    assert pump(jid)['status'] == 'paused'
    assert len(calls) == 2
    assert client.post(f'/api/writing-jobs/{jid}/resume').status_code == 409


def test_repeated_issue_stops_revision_loop(monkeypatch):
    bid, _ = setup()
    calls, _, _ = remote(monkeypatch, bid, [dict(action='revise', issues=[issue()]), dict(action='review', impact='story'), dict(action='revise', issues=[issue()])])
    done = pump(start(bid))
    assert done['status'] == 'paused' and '重复' in done['message']
    assert len(calls) == 4


def test_cancel_queued_child_resume_and_restart_do_not_duplicate(monkeypatch):
    bid, _ = setup(); calls, _, _ = remote(monkeypatch, bid)
    jid = start(bid)
    asyncio.run(writing.tick(jid))
    child_id = status(jid)['items'][0]['child_id']
    writing.recover_interrupted()
    asyncio.run(cw.tick(child_id))
    assert not calls
    assert client.post(f'/api/writing-jobs/{jid}/resume').status_code == 200
    with SessionLocal.begin() as db: db.get(WritingJob, jid).status = 'running'
    assert client.post(f'/api/writing-jobs/{jid}/cancel').status_code == 200
    assert pump(jid)['status'] == 'cancelled'
    assert client.post(f'/api/writing-jobs/{jid}/resume').status_code == 200
    with SessionLocal.begin() as db: db.get(WritingJob, jid).status = 'running'
    assert pump(jid)['status'] == 'completed'
    assert len(calls) == 2 and len(client.get(f'/api/books/{bid}/chapters').json()) == 1


def test_range_rejects_gaps_drafts_wrong_target_and_occupied_body(monkeypatch):
    bid, plans = setup(3)
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 4}).status_code == 409
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'mode': 'through_plan', 'target_plan_id': 'wrong'}).status_code == 409
    client.post(f'/api/books/{bid}/chapters', json={'title': '已有第三章', 'chapter_number': 3, 'content': '正文'})
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 3}).status_code == 409
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'mode': 'through_plan', 'target_plan_id': plans[1]['id'], 'chapter_count': 2}).status_code == 422


def test_owned_blank_reused_and_single_workflow_mutex(monkeypatch):
    bid, plans = setup(); calls, _, _ = remote(monkeypatch, bid)
    blank = client.post(f"/api/books/{bid}/chapter-plans/{plans[0]['id']}/create-chapter", json={'base_version_id': plans[0]['version']['id']}).json()
    single = client.post(f"/api/chapters/{blank['chapter_id']}/workflow-writing", json={'base_version_id': blank['chapter_version_id']}).json()
    assert client.post(f'/api/books/{bid}/writing-jobs', json={'chapter_count': 1}).status_code == 409
    with SessionLocal() as db:
        asyncio.run(cw.cancel(single['id'], db))
    done = pump(start(bid, chapter_count=1, review_limit=0))
    assert done['status'] == 'completed' and len(calls) == 1
    assert done['items'][0]['chapter_id'] == blank['chapter_id']


def test_decision_requires_version_all_dimensions_and_consistency():
    assert parse_decision(report('review', 3), 'review', 3).action == 'continue'
    for text in [report('review', 2), report('review', 3, checked_dimensions=[]), report('review', 3, issues=[issue()]), report('check', 3)]:
        with pytest.raises(ValueError): parse_decision(text, 'review', 3)


def test_control_input_keeps_template_and_exact_backup_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(cont.settings, 'data_dir', str(tmp_path))
    graph = {'id': 'workflow', 'nodes': [{'id': 'final-review-llm', 'type': 'llm', 'inputs': {'prompt': {'kind': 'prompt-template', 'parts': [{'type': 'text', 'text': '原有完整提示词'}]}}}], 'edges': []}
    original = copy.deepcopy(graph); puts = []
    async def canvas(method, path, **kw):
        puts.append(kw)
        return httpx.Response(200, json=kw['json'], headers={'etag': '"new"'})
    response = asyncio.run(ensure_control(canvas, httpx.Response(200, json=graph, headers={'etag': '"old"'}), 'final-review-llm'))
    assert graph == original and puts[0]['headers']['If-Match'] == '"old"'
    assert json.loads(next((tmp_path / 'workflow-backups').glob('*.json')).read_text(encoding='utf-8')) == original
    assert asyncio.run(ensure_control(canvas, response, 'final-review-llm')) == response and len(puts) == 1


def test_old_task_cannot_resume_direct_model_generation():
    bid, _ = setup()
    with SessionLocal.begin() as db:
        j = WritingJob(book_id=bid, target_count=1, providers={}, status='running'); db.add(j); db.flush(); jid=j.id
    assert not asyncio.run(writing.tick(jid))
    assert client.post(f'/api/writing-jobs/{jid}/resume').status_code == 409



def test_generation_submission_and_checkpoint_rollback_together(monkeypatch):
    bid, _ = setup(); remote(monkeypatch, bid)
    jid = start(bid)
    original = cw.start_job
    async def fail_after_create(*args, **kwargs):
        await original(*args, **kwargs)
        raise ValueError('提交前模拟故障')
    monkeypatch.setattr(cw, 'start_job', fail_after_create)
    with pytest.raises(ValueError): asyncio.run(writing.tick(jid))
    assert client.get(f'/api/books/{bid}/chapters').json() == []
    with SessionLocal() as db:
        assert not db.scalar(select(CanvasWritingJob.id).where(CanvasWritingJob.book_id == bid))
        assert not db.scalar(select(WritingJobItem).where(WritingJobItem.job_id == jid)).execution.get('child_id')
    monkeypatch.setattr(cw, 'start_job', original)
    assert pump(jid)['status'] == 'completed'


def test_unknown_submission_is_never_retried(monkeypatch):
    bid, _ = setup(); calls, _, _ = remote(monkeypatch, bid)
    jid = start(bid); asyncio.run(writing.tick(jid))
    child_id = status(jid)['items'][0]['child_id']
    with SessionLocal.begin() as db:
        db.get(CanvasWritingJob, child_id).status = 'submitting'
    cw.recover_interrupted()
    assert pump(jid)['status'] == 'paused'
    assert client.post(f'/api/writing-jobs/{jid}/resume').status_code == 409
    assert not calls


def test_stop_after_saved_generation_resumes_review_without_regeneration(monkeypatch):
    bid, _ = setup(); calls, _, _ = remote(monkeypatch, bid)
    jid = start(bid); asyncio.run(writing.tick(jid))
    child_id = status(jid)['items'][0]['child_id']
    asyncio.run(cw.tick(child_id)); asyncio.run(cw.tick(child_id))
    client.post(f'/api/writing-jobs/{jid}/cancel')
    assert pump(jid)['status'] == 'cancelled'
    assert client.post(f'/api/writing-jobs/{jid}/resume').status_code == 200
    with SessionLocal.begin() as db: db.get(WritingJob, jid).status = 'running'
    assert pump(jid)['status'] == 'completed' and len(calls) == 2


def test_body_change_between_review_and_revision_pauses(monkeypatch):
    bid, _ = setup(); calls, _, _ = remote(monkeypatch, bid, [dict(action='revise', issues=[issue()])])
    jid = start(bid)
    for _ in range(10):
        pump(jid, steps=1)
        j = status(jid)
        if j['items'][0]['phase'] == 'revision': break
    c = client.get(f"/api/chapters/{j['items'][0]['chapter_id']}").json()
    client.post(f"/api/chapters/{c['id']}/versions", json={'base_version_id': c['current_version_id'], 'content': '作者刚保存的正文'})
    assert pump(jid)['status'] == 'paused'
    assert len(calls) == 2
    assert client.get(f"/api/chapters/{c['id']}").json()['current_content'] == '作者刚保存的正文'
