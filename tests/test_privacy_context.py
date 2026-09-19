import sys
import unittest
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import PrivacyConfig
from relay_gateway.privacy import PrivacyEngine


class PrivacyContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = PrivacyEngine(
            PrivacyConfig(
                enabled=True,
                mode="safe_route",
                vault_paths=["C:\\Users\\alice\\my-vault"],
                keywords=[],
            )
        )
        self.redact_engine = PrivacyEngine(
            PrivacyConfig(enabled=True, mode="redact", vault_paths=[], keywords=[])
        )

    def _scan(self, engine, content):
        return engine.scan_payload(
            {"model": "m", "messages": [{"role": "user", "content": content}]}
        )

    def test_chinese_password_context_sensitive(self):
        scan, mapping = self._scan(self.engine, "我的密码是 hunter2")
        self.assertTrue(scan.sensitive)
        self.assertIn("secret", scan.categories)
        self.assertIsNone(mapping)

    def test_password_assignment_sensitive(self):
        scan, _ = self._scan(self.engine, "password=hunter2")
        self.assertTrue(scan.sensitive)
        self.assertIn("secret", scan.categories)

    def test_token_endpoint_not_false_positive(self):
        scan, _ = self._scan(self.engine, "the token endpoint is /v1/auth")
        self.assertFalse(scan.sensitive)

    def test_github_token_api_key(self):
        scan, _ = self._scan(
            self.engine, "token: ghp_SYNTHabcdefghijklmnopqrstuvwxyz123456"
        )
        self.assertTrue(scan.sensitive)
        self.assertIn("api_key", scan.categories)

    def test_chinese_credit_card_context(self):
        scan, _ = self._scan(self.engine, "我的银行卡号是 6222 8888 8888 8888")
        self.assertTrue(scan.sensitive)
        self.assertTrue({"credit_card", "secret"} & scan.categories)

    def test_redact_mode_masks_context_irreversibly(self):
        payload = {"model": "m", "messages": [{"role": "user", "content": "我的密码是 hunter2"}]}
        scan, mapping = self.redact_engine.scan_payload(payload)
        self.assertTrue(scan.sensitive)
        self.assertNotIn("hunter2", payload["messages"][0]["content"])
        self.assertIn("REDACTED", payload["messages"][0]["content"])
        self.assertNotIn("hunter2", str(mapping or {}))

    def test_redact_mode_masks_english_password_with_copula(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "the password is P@ssw0rd123"}],
        }
        scan, mapping = self.redact_engine.scan_payload(payload)
        self.assertTrue(scan.sensitive)
        self.assertNotIn("P@ssw0rd123", payload["messages"][0]["content"])
        self.assertNotIn("P@ssw0rd123", str(mapping or {}))

    def test_safe_route_does_not_mutate(self):
        content = "我的密码是 hunter2"
        payload = {"model": "m", "messages": [{"role": "user", "content": content}]}
        scan, mapping = self.engine.scan_payload(payload)
        self.assertTrue(scan.sensitive)
        self.assertEqual(payload["messages"][0]["content"], content)
        self.assertIsNone(mapping)

    def test_clean_tech_text_no_false_positive(self):
        scan, _ = self._scan(self.engine, "configure the token endpoint and api key management")
        self.assertFalse(scan.sensitive)

    def test_cn_policy_word_no_false_positive(self):
        scan, _ = self._scan(self.engine, "请帮我配置密码策略")
        self.assertFalse(scan.sensitive)

    def test_agent_policy_language_no_false_positive(self):
        scan, _ = self._scan(
            self.engine,
            "Never expose or log secrets and keys. Never commit tokens or passwords to the repository.",
        )
        self.assertFalse(scan.sensitive)

    def test_refresh_token_handling_no_false_positive(self):
        scan, _ = self._scan(self.engine, "add refresh token handling to auth.ts")
        self.assertFalse(scan.sensitive)

    def test_refresh_token_support_no_false_positive(self):
        scan, _ = self._scan(self.engine, "add refresh token support to auth.ts")
        self.assertFalse(scan.sensitive)

    def test_password_context_with_digits_still_sensitive(self):
        scan, _ = self._scan(self.engine, "the password is hunter2")
        self.assertTrue(scan.sensitive)

    def test_base64_email_encoded_bypass(self):
        encoded = base64.b64encode(b"alice@example.com").decode()
        scan, _ = self._scan(self.engine, encoded)
        self.assertTrue(scan.sensitive)
        self.assertIn("email", scan.categories)
        self.assertTrue(scan.encoded_bypass)

    def test_hex_key_encoded_bypass(self):
        key = "sk-SYNTHabcdefghijklmnopqrstuvwxyz123456"
        encoded = key.encode().hex()
        scan, _ = self._scan(self.engine, encoded)
        self.assertTrue(scan.sensitive)
        self.assertIn("api_key", scan.categories)
        self.assertTrue(scan.encoded_bypass)

    def test_urlencoded_password_encoded_bypass(self):
        import urllib.parse

        encoded = urllib.parse.quote("password=hunter2")
        scan, _ = self._scan(self.engine, encoded)
        self.assertTrue(scan.sensitive)
        self.assertIn("secret", scan.categories)

    def test_redact_mode_masks_encoded_bypass_irreversibly(self):
        encoded = base64.b64encode(b"alice@example.com").decode()
        payload = {"model": "m", "messages": [{"role": "user", "content": encoded}]}
        scan, mapping = self.redact_engine.scan_payload(payload)
        self.assertTrue(scan.sensitive)
        self.assertEqual(payload["messages"][0]["content"], "[REDACTED_ENCODED]")
        self.assertNotIn("alice@example.com", str(mapping or {}))


if __name__ == "__main__":
    unittest.main()
