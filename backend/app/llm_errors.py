from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math

import httpx


def error_signals(payload, depth=0) -> list[str]:
    """Read common gateway wrappers for classification, never for direct display."""
    if depth > 4:
        return []
    if isinstance(payload, str):
        return [payload[:4000].lower()]
    if not isinstance(payload, dict):
        return []
    signals = [payload[key][:4000].lower() for key in ('code', 'type', 'message')
               if isinstance(payload.get(key), str)]
    for key in ('error', 'detail'):
        signals.extend(error_signals(payload.get(key), depth + 1))
    return signals


def retry_wait(response: httpx.Response) -> str:
    value = response.headers.get('Retry-After', '').strip()
    seconds = None
    try:
        if value.isascii() and value.isdigit() and len(value) <= 10:
            seconds = int(value)
        elif value:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is not None:
                seconds = math.ceil((deadline - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        pass
    if seconds is not None and seconds > 0:
        return f'服务建议等待约 {seconds} 秒后重试。'
    return '请稍后手动重试；服务未提供有效等待时间。'


def llm_http_error(exc: httpx.HTTPStatusError, model: str) -> str:
    """Surface the error category without exposing arbitrary upstream payloads or credentials."""
    try:
        payload = exc.response.json()
    except ValueError:
        payload = {}
    signals = error_signals(payload)
    if "model_not_found" in signals:
        return (f"模型“{model}”不存在，或当前 API Key 所属分组未配置该模型。"
                "请按服务模型列表填写完整模型 ID，注意大小写，并核对密钥分组。")
    if exc.response.status_code == 404:
        return "LLM 返回 HTTP 404：未找到聊天接口或上游资源。请核对 Base URL 的路径及服务配置。"
    if exc.response.status_code == 429:
        evidence = ' '.join(signals)
        if any(term in evidence for term in ('insufficient_quota', 'quota_exceeded', 'insufficient balance',
                                             'insufficient_balance', 'billing_hard_limit', '余额不足', '额度耗尽')):
            return '模型服务额度不足（HTTP 429）。请核对服务账号的可用额度或配额；等待重试可能无法解决。'
        if any(term in evidence for term in ('concurrency', 'concurrent', '并发')):
            category = '模型服务并发限流（HTTP 429），当前可用并发已满。'
        elif any(term in evidence for term in ('tokens per min', 'tokens_per_min', 'token rate', 'tpm', 'token用量')):
            category = '模型服务 Token 用量限流（HTTP 429）。请等待用量窗口恢复，或核对服务的 Token 配额。'
        else:
            category = '模型服务限流（HTTP 429），可能达到请求频率、并发或用量限制。'
        return category + retry_wait(exc.response) + '程序不会自动重复提交；已有内容保留。'
    return f"模型接口返回 HTTP {exc.response.status_code}，请检查 LLM 配置。"
