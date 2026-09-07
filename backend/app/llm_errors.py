import httpx


def llm_http_error(exc: httpx.HTTPStatusError, model: str) -> str:
    """Surface the error category without exposing arbitrary upstream payloads or credentials."""
    try:
        payload = exc.response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
    except ValueError:
        error = {}
    if isinstance(error, dict) and "model_not_found" in (error.get("type"), error.get("code")):
        return (f"模型“{model}”不存在，或当前 API Key 所属分组未配置该模型。"
                "请按服务模型列表填写完整模型 ID，注意大小写，并核对密钥分组。")
    if exc.response.status_code == 404:
        return "LLM 返回 HTTP 404：未找到聊天接口或上游资源。请核对 Base URL 的路径及服务配置。"
    return f"模型接口返回 HTTP {exc.response.status_code}，请检查 LLM 配置。"
