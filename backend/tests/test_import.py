from fastapi.testclient import TestClient
from app.main import app
from app.routers import core

client = TestClient(app)


def test_preview_gbk_split_and_reorder():
    book = client.post('/api/books', json={'title': '导入测试'}).json()
    url = f"/api/books/{book['id']}/chapters"
    raw = '第一章 开端\n内容一\n第二章 重逢\n内容二'.encode('gb18030')
    preview = client.post(url + '/import/preview', files={'file': ('全书.txt', raw)}).json()
    assert preview['encoding'] == 'gb18030'
    assert [i['title'] for i in preview['chapters']] == ['开端', '重逢']
    assert client.get(url).json() == []
    single = client.post(url + '/import/preview?split=false', files={'file': ('全书.txt', raw)}).json()
    assert len(single['chapters']) == 1
    chapters = client.post(url + '/import/confirm', json=preview).json()
    assert len(chapters) == 2
    ids = [c['id'] for c in chapters][::-1]
    result = client.post(url + '/reorder', json={'chapter_ids': ids})
    assert result.status_code == 200
    assert [c['title'] for c in result.json()] == ['重逢', '开端']
    assert client.get(f"/api/chapters/{ids[0]}").json()['current_content'] == '内容二'


def test_import_rolls_back_every_chapter_on_failure(monkeypatch):
    book = client.post('/api/books', json={'title': '失败导入'}).json()
    original = core.add_chapter
    def fail_second(book_id, payload, db):
        if payload.title == '第二章': raise RuntimeError('injected')
        return original(book_id, payload, db)
    monkeypatch.setattr(core, 'add_chapter', fail_second)
    url = f"/api/books/{book['id']}/chapters"
    with TestClient(app, raise_server_exceptions=False) as failing:
        assert failing.post(url + '/import/confirm', json={'chapters': [{'title': '第一章', 'content': '甲'}, {'title': '第二章', 'content': '乙'}]}).status_code == 500
    assert client.get(url).json() == []
