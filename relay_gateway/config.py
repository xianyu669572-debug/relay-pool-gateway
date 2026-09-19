"""配置加载与校验（TOML + 环境变量密钥引用，零第三方依赖）。"""

from __future__ import annotations

import os
import tomllib
import hashlib
import json as _json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _as_int(value: Any, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"字段 {name} 必须是整数，收到 {value!r}")


def _as_bool(value: Any, default: bool, name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


@dataclass
class CircuitConfig:
    failure_threshold: int = 3          # 连续失败次数达到后熔断
    cooldown_s: float = 60.0            # OPEN 状态冷却时间
    half_open_max_probes: int = 1       # HALF_OPEN 期间并发探测上限
    disable_on_401: bool = True         # 401 视为 key 失效，直接禁用等待人工


@dataclass
class HealthConfig:
    enabled: bool = True
    url_suffix: str = "/models"         # 健康检查路径（GET）
    interval_s: float = 60.0
    timeout_s: float = 8.0
    expected_status: tuple[int, ...] = (200,)


@dataclass
class RetryConfig:
    per_provider_retries: int = 1       # 同一渠道内的重试次数
    backoff_s: float = 0.5              # 指数退避基数
    max_fallback_hops: int = 5          # 单请求最多尝试的渠道数（有界，防无限链）


@dataclass
class ProviderConfig:
    id: str
    name: str
    priority: int
    base_url: str
    api_key_env: str | None
    models: list[str] = field(default_factory=list)
    model_map: dict[str, str] = field(default_factory=dict)  # 虚拟模型名 -> 本渠道实际模型名
    weight: int = 1
    timeout_s: float = 60.0
    group: str = "default"
    health: HealthConfig = field(default_factory=HealthConfig)
    circuit: CircuitConfig = field(default_factory=CircuitConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    extra_headers: dict[str, str] = field(default_factory=dict)
    safe: bool = False                  # True = 官方/本地安全通道（不参与普通优先级池）
    local: bool = False                 # True = 本地模型端点（Ollama/vLLM）

    @property
    def api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None


@dataclass
class PrivacyConfig:
    enabled: bool = True
    mode: str = "safe_route"            # safe_route | redact | block | local
    vault_paths: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    sticky_session: bool = True
    session_ttl_s: float = 14400.0
    audit_log: str = "audit.jsonl"
    audit_max_bytes: int = 10 * 1024 * 1024
    scan_tool_args: bool = True
    block_placeholder_like: bool = True
    custom_patterns: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RoutingConfig:
    strategy: str = "priority"          # priority | weighted（同优先级内按权重轮询）
    fallback_on: list[int] = field(default_factory=lambda: [408, 429, 500, 502, 503, 504])
    allow_degraded_fallback: bool = True  # 全部渠道不健康时是否仍允许尝试最优先渠道
    max_fallback_hops: int = 5          # 单请求最多尝试的渠道数（有界，防无限链）


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8400
    api_keys: list[str] = field(default_factory=list)
    request_max_bytes: int = 20 * 1024 * 1024
    state_file: str = "gateway-state.json"


@dataclass
class GatewayConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    providers: list[ProviderConfig] = field(default_factory=list)

    def normal_providers(self) -> list[ProviderConfig]:
        return [p for p in self.providers if not p.safe]

    def safe_providers(self) -> list[ProviderConfig]:
        return [p for p in self.providers if p.safe]

    def local_providers(self) -> list[ProviderConfig]:
        return [p for p in self.providers if p.local]

    def validate(self) -> None:
        if (
            self.server.host not in ("127.0.0.1", "::1", "localhost")
            and not self.server.api_keys
        ):
            raise ValueError("server.host 非回环地址时必须配置 server.api_keys")

    def state_fingerprint(self) -> str:
        """由渠道关键配置生成指纹；配置变化后旧熔断状态自动失效。"""
        items = []
        for p in sorted(self.providers, key=lambda x: x.id):
            items.append(
                {
                    "id": p.id,
                    "base_url": p.base_url,
                    "api_key_env": p.api_key_env,
                    "models": p.models,
                    "priority": p.priority,
                    "weight": p.weight,
                    "timeout_s": p.timeout_s,
                    "safe": p.safe,
                    "local": p.local,
                    "health": {
                        "enabled": p.health.enabled,
                        "url_suffix": p.health.url_suffix,
                        "interval_s": p.health.interval_s,
                        "timeout_s": p.health.timeout_s,
                        "expected_status": list(p.health.expected_status),
                    },
                    "circuit": {
                        "failure_threshold": p.circuit.failure_threshold,
                        "cooldown_s": p.circuit.cooldown_s,
                        "half_open_max_probes": p.circuit.half_open_max_probes,
                        "disable_on_401": p.circuit.disable_on_401,
                    },
                    "retry": {
                        "per_provider_retries": p.retry.per_provider_retries,
                        "backoff_s": p.retry.backoff_s,
                        "max_fallback_hops": p.retry.max_fallback_hops,
                    },
                }
            )
        blob = _json.dumps(items, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


def _load_health(raw: dict[str, Any] | None) -> HealthConfig:
    raw = raw or {}
    return HealthConfig(
        enabled=_as_bool(raw.get("enabled"), True, "health.enabled"),
        url_suffix=str(raw.get("url_suffix", "/models")),
        interval_s=float(raw.get("interval_s", 60)),
        timeout_s=float(raw.get("timeout_s", 8)),
        expected_status=tuple(int(s) for s in raw.get("expected_status", [200])),
    )


def _load_circuit(raw: dict[str, Any] | None) -> CircuitConfig:
    raw = raw or {}
    return CircuitConfig(
        failure_threshold=_as_int(raw.get("failure_threshold"), 3, "circuit.failure_threshold"),
        cooldown_s=float(raw.get("cooldown_s", 60)),
        half_open_max_probes=_as_int(raw.get("half_open_max_probes"), 1, "circuit.half_open_max_probes"),
        disable_on_401=_as_bool(raw.get("disable_on_401"), True, "circuit.disable_on_401"),
    )


def _load_retry(raw: dict[str, Any] | None) -> RetryConfig:
    raw = raw or {}
    return RetryConfig(
        per_provider_retries=_as_int(raw.get("per_provider_retries"), 1, "retry.per_provider_retries"),
        backoff_s=float(raw.get("backoff_s", 0.5)),
        max_fallback_hops=_as_int(raw.get("max_fallback_hops"), 5, "retry.max_fallback_hops"),
    )


def _load_provider(raw: dict[str, Any]) -> ProviderConfig:
    pid = str(raw.get("id", "")).strip()
    if not pid:
        raise ValueError("provider 缺少 id")
    if not raw.get("base_url"):
        raise ValueError(f"provider {pid} 缺少 base_url")
    provider = ProviderConfig(
        id=pid,
        name=str(raw.get("name", pid)),
        priority=_as_int(raw.get("priority"), 100, f"provider.{pid}.priority"),
        base_url=str(raw["base_url"]).rstrip("/"),
        api_key_env=raw.get("api_key_env"),
        models=[str(m) for m in raw.get("models", [])],
        model_map={str(k): str(v) for k, v in raw.get("model_map", {}).items()},
        weight=max(1, _as_int(raw.get("weight"), 1, f"provider.{pid}.weight")),
        timeout_s=float(raw.get("timeout_s", 60)),
        group=str(raw.get("group", "default")),
        health=_load_health(raw.get("health")),
        circuit=_load_circuit(raw.get("circuit")),
        retry=_load_retry(raw.get("retry")),
        extra_headers={str(k): str(v) for k, v in raw.get("extra_headers", {}).items()},
        safe=_as_bool(raw.get("safe"), False, f"provider.{pid}.safe"),
        local=_as_bool(raw.get("local"), False, f"provider.{pid}.local"),
    )
    return provider


def load_config(path: str | Path) -> GatewayConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with p.open("rb") as f:
        data = tomllib.load(f)

    def _expand_env(value: str) -> str:
        """支持 ${ENV_NAME} 引用环境变量，避免把 vault 路径等敏感元数据写进配置文件。"""
        if value.startswith("${") and value.endswith("}"):
            return os.environ.get(value[2:-1], "")
        return value

    server_raw = data.get("server", {}) or {}
    routing_raw = data.get("routing", {}) or {}
    privacy_raw = data.get("privacy", {}) or {}

    server = ServerConfig(
        host=str(server_raw.get("host", "127.0.0.1")),
        port=_as_int(server_raw.get("port"), 8400, "server.port"),
        api_keys=[str(k) for k in server_raw.get("api_keys", [])],
        request_max_bytes=_as_int(server_raw.get("request_max_bytes"), 20 * 1024 * 1024, "server.request_max_bytes"),
        state_file=str(server_raw.get("state_file", "gateway-state.json")),
    )
    routing = RoutingConfig(
        strategy=str(routing_raw.get("strategy", "priority")),
        fallback_on=[int(s) for s in routing_raw.get("fallback_on", [408, 429, 500, 502, 503, 504])],
        allow_degraded_fallback=_as_bool(routing_raw.get("allow_degraded_fallback"), True, "routing.allow_degraded_fallback"),
        max_fallback_hops=_as_int(routing_raw.get("max_fallback_hops"), 5, "routing.max_fallback_hops"),
    )
    privacy = PrivacyConfig(
        enabled=_as_bool(privacy_raw.get("enabled"), True, "privacy.enabled"),
        mode=str(privacy_raw.get("mode", "safe_route")),
        vault_paths=[_expand_env(str(x)) for x in privacy_raw.get("vault_paths", [])],
        keywords=[str(x) for x in privacy_raw.get("keywords", [])],
        sticky_session=_as_bool(privacy_raw.get("sticky_session"), True, "privacy.sticky_session"),
        session_ttl_s=float(privacy_raw.get("session_ttl_s", 14400)),
        audit_log=str(privacy_raw.get("audit_log", "audit.jsonl")),
        audit_max_bytes=_as_int(privacy_raw.get("audit_max_bytes"), 10 * 1024 * 1024, "privacy.audit_max_bytes"),
        scan_tool_args=_as_bool(privacy_raw.get("scan_tool_args"), True, "privacy.scan_tool_args"),
        block_placeholder_like=_as_bool(privacy_raw.get("block_placeholder_like"), True, "privacy.block_placeholder_like"),
        custom_patterns=[dict(x) for x in privacy_raw.get("custom_patterns", [])],
    )
    if privacy.mode not in ("safe_route", "redact", "block", "local"):
        raise ValueError(f"privacy.mode 必须是 safe_route/redact/block/local 之一，收到 {privacy.mode}")

    providers = [_load_provider(raw) for raw in data.get("providers", [])]
    if not providers:
        raise ValueError("至少需要一个 provider")
    ids = [p.id for p in providers]
    if len(ids) != len(set(ids)):
        raise ValueError("provider id 重复")
    for p in providers:
        for virtual, actual in p.model_map.items():
            if not virtual or not actual:
                raise ValueError(f"provider {p.id} 的 model_map 不能有空键或空值")

    cfg = GatewayConfig(server=server, routing=routing, privacy=privacy, providers=providers)
    cfg.validate()
    return cfg
