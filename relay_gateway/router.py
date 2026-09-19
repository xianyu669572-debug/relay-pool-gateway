"""路由引擎：优先级排序 + 同优先级权重轮询 + 有界故障转移 + 按状态码分类。"""

from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from .config import GatewayConfig, ProviderConfig
from .health import HealthMonitor
from .upstream import UpstreamError, UpstreamResponse, call_json, open_stream


@dataclass
class Attempt:
    provider_id: str
    ok: bool
    status: int | None = None
    error: str = ""
    latency_ms: float = 0.0


@dataclass
class RouteResult:
    status: int
    headers: dict[str, str]
    body: bytes | None = None
    stream: Iterator[bytes] | None = None
    provider_id: str = ""
    attempts: list[Attempt] = field(default_factory=list)


class Router:
    def __init__(self, config: GatewayConfig, health: HealthMonitor):
        self.config = config
        self.health = health
        self._rr: dict[int, itertools.cycle] = {}
        self._lock = threading.Lock()

    def _cycle(self, priority: int) -> itertools.cycle:
        with self._lock:
            if priority not in self._rr:
                self._rr[priority] = itertools.cycle(range(2**31))
            return self._rr[priority]

    def _candidates(self, pool: list[ProviderConfig], model: str) -> list[ProviderConfig]:
        """按优先级升序（数值小优先）；同优先级内权重轮询。"""
        by_priority: dict[int, list[ProviderConfig]] = {}
        for p in pool:
            if (
                p.models
                and model
                and model not in p.models
                and model not in p.model_map
            ):
                continue
            by_priority.setdefault(p.priority, []).append(p)

        ordered: list[ProviderConfig] = []
        for priority in sorted(by_priority):
            group = by_priority[priority]
            if len(group) == 1:
                ordered.append(group[0])
                continue
            # 权重轮询：按权重展开一个轮转序列
            expanded: list[ProviderConfig] = []
            for p in group:
                expanded.extend([p] * max(1, p.weight))
            cycle = itertools.cycle(expanded)
            seen = 0
            visited: set[str] = set()
            offset = next(self._cycle(priority)) % max(1, len(expanded))
            for _ in range(offset):
                next(cycle)
            while seen < len(group):
                cand = next(cycle)
                if cand.id in visited:
                    continue
                visited.add(cand.id)
                ordered.append(cand)
                seen += 1
        return ordered

    def _available_candidates(self, pool: list[ProviderConfig], model: str) -> list[ProviderConfig]:
        candidates = self._candidates(pool, model)
        available = [p for p in candidates if self.health.is_available(p)]
        if available:
            return available
        if self.config.routing.allow_degraded_fallback:
            # 全部不健康：仍按优先级尽力而为（跳过 401 隔离）
            return [p for p in candidates if not self.health.breakers[p.id].state.quarantined]
        return []

    def _should_failover(self, status: int) -> bool:
        if status == 400:
            return False
        if status == 401 or status in self.config.routing.fallback_on or status in (403, 404, 408, 409, 429) or status >= 500:
            return True
        return status >= 400

    @staticmethod
    def normalize_payload_for_upstream(payload: dict[str, Any]) -> dict[str, Any]:
        """chat/completions 上游兼容：developer role -> system（Hermes 会发 developer，DeepSeek 等不认）。"""
        out = dict(payload)
        messages = out.get("messages")
        if isinstance(messages, list):
            normalized = []
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "developer":
                    normalized.append({**msg, "role": "system"})
                else:
                    normalized.append(msg)
            out["messages"] = normalized
        return out

    def execute(
        self,
        payload: dict[str, Any],
        pool: list[ProviderConfig],
        model: str,
        path: str,
        stream: bool,
        request_id: str,
    ) -> RouteResult:
        candidates = self._available_candidates(pool, model)
        if not candidates:
            return RouteResult(
                status=503,
                headers={"Content-Type": "application/json"},
                body=json.dumps({"error": {"message": "no available provider", "type": "no_provider"}}, ensure_ascii=False).encode(),
                provider_id="",
                attempts=[],
            )

        max_hops = min(self.config.routing.max_fallback_hops, len(candidates))
        max_hops = max(1, max_hops)
        attempts: list[Attempt] = []

        for provider in candidates[:max_hops]:
            out_payload = payload
            mapped = provider.model_map.get(model)
            if mapped:
                out_payload = dict(payload)
                out_payload["model"] = mapped
            out_payload = self.normalize_payload_for_upstream(out_payload)
            body = json.dumps(out_payload, ensure_ascii=False).encode("utf-8")
            retries = provider.retry.per_provider_retries
            for attempt_no in range(retries + 1):
                start = time.monotonic()
                try:
                    if stream:
                        status, headers, stream_iter = open_stream(provider, path, body, provider.timeout_s)
                        # 拿到首个数据块前仍可安全 failover；首个字节后提交
                        it = iter(stream_iter)
                        try:
                            first = next(it)
                        except StopIteration:
                            first = None
                        except UpstreamError as exc:
                            raise exc
                        latency = (time.monotonic() - start) * 1000
                        if status >= 400:
                            attempts.append(Attempt(provider.id, False, status, f"http {status}", latency))
                            self.health.record_failure(provider.id, f"http {status}")
                            if self._should_failover(status):
                                break  # 换下一个渠道
                            return RouteResult(
                                status=status,
                                headers={"Content-Type": "application/json"},
                                body=b'{"error":{"message":"upstream error"}}',
                                provider_id=provider.id,
                                attempts=attempts,
                            )
                        self.health.record_success(provider.id)
                        attempts.append(Attempt(provider.id, True, status, "", latency))

                        def chain(first_chunk: bytes | None, inner: Iterator[bytes]) -> Iterator[bytes]:
                            if first_chunk is not None:
                                yield first_chunk
                            yield from inner

                        return RouteResult(
                            status=status,
                            headers=headers,
                            stream=chain(first, it),
                            provider_id=provider.id,
                            attempts=attempts,
                        )
                    else:
                        resp: UpstreamResponse = call_json(provider, path, body, provider.timeout_s)
                        latency = (time.monotonic() - start) * 1000
                        if resp.status >= 400:
                            attempts.append(Attempt(provider.id, False, resp.status, f"http {resp.status}", latency))
                            if resp.status == 401 and provider.circuit.disable_on_401:
                                self.health.quarantine(provider.id, "401 unauthorized")
                            elif self._should_failover(resp.status):
                                self.health.record_failure(provider.id, f"http {resp.status}")
                            if self._should_failover(resp.status):
                                break
                            return RouteResult(
                                status=resp.status,
                                headers={"Content-Type": "application/json"},
                                body=resp.body,
                                provider_id=provider.id,
                                attempts=attempts,
                            )
                        self.health.record_success(provider.id)
                        attempts.append(Attempt(provider.id, True, resp.status, "", latency))
                        return RouteResult(
                            status=resp.status,
                            headers=resp.headers,
                            body=resp.body,
                            provider_id=provider.id,
                            attempts=attempts,
                        )
                except UpstreamError as exc:
                    latency = (time.monotonic() - start) * 1000
                    attempts.append(Attempt(provider.id, False, exc.status, exc.message, latency))
                    if exc.status == 429 and exc.retry_after is not None:
                        self.health.record_throttle(provider.id, exc.retry_after)
                    if exc.status == 401 and provider.circuit.disable_on_401:
                        self.health.quarantine(provider.id, "401 unauthorized")
                    else:
                        self.health.record_failure(provider.id, exc.message)
                    if not exc.retryable and exc.status not in (401, 403, 404, 429):
                        return RouteResult(
                            status=exc.status or 502,
                            headers={"Content-Type": "application/json"},
                            body=json.dumps({"error": {"message": exc.message[:300], "type": "upstream_error"}}, ensure_ascii=False).encode(),
                            provider_id=provider.id,
                            attempts=attempts,
                        )
                    if attempt_no < retries:
                        time.sleep(provider.retry.backoff_s * (2**attempt_no))
                    else:
                        break
                except Exception as exc:  # 兜底：本地错误不算上游故障
                    latency = (time.monotonic() - start) * 1000
                    attempts.append(Attempt(provider.id, False, None, f"local error: {exc}", latency))
                    break

        return RouteResult(
            status=502,
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "error": {
                        "message": "all providers failed",
                        "type": "all_providers_failed",
                        "attempts": [{"provider": a.provider_id, "status": a.status, "error": a.error[:200]} for a in attempts],
                    }
                },
                ensure_ascii=False,
            ).encode(),
            provider_id="",
            attempts=attempts,
        )
