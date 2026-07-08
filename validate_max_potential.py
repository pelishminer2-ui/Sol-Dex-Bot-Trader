"""Validation tests for max-potential discovery settings."""
import sys
from unittest.mock import patch

from config import (
    Config,
    DEFAULT_DEXSCREENER_MAX_SEED_MINTS,
    DEFAULT_WATCHLIST_TOP_N,
    MAX_POTENTIAL_DEXSCREENER_MAX_SEED_MINTS,
    MAX_POTENTIAL_WATCHLIST_TOP_N,
)
from scanner import scan_unified


def test_max_potential_effective_filters():
    with patch.object(Config, "MAX_POTENTIAL_MODE", True):
        with patch.object(Config, "MIN_LIQUIDITY_USD", 12000):
            assert Config.effective_min_liquidity_usd() == 10000
        with patch.object(Config, "MIN_VOLUME_24H_USD", 50000):
            assert Config.effective_min_volume_24h_usd() == 35000
        with patch.object(Config, "MAX_ENTRY_PRICE_IMPACT_PCT", 1.0):
            assert Config.effective_max_entry_price_impact_pct() == 1.25
    print("PASS: max_potential_effective_filters")


def test_standard_mode_uses_config_values():
    with patch.object(Config, "MAX_POTENTIAL_MODE", False):
        with patch.object(Config, "MIN_LIQUIDITY_USD", 12000):
            assert Config.effective_min_liquidity_usd() == 12000
    print("PASS: standard_mode_uses_config_values")


def test_max_potential_defaults_in_config_dict():
    with patch.object(Config, "MAX_POTENTIAL_MODE", True):
        with patch.object(Config, "DEXSCREENER_MAX_SEED_MINTS", 30):
            with patch.object(Config, "WATCHLIST_TOP_N", 50):
                cfg = Config.to_dict()
                assert cfg["max_potential_mode"] is True
                assert cfg["dexscreener_max_seed_mints"] == 30
                assert cfg["watchlist_top_n"] == 50
                assert "scanner_api_keys" in cfg
    print("PASS: max_potential_defaults_in_config_dict")


def test_scan_unified_returns_five_tuple():
    dex_candidate = type("C", (), {"mint": "x", "momentum_pct": 0.1, "source": "dexscreener"})()
    with patch("scanner.MoverScanner.scan", return_value=[dex_candidate]):
        with patch("pumpfun_scanner.PumpFunScanner.scan", return_value=[]):
            with patch("birdeye_scanner.BirdeyeScanner.scan", return_value=[]):
                with patch("gmgn_scanner.GmgnScanner.scan", return_value=[]):
                    result = scan_unified(include_pumpfun=True, include_birdeye=True, include_gmgn=True)
    assert len(result) == 5
    merged, dex_count, pumpfun_count, birdeye_count, gmgn_count = result
    assert dex_count == 1
    assert pumpfun_count == 0
    assert birdeye_count == 0
    assert gmgn_count == 0
    assert len(merged) == 1
    print("PASS: scan_unified_returns_five_tuple")


def test_default_breadth_constants():
    assert DEFAULT_DEXSCREENER_MAX_SEED_MINTS == 30
    assert DEFAULT_WATCHLIST_TOP_N >= 30
    assert MAX_POTENTIAL_DEXSCREENER_MAX_SEED_MINTS == DEFAULT_DEXSCREENER_MAX_SEED_MINTS
    assert MAX_POTENTIAL_WATCHLIST_TOP_N > DEFAULT_WATCHLIST_TOP_N
    print("PASS: default_breadth_constants")


def main():
    test_max_potential_effective_filters()
    test_standard_mode_uses_config_values()
    test_max_potential_defaults_in_config_dict()
    test_scan_unified_returns_five_tuple()
    test_default_breadth_constants()
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
