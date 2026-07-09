"""Validate hot-market regime detection and adaptive entry gates."""

from unittest.mock import patch

from config import (
    BEST_WIN_PRESET,
    Config,
    DEFAULT_HOT_MARKET_MIN_SCANNER_CANDIDATES,
    DEFAULT_HOT_MARKET_SOL_MIN_1H_PCT,
    STEADY_TRADE_PRESET,
)
from market_regime import (
    REGIME_COLD,
    REGIME_HOT,
    REGIME_NEUTRAL,
    detect_market_regime,
    get_regime_gates,
    reset_market_regime_for_tests,
    update_market_regime,
)
from scanner import MoverCandidate


def _candidate(mint: str = "mint1234567890123456789012345678901234") -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol="TEST",
        name="TEST",
        pair_address="pair",
        dex="test",
        price_usd=1.0,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.05,
    )


def test_hot_regime_detection():
    reset_market_regime_for_tests()
    sol = {"sol_trend_1h_pct": 1.0, "sol_trend_4h_pct": 0.5}
    watchlist = [_candidate(f"mint{i:032d}") for i in range(6)]
    with patch.object(Config, "HOT_MARKET_MODE_ENABLED", True), patch.object(
        Config, "HOT_MARKET_SOL_MIN_1H_PCT", DEFAULT_HOT_MARKET_SOL_MIN_1H_PCT
    ), patch.object(
        Config, "HOT_MARKET_MIN_SCANNER_CANDIDATES",
        DEFAULT_HOT_MARKET_MIN_SCANNER_CANDIDATES,
    ):
        assert detect_market_regime(sol, watchlist) == REGIME_HOT
    print("PASS: hot regime when SOL rising + enough candidates")


def test_cold_regime_on_sol_dump():
    with patch.object(Config, "HOT_MARKET_MODE_ENABLED", True), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -0.5
    ), patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5):
        assert detect_market_regime(
            {"sol_trend_1h_pct": -0.3, "sol_trend_4h_pct": 1.0}, [_candidate()]
        ) == REGIME_NEUTRAL
        assert detect_market_regime(
            {"sol_trend_1h_pct": -0.6, "sol_trend_4h_pct": 1.0}, [_candidate()]
        ) == REGIME_COLD
    print("PASS: cold regime when SOL below macro trend thresholds")


def test_neutral_regime_insufficient_candidates():
    sol = {"sol_trend_1h_pct": 1.0, "sol_trend_4h_pct": 0.5}
    with patch.object(Config, "HOT_MARKET_MODE_ENABLED", True), patch.object(
        Config, "HOT_MARKET_MIN_SCANNER_CANDIDATES", 5
    ):
        assert detect_market_regime(sol, [_candidate()]) == REGIME_NEUTRAL
    print("PASS: neutral when SOL hot but too few scanner candidates")


def test_regime_gates_hot_vs_cold():
    reset_market_regime_for_tests()
    sol_hot = {"sol_trend_1h_pct": 1.0, "sol_trend_4h_pct": 0.5}
    watchlist = [_candidate(f"mint{i:032d}") for i in range(6)]
    with patch.object(Config, "HOT_MARKET_MODE_ENABLED", True), patch.object(
        Config, "STEADY_TRADE_AUTO_ADJUST", True
    ), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -1.5
    ), patch.object(
        Config, "SOL_MIN_CHANGE_4H_PCT", -1.5
    ), patch.object(
        Config, "ENTRY_MOMENTUM_PCT", STEADY_TRADE_PRESET["entry_momentum_pct"]
    ), patch.object(
        Config, "MIN_MOMENTUM_PCT", STEADY_TRADE_PRESET["min_momentum_pct"]
    ), patch.object(
        Config, "MIN_VOLUME_24H_USD", STEADY_TRADE_PRESET["min_volume_24h_usd"]
    ), patch.object(
        Config, "NON_WATCHLIST_MIN_VOLUME_24H_USD",
        STEADY_TRADE_PRESET["non_watchlist_min_volume_24h_usd"],
    ):
        hot_snap = update_market_regime(sol_hot, watchlist)
        hot_gates = hot_snap["regime_gates"]
        assert hot_gates["entry_momentum_pct"] == Config.HOT_MARKET_ENTRY_MOMENTUM_PCT
        assert hot_gates["min_momentum_pct"] == Config.HOT_MARKET_MIN_MOMENTUM_PCT

        # SOL below the -1.5 macro floor -> genuine COLD regime (tightest gates).
        cold_snap = update_market_regime(
            {"sol_trend_1h_pct": -2.0, "sol_trend_4h_pct": -2.0}, watchlist
        )
        cold_gates = cold_snap["regime_gates"]
        assert cold_gates["entry_momentum_pct"] == Config.COLD_MARKET_ENTRY_MOMENTUM_PCT
        assert cold_gates["min_momentum_pct"] == Config.COLD_MARKET_MIN_MOMENTUM_PCT
        # Self-adjust: cold must be strictly TIGHTER than hot.
        assert cold_gates["entry_momentum_pct"] > hot_gates["entry_momentum_pct"]
        assert cold_gates["min_volume_24h_usd"] >= hot_gates["min_volume_24h_usd"]
    print("PASS: regime gates differ hot vs cold (cold tighter)")


def test_effective_entry_momentum_follows_regime():
    reset_market_regime_for_tests()
    with patch.object(Config, "HOT_MARKET_MODE_ENABLED", True):
        update_market_regime(
            {"sol_trend_1h_pct": 1.0, "sol_trend_4h_pct": 0.5},
            [_candidate(f"mint{i:032d}") for i in range(6)],
        )
        assert Config.effective_entry_momentum_pct() == get_regime_gates()["entry_momentum_pct"]
    print("PASS: Config.effective_entry_momentum_pct follows regime")


def test_target_win_rate_hot():
    reset_market_regime_for_tests()
    with patch.object(Config, "HOT_MARKET_MODE_ENABLED", True):
        snap = update_market_regime(
            {"sol_trend_1h_pct": 1.0, "sol_trend_4h_pct": 0.5},
            [_candidate(f"mint{i:032d}") for i in range(6)],
        )
        assert snap["target_win_rate"] == Config.HOT_MARKET_TARGET_WIN_RATE
        assert snap["target_win_rate"] == 0.65
    print("PASS: hot target win rate is 65%")


def test_feasibility_breakeven_math():
    """Plain-text expectancy check: instant +5% vs 1.5% SL with fees."""
    trade_sol = 0.10
    win_gross = trade_sol * 0.05
    loss_gross = trade_sol * 0.015
    fees = 0.003
    win_net = win_gross - fees
    loss_net = loss_gross + fees
    breakeven_wr = loss_net / (win_net + loss_net)
    wr_55_ev = 0.55 * win_net - 0.45 * loss_net
    # Session-observed avg win/loss (6hr paper) for comparison
    session_win_net = 0.00389
    session_loss_net = 0.00630
    session_breakeven = session_loss_net / (session_win_net + session_loss_net)
    assert breakeven_wr > 0.55
    print(
        f"PASS: feasibility math — ideal breakeven WR ~{breakeven_wr*100:.1f}% "
        f"(55% EV {wr_55_ev:+.4f} SOL/trade); session breakeven ~{session_breakeven*100:.1f}%"
    )


if __name__ == "__main__":
    test_hot_regime_detection()
    test_cold_regime_on_sol_dump()
    test_neutral_regime_insufficient_candidates()
    test_regime_gates_hot_vs_cold()
    test_effective_entry_momentum_follows_regime()
    test_target_win_rate_hot()
    test_feasibility_breakeven_math()
    print("\nAll hot-market validation tests passed.")
