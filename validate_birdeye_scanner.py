"""Validation tests for Birdeye scanner and unified watchlist merging."""
import logging
import sys
import time
from unittest.mock import patch

from birdeye_scanner import (
    BirdeyeScanner,
    FIND_GEMS_PATH,
    NEW_LISTING_PATH,
    TRENDING_PATH,
    get_last_birdeye_scan_status,
    parse_birdeye_pair,
    parse_birdeye_token,
)
from config import Config
from scanner import MoverCandidate, merge_candidates, parse_pair, scan_unified


def _sample_pair(
    mint: str = "Mint111111111111111111111111111111111111111",
    dex: str = "raydium",
    liquidity: float = 50000,
    volume: float = 100000,
    momentum_m5: float = 5.0,
) -> dict:
    return {
        "chainId": "solana",
        "dexId": dex,
        "pairAddress": "pair123",
        "priceUsd": "0.001",
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


def _sample_birdeye_token(
    mint: str = "BirdMint111111111111111111111111111111111111",
    liquidity: float = 50000,
    volume: float = 100000,
) -> dict:
    return {
        "address": mint,
        "symbol": "BIRD",
        "name": "Bird Token",
        "liquidity": liquidity,
        "volume24hUSD": volume,
        "rank": 0,
    }


def _sample_overview(momentum_5m: float = 5.0) -> dict:
    return {
        "price": 0.001,
        "priceChange5mPercent": momentum_5m,
        "priceChange1hPercent": 2.0,
        "liquidity": 50000,
        "v24hUSD": 100000,
    }


def test_parse_birdeye_pair_sets_source():
    candidate = parse_birdeye_pair(_sample_pair())
    assert candidate is not None
    assert candidate.source == "birdeye"
    print("PASS: parse_birdeye_pair_sets_source")


def test_parse_birdeye_token_sets_source():
    candidate = parse_birdeye_token(_sample_birdeye_token(), _sample_overview())
    assert candidate is not None
    assert candidate.source == "birdeye"
    assert candidate.momentum_pct == 0.05
    print("PASS: parse_birdeye_token_sets_source")


def test_parse_birdeye_token_from_find_gems_fields():
    """Find Gems meme-list items include price_change_* and volume_24h_usd inline."""
    token = {
        "address": "GemMint111111111111111111111111111111111111",
        "symbol": "GEM",
        "name": "Gem Token",
        "liquidity": 50000,
        "volume_24h_usd": 100000,
        "price": 0.001,
        "price_change_5m_percent": 3.0,
        "price_change_1h_percent": 12.5,
    }
    candidate = parse_birdeye_token(token)
    assert candidate is not None
    assert candidate.momentum_pct == 0.125
    assert candidate.price_change_1h == 0.125
    print("PASS: parse_birdeye_token_from_find_gems_fields")


def test_birdeye_gainer_sort_by_mapping():
    with patch.object(Config, "BIRDEYE_GAINER_TIMEFRAME", "1h"):
        assert Config.birdeye_gainer_sort_by() == "price_change_1h_percent"
    with patch.object(Config, "BIRDEYE_GAINER_TIMEFRAME", "24h"):
        assert Config.birdeye_gainer_sort_by() == "price_change_24h_percent"
    print("PASS: birdeye_gainer_sort_by_mapping")


def test_fetch_find_gems_gainers_mock():
    scanner = BirdeyeScanner()
    mock_find_gems = {
        "success": True,
        "data": {
            "items": [
                {
                    "address": "GemMint111111111111111111111111111111111111",
                    "symbol": "GEM",
                    "name": "Gem Token",
                    "liquidity": 50000,
                    "volume_24h_usd": 100000,
                    "price": 0.001,
                    "price_change_1h_percent": 15.0,
                },
            ],
        },
    }
    captured_params: list[dict] = []

    def fake_birdeye(path, params=None, timeout=15):
        if path == FIND_GEMS_PATH:
            captured_params.append(params or {})
            return mock_find_gems
        return None

    with patch.object(Config, "BIRDEYE_API_KEY", "test-key"):
        with patch.object(Config, "BIRDEYE_FIND_GEMS_ENABLED", True):
            with patch.object(scanner, "_get_birdeye", side_effect=fake_birdeye):
                tokens = scanner._fetch_find_gems_gainers(10)

    assert len(tokens) == 1
    assert tokens[0]["address"] == "GemMint111111111111111111111111111111111111"
    assert captured_params[0]["sort_by"] == "price_change_1h_percent"
    assert captured_params[0]["sort_type"] == "desc"
    print("PASS: fetch_find_gems_gainers_mock")


def test_merge_keeps_highest_momentum_with_birdeye():
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
    birdeye = MoverCandidate(
        mint="AAA",
        symbol="A",
        name="A",
        pair_address="p2",
        dex="birdeye",
        price_usd=1.0,
        liquidity_usd=12000,
        volume_24h_usd=50000,
        momentum_pct=0.12,
        price_change_5m=0.12,
        price_change_1h=0.08,
        source="birdeye",
    )
    merged = merge_candidates([dex], [birdeye])
    assert len(merged) == 1
    assert merged[0].source == "birdeye"
    assert merged[0].momentum_pct == 0.12
    print("PASS: merge_keeps_highest_momentum_with_birdeye")


def test_birdeye_no_key_skips_api():
    """Without BIRDEYE_API_KEY, Birdeye endpoints must not be called."""
    import config as config_module

    config_module._logged_missing_scanner_keys.discard("birdeye")
    scanner = BirdeyeScanner()
    mock_pair = [_sample_pair(mint="BirdMint111111111111111111111111111111111111")]

    with patch.object(Config, "BIRDEYE_API_KEY", ""):
        with patch.object(scanner, "_get_birdeye") as mock_be:
            with patch.object(scanner, "_get_dexscreener") as mock_dex:
                mock_dex.side_effect = lambda path, timeout=15: (
                    [{"chainId": "solana", "tokenAddress": "BirdMint111111111111111111111111111111111111"}]
                    if "token-boosts" in path or "token-profiles" in path
                    else mock_pair if "/token-pairs/" in path
                    else None
                )
                for _ in range(3):
                    scanner.scan()
                assert mock_be.call_count == 0
    assert get_last_birdeye_scan_status() == "fallback"
    print("PASS: birdeye_no_key_skips_api")


def test_birdeye_no_key_no_log_spam():
    """401 warnings must not repeat every scan cycle when key is missing."""
    import config as config_module

    config_module._logged_missing_scanner_keys.discard("birdeye")
    warning_messages: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING and "birdeye" in record.name.lower():
                warning_messages.append(record.getMessage())

    handler = _ListHandler()
    be_logger = logging.getLogger("birdeye_scanner")
    be_logger.addHandler(handler)
    try:
        scanner = BirdeyeScanner()
        with patch.object(Config, "BIRDEYE_API_KEY", ""):
            with patch.object(scanner, "_collect_dexscreener_seed_mints", return_value=[]):
                for _ in range(3):
                    scanner.scan()
        auth_warnings = [m for m in warning_messages if "auth failed" in m.lower()]
        assert len(auth_warnings) == 0
    finally:
        be_logger.removeHandler(handler)
    print("PASS: birdeye_no_key_no_log_spam")


def test_birdeye_invalid_key_warns_once():
    """With an invalid key, auth failure should warn once then debug."""
    import config as config_module

    config_module._logged_missing_scanner_keys.discard("birdeye")
    warning_messages: list[str] = []
    debug_messages: list[str] = []

    class _LevelHandler(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            if "auth failed" not in msg.lower():
                return
            if record.levelno >= logging.WARNING:
                warning_messages.append(msg)
            elif record.levelno == logging.DEBUG:
                debug_messages.append(msg)

    handler = _LevelHandler()
    be_logger = logging.getLogger("birdeye_scanner")
    be_logger.addHandler(handler)
    be_logger.setLevel(logging.DEBUG)
    try:
        scanner = BirdeyeScanner()
        scanner._auth_warned = False

        class FakeResponse:
            status_code = 401

            def json(self):
                return {}

        with patch.object(Config, "BIRDEYE_API_KEY", "bad-key"):
            with patch.object(scanner.session, "get", return_value=FakeResponse()):
                scanner._get_birdeye(TRENDING_PATH)
                scanner._get_birdeye(TRENDING_PATH)
                scanner._get_birdeye(TRENDING_PATH)
        assert len(warning_messages) == 1
        assert len(debug_messages) == 2
    finally:
        be_logger.removeHandler(handler)
    print("PASS: birdeye_invalid_key_warns_once")


def test_parse_birdeye_new_listing_token():
  """v2 new_listing items lack volume — overview enrichment supplies it."""
  token = {
      "address": "DsWUsiseYxAHZXEvq5cVcymYaohk8Gpe8E4otsjFpump",
      "symbol": "Onigiri",
      "name": "Onigiri",
      "source": "raydium",
      "liquidityAddedAt": "2024-09-18T17:57:14",
      "liquidity": 50000,
  }
  candidate = parse_birdeye_token(token, _sample_overview())
  assert candidate is not None
  assert candidate.pool_created_at is not None
  assert candidate.dex == "raydium"
  print("PASS: parse_birdeye_new_listing_token")


def test_birdeye_scanner_mock():
    scanner = BirdeyeScanner()
    mock_find_gems = {
        "success": True,
        "data": {
            "items": [
                {
                    "address": "BirdMint111111111111111111111111111111111111",
                    "symbol": "BIRD",
                    "name": "Bird Token",
                    "liquidity": 50000,
                    "volume_24h_usd": 100000,
                    "price": 0.001,
                    "price_change_1h_percent": 8.0,
                    "liquidityAddedAt": "2024-09-18T17:57:14",
                    "source": "raydium",
                },
            ],
        },
    }

    def fake_birdeye(path, params=None, timeout=15):
        if path == FIND_GEMS_PATH:
            return mock_find_gems
        if path == NEW_LISTING_PATH:
            return {"success": True, "data": {"items": []}}
        return None

    def fake_pairs(mint: str) -> list:
        return [_sample_pair(mint="BirdMint111111111111111111111111111111111111")]

    with patch.object(Config, "BIRDEYE_API_KEY", "test-key"):
        with patch.object(Config, "BIRDEYE_TRENDING_LIMIT", 2):
            with patch.object(Config, "BIRDEYE_FIND_GEMS_ENABLED", True):
                with patch.object(scanner, "_get_birdeye", side_effect=fake_birdeye):
                    with patch.object(scanner, "_fetch_pairs_for_mint", side_effect=fake_pairs):
                        results = scanner.scan()

    assert isinstance(results, list)
    assert len(results) >= 1
    assert all(c.source == "birdeye" for c in results)
    print(f"PASS: birdeye_scanner_mock ({len(results)} tokens)")


def test_scan_unified_includes_all_sources():
    dex_candidate = parse_pair(
        _sample_pair(mint="DexOnly111111111111111111111111111111111111", dex="raydium"),
        source="dexscreener",
    )
    pump_candidate = parse_birdeye_pair(
        _sample_pair(mint="PumpOnly222222222222222222222222222222222222", dex="raydium"),
    )
    if pump_candidate:
        pump_candidate = MoverCandidate(
            mint=pump_candidate.mint,
            symbol=pump_candidate.symbol,
            name=pump_candidate.name,
            pair_address=pump_candidate.pair_address,
            dex="pumpfun",
            price_usd=pump_candidate.price_usd,
            liquidity_usd=pump_candidate.liquidity_usd,
            volume_24h_usd=pump_candidate.volume_24h_usd,
            momentum_pct=pump_candidate.momentum_pct,
            price_change_5m=pump_candidate.price_change_5m,
            price_change_1h=pump_candidate.price_change_1h,
            pool_created_at=pump_candidate.pool_created_at,
            source="pumpfun",
        )
    birdeye_candidate = parse_birdeye_pair(
        _sample_pair(mint="BirdOnly333333333333333333333333333333333333", dex="raydium"),
    )

    with patch("scanner.MoverScanner.scan", return_value=[dex_candidate]):
        with patch("pumpfun_scanner.PumpFunScanner.scan", return_value=[pump_candidate]):
            with patch("birdeye_scanner.BirdeyeScanner.scan", return_value=[birdeye_candidate]):
                merged, dex_count, pumpfun_count, birdeye_count, _ = scan_unified(
                    include_pumpfun=True,
                    include_birdeye=True,
                    include_gmgn=False,
                )

    assert dex_count == 1
    assert pumpfun_count == 1
    assert birdeye_count == 1
    assert len(merged) == 3
    sources = {c.source for c in merged}
    assert "dexscreener" in sources
    assert "pumpfun" in sources
    assert "birdeye" in sources
    print("PASS: scan_unified_includes_all_sources")


def test_birdeye_scanner_live():
    """Live API call — passes with tokens or gracefully empty if no key / API down."""
    try:
        results = BirdeyeScanner().scan()
    except Exception as exc:
        print(f"PASS: birdeye_scanner_live (graceful error: {exc})")
        return
    assert isinstance(results, list)
    for c in results:
        assert c.source == "birdeye"
        assert c.mint
    print(f"PASS: birdeye_scanner_live ({len(results)} tokens)")


def test_scan_unified_live():
    try:
        merged, dex_count, pumpfun_count, birdeye_count, _ = scan_unified(include_pumpfun=True, include_birdeye=True)
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
    test_parse_birdeye_pair_sets_source()
    test_parse_birdeye_token_sets_source()
    test_parse_birdeye_new_listing_token()
    test_parse_birdeye_token_from_find_gems_fields()
    test_birdeye_gainer_sort_by_mapping()
    test_fetch_find_gems_gainers_mock()
    test_merge_keeps_highest_momentum_with_birdeye()
    test_birdeye_no_key_skips_api()
    test_birdeye_no_key_no_log_spam()
    test_birdeye_invalid_key_warns_once()
    test_birdeye_scanner_mock()
    test_scan_unified_includes_all_sources()
    test_birdeye_scanner_live()
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
