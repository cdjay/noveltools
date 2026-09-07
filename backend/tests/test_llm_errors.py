import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.routers import core

client = TestClient(app)


def test_connection_test_reports_model_not_found_instead_of_bad_address(monkeypatch):
    provider = client.post('/api/llm/providers', json={
        'provider_name': '大小写错误复现', 'base_url': 'http://127.0.0.1:8080/v1',
        'model': 'deepseek-v4-flash', 'api_key': 'test-only', 'enabled': True,
    }).json()
    original_client = httpx.AsyncClient

    def respond(request):
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(404, json={'error': {
            'message': 'Model "deepseek-v4-flash" is not supported by any configured account in this group',
            'type': 'model_not_found',
        }})

    monkeypatch.setattr(core.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    result = client.post(f"/api/llm/providers/{provider['id']}/test").json()
    assert result['ok'] is False
    assert 'deepseek-v4-flash' in result['message']
    assert '大小写' in result['message']
    assert '分组' in result['message']
    assert '检查服务状态和接口地址' not in result['message']
