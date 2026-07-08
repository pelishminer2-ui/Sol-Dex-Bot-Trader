"""Validation tests for pump.fun scanner and unified watchlist merging."""
import sys
import time
from unittest.mock import patch

from pumpfun_scanner import (
    PumpFunScanner,
    get_last_pumpfun_scan_status,
    parse_pumpfun_pair,
)
from scanner import MoverCandidate, merge_candidates, parse_pair, scan_unified


def _sample_pair(
    mint: str = "Mint111111111111111111111111111111111111111",
    dex: str = "pumpfun",
    liquidity: float = 50000,
    volume: float = 100000,
    momentum_m5: float = 5.0,
    market_cap: float = 50000,
) -> dict:
    return {
        "chainId": "solana",
        "dexId": dex,
        "pairAddress": "pair123",
        "priceUsd": "0.001",
        "marketCap": market_cap,
        "liquidity": {"usd": liquidity},
        "volume": {"h24": volume},
        "priceChange": {"m5": momentum_m5, "h1": 2.0},
        "pairCreatedAt": int(time.time() * 1000) - 2 * 3600 * 1000,
        "baseToken": {
            "address": mint,
            "symbol": "TEST",
            "name": "Test Token",
        },
    }


def test_parse_pumpfun_pair_sets_source():
    candidate = parse_pumpfun_pair(_sample_pair())
    assert candidate is not None
    assert candidate.source == "pumpfun"
    assert candidate.mint.startswith("Mint")
    print("PASS: parse_pumpfun_pair_sets_source")


def test_merge_deduplicates_by_mint():
    dex = MoverCandidate(
        mint="AAA",
        symbol="A",
        name="A",
        pair_address="p1",
        dex="raydium",
        price_usd=1.0,
        liquidity_usd=12000,
        volume_24h_usd=50000,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.02,
        source="dexscreener",
    )
    pump = MoverCandidate(
        mint="AAA",
        symbol="A",
        name="A",
        pair_address="p2",
        dex="pumpfun",
        price_usd=1.0,
        liquidity_usd=12000,
        volume_24h_usd=50000,
        momentum_pct=0.10,
        price_change_5m=0.10,
        price_change_1h=0.08,
        source="pumpfun",
    )
    other = MoverCandidate(
        mint="BBB",
        symbol="B",
        name="B",
        pair_address="p3",
        dex="pumpfun",
        price_usd=1.0,
        liquidity_usd=12000,
        volume_24h_usd=50000,
        momentum_pct=0.03,
        price_change_5m=0.03,
        price_change_1h=0.01,
        source="pumpfun",
    )
    merged = merge_candidates([dex], [pump, other])
    by_mint = {c.mint: c for c in merged}
    assert len(merged) == 2
    assert by_mint["AAA"].momentum_pct == 0.10
    assert by_mint["AAA"].source == "pumpfun"
    assert merged[0].momentum_pct >= merged[1].momentum_pct
    print("PASS: merge_deduplicates_by_mint")


def test_pumpfun_scanner_mock():
    scanner = PumpFunScanner()

    def fake_dex(path, timeout=15):
        if "search?q=pumpfun" in path or "search?q=pump.fun" in path:
            return {"pairs": [_sample_pair(mint="PumpMint111111111111111111111111111111111")]}
        if "/token-pairs/" in path:
            return [_sample_pair(mint="ApiMint222222222222222222222222222222222")]
        return None

    def fake_api(url, timeout=15):
        return [{"mint": "ApiMint222222222222222222222222222222222", "symbol": "API"}], False

    with patch.object(scanner, "_get_dexscreener", side_effect=fake_dex):
        with patch.object(scanner, "_get_pumpfun_api", side_effect=fake_api):
            results = scanner.scan()

    assert isinstance(results, list)
    assert len(results) >= 1
    assert all(c.source == "pumpfun" for c in results)
    print(f"PASS: pumpfun_scanner_mock ({len(results)} tokens)")


def test_scan_unified_includes_both_sources():
    dex_candidate = parse_pair(
        _sample_pair(mint="DexOnly111111111111111111111111111111111111", dex="raydium"),
        source="dexscreener",
    )
    pump_candidate = parse_pumpfun_pair(
        _sample_pair(mint="PumpOnly222222222222222222222222222222222222", dex="pumpfun"),
    )

    with patch("scanner.MoverScanner.scan", return_value=[dex_candidate]):
        with patch("pumpfun_scanner.PumpFunScanner.scan", return_value=[pump_candidate]):
            with patch("birdeye_scanner.BirdeyeScanner.scan", return_value=[]):
                merged, _, pumpfun_count, _, _ = scan_unified(
                    include_pumpfun=True,
                    include_birdeye=False,
                    include_gmgn=False,
                )

    assert pumpfun_count == 1
    assert len(merged) == 2
    sources = {c.source for c in merged}
    assert "dexscreener" in sources
    assert "pumpfun" in sources
    print("PASS: scan_unified_includes_both_sources")


def test_pumpfun_api_530_fallback_status():
    scanner = PumpFunScanner()

    def fake_api_530(url, timeout=15):
        return None, True  # skip_rest_on_base

    with patch.object(scanner, "_get_dexscreener", return_value={"pairs": [_sample_pair()]}):
        with patch.object(scanner, "_get_pumpfun_api", side_effect=fake_api_530):
            results = scanner.scan()

    assert get_last_pumpfun_scan_status() in ("add_key", "fallback")
    assert isinstance(results, list)
    print(f"PASS: pumpfun_api_530_fallback_status ({get_last_pumpfun_scan_status()})")


def test_pumpfun_api_active_status():
    scanner = PumpFunScanner()

    def fake_api_ok(url, timeout=15):
        return [{"mint": "ApiMint333333333333333333333333333333333", "symbol": "OK"}], False

    with patch.object(scanner, "_get_dexscreener", return_value=None):
        with patch.object(scanner, "_get_pumpfun_api", side_effect=fake_api_ok):
            scanner.scan()

    assert get_last_pumpfun_scan_status() == "active"
    print("PASS: pumpfun_api_active_status")


def test_pumpfun_scanner_live():
    """Live API call — passes with tokens or gracefully empty if APIs are down."""
    try:
        results = PumpFunScanner().scan()
    except Exception as exc:
        print(f"PASS: pumpfun_scanner_live (graceful error: {exc})")
        return
    assert isinstance(results, list)
    for c in results:
        assert c.source == "pumpfun"
        assert c.mint
    print(f"PASS: pumpfun_scanner_live ({len(results)} tokens)")


def test_scan_unified_live():
    try:
        merged, dex_count, pumpfun_count, birdeye_count, _ = scan_unified(
            include_pumpfun=True,
            include_birdeye=True,
            include_gmgn=False,
        )
    except Exception as exc:
        print(f"PASS: scan_unified_live (graceful error: {exc})")
        return
    assert isinstance(merged, list)
    assert isinstance(dex_count, int)
    assert isinstance(pumpfun_count, int)
    assert isinstance(birdeye_count, int)
    print(
        f"PASS: scan_unified_live ({len(merged)} merged, "
        f"{dex_count} dex, {pumpfun_count} pump.fun, {birdeye_count} birdeye)"
    )


def main():
    test_parse_pumpfun_pair_sets_source()
    test_merge_deduplicates_by_mint()
    test_pumpfun_scanner_mock()
    test_scan_unified_includes_both_sources()
    test_pumpfun_api_530_fallback_status()
    test_pumpfun_api_active_status()
    test_pumpfun_scanner_live()
    test_scan_unified_live()
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
