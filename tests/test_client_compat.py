"""客户端兼容性矩阵：/v1/chat/completions 非流式/流式/tools/错误语义（curl 等价）。"""

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


class MockSSEUpstream:
    def __init__(self, provider_id: str, sse_lines: list[str]):
        self.provider_id = provider_id
        self.sse_lines = sse_lines
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.requests: list[dict] = []

    def _make_handler(self):
        owner = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception:
                    payload = {}
                owner.requests.append({"path": self.path, "payload": payload})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for line in owner.sse_lines:
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.01)

        return H

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def provider(
    pid: str,
    priority: int,
    base_url: str,
    env: str = "",
    models: list[str] | None = None,
    timeout_s: float = 60.0,
) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        name=pid,
        priority=priority,
        base_url=base_url,
        api_key_env=env or None,
        health=HealthConfig(enabled=False),
        models=models or [],
        timeout_s=timeout_s,
    )


class ClientCompatTests(unittest.TestCase):
    def setUp(self):
        os.environ["C_A_KEY"] = "sk-a"
        os.environ["C_B_KEY"] = "sk-b"

    def start_gateway(self, providers):
        cfg = GatewayConfig(
            server=ServerConfig(host="127.0.0.1", port=0),
            routing=RoutingConfig(),
            privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
            providers=providers,
        )
        srv = RelayPoolServer(("127.0.0.1", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    def _request(self, url, payload, path="/v1/chat/completions"):
        req = urllib.request.Request(
            url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_non_stream_chat_completion(self):
        a = MockUpstream("a", lambda p, path: (200, ok_response(text="from-a"))).start()
        srv, url = self.start_gateway([provider("a", 1, a.base_url, "C_A_KEY")])
        try:
            status, headers, body = self._request(url, {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "a")
            data = json.loads(body.decode())
            self.assertIn("from-a", data["choices"][0]["message"]["content"])
        finally:
            a.stop()
            srv.shutdown()
            srv.server_close()

    def test_stream_passthrough_with_keepalive(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"hello"},"index":0}]}\n\n',
            ": ping\n\n",
            'data: {"choices":[{"delta":{"content":" world"},"index":0}]}\n\n',
            "data: [DONE]\n\n",
        ]
        up = MockSSEUpstream("a", lines).start()
        srv, url = self.start_gateway([provider("a", 1, up.base_url, "C_A_KEY")])
        try:
            status, headers, body = self._request(
                url, {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}
            )
            self.assertEqual(status, 200)
            text = body.decode("utf-8", errors="replace")
            self.assertIn('"content":"hello"', text)
            self.assertIn(": ping", text)
            self.assertIn("[DONE]", text)
        finally:
            up.stop()
            srv.shutdown()
            srv.server_close()

    def test_tools_passthrough(self):
        tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]

        def handler(payload, path):
            return (
                200,
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "model": "m",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "get_weather", "arguments": '{"city":"sh"}'},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            )

        a = MockUpstream("a", handler).start()
        srv, url = self.start_gateway([provider("a", 1, a.base_url, "C_A_KEY")])
        try:
            status, headers, body = self._request(
                url, {"model": "m", "messages": [{"role": "user", "content": "weather"}], "tools": tools}
            )
            self.assertEqual(status, 200)
            self.assertEqual(a.requests[-1]["payload"]["tools"], tools)
            data = json.loads(body.decode())
            self.assertEqual(data["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "get_weather")
        finally:
            a.stop()
            srv.shutdown()
            srv.server_close()

    def test_stream_tool_calls_delta_passthrough(self):
        lines = [
            'data: {"choices":[{"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"get_weather","arguments":""}}]},"index":0}]}\n\n',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\":\\"sh\\"}"}}]},"index":0}]}\n\n',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls","index":0}]}\n\n',
            "data: [DONE]\n\n",
        ]
        up = MockSSEUpstream("a", lines).start()
        srv, url = self.start_gateway([provider("a", 1, up.base_url, "C_A_KEY")])
        try:
            status, headers, body = self._request(
                url, {"model": "m", "messages": [{"role": "user", "content": "weather"}], "stream": True}
            )
            text = body.decode("utf-8", errors="replace")
            self.assertEqual(status, 200)
            self.assertIn("get_weather", text)
            self.assertIn("tool_calls", text)
        finally:
            up.stop()
            srv.shutdown()
            srv.server_close()

    def test_400_no_failover(self):
        a = MockUpstream("a", lambda p, path: (400, {"error": {"message": "bad request"}})).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="b"))).start()
        srv, url = self.start_gateway([provider("a", 1, a.base_url, "C_A_KEY"), provider("b", 2, b.base_url, "C_B_KEY")])
        try:
            status, headers, body = self._request(url, {"model": "m", "messages": [{"role": "user", "content": "x"}]})
            self.assertEqual(status, 400)
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "a")
            self.assertEqual(len(b.requests), 0)
        finally:
            a.stop()
            b.stop()
            srv.shutdown()
            srv.server_close()

    def test_401_failover_and_quarantine(self):
        a = MockUpstream("a", lambda p, path: (401, {"error": {"message": "unauthorized"}})).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        srv, url = self.start_gateway([provider("a", 1, a.base_url, "C_A_KEY"), provider("b", 2, b.base_url, "C_B_KEY")])
        try:
            status, headers, body = self._request(url, {"model": "m", "messages": [{"role": "user", "content": "x"}]})
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "b")
            health = json.loads(urllib.request.urlopen(url + "/health", timeout=5).read().decode())
            quarantined = {p["provider"]: p["quarantined"] for p in health["providers"]}
            self.assertTrue(quarantined["a"])
        finally:
            a.stop()
            b.stop()
            srv.shutdown()
            srv.server_close()

    def test_429_failover(self):
        a = MockUpstream("a", lambda p, path: (429, {"error": {"message": "rate limited"}})).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        srv, url = self.start_gateway([provider("a", 1, a.base_url, "C_A_KEY"), provider("b", 2, b.base_url, "C_B_KEY")])
        try:
            status, headers, _ = self._request(url, {"model": "m", "messages": [{"role": "user", "content": "x"}]})
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "b")
        finally:
            a.stop()
            b.stop()
            srv.shutdown()
            srv.server_close()

    def test_500_failover(self):
        a = MockUpstream("a", lambda p, path: (500, {"error": {"message": "boom"}})).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        srv, url = self.start_gateway([provider("a", 1, a.base_url, "C_A_KEY"), provider("b", 2, b.base_url, "C_B_KEY")])
        try:
            status, headers, _ = self._request(url, {"model": "m", "messages": [{"role": "user", "content": "x"}]})
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "b")
        finally:
            a.stop()
            b.stop()
            srv.shutdown()
            srv.server_close()

    def test_timeout_failover(self):
        a = MockUpstream("a", lambda p, path: (time.sleep(3) or (200, ok_response(text="slow")))).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        srv, url = self.start_gateway(
            [provider("a", 1, a.base_url, "C_A_KEY", timeout_s=1), provider("b", 2, b.base_url, "C_B_KEY")]
        )
        try:
            status, headers, _ = self._request(url, {"model": "m", "messages": [{"role": "user", "content": "x"}]})
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Relay-Pool-Provider"), "b")
        finally:
            a.stop()
            b.stop()
            srv.shutdown()
            srv.server_close()

    def test_models_union(self):
        a = MockUpstream("a", lambda p, path: (200, ok_response())).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response())).start()
        srv, url = self.start_gateway(
            [
                provider("a", 1, a.base_url, "C_A_KEY", models=["deepseek-v4-flash"]),
                provider("b", 2, b.base_url, "C_B_KEY", models=["gpt-5.5"]),
            ]
        )
        try:
            body = urllib.request.urlopen(url + "/v1/models", timeout=5).read().decode()
            ids = [m["id"] for m in json.loads(body)["data"]]
            self.assertIn("deepseek-v4-flash", ids)
            self.assertIn("gpt-5.5", ids)
        finally:
            a.stop()
            b.stop()
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
