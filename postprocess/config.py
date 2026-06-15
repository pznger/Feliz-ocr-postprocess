"""OCR 后处理共享配置。

所有需要调 LLM 的模块统一从这里拿客户端，避免散落各处。
"""

from __future__ import annotations

import os
import time
from typing import Any

# ── FelizAI API（OpenAI 兼容） ──────────────────────────
FELIZ_BASE_URL = "https://v2.llm.api.felizai.cn/v1"
FELIZ_API_KEY = os.environ.get("FELIZ_API_KEY", "sk-tmGVKKze4krQQJ2yd4xhMe8SA23uFj5wfcr8Z1TYHHcZ7X3W")
FELIZ_MODEL = "qwen-vl-plus"

# 默认忽略系统 HTTP(S)_PROXY，避免代理导致 SSL EOF（Windows 常见）
# 若必须走代理，设置环境变量 FELIZ_TRUST_ENV=1
DEFAULT_TRUST_ENV = os.environ.get("FELIZ_TRUST_ENV", "").lower() in ("1", "true", "yes")
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_ATTEMPTS = 5


def get_openai_client(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    trust_env: bool | None = None,
    timeout: float = DEFAULT_TIMEOUT,
):
    """返回一个已配置的 OpenAI 兼容客户端。

    trust_env=False（默认）时不读取 HTTP_PROXY / HTTPS_PROXY，
    直连 API，可避免经错误代理握手时的 SSLEOFError。
    """
    import httpx
    from openai import OpenAI  # type: ignore

    use_trust_env = DEFAULT_TRUST_ENV if trust_env is None else trust_env
    http_client = httpx.Client(trust_env=use_trust_env, timeout=timeout)
    return OpenAI(
        base_url=base_url or FELIZ_BASE_URL,
        api_key=api_key or FELIZ_API_KEY,
        http_client=http_client,
        max_retries=0,
    )


def create_chat_completion(client, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS, **kwargs: Any):
    """调用 chat.completions.create，对瞬时网络错误指数退避重试。"""
    from openai import (  # type: ignore
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    retryable = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except retryable as err:
            last_err = err
            if attempt >= max_attempts:
                raise
            delay = min(2.0 * (2 ** (attempt - 1)), 60.0)
            print(
                f"\n  [api] 请求失败 ({attempt}/{max_attempts}): {err!s}；{delay:.0f}s 后重试...",
                flush=True,
            )
            time.sleep(delay)

    assert last_err is not None
    raise last_err
