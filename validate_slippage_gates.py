"""Validate tightened slippage / price-impact gates."""

from unittest.mock import patch

from config import (
    BEST_WIN_PRESET,
    DEFAULT_MAX_ABSOLUTE_PRICE_IMPACT_PCT,
    DEFAULT_MAX_ENTRY_PRICE_IMPACT_PCT,
    DEFAULT_MAX_EXIT_PRICE_IMPACT_PCT,
    DEFAULT_MAX_ROUND_TRIP_IMPACT_PCT,
    DEFAULT_PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT,
    Config,
)
from risk import (
    RiskManager,
    SKIP_REASON_PREFIX,
    is_pumpfun_amm_route,
    round_trip_impact_pct,
)


def test_config_slippage_defaults():
    assert DEFAULT_MAX_ENTRY_PRICE_IMPACT_PCT == 1.0
    assert DEFAULT_MAX_EXIT_PRICE_IMPACT_PCT == 1.5
    assert DEFAULT_MAX_ROUND_TRIP_IMPACT_PCT == 2.0
    assert DEFAULT_MAX_ABSOLUTE_PRICE_IMPACT_PCT == 15.0
    assert DEFAULT_PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT == 0.75
    assert Config.MAX_ENTRY_PRICE_IMPACT_PCT == 1.0
    assert Config.MAX_EXIT_PRICE_IMPACT_PCT == 1.5
    assert Config.MAX_ROUND_TRIP_IMPACT_PCT == 2.0
    print("PASS: slippage config defaults")


def test_best_win_preset_entry_impact():
    assert BEST_WIN_PRESET["max_entry_price_impact_pct"] == 1.0
    print("PASS: BEST_WIN_PRESET entry impact")


def test_entry_blocked_high_buy_impact():
    risk = RiskManager()
    ok, reason = risk.check_entry_eligibility(0.10, 50000, 1.25)
    assert not ok
    assert SKIP_REASON_PREFIX in reason
    assert "buy price impact" in reason
    print(f"PASS: high buy impact blocked — {reason}")


def test_entry_blocked_high_sell_preview_impact():
    risk = RiskManager()
    ok, reason = risk.check_entry_eligibility(
        0.10,
        50000,
        0.2,
        sell_preview_impact_pct=1.4,
    )
    assert not ok
    assert "sell-preview price impact" in reason
    print(f"PASS: high sell-preview impact blocked — {reason}")


def test_entry_blocked_round_trip_impact():
    risk = RiskManager()
    with patch.object(Config, "MAX_ROUND_TRIP_IMPACT_PCT", 1.5):
        ok, reason = risk.check_entry_eligibility(
            0.10,
            50000,
            0.8,
            sell_preview_impact_pct=0.8,
        )
    assert not ok
    assert "round-trip impact" in reason
    assert round_trip_impact_pct(0.8, 0.8) == 1.6
    print(f"PASS: round-trip impact blocked — {reason}")


def test_entry_blocked_pumpfun_amm_sell_preview():
    risk = RiskManager()
    ok, reason = risk.check_entry_eligibility(
        0.10,
        50000,
        0.1,
        sell_preview_impact_pct=1.0,
        route_labels_sell=["Pump.fun Amm"],
    )
    assert not ok
    assert "Pump.fun Amm" in reason
    assert is_pumpfun_amm_route(["Pump.fun Amm"])
    print(f"PASS: Pump.fun Amm sell-preview blocked — {reason}")


def test_entry_pre_trade_uses_entry_threshold():
    risk = RiskManager()
    ok, reason = risk.pre_trade_check(
        1.0,
        1.25,
        dry_run=True,
        max_impact_pct=Config.effective_max_entry_price_impact_pct(),
    )
    assert not ok
    assert "price impact" in reason
    print("PASS: entry pre_trade_check uses entry threshold")


def test_exit_impact_gate_never_defers():
    counts: dict[str, int] = {}
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
    print("PASS: exit impact gate never defers (logs high slippage only)")


def test_exit_impact_gate_executes_stop_loss_immediately():
    counts = {"mint123456789": Config.EXIT_IMPACT_FORCE_RETRIES}
    defer, counts, forced = RiskManager.should_defer_exit_for_impact(
        "mint123456789",
        "TEST",
        10.0,
        is_stop_loss=True,
        defer_counts=counts,
        signal_name="sell_stop_loss",
    )
    assert defer is False
    assert forced is True
    assert "mint123456789" not in counts
    print("PASS: exit impact gate executes stop-loss immediately at high impact")


def test_exit_impact_gate_allows_low_impact():
    counts = {"mint123456789": 2}
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


def test_config_api_exposes_slippage_fields():
    cfg = Config.to_dict()
    for key in (
        "max_entry_price_impact_pct",
        "max_exit_price_impact_pct",
        "max_round_trip_impact_pct",
        "max_absolute_price_impact_pct",
        "pumpfun_amm_max_sell_preview_impact_pct",
        "exit_impact_force_retries",
    ):
        assert key in cfg, f"missing {key}"
    assert cfg["max_entry_price_impact_pct"] == 1.0
    assert cfg["max_exit_price_impact_pct"] == 1.5
    print("PASS: Config.to_dict slippage fields")


if __name__ == "__main__":
    test_config_slippage_defaults()
    test_best_win_preset_entry_impact()
    test_entry_blocked_high_buy_impact()
    test_entry_blocked_high_sell_preview_impact()
    test_entry_blocked_round_trip_impact()
    test_entry_blocked_pumpfun_amm_sell_preview()
    test_entry_pre_trade_uses_entry_threshold()
    test_exit_impact_gate_never_defers()
    test_exit_impact_gate_executes_stop_loss_immediately()
    test_exit_impact_gate_allows_low_impact()
    test_config_api_exposes_slippage_fields()
    print("\nAll slippage gate validation tests passed.")
