"""Validation: shared DexScreener client throttling, caching, and batch rotation."""
import logging
import sys
import time
from unittest.mock import MagicMock, patch

from config import Config
from dexscreener_client import (
    DexScreenerClient,
    get_dexscreener_client,
    reset_dexscreener_client_for_tests,
)


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_cache_avoids_duplicate_requests():
    reset_dexscreener_client_for_tests()
    client = DexScreenerClient()
    with patch.object(Config, "DEXSCREENER_REQUEST_DELAY_SEC", 0.0):
        with patch.object(Config, "DEXSCREENER_PAIR_CACHE_TTL_SEC", 60):
            calls = {"n": 0}

            def fake_get(url, timeout=15, headers=None):
                calls["n"] += 1
                return _FakeResponse(200, [{"chainId": "solana"}])

            with patch.object(client._session, "get", side_effect=fake_get):
                assert client.get("/token-boosts/top/v1") is not None
                assert client.get("/token-boosts/top/v1") is not None
            assert calls["n"] == 1
    print("PASS: cache_avoids_duplicate_requests")


def test_rate_limit_warns_once_per_path():
    reset_dexscreener_client_for_tests()
    client = DexScreenerClient()
    warnings: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    logger = logging.getLogger("dexscreener_client")
    handler = _Handler()
    logger.addHandler(handler)
    try:
        with patch.object(Config, "DEXSCREENER_REQUEST_DELAY_SEC", 0.0):
            with patch.object(
                client._session,
                "get",
                return_value=_FakeResponse(429),
            ):
                client.get("/token-pairs/v1/solana/Mint111", max_retries=2)
                client.get("/token-pairs/v1/solana/Mint111", max_retries=2)
        assert len([w for w in warnings if "rate limited" in w.lower()]) == 1
    finally:
        logger.removeHandler(handler)
    print("PASS: rate_limit_warns_once_per_path")


def test_seed_batch_rotates():
    reset_dexscreener_client_for_tests()
    client = DexScreenerClient()
    with patch.object(Config, "DEXSCREENER_DEEP_SCAN_PER_CYCLE", 3):
        with patch.object(Config, "DEXSCREENER_MAX_SEED_MINTS", 10):
            seeds = [f"mint{i}" for i in range(10)]
            client.begin_scan_cycle()
            batch1 = client.get_seed_batch(seeds)
            client.begin_scan_cycle()
            batch2 = client.get_seed_batch(seeds)
            assert batch1 != batch2
            assert len(batch1) == 3
    print("PASS: seed_batch_rotates")


def test_seed_batch_respects_per_cycle_override():
    reset_dexscreener_client_for_tests()
    client = DexScreenerClient()
    with patch.object(Config, "DEXSCREENER_DEEP_SCAN_PER_CYCLE", 15):
        with patch.object(Config, "DEXSCREENER_MAX_SEED_MINTS", 10):
            seeds = [f"mint{i}" for i in range(10)]
            client.begin_scan_cycle()
            batch = client.get_seed_batch(seeds, per_cycle=5)
            assert len(batch) == 5
    print("PASS: seed_batch_respects_per_cycle_override")


def test_scan_unified_uses_shared_client():
    reset_dexscreener_client_for_tests()
    from scanner import scan_unified

    begin = MagicMock()
    with patch("scanner.get_dexscreener_client") as mock_get:
        mock_client = MagicMock()
        mock_client.begin_scan_cycle = begin
        mock_client.get_health.return_value = {"status": "ok"}
        mock_get.return_value = mock_client
        with patch("scanner.MoverScanner") as dex_cls:
            dex_cls.return_value.scan = MagicMock(return_value=[])
            with patch("pumpfun_scanner.PumpFunScanner") as pump_cls:
                pump_cls.return_value.scan = MagicMock(return_value=[])
                with patch("birdeye_scanner.BirdeyeScanner") as bird_cls:
                    bird_cls.return_value.scan = MagicMock(return_value=[])
                    scan_unified(include_pumpfun=False, include_birdeye=False)
    begin.assert_called_once()
    print("PASS: scan_unified_uses_shared_client")


def test_effective_max_seeds_reduced_when_limited():
    reset_dexscreener_client_for_tests()
    client = DexScreenerClient()
    client._rate_limit_until = time.time() + 30
    client._cycle_rate_limited = True
    with patch.object(Config, "DEXSCREENER_MAX_SEED_MINTS", 75):
        assert client.effective_max_seeds() == 15
    print("PASS: effective_max_seeds_reduced_when_limited")


def test_scan_interval_boost_when_rate_limited():
    reset_dexscreener_client_for_tests()
    client = DexScreenerClient()
    client._cycle_429_count = 2
    assert client.get_scan_interval_boost() == 5
    print("PASS: scan_interval_boost_when_rate_limited")


def main():
    test_cache_avoids_duplicate_requests()
    test_rate_limit_warns_once_per_path()
    test_seed_batch_rotates()
    test_seed_batch_respects_per_cycle_override()
    test_scan_unified_uses_shared_client()
    test_effective_max_seeds_reduced_when_limited()
    test_scan_interval_boost_when_rate_limited()
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
