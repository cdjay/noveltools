"""Decode strictly and preview chapter boundaries before committing any content."""
import re
from fastapi import HTTPException

HEADING = re.compile(r"^\s*(?:#{1,3}\s+)?(?:第[零〇一二三四五六七八九十百千万两\d]+[章节回卷][\s　:：、.．-]*(.*)|Chapter\s+\d+\b[\s:.-]*(.*))$", re.IGNORECASE)
MARKDOWN = re.compile(r"^#{1,2}\s+(.+)$")


def decode_text(raw: bytes):
    if len(raw) > 30 * 1024 * 1024:
        raise HTTPException(400, "文件超过 30 MB，请分批导入。")
    encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            if '\x00' in text:
                raise ValueError()
            if not text.strip():
                raise HTTPException(400, "文件没有正文内容。")
            return text.replace('\r\n', '\n').replace('\r', '\n'), encoding
        except (UnicodeError, ValueError):
            continue
    raise HTTPException(400, "无法识别文件编码，请另存为 UTF-8 文本后导入。")


def split_chapters(text: str, title: str, split: bool = True):
    if not split:
        return [{'title': title, 'content': text}]
    sections, lines, current_title, found = [], [], title, False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip('\n')
        match = HEADING.fullmatch(stripped) or MARKDOWN.fullmatch(stripped)
        if match:
            if found or ''.join(lines).strip():
                sections.append({'title': current_title if found else f'{title} · 序言', 'content': ''.join(lines)})
            current_title = next((value.strip() for value in match.groups() if value and value.strip()), stripped.strip('# '))
            lines, found = [], True
        else:
            lines.append(line)
    if found:
        sections.append({'title': current_title, 'content': ''.join(lines)})
        return sections
    return [{'title': title, 'content': text}]
