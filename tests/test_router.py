import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import GatewayConfig, HealthConfig, PrivacyConfig, ProviderConfig, RoutingConfig, ServerConfig
from relay_gateway.health import HealthMonitor
from relay_gateway.router import Router
from tests.mock_upstream import MockUpstream, ok_response


def provider(
    pid: str,
    priority: int,
    base_url: str,
    env: str = "",
    health_enabled: bool = False,
    failure_threshold: int = 2,
) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        name=pid,
        priority=priority,
        base_url=base_url,
        api_key_env=env or None,
        health=HealthConfig(enabled=health_enabled, interval_s=3600),
        circuit=__import__("relay_gateway.config", fromlist=["CircuitConfig"]).CircuitConfig(
            failure_threshold=failure_threshold, cooldown_s=0.2
        ),
    )


class RouterTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("RELAY_A_KEY", "sk-a")
        os.environ.setdefault("RELAY_B_KEY", "sk-b")
        os.environ.setdefault("RELAY_C_KEY", "sk-c")

    def make_gateway(self, providers, fallback_on=None, max_hops=5):
        cfg = GatewayConfig(
            server=ServerConfig(),
            routing=RoutingConfig(fallback_on=fallback_on or [408, 429, 500, 502, 503, 504], max_fallback_hops=max_hops),
            privacy=PrivacyConfig(enabled=False),
            providers=providers,
        )
        health = HealthMonitor(providers)
        return Router(cfg, health), health

    def test_priority_routing_uses_first_healthy(self):
        a = MockUpstream("a", lambda p, path: (200, ok_response(text="from-a"))).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        pa = provider("a", 1, a.base_url)
        pb = provider("b", 2, b.base_url)
        try:
            router, health = self.make_gateway([pa, pb])
            result = router.execute({"model": "m", "messages": []}, [pa, pb], "m", "/v1/chat/completions", False, "r1")
            self.assertEqual(result.status, 200)
            self.assertEqual(result.provider_id, "a")
            self.assertIn("from-a", result.body.decode())
        finally:
            a.stop()
            b.stop()

    def test_failover_on_500(self):
        a = MockUpstream("a", lambda p, path: (500, {"error": "boom"})).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        pa = provider("a", 1, a.base_url)
        pb = provider("b", 2, b.base_url)
        try:
            router, health = self.make_gateway([pa, pb])
            result = router.execute({"model": "m", "messages": []}, [pa, pb], "m", "/v1/chat/completions", False, "r2")
            self.assertEqual(result.status, 200)
            self.assertEqual(result.provider_id, "b")
        finally:
            a.stop()
            b.stop()

    def test_no_failover_on_400(self):
        a = MockUpstream("a", lambda p, path: (400, {"error": "bad request"})).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response())).start()
        pa = provider("a", 1, a.base_url)
        pb = provider("b", 2, b.base_url)
        try:
            router, health = self.make_gateway([pa, pb])
            result = router.execute({"model": "m", "messages": []}, [pa, pb], "m", "/v1/chat/completions", False, "r3")
            self.assertEqual(result.status, 400)
            self.assertEqual(result.provider_id, "a")
        finally:
            a.stop()
            b.stop()

    def test_401_quarantines_and_fails_over(self):
        a = MockUpstream("a", lambda p, path: (401, {"error": "bad key"})).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        pa = provider("a", 1, a.base_url)
        pb = provider("b", 2, b.base_url)
        try:
            router, health = self.make_gateway([pa, pb])
            result = router.execute({"model": "m", "messages": []}, [pa, pb], "m", "/v1/chat/completions", False, "r4")
            self.assertEqual(result.status, 200)
            self.assertEqual(result.provider_id, "b")
            self.assertTrue(health.breakers["a"].state.quarantined)
        finally:
            a.stop()
            b.stop()

    def test_circuit_opens_and_half_open_recovers(self):
        import time

        state = {"fail": True}
        a = MockUpstream("a", lambda p, path: (500, {"error": "x"}) if state["fail"] else (200, ok_response())).start()
        b = MockUpstream("b", lambda p, path: (200, ok_response(text="from-b"))).start()
        pa = provider("a", 1, a.base_url, failure_threshold=2)
        pb = provider("b", 2, b.base_url)
        try:
            router, health = self.make_gateway([pa, pb])
            for _ in range(2):
                router.execute({"model": "m", "messages": []}, [pa, pb], "m", "/v1/chat/completions", False, "r5")
            self.assertEqual(health.breakers["a"].state.state, "OPEN")
            state["fail"] = False
            time.sleep(0.25)
            result = router.execute({"model": "m", "messages": []}, [pa, pb], "m", "/v1/chat/completions", False, "r6")
            self.assertEqual(result.provider_id, "a")
            self.assertEqual(health.breakers["a"].state.state, "CLOSED")
        finally:
            a.stop()
            b.stop()


class RoleNormalizationTests(unittest.TestCase):
    def test_developer_role_mapped_to_system(self):
        from relay_gateway.router import Router

        payload = {
            "model": "m",
            "messages": [
                {"role": "developer", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
        }
        out = Router.normalize_payload_for_upstream(payload)
        self.assertEqual([m["role"] for m in out["messages"]], ["system", "user"])
        self.assertEqual(out["messages"][0]["content"], "sys")

    def test_other_roles_untouched(self):
        from relay_gateway.router import Router

        payload = {
            "model": "m",
            "messages": [
                {"role": "system", "content": "s"},
                {"role": "assistant", "content": "a"},
                {"role": "tool", "tool_call_id": "1", "content": "t"},
            ],
        }
        out = Router.normalize_payload_for_upstream(payload)
        self.assertEqual([m["role"] for m in out["messages"]], ["system", "assistant", "tool"])


if __name__ == "__main__":
    unittest.main()
