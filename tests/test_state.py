import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import CircuitConfig, ProviderConfig
from relay_gateway.health import HealthMonitor
from relay_gateway.state import CircuitStateStore, PersistedState


def _provider() -> ProviderConfig:
    return ProviderConfig(
        id="p1",
        name="p1",
        priority=1,
        base_url="http://127.0.0.1:9",
        api_key_env=None,
        circuit=CircuitConfig(failure_threshold=1, cooldown_s=60),
    )


class StatePersistenceTests(unittest.TestCase):
    def test_open_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            store = CircuitStateStore(os.path.join(td, "state.json"))
            hm = HealthMonitor([_provider()], state_store=store, fingerprint="fp1")
            hm.record_failure("p1", "boom")
            self.assertFalse(hm.is_available(_provider()))

            hm2 = HealthMonitor([_provider()], state_store=store, fingerprint="fp1")
            self.assertFalse(hm2.is_available(_provider()))

    def test_fingerprint_mismatch_ignores_stale_state(self):
        with tempfile.TemporaryDirectory() as td:
            store = CircuitStateStore(os.path.join(td, "state.json"))
            hm = HealthMonitor([_provider()], state_store=store, fingerprint="fp1")
            hm.record_failure("p1", "boom")

            hm3 = HealthMonitor([_provider()], state_store=store, fingerprint="fp2")
            self.assertTrue(hm3.is_available(_provider()))

    def test_corrupt_file_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            Path(path).write_text("{bad json", encoding="utf-8")
            store = CircuitStateStore(path)
            ps = store.load()
            self.assertIsInstance(ps, PersistedState)
            self.assertEqual(ps.fingerprint, "")
            self.assertEqual(ps.states, {})

    def test_reset_persists_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            store = CircuitStateStore(path)
            hm = HealthMonitor([_provider()], state_store=store, fingerprint="fp1")
            hm.record_failure("p1", "boom")
            self.assertFalse(hm.is_available(_provider()))

            hm.reset("p1")
            self.assertTrue(hm.is_available(_provider()))
            saved = store.load()
            self.assertEqual(saved.states["p1"]["state"], "CLOSED")
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_state_fingerprint_stable_and_sensitive_to_config(self):
        from relay_gateway.config import GatewayConfig

        cfg1 = GatewayConfig(providers=[_provider()])
        cfg2 = GatewayConfig(providers=[_provider()])
        self.assertEqual(cfg1.state_fingerprint(), cfg2.state_fingerprint())
        p2 = _provider()
        p2.timeout_s = 99
        cfg3 = GatewayConfig(providers=[p2])
        self.assertNotEqual(cfg1.state_fingerprint(), cfg3.state_fingerprint())


if __name__ == "__main__":
    unittest.main()
