import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import GatewayConfig, HealthConfig, PrivacyConfig, ProviderConfig, RoutingConfig
from relay_gateway.health import HealthMonitor
from relay_gateway.router import Router
from relay_gateway.upstream import UpstreamError


def _provider(pid: str, priority: int, base_url: str) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        name=pid,
        priority=priority,
        base_url=base_url,
        api_key_env=None,
        health=HealthConfig(enabled=False),
    )


class ThrottleTests(unittest.TestCase):
    def test_429_retry_after_throttles_provider(self):
        cfg = GatewayConfig(
            routing=RoutingConfig(),
            privacy=PrivacyConfig(enabled=True, mode="safe_route", keywords=[]),
            providers=[
                _provider("a", 1, "http://127.0.0.1:9"),
                _provider("b", 2, "http://127.0.0.1:10"),
            ],
        )
        health = HealthMonitor(cfg.providers)
        router = Router(cfg, health)

        def fake_call(provider, path, body, timeout_s):
            if provider.id == "a":
                raise UpstreamError("rate limited", status=429, retry_after=30)
            return mock.Mock(
                status=200,
                headers={"Content-Type": "application/json"},
                body=b'{"choices":[{"message":{"content":"ok"}}]}',
            )

        with mock.patch("relay_gateway.router.call_json", side_effect=fake_call):
            result = router.execute(
                {"model": "m", "messages": [{"role": "user", "content": "x"}]},
                cfg.normal_providers(),
                "m",
                "/v1/chat/completions",
                False,
                "req-1",
            )
        self.assertEqual(result.provider_id, "b")
        self.assertFalse(health.is_available(_provider("a", 1, "http://127.0.0.1:9")))

    def test_throttle_capped_at_60(self):
        health = HealthMonitor([])
        health.record_throttle("p", 9999)
        with health._lock:
            remaining = health._throttle_until["p"] - __import__("time").time()
        self.assertLessEqual(remaining, 60.0 + 1)


if __name__ == "__main__":
    unittest.main()
