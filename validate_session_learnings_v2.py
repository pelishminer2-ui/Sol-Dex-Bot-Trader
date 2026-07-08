"""Validate Jul 7 session-learned defaults and exit-side price impact gate."""

from unittest.mock import patch

from config import (
    BEST_WIN_PRESET,
    DEFAULT_EXIT_IMPACT_FORCE_RETRIES,
    DEFAULT_GMGN_MIN_LIQUIDITY_USD,
    DEFAULT_LOSS_REENTRY_COOLDOWN_MINUTES,
    DEFAULT_LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES,
    DEFAULT_MAX_CONSECUTIVE_LOSSES,
    DEFAULT_MAX_ENTRY_PRICE_IMPACT_PCT,
    DEFAULT_MAX_EXIT_PRICE_IMPACT_PCT,
    DEFAULT_MAX_ROUND_TRIP_IMPACT_PCT,
    DEFAULT_MIN_EXPECTED_NET_PROFIT_SOL,
    DEFAULT_MIN_NET_WIN_SOL,
    DEFAULT_MIN_VOLUME_24H_USD,
    DEFAULT_NON_WATCHLIST_MIN_VOLUME_24H_USD,
    DEFAULT_REENTRY_MIN_MOMENTUM_PCT,
    Config,
    instant_profit_exempt_from_min_net_win,
)
from risk import RiskManager


def test_config_defaults_session_learnings():
    assert instant_profit_exempt_from_min_net_win("anymint") is False
    assert DEFAULT_REENTRY_MIN_MOMENTUM_PCT == 0.015
    assert DEFAULT_LOSS_REENTRY_COOLDOWN_MINUTES == 120
    assert DEFAULT_LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES == 240
    assert DEFAULT_MAX_ENTRY_PRICE_IMPACT_PCT == 1.0
    assert DEFAULT_MAX_ROUND_TRIP_IMPACT_PCT == 2.0
    assert DEFAULT_GMGN_MIN_LIQUIDITY_USD == 25000
    assert DEFAULT_MIN_VOLUME_24H_USD == 75000
    assert DEFAULT_NON_WATCHLIST_MIN_VOLUME_24H_USD == 75000
    assert DEFAULT_MIN_NET_WIN_SOL == 0.003
    assert DEFAULT_MIN_EXPECTED_NET_PROFIT_SOL == 0.003
    assert DEFAULT_MAX_CONSECUTIVE_LOSSES == 3
    assert DEFAULT_MAX_EXIT_PRICE_IMPACT_PCT == 1.5
    assert DEFAULT_EXIT_IMPACT_FORCE_RETRIES == 5
    print("PASS: session-learned config defaults")


def test_best_win_preset_session_learnings():
    assert BEST_WIN_PRESET["max_entry_price_impact_pct"] == 1.0
    assert BEST_WIN_PRESET["reentry_min_momentum_pct"] == 0.015
    assert BEST_WIN_PRESET["loss_reentry_cooldown_minutes"] == 120
    assert BEST_WIN_PRESET["loss_reentry_repeat_cooldown_minutes"] == 240
    assert BEST_WIN_PRESET["min_volume_24h_usd"] == 75000.0
    assert BEST_WIN_PRESET["min_net_win_sol"] == 0.003
    print("PASS: BEST_WIN_PRESET session-learned values")


def test_exit_impact_gate_never_defers():
    counts: dict[str, int] = {}
    with patch.object(Config, "MAX_EXIT_PRICE_IMPACT_PCT", 1.5), patch.object(
        Config, "EXIT_IMPACT_FORCE_RETRIES", 5
    ):
        defer, counts, forced = RiskManager.should_defer_exit_for_impact(
            "mint123456789",
            "TEST",
            2.5,
            is_stop_loss=True,
            defer_counts=counts,
            signal_name="sell_stop_loss",
        )
    assert defer is False
    assert counts == {}
    assert forced is True
    print("PASS: exit impact gate never defers high-impact stop-loss")


def test_exit_impact_gate_executes_immediately_after_retries():
    counts = {"mint123456789": 5}
    with patch.object(Config, "MAX_EXIT_PRICE_IMPACT_PCT", 1.5), patch.object(
        Config, "EXIT_IMPACT_FORCE_RETRIES", 5
    ):
        defer, counts, forced = RiskManager.should_defer_exit_for_impact(
            "mint123456789",
            "TEST",
            2.5,
            is_stop_loss=True,
            defer_counts=counts,
            signal_name="sell_stop_loss",
        )
    assert defer is False
    assert forced is True
    assert "mint123456789" not in counts
    print("PASS: exit impact gate executes immediately at high impact")


def test_exit_impact_gate_allows_low_impact():
    counts = {"mint123456789": 2}
    with patch.object(Config, "MAX_EXIT_PRICE_IMPACT_PCT", 1.5):
        defer, counts, forced = RiskManager.should_defer_exit_for_impact(
            "mint123456789",
            "TEST",
            1.0,
            is_stop_loss=True,
            defer_counts=counts,
            signal_name="sell_stop_loss",
        )
    assert defer is False
    assert forced is False
    assert "mint123456789" not in counts
    print("PASS: exit impact gate allows sells at or below threshold")


if __name__ == "__main__":
    test_config_defaults_session_learnings()
    test_best_win_preset_session_learnings()
    test_exit_impact_gate_never_defers()
    test_exit_impact_gate_executes_immediately_after_retries()
    test_exit_impact_gate_allows_low_impact()
    print("\nAll session-learning v2 validation tests passed.")
