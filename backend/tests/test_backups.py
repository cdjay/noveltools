import json
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Chapter

client = TestClient(app)


def test_backup_round_trip_preserves_history_and_summary_references():
    book = client.post('/api/books', json={'title': '完整备份', 'author': '作者'}).json()
    chapter = client.post(f"/api/books/{book['id']}/chapters", json={'title': '正文', 'content': '旧稿'}).json()
    version = client.post(f"/api/chapters/{chapter['id']}/versions", json={'content': '新稿'}).json()
    with SessionLocal.begin() as db:
        current = db.get(Chapter, chapter['id'])
        current.summary = '旧稿的梗概'
        current.summary_source_version_id = chapter['current_version_id']
        current.reading_position = 123
    client.post(f"/api/books/{book['id']}/outline/versions", json={'content': '大纲全文', 'base_version_id': None})
    exported = client.get(f"/api/books/{book['id']}/backup")
    assert exported.status_code == 200
    assert 'api_key' not in exported.text and 'content_path' not in exported.text
    preview = client.post('/api/backups/preview', files={'file': ('book.json', exported.content)}).json()
    assert (preview['chapters'], preview['versions'], preview['outlines']) == (1, 2, 1)
    restored = client.post('/api/backups/restore', files={'file': ('book.json', exported.content)})
    assert restored.status_code == 200
    new_book = restored.json()
    assert new_book['id'] != book['id']
    new_chapter = client.get(f"/api/books/{new_book['id']}/chapters").json()[0]
    history = client.get(f"/api/chapters/{new_chapter['id']}/versions").json()
    assert [v['content'] for v in history] == ['新稿', '旧稿']
    assert new_chapter['summary_source_version_id'] == history[1]['id']
    assert new_chapter['current_version_id'] == history[0]['id']
    assert new_chapter['reading_position'] == 123
    assert client.get(f"/api/books/{new_book['id']}/outline/versions").json()[0]['content'] == '大纲全文'
    assert client.get(f"/api/chapters/{chapter['id']}").json()['current_content'] == '新稿'
    invalid = exported.json()
    invalid['chapters'][0]['current_version_id'] = -100
    before = len(client.get('/api/books').json())
    assert client.post('/api/backups/restore', files={'file': ('bad.json', json.dumps(invalid).encode())}).status_code == 400
    assert len(client.get('/api/books').json()) == before
