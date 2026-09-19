import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import urllib.error

from relay_gateway.config import ProviderConfig
from relay_gateway.upstream import UpstreamError, call_json, open_stream


def _provider() -> ProviderConfig:
    return ProviderConfig(
        id="p",
        name="p",
        priority=1,
        base_url="http://127.0.0.1:9",
        api_key_env=None,
    )


def _http_error(status: int, headers: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:9/v1/chat/completions",
        status,
        "err",
        headers,
        io.BytesIO(b"{}"),
    )


class RetryAfterTests(unittest.TestCase):
    def _assert_retry_after(self, headers: dict, expected: float | None, status: int = 429):
        err = _http_error(status, headers)
        with mock.patch("relay_gateway.upstream.urllib.request.urlopen", side_effect=err):
            with self.assertRaises(UpstreamError) as cm:
                call_json(_provider(), "/v1/chat/completions", b"{}", 5)
            self.assertEqual(cm.exception.retry_after, expected)

    def test_retry_after_seconds(self):
        self._assert_retry_after({"Retry-After": "30"}, 30.0)

    def test_retry_after_zero(self):
        self._assert_retry_after({"Retry-After": "0"}, 0.0)

    def test_retry_after_missing(self):
        self._assert_retry_after({}, None)

    def test_retry_after_invalid(self):
        self._assert_retry_after({"Retry-After": "abc"}, None)

    def test_retry_after_on_503(self):
        self._assert_retry_after({"Retry-After": "5"}, 5.0, status=503)

    def test_retry_after_capped(self):
        self._assert_retry_after({"Retry-After": "99999"}, 3600.0)

    def test_open_stream_propagates_retry_after(self):
        err = _http_error(429, {"Retry-After": "12"})
        with mock.patch("relay_gateway.upstream.urllib.request.urlopen", side_effect=err):
            with self.assertRaises(UpstreamError) as cm:
                open_stream(_provider(), "/v1/chat/completions", b"{}", 5)
            self.assertEqual(cm.exception.retry_after, 12.0)

    def test_retry_after_http_date(self):
        import datetime

        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=45)
        date_str = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
        err = _http_error(503, {"Retry-After": date_str})
        with mock.patch("relay_gateway.upstream.urllib.request.urlopen", side_effect=err):
            with self.assertRaises(UpstreamError) as cm:
                call_json(_provider(), "/v1/chat/completions", b"{}", 5)
            self.assertIsNotNone(cm.exception.retry_after)
            self.assertAlmostEqual(cm.exception.retry_after, 45.0, delta=1.0)

    def test_no_double_v1_in_url(self):
        from relay_gateway.upstream import _build_url

        provider = _provider()
        self.assertEqual(
            _build_url(provider, "/v1/chat/completions"),
            "http://127.0.0.1:9/v1/chat/completions",
        )

    def test_base_url_without_v1_still_works(self):
        from relay_gateway.upstream import _build_url

        provider = ProviderConfig(
            id="p",
            name="p",
            priority=1,
            base_url="http://127.0.0.1:9",
            api_key_env=None,
        )
        self.assertEqual(
            _build_url(provider, "/v1/chat/completions"),
            "http://127.0.0.1:9/v1/chat/completions",
        )


if __name__ == "__main__":
    unittest.main()
