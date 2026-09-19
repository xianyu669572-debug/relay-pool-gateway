import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import GatewayConfig, HealthConfig, PrivacyConfig, ProviderConfig, RoutingConfig, ServerConfig
from relay_gateway.server import RelayPoolServer
from tests.mock_upstream import MockUpstream, ok_response


def _provider(base_url: str) -> ProviderConfig:
    return ProviderConfig(
        id="a",
        name="a",
        priority=1,
        base_url=base_url,
        api_key_env="AUTH_A_KEY",
        health=HealthConfig(enabled=False),
    )


class AuthTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTH_A_KEY"] = "sk-a"

    def test_non_loopback_without_keys_raises(self):
        cfg = GatewayConfig(
            server=ServerConfig(host="0.0.0.0", port=0, api_keys=[]),
            routing=RoutingConfig(),
            privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
            providers=[_provider("http://127.0.0.1:9")],
        )
        with self.assertRaises(ValueError):
            RelayPoolServer(("0.0.0.0", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)

    def test_non_loopback_with_keys_ok(self):
        cfg = GatewayConfig(
            server=ServerConfig(host="0.0.0.0", port=0, api_keys=["local-secret"]),
            routing=RoutingConfig(),
            privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
            providers=[_provider("http://127.0.0.1:9")],
        )
        # 仅验证校验通过（绑定 0.0.0.0 端口 0 在本机测试环境可用）
        srv = RelayPoolServer(("0.0.0.0", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)
        srv.server_close()

    def test_missing_auth_401_with_keys(self):
        up = MockUpstream("a", lambda p, path: (200, ok_response(text="ok"))).start()
        cfg = GatewayConfig(
            server=ServerConfig(host="127.0.0.1", port=0, api_keys=["local-secret"]),
            routing=RoutingConfig(),
            privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
            providers=[_provider(up.base_url)],
        )
        srv = RelayPoolServer(("127.0.0.1", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            req = urllib.request.Request(
                url + "/v1/chat/completions",
                data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 401)
        finally:
            srv.shutdown()
            srv.server_close()
            up.stop()

    def test_valid_auth_200(self):
        up = MockUpstream("a", lambda p, path: (200, ok_response(text="ok"))).start()
        cfg = GatewayConfig(
            server=ServerConfig(host="127.0.0.1", port=0, api_keys=["local-secret"]),
            routing=RoutingConfig(),
            privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
            providers=[_provider(up.base_url)],
        )
        srv = RelayPoolServer(("127.0.0.1", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            req = urllib.request.Request(
                url + "/v1/chat/completions",
                data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer local-secret"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            srv.shutdown()
            srv.server_close()
            up.stop()


if __name__ == "__main__":
    unittest.main()
