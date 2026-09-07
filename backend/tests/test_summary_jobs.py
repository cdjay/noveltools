import asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.main import app
from app.database import SessionLocal
from app.models import LLMProvider, SummaryJob, SummaryJobItem
from app.routers import summary, summary_jobs

client = TestClient(app)


def setup_job():
    with SessionLocal.begin() as db:
        db.execute(delete(LLMProvider))
    client.post('/api/llm/providers', json={'base_url': 'https://test.invalid/v1', 'api_key': 'test', 'model': 'test', 'enabled': True})
    book = client.post('/api/books', json={'title': '任务测试'}).json()
    for i in range(3):
        client.post(f"/api/books/{book['id']}/chapters", json={'title': str(i), 'content': f'正文{i}'})
    return client.post(f"/api/books/{book['id']}/summary-jobs").json()


def run(job):
    with SessionLocal.begin() as db:
        db.get(SummaryJob, job['id']).status = 'running'
    asyncio.run(summary_jobs.run_job(job['id']))
    return client.get(f"/api/summary-jobs/{job['id']}").json()


def test_cancel_resume_deduplicate_and_keep_completed(monkeypatch):
    job = setup_job()
    assert client.post(f"/api/books/{job['book_id']}/summary-jobs").json()['id'] == job['id']
    calls = []
    async def fake(provider, title, content):
        calls.append(title)
        if title == '0':
            client.post(f"/api/summary-jobs/{job['id']}/cancel")
        return '已生成' + content
    monkeypatch.setattr(summary, 'generate_summary', fake)
    stopped = run(job)
    assert stopped['status'] == 'cancelled'
    assert stopped['updated'] == 1
    assert calls == ['0']
    assert client.post(f"/api/summary-jobs/{job['id']}/resume").status_code == 200
    completed = run(job)
    assert completed['status'] == 'completed'
    assert completed['updated'] == 3
    assert calls == ['0', '1', '2']


def test_restart_waits_for_explicit_resume_and_retries_failed_only(monkeypatch):
    job = setup_job()
    calls = []
    async def fake(provider, title, content):
        calls.append(title)
        if title == '1': raise ValueError('模拟失败')
        return content
    monkeypatch.setattr(summary, 'generate_summary', fake)
    failed = run(job)
    assert (failed['updated'], failed['failed'], failed['status']) == (2, 1, 'failed')
    client.post(f"/api/summary-jobs/{job['id']}/resume")
    summary_jobs.recover_interrupted()
    assert client.get(f"/api/summary-jobs/{job['id']}").json()['status'] == 'interrupted'
    assert calls == ['0', '1', '2']
    async def good(provider, title, content):
        calls.append(title)
        return content
    monkeypatch.setattr(summary, 'generate_summary', good)
    client.post(f"/api/summary-jobs/{job['id']}/resume")
    assert run(job)['updated'] == 3
    assert calls == ['0', '1', '2', '1']
