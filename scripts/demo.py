"""可复现 demo：优先级路由 → 故障切换 → 熔断恢复 → 隐私路由 → 粘性会话（mock 上游）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import (
    CircuitConfig,
    GatewayConfig,
    HealthConfig,
    PrivacyConfig,
    ProviderConfig,
    RetryConfig,
    RoutingConfig,
    ServerConfig,
)
from relay_gateway.server import RelayPoolServer


class DemoUpstream:
    def __init__(self, pid: str, mode: str = "ok"):
        self.pid = pid
        self.mode = mode
        self.fail_count = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _make_handler(self):
        owner = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                if owner.mode == "500":
                    owner.fail_count += 1
                    data = json.dumps({"error": {"message": "boom"}}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                data = json.dumps(
                    {
                        "id": "chatcmpl-demo",
                        "object": "chat.completion",
                        "model": "m",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": f"from-{owner.pid}"},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                data = json.dumps({"object": "list", "data": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return H

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _provider(pid: str, priority: int, base_url: str, safe: bool = False) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        name=pid,
        priority=priority,
        base_url=base_url,
        api_key_env=f"DEMO_{pid.upper()}_KEY",
        health=HealthConfig(enabled=False),
        circuit=CircuitConfig(failure_threshold=2, cooldown_s=1.0),
        retry=RetryConfig(per_provider_retries=0, backoff_s=0.1),
        safe=safe,
    )


def _post(url: str, payload: dict, extra_headers: dict | None = None) -> tuple[int, dict, dict]:
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, dict(resp.headers), json.loads(resp.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="relay-pool-gateway demo (mock upstream)")
    parser.add_argument("--out", default=None, help="可选：输出文件路径")
    args = parser.parse_args()

    lines: list[str] = []

    def log(msg: str) -> None:
        lines.append(msg)
        print(msg, flush=True)

    os.environ.update(
        {
            "DEMO_A_KEY": "sk-demo-a",
            "DEMO_B_KEY": "sk-demo-b",
            "DEMO_C_KEY": "sk-demo-c",
            "DEMO_SAFE_KEY": "sk-demo-safe",
        }
    )

    a = DemoUpstream("a")
    b = DemoUpstream("b")
    c = DemoUpstream("c")
    safe = DemoUpstream("safe")

    cfg = GatewayConfig(
        server=ServerConfig(host="127.0.0.1", port=0),
        routing=RoutingConfig(),
        privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
        providers=[
            _provider("a", 1, a.base_url),
            _provider("b", 2, b.base_url),
            _provider("c", 3, c.base_url),
            _provider("safe", 0, safe.base_url, safe=True),
        ],
    )
    srv = RelayPoolServer(("127.0.0.1", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    try:
        log("== Step 1: 全部健康，按优先级调用 ==")
        status, headers, _ = _post(url, {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        log(f"  status={status} provider={headers.get('X-Relay-Pool-Provider')} (expect a)")
        assert headers.get("X-Relay-Pool-Provider") == "a"

        log("== Step 2: 最高优先级渠道故障（500），自动切换 ==")
        a.mode = "500"
        status, headers, _ = _post(url, {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        log(f"  status={status} provider={headers.get('X-Relay-Pool-Provider')} (expect b)")
        assert headers.get("X-Relay-Pool-Provider") == "b"

        log("== Step 3: 连续失败触发熔断，恢复后自动回切 ==")
        status, headers, _ = _post(url, {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        log(f"  熔断期内 provider={headers.get('X-Relay-Pool-Provider')} (expect b)")
        assert headers.get("X-Relay-Pool-Provider") == "b"
        a.mode = "ok"
        time.sleep(1.5)
        status, headers, _ = _post(url, {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        log(f"  冷却后 provider={headers.get('X-Relay-Pool-Provider')} (expect a)")
        assert headers.get("X-Relay-Pool-Provider") == "a"

        log("== Step 4: 敏感内容自动改走官方/本地安全通道 ==")
        status, headers, _ = _post(
            url,
            {
                "model": "m",
                "messages": [{"role": "user", "content": "my email is alice@example.com"}],
                "metadata": {"session_id": "demo-session"},
            },
        )
        log(f"  status={status} provider={headers.get('X-Relay-Pool-Provider')} (expect safe)")
        assert headers.get("X-Relay-Pool-Provider") == "safe"

        log("== Step 5: 会话粘性——同会话后续干净请求仍走安全通道 ==")
        status, headers, _ = _post(
            url,
            {"model": "m", "messages": [{"role": "user", "content": "continue"}], "metadata": {"session_id": "demo-session"}},
        )
        log(f"  provider={headers.get('X-Relay-Pool-Provider')} (expect safe)")
        assert headers.get("X-Relay-Pool-Provider") == "safe"

        log("== Step 6: /health 状态快照 ==")
        health = json.loads(urllib.request.urlopen(url + "/health", timeout=5).read().decode())
        for p in health["providers"]:
            log(f"  {p['provider']}: circuit={p['circuit']} quarantine={p['quarantined']}")

        log("\nDEMO PASS")
    finally:
        srv.shutdown()
        srv.server_close()
        for up in (a, b, c, safe):
            up.stop()

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
