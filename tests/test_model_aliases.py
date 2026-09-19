import json
import os
import sys
import threading
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


def _provider(pid, priority, base_url, model_map=None, models=None, env="M_A_KEY"):
    return ProviderConfig(
        id=pid,
        name=pid,
        priority=priority,
        base_url=base_url,
        api_key_env=env,
        health=HealthConfig(enabled=False),
        model_map=model_map or {},
        models=models or [],
    )


class ModelAliasTests(unittest.TestCase):
    def setUp(self):
        os.environ["M_A_KEY"] = "sk-a"
        os.environ["M_B_KEY"] = "sk-b"

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

    def test_alias_maps_model_per_provider(self):
        up = MockUpstream("a", lambda p, path: (200, ok_response())).start()
        srv, url = self.start_gateway(
            [_provider("a", 1, up.base_url, model_map={"gpt-4o": "deepseek-v4-flash"})]
        )
        try:
            req = urllib.request.Request(
                url + "/v1/chat/completions",
                data=json.dumps({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
            self.assertEqual(up.requests[-1]["payload"]["model"], "deepseek-v4-flash")
        finally:
            up.stop()
            srv.shutdown()
            srv.server_close()

    def test_unknown_model_passthrough(self):
        up = MockUpstream("a", lambda p, path: (200, ok_response())).start()
        # 渠道未限制模型列表时，未知模型应透传
        srv, url = self.start_gateway([_provider("a", 1, up.base_url)])
        try:
            req = urllib.request.Request(
                url + "/v1/chat/completions",
                data=json.dumps({"model": "some-other", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
            self.assertEqual(up.requests[-1]["payload"]["model"], "some-other")
        finally:
            up.stop()
            srv.shutdown()
            srv.server_close()

    def test_models_union_includes_alias(self):
        up = MockUpstream("a", lambda p, path: (200, ok_response())).start()
        srv, url = self.start_gateway(
            [_provider("a", 1, up.base_url, model_map={"gpt-4o": "deepseek-v4-flash"}, models=["deepseek-v4-flash"])]
        )
        try:
            body = urllib.request.urlopen(url + "/v1/models", timeout=5).read().decode()
            ids = [m["id"] for m in json.loads(body)["data"]]
            self.assertIn("gpt-4o", ids)
            self.assertIn("deepseek-v4-flash", ids)
        finally:
            up.stop()
            srv.shutdown()
            srv.server_close()

    def test_alias_404_failover(self):
        a = MockUpstream("a", lambda p, path: (404, {"error": {"message": "model not found"}})).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        srv, url = self.start_gateway(
            [
                _provider("a", 1, a.base_url, model_map={"gpt-4o": "deepseek-v4-flash"}, env="M_A_KEY"),
                _provider("b", 2, b.base_url, env="M_B_KEY"),
            ]
        )
        try:
            req = urllib.request.Request(
                url + "/v1/chat/completions",
                data=json.dumps({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("X-Relay-Pool-Provider"), "b")
        finally:
            a.stop()
            b.stop()
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
