"""Validation: shared Jupiter client throttling, caching, and 429 handling."""
import logging
import sys
from unittest.mock import patch

from config import Config
from jupiter_client import JupiterClient, get_jupiter_client, reset_jupiter_client_for_tests


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_price_cache_avoids_duplicate_requests():
    reset_jupiter_client_for_tests()
    client = JupiterClient()
    mint = "So11111111111111111111111111111111111111112"
    payload = {mint: {"usdPrice": 150.0}}
    with patch.object(Config, "JUPITER_REQUEST_DELAY_SEC", 0.0):
        with patch.object(Config, "JUPITER_PRICE_CACHE_TTL_SEC", 60):
            calls = {"n": 0}

            def fake_request(method, url, params=None, json=None, timeout=15):
                calls["n"] += 1
                return _FakeResponse(200, payload)

            with patch.object(client._session, "request", side_effect=fake_request):
                assert client.get_prices([mint]).get(mint) == 150.0
                assert client.get_prices([mint]).get(mint) == 150.0
            assert calls["n"] == 1
    print("PASS: price_cache_avoids_duplicate_requests")


def test_quote_cache_avoids_duplicate_requests():
    reset_jupiter_client_for_tests()
    client = JupiterClient()
    quote_payload = {"inAmount": "1000000", "outAmount": "500", "priceImpactPct": "0.1"}
    with patch.object(Config, "JUPITER_REQUEST_DELAY_SEC", 0.0):
        with patch.object(Config, "JUPITER_QUOTE_CACHE_TTL_SEC", 30):
            calls = {"n": 0}

            def fake_request(method, url, params=None, json=None, timeout=15):
                calls["n"] += 1
                return _FakeResponse(200, quote_payload)

            with patch.object(client._session, "request", side_effect=fake_request):
                q1 = client.get_quote("mintA", "mintB", 1_000_000, 100)
                q2 = client.get_quote("mintA", "mintB", 1_000_000, 100)
                assert q1 == quote_payload
                assert q2 == quote_payload
            assert calls["n"] == 1
    print("PASS: quote_cache_avoids_duplicate_requests")


def test_rate_limit_warns_once_per_key():
    reset_jupiter_client_for_tests()
    client = JupiterClient()
    warnings: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    logger = logging.getLogger("jupiter_client")
    handler = _Handler()
    logger.addHandler(handler)
    try:
        with patch.object(Config, "JUPITER_REQUEST_DELAY_SEC", 0.0):
            with patch.object(
                client._session,
                "request",
                return_value=_FakeResponse(429, headers={"Retry-After": "1"}),
            ):
                client.get_quote("mintA", "mintB", 1, 100, max_retries=2)
                client.get_quote("mintA", "mintB", 1, 100, max_retries=2)
        assert len([w for w in warnings if "rate limited" in w.lower()]) == 1
    finally:
        logger.removeHandler(handler)
    print("PASS: rate_limit_warns_once_per_key")


def test_poll_boost_when_rate_limited():
    reset_jupiter_client_for_tests()
    client = JupiterClient()
    client._cycle_429_count = 2
    assert client.get_poll_interval_boost() == 3
    print("PASS: poll_boost_when_rate_limited")


def test_singleton():
    reset_jupiter_client_for_tests()
    assert get_jupiter_client() is get_jupiter_client()
    print("PASS: singleton")


def main():
    test_price_cache_avoids_duplicate_requests()
    test_quote_cache_avoids_duplicate_requests()
    test_rate_limit_warns_once_per_key()
    test_poll_boost_when_rate_limited()
    test_singleton()
    print("VALIDATION_OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"VALIDATION_FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"VALIDATION_FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
