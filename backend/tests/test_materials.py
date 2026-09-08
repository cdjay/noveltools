import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.database import SessionLocal
from app.models import BookMaterialVersion

client = TestClient(app)


def create_book():
    book = client.post('/api/books', json={'title': '书籍资料验收'}).json()
    return book, f"/api/books/{book['id']}/materials"


def test_virtual_rules_reads_do_not_write_and_material_history_is_isolated():
    book, base = create_book()
    _, other = create_book()
    rules = base + '/writing-rules'
    assert client.get(rules).json()['version'] is None
    assert client.get(rules + '/versions').json() == []
    assert client.get(rules + '/export.md').status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BookMaterialVersion).where(BookMaterialVersion.book_id == book['id'])) == 0
    v1 = client.post(rules + '/versions', json={'title': '写作规则', 'content': '## 必须遵守\n第三人称。', 'base_version_id': None}).json()
    assert v1['version_number'] == 1
    assert client.get(other + '/writing-rules').json()['version'] is None
    note = client.post(base, json={'title': '灵感随记', 'content': '初稿'}).json()
    note_url = base + '/' + note['material_key']
    v2 = client.post(note_url + '/versions', json={'title': '新的标题', 'content': '修改后', 'base_version_id': note['id']}).json()
    assert v2['version_number'] == 2
    assert client.post(note_url + '/versions', json={'title': '冲突', 'content': '旧窗口', 'base_version_id': note['id']}).status_code == 409
    assert client.get(other + '/' + note['material_key']).status_code == 404
    assert client.get(rules + f"/versions/{note['id']}").status_code == 404
    restored = client.post(note_url + f"/versions/{note['id']}/restore", json={'base_version_id': v2['id']}).json()
    assert (restored['title'], restored['content'], restored['version_number']) == ('灵感随记', '初稿', 3)
    assert len(client.get(note_url + '/versions').json()) == 3
    assert 'content' not in client.get(note_url + '/versions').json()[0]
    assert client.get(note_url + '/export.md').text == '初稿'
    assert client.get(note_url + '/export.md').headers['X-Material-Version'] == '3'
    assert client.post(base, json={'title': '  '}).status_code == 422
    assert client.post(rules + '/versions', json={'title': '改名', 'content': '', 'base_version_id': v1['id']}).status_code == 400
    assert client.post(rules + '/versions', json={'title': '写作规则', 'content': ''}).status_code == 422
    assert client.get(base).json()[0]['material_key'] == 'writing-rules'
    client.delete(f"/api/books/{book['id']}")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BookMaterialVersion).where(BookMaterialVersion.book_id == book['id'])) == 0


def test_concurrent_first_save_keeps_one_authoritative_version():
    _, base = create_book()
    gate = Barrier(2)
    def save(content):
        gate.wait(timeout=5)
        return client.post(base + '/writing-rules/versions', json={'title': '写作规则', 'content': content, 'base_version_id': None})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['第一个窗口', '第二个窗口']))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert len(client.get(base + '/writing-rules/versions').json()) == 1


def test_material_backup_roundtrip_and_old_formats():
    book, base = create_book()
    v1 = client.post(base + '/writing-rules/versions', json={'title': '写作规则', 'content': '规则一', 'base_version_id': None}).json()
    client.post(base + '/writing-rules/versions', json={'title': '写作规则', 'content': '规则二', 'base_version_id': v1['id']})
    note = client.post(base, json={'title': '另一份资料', 'content': '资料原文'}).json()
    backup = client.get(f"/api/books/{book['id']}/backup").json()
    assert backup['schema_version'] == 3 and len(backup['material_versions']) == 3
    def upload(path, archive):
        return client.post(path, files={'file': ('backup.json', json.dumps(archive).encode())})
    assert upload('/api/backups/preview', backup).json()['materials'] == 2
    copied = upload('/api/backups/restore', backup).json()
    copied_base = f"/api/books/{copied['id']}/materials"
    assert client.get(copied_base + '/writing-rules').json()['version']['content'] == '规则二'
    assert client.get(copied_base + '/' + note['material_key']).json()['version']['content'] == '资料原文'
    assert len(client.get(copied_base + '/writing-rules/versions').json()) == 2
    for schema in [1, 2]:
        legacy = {k: v for k, v in backup.items() if k != 'material_versions'}
        legacy['schema_version'] = schema
        restored = upload('/api/backups/restore', legacy).json()
        assert client.get(f"/api/books/{restored['id']}/materials/writing-rules").json()['version'] is None
    count = len(client.get('/api/books').json())
    backup['material_versions'].append(backup['material_versions'][0])
    assert upload('/api/backups/restore', backup).status_code == 400
    assert len(client.get('/api/books').json()) == count
