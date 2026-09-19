import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import PrivacyConfig
from relay_gateway.privacy import PrivacyEngine, StreamRestorer


# 合成测试密钥：拆段拼接，避免发布前清扫门禁误命中真实格式的 sk- 字面量
SYNTH_SK_KEY = "sk-" + "SYNTHabcdefghijklmnopqrstuvwxyz123456"


class PrivacyEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = PrivacyEngine(
            PrivacyConfig(
                enabled=True,
                mode="safe_route",
                vault_paths=["C:\\Users\\alice\\my-vault"],
                keywords=[],
            )
        )

    def test_detects_email_ip_password_key_and_vault_path(self):
        payload = {
            "model": "mock",
            "messages": [
                {
                    "role": "user",
                    "content": f"my email is a@b.com, ip 8.8.8.8, password=supersecret123, key {SYNTH_SK_KEY}, vault C:\\Users\\alice\\my-vault\\note.md",
                }
            ],
        }
        scan, mapping = self.engine.scan_payload(payload)
        self.assertTrue(scan.sensitive)
        self.assertIn("email", scan.categories)
        self.assertIn("ip", scan.categories)
        self.assertIn("secret", scan.categories)
        self.assertIn("api_key", scan.categories)
        self.assertIn("vault_path", scan.categories)
        self.assertIsNone(mapping)  # safe_route 不脱敏

    def test_redact_and_restore_roundtrip(self):
        engine = PrivacyEngine(PrivacyConfig(enabled=True, mode="redact", keywords=[]))
        payload = {
            "model": "mock",
            "messages": [{"role": "user", "content": "write to marie@acme.com please"}],
        }
        scan, mapping = engine.scan_payload(payload)
        self.assertTrue(scan.sensitive)
        self.assertIsNotNone(mapping)
        redacted_text = payload["messages"][0]["content"]
        self.assertNotIn("marie@acme.com", redacted_text)
        self.assertIn("<EMAIL_1>", redacted_text)
        restored = engine.restore(redacted_text, mapping)
        self.assertIn("marie@acme.com", restored)

    def test_irreversible_secrets_not_restored(self):
        engine = PrivacyEngine(PrivacyConfig(enabled=True, mode="redact", keywords=[]))
        payload = {"messages": [{"role": "user", "content": f"key={SYNTH_SK_KEY}"}]}
        scan, mapping = engine.scan_payload(payload)
        self.assertIsNotNone(mapping)
        redacted = payload["messages"][0]["content"]
        self.assertIn("[REDACTED_API_KEY]", redacted)
        restored = engine.restore(redacted, mapping)
        self.assertNotIn(SYNTH_SK_KEY, restored)

    def test_scans_tool_call_arguments(self):
        payload = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "send", "arguments": '{"to":"victim@evil.com","note":"secret"}'},
                        }
                    ],
                }
            ]
        }
        scan, _ = self.engine.scan_payload(payload)
        self.assertTrue(scan.sensitive)
        self.assertIn("email", scan.categories)

    def test_block_rule(self):
        config = PrivacyConfig(enabled=True, mode="block", keywords=[], custom_patterns=[{"name": "BLOCK", "regex": r"forbidden-zone", "category": "custom", "block": True}])
        engine = PrivacyEngine(config)
        payload = {"messages": [{"role": "user", "content": "go to forbidden-zone now"}]}
        scan, _ = engine.scan_payload(payload)
        self.assertTrue(scan.blocked)

    def test_sticky_session(self):
        from relay_gateway.privacy import SessionSticky

        sticky = SessionSticky(True, ttl_s=60)
        self.assertFalse(sticky.is_pinned("s1"))
        sticky.pin("s1")
        self.assertTrue(sticky.is_pinned("s1"))
        sticky2 = SessionSticky(True, ttl_s=0)
        sticky2.pin("s2")
        import time

        time.sleep(0.01)
        self.assertFalse(sticky2.is_pinned("s2"))

    def test_stream_restorer_handles_split_placeholder(self):
        mapping = {"<EMAIL_1>": "marie@acme.com"}
        restorer = StreamRestorer(mapping, max_ph_len=8)
        out1 = restorer.process('data: {"content":"hi <EMAI')
        out2 = restorer.process("L_1> done\"}\n\n")
        tail = restorer.flush()
        self.assertIn("marie@acme.com", out1 + out2 + tail)
        self.assertNotIn("<EMAI", out1 + out2 + tail)


if __name__ == "__main__":
    unittest.main()
