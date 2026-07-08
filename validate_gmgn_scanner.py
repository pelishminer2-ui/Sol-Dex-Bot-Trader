"""Validation tests for GMGN scanner and unified watchlist merging."""
import sys
from unittest.mock import MagicMock, patch

from config import Config
from gmgn_scanner import (
    GmgnScanner,
    get_last_gmgn_scan_status,
    parse_gmgn_token,
)
from scanner import MoverCandidate, merge_candidates, scan_unified


def _sample_gmgn_token(**overrides) -> dict:
    base = {
        "address": "GmgnMint1111111111111111111111111111111",
        "symbol": "GMGN",
        "name": "GMGN Token",
        "price": 0.01,
        "liquidity": 50000,
        "volume": 100000,
        "price_change_percent5m": 2.5,
        "price_change_percent1h": 1.5,
        "open_timestamp": 1700000000,
        "launchpad": "pump",
    }
    base.update(overrides)
    return base


def test_parse_gmgn_token_qualifies():
    candidate = parse_gmgn_token(_sample_gmgn_token())
    assert candidate is not None
    assert candidate.source == "gmgn"
    assert candidate.mint == "GmgnMint1111111111111111111111111111111"
    assert candidate.momentum_pct == 0.025
    print("PASS: parse_gmgn_token_qualifies")


def test_parse_gmgn_token_rejects_low_liquidity():
    candidate = parse_gmgn_token(_sample_gmgn_token(liquidity=1000))
    assert candidate is None
    print("PASS: parse_gmgn_token_rejects_low_liquidity")


def test_parse_gmgn_token_rejects_18k_liquidity_floor():
    with patch.object(Config, "MIN_LIQUIDITY_USD", 15000.0), patch.object(
        Config, "GMGN_MIN_LIQUIDITY_USD", None
    ):
        candidate = parse_gmgn_token(_sample_gmgn_token(liquidity=18000))
    assert candidate is None
    print("PASS: parse_gmgn_token_rejects_18k_liquidity_floor")


def test_gmgn_scan_status_on_failure():
    with patch.object(GmgnScanner, "_collect_seed_tokens", return_value=[]):
        result = GmgnScanner().scan()
    assert result == []
    assert get_last_gmgn_scan_status() == "failed"
    print("PASS: gmgn_scan_status_on_failure")


def test_scan_unified_includes_gmgn_when_enabled():
    gmgn_mover = MoverCandidate(
        mint="GmgnMint",
        symbol="GMGN",
        name="GMGN",
        pair_address="",
        dex="pump",
        price_usd=0.01,
        liquidity_usd=50000,
        volume_24h_usd=100000,
        momentum_pct=0.03,
        price_change_5m=0.03,
        price_change_1h=0.02,
        source="gmgn",
    )
    with patch("scanner.MoverScanner") as dex_cls:
        dex_cls.return_value.scan = MagicMock(return_value=[])
        with patch("pumpfun_scanner.PumpFunScanner") as pump_cls:
            pump_cls.return_value.scan = MagicMock(return_value=[])
            with patch("birdeye_scanner.BirdeyeScanner") as bird_cls:
                bird_cls.return_value.scan = MagicMock(return_value=[])
                with patch("gmgn_scanner.GmgnScanner") as gmgn_cls:
                    gmgn_cls.return_value.scan = MagicMock(return_value=[gmgn_mover])
                    merged, dex_count, pumpfun_count, birdeye_count, gmgn_count = scan_unified(
                        include_pumpfun=True,
                        include_birdeye=True,
                        include_gmgn=True,
                    )
    assert gmgn_count == 1
    assert any(c.source == "gmgn" for c in merged)
    assert dex_count == 0 and pumpfun_count == 0 and birdeye_count == 0
    print("PASS: scan_unified_includes_gmgn_when_enabled")


def test_scan_unified_skips_gmgn_when_disabled():
    with patch("scanner.MoverScanner") as dex_cls:
        dex_cls.return_value.scan = MagicMock(return_value=[])
        with patch("pumpfun_scanner.PumpFunScanner") as pump_cls:
            pump_cls.return_value.scan = MagicMock(return_value=[])
            with patch("birdeye_scanner.BirdeyeScanner") as bird_cls:
                bird_cls.return_value.scan = MagicMock(return_value=[])
                with patch("gmgn_scanner.GmgnScanner") as gmgn_cls:
                    gmgn_cls.return_value.scan = MagicMock(return_value=[])
                    _, _, _, _, gmgn_count = scan_unified(
                        include_pumpfun=False,
                        include_birdeye=False,
                        include_gmgn=False,
                    )
    gmgn_cls.return_value.scan.assert_not_called()
    assert gmgn_count == 0
    print("PASS: scan_unified_skips_gmgn_when_disabled")


def test_scan_unified_works_without_gmgn_key():
    with patch.object(Config, "GMGN_API_KEY", ""):
        with patch("scanner.MoverScanner") as dex_cls:
            dex_cls.return_value.scan = MagicMock(return_value=[])
            with patch("gmgn_scanner.GmgnScanner") as gmgn_cls:
                gmgn_cls.return_value.scan = MagicMock(return_value=[])
                merged, _, _, _, gmgn_count = scan_unified(
                    include_pumpfun=False,
                    include_birdeye=False,
                    include_gmgn=True,
                )
    assert merged == []
    assert gmgn_count == 0
    print("PASS: scan_unified_works_without_gmgn_key")


def test_merge_candidates_prefers_higher_momentum():
    dex = MoverCandidate(
        mint="SameMint",
        symbol="X",
        name="X",
        pair_address="p1",
        dex="raydium",
        price_usd=1.0,
        liquidity_usd=20000,
        volume_24h_usd=60000,
        momentum_pct=0.02,
        price_change_5m=0.02,
        price_change_1h=0.01,
        source="dexscreener",
    )
    gmgn = MoverCandidate(
        mint="SameMint",
        symbol="X",
        name="X",
        pair_address="",
        dex="pump",
        price_usd=1.0,
        liquidity_usd=20000,
        volume_24h_usd=60000,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.03,
        source="gmgn",
    )
    merged = merge_candidates([dex], [gmgn])
    assert len(merged) == 1
    assert merged[0].source == "gmgn"
    print("PASS: merge_candidates_prefers_higher_momentum")


def test_gmgn_min_liquidity_floor_20k():
    candidate = parse_gmgn_token(_sample_gmgn_token(liquidity=15000))
    assert candidate is None
    candidate = parse_gmgn_token(_sample_gmgn_token(liquidity=25000))
    assert candidate is not None
    assert candidate.source == "gmgn"
    print("PASS: gmgn_min_liquidity_floor_20k")


def main():
    test_parse_gmgn_token_qualifies()
    test_parse_gmgn_token_rejects_low_liquidity()
    test_parse_gmgn_token_rejects_18k_liquidity_floor()
    test_gmgn_scan_status_on_failure()
    test_scan_unified_includes_gmgn_when_enabled()
    test_scan_unified_skips_gmgn_when_disabled()
    test_scan_unified_works_without_gmgn_key()
    test_merge_candidates_prefers_higher_momentum()
    test_gmgn_min_liquidity_floor_20k()
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
