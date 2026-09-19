"""HTTP 服务：OpenAI 兼容入口 + 隐私决策 + 故障转移执行 + SSE 流式 + 审计。"""

from __future__ import annotations

import datetime as _dt
import hmac
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .config import GatewayConfig
from .health import HealthMonitor
from .privacy import PrivacyEngine, StreamRestorer
from .router import Router
from .state import CircuitStateStore
from .upstream import UpstreamError


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


class RelayPoolServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, config: GatewayConfig):
        config.validate()
        if config.server.host in ("127.0.0.1", "::1", "localhost") and not config.server.api_keys:
            import sys as _sys

            print(
                "WARNING: 网关仅绑定本机且未配置 api_keys，本机访问无鉴权；"
                "建议设置 server.api_keys 或仅在本机可信环境使用。",
                file=_sys.stderr,
            )
        super().__init__(addr, handler)
        self.gateway_config = config
        self.privacy = PrivacyEngine(config.privacy)
        self.health = HealthMonitor(
            config.providers,
            state_store=CircuitStateStore(config.server.state_file),
            fingerprint=config.state_fingerprint(),
        )
        self.router = Router(config, self.health)
        self.start_time = time.time()
        self.audit_lock = threading.Lock()

    def reload(self, config: GatewayConfig) -> None:
        config.validate()
        # 双缓冲：先构建新组件，再原子替换引用，最后停旧健康检查，避免在途请求读到半初始化对象
        new_health = HealthMonitor(
            config.providers,
            state_store=CircuitStateStore(config.server.state_file),
            fingerprint=config.state_fingerprint(),
        )
        new_privacy = PrivacyEngine(config.privacy)
        new_router = Router(config, new_health)
        old_health = self.health
        self.gateway_config = config
        self.privacy = new_privacy
        self.router = new_router
        self.health = new_health
        new_health.start()
        old_health.stop()

    def audit(self, entry: dict[str, Any]) -> None:
        try:
            line = json.dumps(entry, ensure_ascii=False)
            with self.audit_lock:
                path = Path(self.gateway_config.privacy.audit_log)
                max_bytes = self.gateway_config.privacy.audit_max_bytes
                try:
                    if path.exists() and path.stat().st_size >= max_bytes:
                        rotated = path.with_name(path.name + ".1")
                        if rotated.exists():
                            rotated.unlink()
                        os.replace(path, rotated)
                except OSError:
                    pass
                with path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass  # 审计失败不阻断请求


class Handler(BaseHTTPRequestHandler):
    server: RelayPoolServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # 只记请求路径与状态，绝不记请求体/密钥
        try:
            super().log_message(fmt, *args)
        except Exception:
            pass

    # ---------- 工具 ----------

    def _auth_ok(self) -> bool:
        keys = self.server.gateway_config.server.api_keys
        if not keys:
            return True
        header = self.headers.get("Authorization", "")
        if not header.lower().startswith("bearer "):
            return False
        token = header[7:].strip()
        return any(hmac.compare_digest(token, k) for k in keys)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        limit = self.server.gateway_config.server.request_max_bytes
        if length > limit:
            raise ValueError("request too large")
        return self.rfile.read(length)

    def _send_json(self, status: int, obj: Any, extra_headers: dict[str, str] | None = None) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_plain(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _session_id(self, payload: dict[str, Any]) -> str | None:
        header = self.headers.get("X-Session-Id")
        if header:
            return header.strip()[:200]
        try:
            meta = payload.get("metadata")
            if isinstance(meta, dict):
                sid = meta.get("session_id") or meta.get("litellm_session_id")
                if sid:
                    return str(sid)[:200]
        except Exception:
            pass
        return None

    # ---------- 路由 ----------

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._handle_health()
        elif self.path.rstrip("/") == "/v1/models":
            self._handle_models()
        else:
            self._send_plain(404, "not found")

    def do_POST(self) -> None:
        path = self.path.rstrip("/")
        if path in ("/v1/chat/completions", "/v1/completions", "/v1/responses"):
            self._handle_completion(path)
        else:
            self._send_plain(404, "not found")

    def _handle_health(self) -> None:
        if not self._auth_ok():
            self._send_json(401, {"error": {"message": "unauthorized"}})
            return
        self._send_json(
            200,
            {
                "status": "ok",
                "version": __version__,
                "uptime_s": round(time.time() - self.server.start_time, 1),
                "providers": json.loads(self.server.health.to_json()),
                "privacy": {
                    "enabled": self.server.gateway_config.privacy.enabled,
                    "mode": self.server.gateway_config.privacy.mode,
                    "sticky_sessions": self.server.gateway_config.privacy.sticky_session,
                },
            },
        )

    def _handle_models(self) -> None:
        if not self._auth_ok():
            self._send_json(401, {"error": {"message": "unauthorized"}})
            return
        models: list[str] = []
        for p in self.server.gateway_config.providers:
            models.extend(p.models or [])
            models.extend(p.model_map.keys())
        if not models:
            models = ["*"]  # 未配置模型列表时表示全部透传
        unique = sorted(set(models))
        self._send_json(200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "relay-pool"} for m in unique]})

    def _handle_completion(self, path: str) -> None:
        if not self._auth_ok():
            self._send_json(401, {"error": {"message": "unauthorized"}})
            return
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            body = self._read_body()
        except ValueError:
            self._send_json(413, {"error": {"message": "request too large"}})
            return
        except Exception:
            self._send_json(400, {"error": {"message": "bad request body"}})
            return

        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            self._send_json(400, {"error": {"message": "invalid json"}})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": {"message": "payload must be object"}})
            return

        model = str(payload.get("model", ""))
        stream = bool(payload.get("stream", False))
        session_id = self._session_id(payload)
        cfg = self.server.gateway_config
        engine = self.server.privacy

        # ---------------- 隐私决策 ----------------
        action = "normal"
        categories: set[str] = set()
        sensitive = False
        mapping: dict[str, str] = {}
        pinned = engine.sticky.is_pinned(session_id)
        pool = cfg.normal_providers()

        if cfg.privacy.enabled:
            scan, redact_mapping = engine.scan_payload(payload)
            sensitive = scan.sensitive or pinned
            categories = scan.categories
            if redact_mapping:
                mapping.update(redact_mapping)

            if scan.blocked:
                self.server.audit(
                    {
                        "ts": _now_iso(), "request_id": request_id, "session_id": session_id, "model": model,
                        "sensitive": True, "categories": sorted(categories), "action": "blocked",
                        "provider": None, "status": 400, "latency_ms": round((time.monotonic() - started) * 1000, 1),
                    }
                )
                self._send_json(400, {"error": {"message": "policy violation: sensitive content blocked", "type": "privacy_block"}})
                return

            if sensitive:
                if cfg.privacy.mode == "safe_route":
                    pool = cfg.safe_providers() or cfg.local_providers()
                    action = "safe_route"
                    engine.sticky.pin(session_id)
                elif cfg.privacy.mode == "local":
                    pool = cfg.local_providers() or cfg.safe_providers()
                    action = "local_route"
                    engine.sticky.pin(session_id)
                elif cfg.privacy.mode == "redact":
                    action = "redact"
                    # payload 已被 scan_payload 原地脱敏
                elif cfg.privacy.mode == "block":
                    action = "blocked"
                    self.server.audit(
                        {
                            "ts": _now_iso(), "request_id": request_id, "session_id": session_id, "model": model,
                            "sensitive": True, "categories": sorted(categories), "action": "blocked",
                            "provider": None, "status": 400, "latency_ms": round((time.monotonic() - started) * 1000, 1),
                        }
                    )
                    self._send_json(400, {"error": {"message": "policy violation: sensitive content blocked", "type": "privacy_block"}})
                    return

        if not pool:
            self.server.audit(
                {
                    "ts": _now_iso(), "request_id": request_id, "session_id": session_id, "model": model,
                    "sensitive": sensitive, "categories": sorted(categories), "action": action,
                    "provider": None, "status": 503, "latency_ms": round((time.monotonic() - started) * 1000, 1),
                }
            )
            self._send_json(503, {"error": {"message": "no provider available for routing decision", "type": "policy_no_provider"}})
            return

        # ---------------- 执行 ----------------
        result = self.server.router.execute(payload, pool, model, path, stream, request_id)
        latency = round((time.monotonic() - started) * 1000, 1)
        self.server.audit(
            {
                "ts": _now_iso(), "request_id": request_id, "session_id": session_id, "model": model,
                "sensitive": sensitive, "categories": sorted(categories), "action": action,
                "pinned": pinned, "provider": result.provider_id or None, "status": result.status,
                "latency_ms": latency,
                "attempts": [{"provider": a.provider_id, "ok": a.ok, "status": a.status, "error": a.error[:200]} for a in result.attempts],
            }
        )

        if result.stream is not None:
            self._send_stream(result, mapping, request_id)
            return

        out = result.body or b""
        if mapping:
            try:
                text = out.decode("utf-8")
                restored = engine.restore(text, mapping)
                out = restored.encode("utf-8")
            except Exception:
                pass
        self.send_response(result.status)
        for k, v in result.headers.items():
            if k.lower() in ("content-encoding", "transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("X-Relay-Pool-Provider", result.provider_id)
        self.send_header("X-Relay-Pool-Request-Id", request_id)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        try:
            self.wfile.write(out)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_stream(self, result, mapping: dict[str, str], request_id: str) -> None:
        restorer = StreamRestorer(mapping)
        self.send_response(result.status)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        # SSE 结束后关闭连接，兼容 curl/urllib 读到 EOF 的客户端（OpenAI 兼容客户端通常以 [DONE] 结束）
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Relay-Pool-Provider", result.provider_id)
        self.send_header("X-Relay-Pool-Request-Id", request_id)
        self.end_headers()
        try:
            for chunk in result.stream:
                text = chunk.decode("utf-8", errors="replace")
                out = restorer.process(text)
                if out:
                    self.wfile.write(out.encode("utf-8"))
                    self.wfile.flush()
            tail = restorer.flush()
            if tail:
                self.wfile.write(tail.encode("utf-8"))
                self.wfile.flush()
        except UpstreamError as exc:
            try:
                tail = restorer.flush()
                if tail:
                    self.wfile.write(tail.encode("utf-8"))
                self.wfile.write(
                    (
                        'data: {"error":{"message":"stream interrupted: %s","type":"stream_interrupted"}}\n\n'
                        % exc.message.replace('"', "'")
                    ).encode("utf-8")
                )
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            pass
