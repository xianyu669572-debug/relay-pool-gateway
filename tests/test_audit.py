import json
import os
import sys
import tempfile
import unittest
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


def _provider() -> ProviderConfig:
    return ProviderConfig(
        id="a",
        name="a",
        priority=1,
        base_url="http://127.0.0.1:9",
        api_key_env=None,
        health=HealthConfig(enabled=False),
    )


class AuditTests(unittest.TestCase):
    def test_audit_rotates_and_never_writes_body(self):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "audit.jsonl")
            cfg = GatewayConfig(
                server=ServerConfig(host="127.0.0.1", port=0),
                routing=RoutingConfig(),
                privacy=PrivacyConfig(
                    enabled=True,
                    mode="safe_route",
                    keywords=[],
                    audit_log=log,
                    audit_max_bytes=150,
                ),
                providers=[_provider()],
            )
            srv = RelayPoolServer(("127.0.0.1", 0), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, cfg)
            try:
                for i in range(5):
                    srv.audit(
                        {
                            "ts": "2026-08-06T00:00:00Z",
                            "request_id": f"req-{i}",
                            "provider": "a",
                            "status": 200,
                            "action": "normal",
                        }
                    )
                self.assertTrue(os.path.exists(log + ".1"))
                for path in (log, log + ".1"):
                    with open(path, encoding="utf-8") as f:
                        for line in f:
                            entry = json.loads(line)
                            self.assertNotIn("body", entry)
                            self.assertNotIn("authorization", entry)
                            self.assertNotIn("content", entry)
            finally:
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
