"""Bounded failover of one unchanged generation request; no content or credentials in logs."""
import hashlib
import json
import re
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from fastapi import HTTPException

from .database import SessionLocal
from .llm_errors import error_signals, llm_http_error
from .llm_routing import resolve_chain, MODULES
from .models import LLMProvider, LLMProviderCooldown, LLMFailoverEvent

writing_job_context = ContextVar('writing_job_context', default=None)
CONFIG_KEYS = ('model', 'base_url', 'temperature', 'max_tokens', 'timeout')


def snapshot_provider(provider):
    return {'id': provider.id, 'created_at': provider.created_at.isoformat(),
            'config': {key: getattr(provider, key) for key in CONFIG_KEYS}}


def snapshot_chain(db, module):
    providers = resolve_chain(db, module)
    return {**snapshot_provider(providers[0]), 'fallbacks': [snapshot_provider(p) for p in providers[1:]]}


def load_provider(saved):
    with SessionLocal() as db:
        provider = db.get(LLMProvider, saved['id'])
        if not provider or not provider.api_key.strip() or (saved.get('created_at') and provider.created_at.isoformat() != saved['created_at']):
            raise HTTPException(400, '任务模型已删除或密钥已清除，请修改模块分流后继续任务。')
        if provider.base_url != saved['config']['base_url']:
            raise HTTPException(400, '模型接口地址已改变，请确认配置后继续任务以更新快照。')
        return LLMProvider(id=provider.id, **saved['config'], api_key=provider.api_key)


def fingerprint(provider):
    return hashlib.sha256(json.dumps([provider.base_url, provider.model, provider.api_key]).encode()).hexdigest()


def fallback_reason(response):
    if response.status_code in (403, 429):
        return f'HTTP {response.status_code}，访问受限或用量限制'
    try:
        payload = response.json()
    except ValueError:
        return None
    # Successful prose may mention quotas. Inspect only an error envelope on HTTP 2xx.
    if response.is_success and (not isinstance(payload, dict) or 'error' not in payload or 'choices' in payload):
        return None
    evidence = ' '.join(error_signals(payload))
    quota = ('insufficient_quota', 'quota_exceeded', 'usage_limit_reached', 'rate_limit_exceeded',
             'billing_hard_limit', 'insufficient balance', 'insufficient_balance',
             'usage limit', 'quota exhausted', 'quota exceeded', '额度耗尽', '额度不足', '余额不足',
             '5-hour', '5 hour', 'five-hour', '5h limit', '5小时', '五小时', '5 小时')
    if any(term in evidence for term in quota):
        return '模型额度或用量窗口已耗尽'
    return None


def generation_http_error(exc, model):
    try:
        evidence = ' '.join(error_signals(exc.response.json()))
    except ValueError:
        evidence = ''
    if exc.response.status_code == 400:
        limit = re.search(r'maxoutputtokens.*?supported range.*?to (\d+) \(exclusive\)', evidence)
        if limit and 1 < int(limit.group(1)) <= 10000000:
            return (f'模型“{model}”的最大输出 Tokens 超出范围（HTTP 400），上游最多允许 {int(limit.group(1)) - 1:,}。'
                    '请降低最大输出 Tokens 后继续任务；此参数不是输入上下文容量，已有内容保留。')
        if any(term in evidence for term in ('context_length_exceeded', 'maximum context length', 'input token count exceeds', 'input is too long')):
            return f'模型“{model}”的输入超过上下文容量（HTTP 400），请核对模型限制与输入资料；原内容保留。'
    return llm_http_error(exc, model)


def check_response(response):
    response.raise_for_status()
    if fallback_reason(response):
        raise httpx.HTTPStatusError('Upstream quota error', request=response.request, response=response)


def cooldown_deadline(response):
    now = datetime.now(timezone.utc)
    retry = response.headers.get('Retry-After', '').strip()
    try:
        candidate = now + timedelta(seconds=float(retry)) if retry.replace('.', '', 1).isdigit() else parsedate_to_datetime(retry)
        if candidate.tzinfo and candidate > now:
            return min(candidate, now + timedelta(days=1)).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        pass
    # Absolute Unix reset times, when the gateway actually supplies them.
    for header in ('x-ratelimit-reset', 'x-ratelimit-reset-requests', 'x-ratelimit-reset-tokens'):
        try:
            candidate = datetime.fromtimestamp(float(response.headers.get(header, '')), timezone.utc)
            if candidate > now:
                return min(candidate, now + timedelta(days=1)).replace(tzinfo=None)
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    return (now + timedelta(minutes=5)).replace(tzinfo=None)


def log_event(module, message):
    job_id = writing_job_context.get()
    if job_id is not None:
        with SessionLocal.begin() as db:
            db.add(LLMFailoverEvent(writing_job_id=job_id, module=module, message=message))


async def run_with_fallback(saved, module, operation):
    """Return (validated result, actual provider). Each configured provider is tried at most once."""
    candidates = [saved, *saved.get('fallbacks', [])]
    bounded_failover = len(candidates) > 1
    attempted = set()
    skipped = []
    for candidate in candidates:
        if candidate['id'] in attempted:
            continue
        attempted.add(candidate['id'])
        provider = load_provider(candidate)
        if bounded_failover:
            with SessionLocal() as db:
                cooldown = db.get(LLMProviderCooldown, provider.id)
                if cooldown and cooldown.fingerprint == fingerprint(provider) and cooldown.until > datetime.utcnow():
                    skipped.append(provider.model)
                    log_event(module, f'{MODULES[module][0]}：{provider.model} 仍在冷却，跳过；继续检查下一个备用模型。')
                    continue
        if skipped:
            log_event(module, f'{MODULES[module][0]}：切换至备用模型 {provider.model}，发送相同提示词和资料。')
        try:
            result = await operation(provider)
            if skipped:
                log_event(module, f'{MODULES[module][0]}：备用模型 {provider.model} 返回成功。')
            return result, provider
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
            response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
            reason = fallback_reason(response) if response is not None else None
            # Summaries/state may use their own backups for unavailable or invalid
            # responses. Writing retains its stricter, explicitly configured policy;
            # a prose conflict report must not be bypassed by another model.
            quota_failure = bool(reason)
            if not reason and module != 'chapter_writing':
                if response is not None:
                    reason = f'接口报错 HTTP {response.status_code}'
                elif isinstance(exc, httpx.RequestError):
                    reason = '连接中断或超时'
                else:
                    reason = '返回格式无法使用'
            if not bounded_failover or not reason:
                raise
            with SessionLocal.begin() as db:
                db.merge(LLMProviderCooldown(provider_id=provider.id, fingerprint=fingerprint(provider),
                    until=cooldown_deadline(response) if quota_failure else datetime.utcnow() + timedelta(minutes=1), reason=reason))
            skipped.append(provider.model)
            log_event(module, f'{MODULES[module][0]}：{provider.model} 因{reason}暂时跳过；继续检查下一个备用模型。')
    raise HTTPException(503, f'{MODULES[module][0]}的主模型和备用模型均受限、调用失败或仍在冷却（{"、".join(skipped)}）。已有内容保留，请等待服务或额度恢复，或修改本模块分流后继续。')
