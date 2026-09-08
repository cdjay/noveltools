import httpx


DEFAULT_LLM_TIMEOUT = 1800


def llm_timeout(seconds: int) -> httpx.Timeout:
    """Allow long model processing without waiting equally long for a dead endpoint."""
    return httpx.Timeout(read=seconds, write=seconds, connect=min(seconds, 30), pool=min(seconds, 30))
