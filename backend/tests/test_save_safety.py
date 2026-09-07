from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.database import SessionLocal
from app.models import ChapterVersion
from app.routers import core

client = TestClient(app)


def make_chapter():
    book = client.post('/api/books', json={'title': '保存安全'}).json()
    return client.post(f"/api/books/{book['id']}/chapters", json={'content': '原稿', 'title': '原题'}).json()


def test_concurrent_saves_cannot_corrupt_winner(monkeypatch):
    chapter = make_chapter()
    barrier = Barrier(2)
    original = core.require_chapter

    def read_together(db, chapter_id):
        result = original(db, chapter_id)
        barrier.wait(timeout=5)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(core, 'require_chapter', read_together)
        def save(text):
            return client.post(f"/api/chapters/{chapter['id']}/versions", json={
                'content': text, 'title': text, 'base_version_id': chapter['current_version_id']})
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(save, ['甲稿', '乙稿']))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r.json() for r in results if r.status_code == 200)
    current = client.get(f"/api/chapters/{chapter['id']}").json()
    assert current['current_content'] == current['title'] == winner['content']
    versions = client.get(f"/api/chapters/{chapter['id']}/versions").json()
    assert [v['content'] for v in versions] == [winner['content'], '原稿']


def test_transaction_failure_preserves_title_and_body(monkeypatch):
    chapter = make_chapter()
    def fail(*args):
        raise RuntimeError('injected database failure')
    monkeypatch.setattr(core, 'next_version_number', fail)
    with TestClient(app, raise_server_exceptions=False) as failing:
        assert failing.post(f"/api/chapters/{chapter['id']}/versions", json={
            'content': '失败稿', 'title': '失败题', 'base_version_id': chapter['current_version_id']}).status_code == 500
    current = client.get(f"/api/chapters/{chapter['id']}").json()
    assert (current['current_content'], current['title']) == ('原稿', '原题')


def test_missing_legacy_file_is_an_error(tmp_path):
    chapter = make_chapter()
    with SessionLocal.begin() as db:
        version = db.get(ChapterVersion, chapter['current_version_id'])
        version.content_text = None
        version.content_path = str(tmp_path / 'missing.txt')
    response = client.get(f"/api/chapters/{chapter['id']}")
    assert response.status_code == 409
    assert '文件缺失' in response.json()['detail']


def test_restore_rejects_outdated_base():
    chapter = make_chapter()
    url = f"/api/chapters/{chapter['id']}/versions"
    client.post(url, json={'content': '新稿'})
    result = client.post(f"{url}/{chapter['current_version_id']}/restore?base_version_id={chapter['current_version_id']}")
    assert result.status_code == 409
