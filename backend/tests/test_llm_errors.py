import httpx
import json
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


from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app.llm_errors import llm_http_error


def describe(payload, headers=None, status=429):
    response = httpx.Response(status, json=payload, headers=headers,
                              request=httpx.Request('POST', 'https://model.invalid/v1/chat/completions'))
    return llm_http_error(httpx.HTTPStatusError('failure', request=response.request, response=response), 'test-model')


@pytest.mark.parametrize('payload, expected', [
    ({'detail': {'error': {'code': 'global_concurrency_rate_limit_exceeded'}}}, '并发'),
    ({'error': {'code': 'insufficient_quota'}}, '额度'),
    ({'error': {'message': 'Token rate limit exceeded'}}, 'Token'),
    ({'error': {'message': 'Too many requests'}}, '限流'),
    ({'error': {'code': ['unexpected'], 'type': {'unexpected': True}}}, '限流'),
])
def test_rate_limit_categories(payload, expected):
    assert expected in describe(payload)


def test_rate_limit_never_echoes_secrets_or_invents_retry_delay():
    result = describe({'error': {'message': 'private novel; api_key=secret'}}, {'Retry-After': 'secret'})
    assert 'secret' not in result and 'private novel' not in result
    assert '稍后' in result
    # Quota exhaustion needs configuration/account action, not a retry countdown.
    quota = describe({'error': {'type': 'insufficient_quota'}}, {'Retry-After': '30'})
    assert '额度' in quota and '30' not in quota


def test_retry_after_http_date_and_model_not_found():
    deadline = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=90), usegmt=True)
    assert '秒' in describe({}, {'Retry-After': deadline})
    assert '稍后' in describe({}, {'Retry-After': '-1'})
    assert '秒' not in describe({}, {'Retry-After': 'Infinity'})
    assert '模型“test-model”不存在' in describe({'error': {'code': 'model_not_found'}}, status=404)


@pytest.mark.parametrize('temperature, expected_ok', [(0.7, False), (1, True)])
def test_connection_checks_configured_temperature(monkeypatch, temperature, expected_ok):
    provider = client.post('/api/llm/providers', json={
        'base_url': 'https://temperature.invalid/v1', 'model': 'k3',
        'api_key': 'test-only', 'temperature': temperature,
    }).json()
    original = httpx.AsyncClient

    def respond(request):
        payload = json.loads(request.content)
        # Observed k3 behavior: omitted temperature defaults to 1.
        if payload.get('temperature', 1) != 1:
            return httpx.Response(400, json={'error': {
                'type': 'invalid_request_error',
                'message': 'invalid temperature: only 1 is allowed for this model',
            }})
        return httpx.Response(200, json={'choices': [{'message': {'content': '连接成功'}}]})

    monkeypatch.setattr(core.httpx, 'AsyncClient', lambda **kw: original(
        **kw, transport=httpx.MockTransport(respond)))
    result = client.post(f"/api/llm/providers/{provider['id']}/test").json()
    assert result['ok'] is expected_ok
    if not expected_ok:
        assert '温度' in result['message'] and '1' in result['message']


def test_temperature_error_explains_required_value_without_echoing_payload():
    result = describe({'error': {'type': 'invalid_request_error', 'message':
        'invalid temperature: only 1 is allowed for this model; api_key=secret; private novel'}}, status=400)
    assert '温度' in result and '1' in result and 'HTTP 400' in result
    assert 'secret' not in result and 'private novel' not in result
    assert '温度' not in describe({'error': {'message': 'other invalid parameter'}}, status=400)
