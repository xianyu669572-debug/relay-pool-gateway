"""上游 HTTP 客户端：普通 JSON 与 SSE 流式透传（urllib，零依赖）。"""

from __future__ import annotations

import socket
import time as _time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Iterator

from .config import ProviderConfig


class UpstreamError(Exception):
    def __init__(
        self,
        message: str,
        status: int | None = None,
        retryable: bool = True,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class UpstreamResponse:
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        import json

        return json.loads(self.body.decode("utf-8", errors="replace"))


def _headers(provider: ProviderConfig, extra: dict[str, str] | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **provider.extra_headers,
    }
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    if extra:
        headers.update(extra)
    return headers


def _build_url(provider: ProviderConfig, path: str) -> str:
    """拼接上游 URL；base_url 以 /v1 结尾且 path 以 /v1 开头时避免双 /v1。"""
    base = provider.base_url.rstrip("/")
    if base.endswith("/v1") and path.startswith("/v1"):
        return base + path[3:]
    return base + path


def _parse_retry_after(headers) -> float | None:
    """解析 Retry-After：秒数或 HTTP-date；失败返回 None；上限 3600 秒。"""
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            dt = parsedate_to_datetime(raw)
            seconds = max(0.0, dt.timestamp() - _time.time())
        except Exception:
            return None
    return min(max(0.0, seconds), 3600.0)


def _http_error_to_upstream(exc: urllib.error.HTTPError) -> UpstreamError:
    status = exc.code
    retryable = status in (408, 429) or status >= 500
    try:
        body = exc.read(4096).decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return UpstreamError(
        f"upstream {status}: {body[:300]}",
        status=status,
        retryable=retryable,
        retry_after=_parse_retry_after(exc.headers),
    )


def call_json(
    provider: ProviderConfig,
    path: str,
    body: bytes,
    timeout_s: float,
    extra_headers: dict[str, str] | None = None,
) -> UpstreamResponse:
    url = _build_url(provider, path)
    req = urllib.request.Request(url, data=body, headers=_headers(provider, extra_headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = resp.read()
            return UpstreamResponse(
                status=resp.status,
                reason=getattr(resp, "reason", "") or "",
                headers={k: v for k, v in resp.headers.items()},
                body=data,
            )
    except urllib.error.HTTPError as exc:
        raise _http_error_to_upstream(exc) from exc
    except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as exc:
        raise UpstreamError(f"connect error: {exc}") from exc


def open_stream(
    provider: ProviderConfig,
    path: str,
    body: bytes,
    timeout_s: float,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], Iterator[bytes]]:
    """打开 SSE 流。返回 (status, headers, 行迭代器)。迭代器内部把中断转为 UpstreamError。"""
    url = _build_url(provider, path)
    req = urllib.request.Request(url, data=body, headers=_headers(provider, extra_headers), method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
    except urllib.error.HTTPError as exc:
        raise _http_error_to_upstream(exc) from exc
    except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as exc:
        raise UpstreamError(f"connect error: {exc}") from exc

    headers = {k: v for k, v in resp.headers.items()}
    status = resp.status

    def lines() -> Iterator[bytes]:
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                yield line
        except (socket.timeout, TimeoutError) as exc:
            raise UpstreamError(f"stream read timeout: {exc}", status=408) from exc
        except (ConnectionError, OSError) as exc:
            raise UpstreamError(f"stream broken: {exc}", status=None) from exc
        finally:
            resp.close()

    return status, headers, lines()
