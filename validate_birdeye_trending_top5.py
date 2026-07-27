"""Validate Birdeye Find Gems trending top-5 always-attempt path."""
from unittest.mock import MagicMock, patch

from birdeye_scanner import (
    BIRDEYE_TRENDING_TOP5_SOURCE,
    BirdeyeScanner,
    TRENDING_PATH,
    parse_birdeye_trending_top5_token,
)
from config import Config
from entry_filters import entry_winrate_skip_reason, win_lean_reason
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType


def _top5_candidate(
    *,
    symbol: str = "TREND",
    mint: str = "TrendMint11111111111111111111111111111111",
    momentum_pct: float = 5.0,
    liquidity_usd: float = 50000.0,
) -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="",
        dex="birdeye",
        price_usd=0.001,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=100000.0,
        momentum_pct=momentum_pct,
        price_change_5m=1.0,
        price_change_1h=5.0,
        price_change_6h=5.0,
        price_change_24h=10.0,
        source=BIRDEYE_TRENDING_TOP5_SOURCE,
    )


def test_parse_trending_top5_liquidity_only():
    token = {
        "address": "Top5Mint111111111111111111111111111111111",
        "symbol": "TOP5",
        "name": "Top5",
        "liquidity": 50000,
        "volume24hUSD": 1,  # below normal birdeye volume floor
        "price": 0.01,
        "price24hChangePercent": 12.0,
        "rank": 0,
    }
    with patch.object(Config, "BIRDEYE_MIN_LIQUIDITY_USD", 30000.0):
        with patch.object(Config, "MIN_LIQUIDITY_USD", 30000.0):
            cand = parse_birdeye_trending_top5_token(token)
    assert cand is not None
    assert cand.source == BIRDEYE_TRENDING_TOP5_SOURCE
    assert cand.symbol == "TOP5"
    print("PASS: parse_trending_top5_liquidity_only")


def test_parse_trending_top5_rejects_thin_liq():
    token = {
        "address": "ThinMint1111111111111111111111111111111111",
        "symbol": "THIN",
        "liquidity": 100,
        "price": 0.01,
    }
    with patch.object(Config, "BIRDEYE_MIN_LIQUIDITY_USD", 30000.0):
        with patch.object(Config, "MIN_LIQUIDITY_USD", 30000.0):
            cand = parse_birdeye_trending_top5_token(token)
    assert cand is None
    print("PASS: parse_trending_top5_rejects_thin_liq")


def test_win_lean_bypassed_for_trending_top5():
    cand = _top5_candidate()
    learner = MagicMock()
    learner.win_lean_score.return_value = -0.99  # would normally block
    with patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", True):
        assert win_lean_reason(cand, learner) is None
        assert entry_winrate_skip_reason(cand, learner) is None
    # Normal birdeye source still gets win-lean
    normal = _top5_candidate()
    normal.source = "birdeye"
    with patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", True):
        with patch("entry_filters.effective_setup_learning_min_win_lean", return_value=0.0):
            reason = win_lean_reason(normal, learner)
    assert reason is not None and "win-lean" in reason
    print("PASS: win_lean_bypassed_for_trending_top5")


def test_evaluate_entry_trending_top5_bypasses_momentum_floor():
    strategy = MomentumStrategy()
    cand = _top5_candidate(momentum_pct=0.0)
    with patch.object(Config, "SPIKE_TRAP_FILTER_ENABLED", True):
        with patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", True):
            with patch.object(Config, "SOL_TREND_FILTER_ENABLED", False):
                signal = strategy.evaluate_entry(
                    cand,
                    current_price=0.001,
                    momentum=0.0,  # below normal entry floor
                    sol_trend_snapshot={"sol_trend_ok": True},
                    setup_learner=MagicMock(win_lean_score=MagicMock(return_value=-1.0)),
                )
    assert signal == SignalType.BUY
    print("PASS: evaluate_entry_trending_top5_bypasses_momentum_floor")


def test_fetch_trending_top5_uses_rank_endpoint():
    tokens = [
        {
            "address": f"Mint{i}111111111111111111111111111111111111",
            "symbol": f"T{i}",
            "name": f"T{i}",
            "liquidity": 80000,
            "volume24hUSD": 200000,
            "price": 0.01,
            "rank": i,
            "price24hChangePercent": 5.0 + i,
        }
        for i in range(5)
    ]
    payload = {"success": True, "data": {"tokens": tokens}}

    with patch.object(Config, "BIRDEYE_API_KEY", "test-key"):
        with patch.object(Config, "BIRDEYE_TRENDING_TOP5_LIMIT", 5):
            with patch.object(Config, "BIRDEYE_MIN_LIQUIDITY_USD", 30000.0):
                with patch.object(Config, "MIN_LIQUIDITY_USD", 30000.0):
                    scanner = BirdeyeScanner()
                    with patch.object(scanner, "_get_birdeye", return_value=payload) as mock_get:
                        result = scanner.fetch_trending_top5()
                    mock_get.assert_called()
                    args, kwargs = mock_get.call_args
                    assert args[0] == TRENDING_PATH
                    assert kwargs["params"]["sort_by"] == "rank"
                    assert kwargs["params"]["sort_type"] == "asc"
                    assert kwargs["params"]["limit"] == 5
                    assert len(result) == 5
                    assert [c.symbol for c in result] == ["T0", "T1", "T2", "T3", "T4"]
                    assert all(c.source == BIRDEYE_TRENDING_TOP5_SOURCE for c in result)
    print("PASS: fetch_trending_top5_uses_rank_endpoint")


def test_fetch_trending_top5_401_uses_dex_fallback():
    with patch.object(Config, "BIRDEYE_API_KEY", "bad-key"):
        scanner = BirdeyeScanner()

        def _fake_get(path, params=None, timeout=15):
            # Simulate auth failure path used by _get_birdeye
            from birdeye_scanner import _set_birdeye_auth_status

            _set_birdeye_auth_status("401")
            return None

        with patch.object(scanner, "_get_birdeye", side_effect=_fake_get):
            with patch.object(
                scanner,
                "_trending_top5_from_dexscreener",
                return_value=[_top5_candidate()],
            ) as mock_fb:
                result = scanner.fetch_trending_top5()
        assert len(result) == 1
        mock_fb.assert_called_once()
        from birdeye_scanner import get_trending_top5_status

        status = get_trending_top5_status()
        assert status.get("source") == "dexscreener_fallback"
        assert status.get("auth") == "401"
    print("PASS: fetch_trending_top5_401_uses_dex_fallback")


if __name__ == "__main__":
    test_parse_trending_top5_liquidity_only()
    test_parse_trending_top5_rejects_thin_liq()
    test_win_lean_bypassed_for_trending_top5()
    test_evaluate_entry_trending_top5_bypasses_momentum_floor()
    test_fetch_trending_top5_uses_rank_endpoint()
    test_fetch_trending_top5_401_uses_dex_fallback()
    print("All trending top5 tests passed.")
