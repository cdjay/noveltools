import asyncio
import json

import httpx
import pytest

from app.models import LLMProvider
from app.routers import writing_jobs as writing


@pytest.mark.parametrize('raw,expected', [
    ('第20章 雨夜来客\n\n雨落在窗沿。\n他推开了门。', ('雨夜来客', '雨落在窗沿。\n他推开了门。')),
    ('\ufeff\n# 第二十章：雨夜来客\r\n\r\n雨落在窗沿。', ('雨夜来客', '雨落在窗沿。')),
    ('**第20章 雨夜来客**\n正文保留 **粗体** 与\n\n分段。', ('雨夜来客', '正文保留 **粗体** 与\n\n分段。')),
    ('雨夜来客\n正文第一段。\n正文第二段。', ('雨夜来客', '正文第一段。\n正文第二段。')),
])
def test_plain_chapter_title_and_body_are_extracted_without_json(monkeypatch, raw, expected):
    original = httpx.AsyncClient
    def respond(request):
        payload = json.loads(request.content)
        assert 'response_format' not in payload
        assert 'JSON' not in payload['messages'][0]['content']
        return httpx.Response(200, json={'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    provider = LLMProvider(model='gemini-test', base_url='https://test.invalid/v1', api_key='test', temperature=1, max_tokens=65536, timeout=1800)
    assert asyncio.run(writing.generate_chapter(provider, {'next_chapter_number': 20})) == expected


@pytest.mark.parametrize('raw', ['第20章 雨夜来客', '第20章\n正文', '   ', '标题\n\n', '{"title":"","content":"正文"}'])
def test_missing_title_or_body_is_rejected(monkeypatch, raw):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}]}))))
    provider = LLMProvider(model='gemini-test', base_url='https://test.invalid/v1', api_key='test', temperature=1, max_tokens=65536, timeout=1800)
    with pytest.raises(ValueError):
        asyncio.run(writing.generate_chapter(provider, {}))
