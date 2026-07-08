"""Validate SOL macro trend filter and one-strike loss rule."""

from unittest.mock import patch

from config import (
    DEFAULT_LOSS_ONE_STRIKE_PER_SESSION,
    DEFAULT_SOL_MIN_CHANGE_1H_PCT,
    DEFAULT_SOL_MIN_CHANGE_4H_PCT,
    DEFAULT_SOL_TREND_FILTER_ENABLED,
    Config,
)
from scanner import MoverCandidate
from sol_trend_filter import (
    memecoin_entry_allowed_by_sol_trend,
    reset_sol_trend_state_for_tests,
    sol_trend_passes,
)
from strategy import MomentumStrategy


def _candidate(symbol: str = "TEST", mint: str = "mint1234567890123456789012345678901234") -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="test",
        price_usd=1.0,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.05,
    )


def test_config_defaults():
    assert DEFAULT_SOL_TREND_FILTER_ENABLED is True
    assert DEFAULT_SOL_MIN_CHANGE_1H_PCT == -0.5
    assert DEFAULT_SOL_MIN_CHANGE_4H_PCT == -1.5
    assert DEFAULT_LOSS_ONE_STRIKE_PER_SESSION is True
    print("PASS: SOL trend config defaults")


def test_sol_trend_passes_thresholds():
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", True), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -0.5
    ), patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5):
        assert sol_trend_passes(-0.3, -1.0) is True
        assert sol_trend_passes(-0.6, 0.0) is False
        assert sol_trend_passes(0.0, -2.0) is False
    print("PASS: sol_trend_passes thresholds")


def test_memecoin_gate_blocks_dump():
    snap = {"data_available": True, "sol_trend_1h_pct": -1.2, "sol_trend_4h_pct": 0.5}
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", True), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -0.5
    ), patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5):
        allowed, reason = memecoin_entry_allowed_by_sol_trend(snap)
    assert allowed is False
    assert reason and "1h" in reason
    print("PASS: memecoin gate blocks SOL dump")


def test_memecoin_gate_disabled():
    snap = {"data_available": True, "sol_trend_1h_pct": -5.0, "sol_trend_4h_pct": -5.0}
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", False):
        allowed, reason = memecoin_entry_allowed_by_sol_trend(snap)
    assert allowed is True
    assert reason is None
    print("PASS: disabled filter allows entries")


def test_strategy_blocks_sol_macro_on_memecoin():
    strategy = MomentumStrategy()
    candidate = _candidate()
    snap = {"data_available": True, "sol_trend_1h_pct": -2.0, "sol_trend_4h_pct": 0.0}
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", True), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -0.5
    ), patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5):
        signal = strategy.evaluate_entry(
            candidate, 1.0, 0.05, sol_trend_snapshot=snap
        )
        reason = strategy.entry_skip_reason(
            candidate, 0.05, sol_trend_snapshot=snap
        )
    from strategy import SignalType

    assert signal == SignalType.NONE
    assert reason and "SOL macro" in reason
    print("PASS: strategy blocks memecoin when SOL dumping")


def test_one_strike_blocks_repeat_loss_mint():
    strategy = MomentumStrategy()
    mint = "mint1234567890123456789012345678901234"
    with patch.object(Config, "LOSS_ONE_STRIKE_PER_SESSION", True), patch.object(
        Config, "LOSS_REENTRY_COOLDOWN_MINUTES", 0
    ):
        strategy.record_loss_reentry_cooldown(mint)
        assert strategy.is_on_loss_reentry_cooldown(mint) is True
        reason = strategy.entry_skip_reason(_candidate(mint=mint), 0.05)
    assert reason and "one-strike" in reason
    print("PASS: one-strike blocks repeat entry after loss")


def main():
    reset_sol_trend_state_for_tests()
    test_config_defaults()
    test_sol_trend_passes_thresholds()
    test_memecoin_gate_blocks_dump()
    test_memecoin_gate_disabled()
    test_strategy_blocks_sol_macro_on_memecoin()
    test_one_strike_blocks_repeat_loss_mint()
    print("\nAll SOL trend filter validations passed.")


if __name__ == "__main__":
    main()
