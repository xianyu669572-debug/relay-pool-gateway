import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import (
    CircuitConfig,
    GatewayConfig,
    HealthConfig,
    PrivacyConfig,
    ProviderConfig,
    RoutingConfig,
    ServerConfig,
)
from relay_gateway.server import RelayPoolServer
from tests.mock_upstream import MockUpstream, ok_response


def _provider(pid, priority, base_url, env="R_A_KEY", failure_threshold=3):
    return ProviderConfig(
        id=pid,
        name=pid,
        priority=priority,
        base_url=base_url,
        api_key_env=env,
        health=HealthConfig(enabled=False),
        circuit=CircuitConfig(failure_threshold=failure_threshold, cooldown_s=60),
    )


class ReloadTests(unittest.TestCase):
    def setUp(self):
        os.environ["R_A_KEY"] = "sk-a"
        os.environ["R_B_KEY"] = "sk-b"

    def start_gateway(self, cfg):
        srv = RelayPoolServer(("127.0.0.1", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    def test_concurrent_reload_no_errors(self):
        a = MockUpstream("a", lambda p, path: (200, ok_response(text="a"))).start()
        cfg = GatewayConfig(
            server=ServerConfig(host="127.0.0.1", port=0),
            routing=RoutingConfig(),
            privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
            providers=[_provider("a", 1, a.base_url)],
        )
        srv, url = self.start_gateway(cfg)
        failures = []
        stop = threading.Event()

        def worker():
            while not stop.is_set():
                req = urllib.request.Request(
                    url + "/v1/chat/completions",
                    data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        if resp.status != 200:
                            failures.append(resp.status)
                except Exception as exc:
                    failures.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        try:
            for _ in range(5):
                srv.reload(cfg)
                import time

                time.sleep(0.2)
        finally:
            stop.set()
            for t in threads:
                t.join(timeout=10)
            srv.shutdown()
            srv.server_close()
            a.stop()
        self.assertEqual(failures, [])

    def test_circuit_state_survives_server_restart(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = os.path.join(td, "gateway-state.json")
            up = MockUpstream("a", lambda p, path: (500, {"error": {"message": "boom"}})).start()

            def make_cfg():
                return GatewayConfig(
                    server=ServerConfig(host="127.0.0.1", port=0, state_file=state_file),
                    routing=RoutingConfig(),
                    privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
                    providers=[_provider("a", 1, up.base_url, failure_threshold=1)],
                )

            srv1, url1 = self.start_gateway(make_cfg())
            req = urllib.request.Request(
                url1 + "/v1/chat/completions",
                data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "x"}]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self.assertRaises(Exception):
                    urllib.request.urlopen(req, timeout=5)
            finally:
                srv1.shutdown()
                srv1.server_close()

            srv2, url2 = self.start_gateway(make_cfg())
            try:
                health = json.loads(urllib.request.urlopen(url2 + "/health", timeout=5).read().decode())
                state = {p["provider"]: p["circuit"] for p in health["providers"]}
                self.assertEqual(state["a"], "OPEN")
            finally:
                srv2.shutdown()
                srv2.server_close()
                up.stop()


if __name__ == "__main__":
    unittest.main()
