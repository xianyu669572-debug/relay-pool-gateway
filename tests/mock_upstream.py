"""测试用假上游：可控状态码 / 延迟 / SSE。"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class MockUpstream:
    def __init__(self, provider_id: str, handler: Callable[[dict, str], tuple[int, dict]]):
        self.provider_id = provider_id
        self.handler = handler
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
                owner.requests.append({"path": self.path, "payload": payload, "auth": self.headers.get("Authorization")})
                status, resp = owner.handler(payload, self.path)
                data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Mock-Provider", owner.provider_id)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                owner.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
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

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def ok_response(model: str = "mock", text: str = "hello"):
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
