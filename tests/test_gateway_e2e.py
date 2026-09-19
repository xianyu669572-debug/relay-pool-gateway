import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import (
    GatewayConfig,
    HealthConfig,
    PrivacyConfig,
    ProviderConfig,
    RoutingConfig,
    ServerConfig,
)
from relay_gateway.server import RelayPoolServer
from tests.mock_upstream import MockUpstream, ok_response


def provider(
    pid: str,
    priority: int,
    base_url: str,
    env: str = "",
    safe: bool = False,
    local: bool = False,
) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        name=pid,
        priority=priority,
        base_url=base_url,
        api_key_env=env or None,
        health=HealthConfig(enabled=False),
        safe=safe,
        local=local,
    )


class GatewayE2ETests(unittest.TestCase):
    def setUp(self):
        os.environ["E2E_A_KEY"] = "sk-a"
        os.environ["E2E_B_KEY"] = "sk-b"
        os.environ["E2E_SAFE_KEY"] = "sk-safe"

    def start_gateway(self, providers, privacy_mode="safe_route"):
        cfg = GatewayConfig(
            server=ServerConfig(host="127.0.0.1", port=0),
            routing=RoutingConfig(),
            privacy=PrivacyConfig(enabled=True, mode=privacy_mode, keywords=[], sticky_session=True, session_ttl_s=30),
            providers=providers,
        )
        srv = RelayPoolServer(("127.0.0.1", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    def post(self, url, payload):
        req = urllib.request.Request(
            url + "/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read().decode())

    def test_clean_request_uses_priority_provider(self):
        a = MockUpstream("a", lambda p, path: (200, ok_response(text="from-a"))).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        srv, url = self.start_gateway([provider("a", 1, a.base_url, "E2E_A_KEY"), provider("b", 2, b.base_url, "E2E_B_KEY")])
        try:
            status, headers, body = self.post(url, {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "a")
            self.assertIn("from-a", body["choices"][0]["message"]["content"])
        finally:
            a.stop()
            b.stop()
            srv.shutdown()
            srv.server_close()

    def test_sensitive_request_routes_to_safe_provider_and_pins_session(self):
        a = MockUpstream("a", lambda p, path: (200, ok_response(text="relay"))).start()
        safe = MockUpstream("safe", lambda p, path: (200, ok_response(text="official"))).start()
        srv, url = self.start_gateway(
            [
                provider("a", 1, a.base_url, "E2E_A_KEY"),
                provider("safe", 0, safe.base_url, "E2E_SAFE_KEY", safe=True),
            ]
        )
        try:
            # 含邮箱 -> 应走 safe
            status, headers, body = self.post(
                url,
                {"model": "m", "messages": [{"role": "user", "content": "email is marie@acme.com"}], "metadata": {"session_id": "sess-1"}},
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "safe")
            self.assertIn("official", body["choices"][0]["message"]["content"])

            # 同会话下一条不含敏感 -> 仍走 safe（粘性）
            status, headers, body = self.post(
                url,
                {"model": "m", "messages": [{"role": "user", "content": "continue"}], "metadata": {"session_id": "sess-1"}},
            )
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "safe")

            # 新会话干净请求 -> 走优先级渠道
            status, headers, body = self.post(url, {"model": "m", "messages": [{"role": "user", "content": "hello"}]})
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "a")
        finally:
            a.stop()
            safe.stop()
            srv.shutdown()
            srv.server_close()

    def test_redact_mode_restores_response(self):
        a = MockUpstream("a", lambda p, path: (200, ok_response(text="hi <EMAIL_1>!"))).start()
        srv, url = self.start_gateway([provider("a", 1, a.base_url, "E2E_A_KEY")], privacy_mode="redact")
        try:
            status, headers, body = self.post(
                url,
                {"model": "m", "messages": [{"role": "user", "content": "email marie@acme.com"}], "stream": False},
            )
            self.assertEqual(status, 200)
            self.assertIn("marie@acme.com", body["choices"][0]["message"]["content"])
            # 上游实际收到的应是占位符
            self.assertEqual(a.requests[-1]["payload"]["messages"][0]["content"], "email <EMAIL_1>")
        finally:
            a.stop()
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
