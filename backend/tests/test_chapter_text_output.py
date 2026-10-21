import asyncio
import json

import httpx
import pytest

from app.models import LLMProvider
from app.routers import writing_jobs as writing


def test_generated_body_normalizes_indent_and_blank_lines_idempotently():
    raw = '第20章 夜雨\r\n\r\n    第一段。   \r\n\r\n \r\n\t\r\n　　第二段。\r\n\t第三段。\r\n\r\n'
    title, body = writing.parse_chapter_text(raw)
    assert title == '夜雨'
    assert body == '　　第一段。\n\n　　第二段。\n\n　　第三段。'
    assert writing.parse_chapter_text(title + '\n' + body) == (title, body)


@pytest.mark.parametrize('raw,expected', [
    ('第20章 雨夜来客\n\n雨落在窗沿。\n他推开了门。', ('雨夜来客', '　　雨落在窗沿。\n\n　　他推开了门。')),
    ('\ufeff\n# 第二十章：雨夜来客\r\n\r\n雨落在窗沿。', ('雨夜来客', '　　雨落在窗沿。')),
    ('**第20章 雨夜来客**\n正文保留 **粗体** 与\n\n分段。', ('雨夜来客', '　　正文保留 **粗体** 与\n\n　　分段。')),
    ('雨夜来客\n正文第一段。\n正文第二段。', ('雨夜来客', '　　正文第一段。\n\n　　正文第二段。')),
])
def test_plain_chapter_title_and_body_are_extracted_without_json(monkeypatch, raw, expected):
    assert writing.parse_chapter_text(raw) == expected


@pytest.mark.parametrize('raw', ['第20章 雨夜来客', '第20章\n正文', '   ', '标题\n\n', '{"title":"","content":"正文"}'])
def test_missing_title_or_body_is_rejected(monkeypatch, raw):
    with pytest.raises(ValueError):
        writing.parse_chapter_text(raw)
