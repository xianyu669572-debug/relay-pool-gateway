"""健康检查 + 三态熔断（CLOSED/OPEN/HALF_OPEN）+ 401 隔离。"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import ProviderConfig
from .state import CircuitStateStore


@dataclass
class CircuitState:
    state: str = "CLOSED"          # CLOSED | OPEN | HALF_OPEN
    consecutive_failures: int = 0
    opened_at: float = 0.0
    probes_used: int = 0
    quarantined: bool = False      # 401 key 失效，等待人工/进程重启
    last_error: str = ""


@dataclass
class HealthSnapshot:
    provider_id: str
    circuit: str
    last_health_check_ok: bool | None
    last_health_check_at: float | None
    quarantined: bool


class CircuitBreaker:
    """按 provider 独立的三态熔断器。"""

    def __init__(self, provider: ProviderConfig):
        self.provider = provider
        self.state = CircuitState()
        self.lock = threading.Lock()

    def is_available(self) -> bool:
        with self.lock:
            st = self.state
            if st.quarantined:
                return False
            if st.state == "CLOSED":
                return True
            if st.state == "OPEN":
                if time.time() >= st.opened_at + self.provider.circuit.cooldown_s:
                    st.state = "HALF_OPEN"
                    st.probes_used = 0
                    st.probes_used = 1
                    return True
                return False
            # HALF_OPEN
            if st.probes_used < self.provider.circuit.half_open_max_probes:
                st.probes_used += 1
                return True
            return False

    def record_success(self) -> None:
        with self.lock:
            st = self.state
            if st.state in ("OPEN", "HALF_OPEN"):
                st.state = "CLOSED"
            st.consecutive_failures = 0
            st.probes_used = 0
            st.last_error = ""

    def record_failure(self, error: str = "") -> None:
        with self.lock:
            st = self.state
            st.consecutive_failures += 1
            st.last_error = error[:300]
            if st.state == "HALF_OPEN":
                st.state = "OPEN"
                st.opened_at = time.time()
                st.probes_used = 0
                return
            if st.state == "CLOSED" and st.consecutive_failures >= self.provider.circuit.failure_threshold:
                st.state = "OPEN"
                st.opened_at = time.time()

    def quarantine(self, reason: str = "401 unauthorized") -> None:
        with self.lock:
            self.state.quarantined = True
            self.state.last_error = reason

    def reset(self) -> None:
        with self.lock:
            self.state = CircuitState()

    def snapshot(self) -> HealthSnapshot:
        with self.lock:
            return HealthSnapshot(
                provider_id=self.provider.id,
                circuit=self.state.state,
                last_health_check_ok=None,
                last_health_check_at=None,
                quarantined=self.state.quarantined,
            )


class HealthMonitor:
    """后台线程：周期 GET /models 探活；请求结果也回流到熔断器。"""

    def __init__(
        self,
        providers: list[ProviderConfig],
        interval_s: float = 60.0,
        state_store: CircuitStateStore | None = None,
        fingerprint: str = "",
    ):
        self.providers = providers
        self.interval_s = interval_s
        self._store = state_store
        self._fingerprint = fingerprint
        self.breakers: dict[str, CircuitBreaker] = {}
        self._health_ok: dict[str, bool | None] = {}
        self._health_at: dict[str, float | None] = {}
        self._throttle_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        for p in providers:
            self.breakers[p.id] = CircuitBreaker(p)
            self._health_ok[p.id] = None
            self._health_at[p.id] = None
        if self._store is not None:
            saved = self._store.load()
            if saved.fingerprint == fingerprint:
                for pid, data in saved.states.items():
                    br = self.breakers.get(pid)
                    if br is None:
                        continue
                    st = br.state
                    for key in (
                        "state",
                        "consecutive_failures",
                        "opened_at",
                        "probes_used",
                        "quarantined",
                        "last_error",
                    ):
                        if key in data:
                            setattr(st, key, data[key])

    def _persist(self) -> None:
        if self._store is None:
            return
        states: dict[str, dict] = {}
        for pid, br in self.breakers.items():
            with br.lock:
                st = br.state
                states[pid] = {
                    "state": st.state,
                    "consecutive_failures": st.consecutive_failures,
                    "opened_at": st.opened_at,
                    "probes_used": st.probes_used,
                    "quarantined": st.quarantined,
                    "last_error": st.last_error,
                }
        try:
            self._store.save(self._fingerprint, states)
        except Exception:
            pass  # 持久化失败不阻断请求

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="health-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            for p in self.providers:
                if p.health.enabled:
                    self.check_once(p)
            self._stop.wait(self.interval_s)

    def check_once(self, provider: ProviderConfig) -> bool:
        url = provider.base_url.rstrip("/") + provider.health.url_suffix
        headers = {"Accept": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        ok = False
        try:
            with urllib.request.urlopen(req, timeout=provider.health.timeout_s) as resp:
                ok = resp.status in provider.health.expected_status
                if ok:
                    # 顺手解析 models 列表可以留作后续模型发现，这里只做探活
                    _ = resp.read(4096)
        except Exception:
            ok = False
        with self._lock:
            self._health_ok[provider.id] = ok
            self._health_at[provider.id] = time.time()
        return ok

    def is_healthy(self, provider: ProviderConfig) -> bool:
        with self._lock:
            ok = self._health_ok.get(provider.id)
        if ok is None:
            return True  # 尚未探测：先信任
        return ok

    def is_available(self, provider: ProviderConfig) -> bool:
        breaker = self.breakers[provider.id]
        if not breaker.is_available():
            return False
        with self._lock:
            throttled_until = self._throttle_until.get(provider.id, 0.0)
        if time.time() < throttled_until:
            return False
        if provider.health.enabled:
            return self.is_healthy(provider)
        return True

    def record_success(self, provider_id: str) -> None:
        self.breakers[provider_id].record_success()
        with self._lock:
            self._health_ok[provider_id] = True
        self._persist()

    def record_failure(self, provider_id: str, error: str = "") -> None:
        self.breakers[provider_id].record_failure(error)
        self._persist()

    def record_throttle(self, provider_id: str, seconds: float) -> None:
        """429 Retry-After 冷却（上限 60 秒，避免长时间误伤渠道）。"""
        capped = min(max(0.0, seconds), 60.0)
        with self._lock:
            self._throttle_until[provider_id] = time.time() + capped

    def quarantine(self, provider_id: str, reason: str = "401") -> None:
        self.breakers[provider_id].quarantine(reason)
        self._persist()

    def reset(self, provider_id: str) -> None:
        self.breakers[provider_id].reset()
        with self._lock:
            self._health_ok[provider_id] = None
            self._health_at[provider_id] = None
        self._persist()

    def snapshots(self) -> list[HealthSnapshot]:
        out = []
        for p in self.providers:
            snap = self.breakers[p.id].snapshot()
            with self._lock:
                snap.last_health_check_ok = self._health_ok.get(p.id)
                snap.last_health_check_at = self._health_at.get(p.id)
            out.append(snap)
        return out

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "provider": s.provider_id,
                    "circuit": s.circuit,
                    "health_ok": s.last_health_check_ok,
                    "health_at": s.last_health_check_at,
                    "quarantined": s.quarantined,
                }
                for s in self.snapshots()
            ],
            ensure_ascii=False,
        )
