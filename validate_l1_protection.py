"""Validate L1 protection band after first ladder partial."""

import time
from unittest.mock import patch

from config import Config, DEFAULT_STOP_LOSS_PCT, DEFAULT_TAKE_PROFIT_LEVELS
from fee_estimator import compute_take_profit_levels, get_fee_budget
from strategy import MomentumStrategy, Position, SignalType


def _make_position(entry_price: float = 1.0, token_raw: int = 10000) -> Position:
    size_sol = 0.05
    return Position(
        mint="TestMint",
        symbol="TEST",
        entry_price=entry_price,
        entry_time=time.time(),
        size_sol=size_sol,
        token_amount_raw=token_raw,
        initial_token_amount_raw=token_raw,
        remaining_token_amount_raw=token_raw,
        tp_levels=compute_take_profit_levels(size_sol),
        tp_portions=list(Config.TAKE_PROFIT_PORTIONS),
        target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
        fee_budget_sol=get_fee_budget(size_sol),
    )


def test_ladder_levels_unchanged():
    assert Config.TAKE_PROFIT_LEVELS == DEFAULT_TAKE_PROFIT_LEVELS == [0.03, 0.04]
    print("PASS: ladder levels are [+3%, +4%]")


def test_l1_partial_arms_protection():
    strategy = MomentumStrategy()
    pos = _make_position()
    assert not pos.l1_protection_armed
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    assert pos.l1_protection_armed is True
    assert pos.remaining_token_amount_raw == 5000
    print("PASS: L1 partial arms protection on remainder")


def test_protection_fires_at_entry():
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    signal = strategy.evaluate_exit(pos, current_price=1.0)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_L1_PROTECTION
    print("PASS: protection fires at 0% (within +0.10% floor)")


def test_protection_fires_at_plus_10pct():
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    signal = strategy.evaluate_exit(pos, current_price=1.001)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_L1_PROTECTION
    print("PASS: protection fires at +0.10%")


def test_protection_fires_on_small_drawdown():
    """Between -0.10% and stop loss — protection exits before SL."""
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    signal = strategy.evaluate_exit(pos, current_price=0.995)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_L1_PROTECTION
    print("PASS: protection fires at -0.50% (before -1.5% SL)")


def test_stop_loss_priority_at_full_sl():
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    with patch.object(Config, "STOP_LOSS_PCT", DEFAULT_STOP_LOSS_PCT):
        signal = strategy.evaluate_exit(pos, current_price=0.978)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: full stop loss at -2% after L1 partial")


def test_hold_above_protection_floor():
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    assert strategy.evaluate_exit(pos, current_price=1.035) is None
    print("PASS: hold at +3.5% above protection floor")


def test_protection_disabled():
    original = Config.ENABLE_L1_PROTECTION
    Config.ENABLE_L1_PROTECTION = False
    try:
        strategy = MomentumStrategy()
        pos = _make_position()
        strategy.apply_partial_tp(pos, 0, 5000, 1.03)
        assert not pos.l1_protection_armed
        assert strategy.evaluate_exit(pos, current_price=1.0) is None
    finally:
        Config.ENABLE_L1_PROTECTION = original
    print("PASS: protection disabled — no arm, no exit at entry")


def test_config_exports_l1_protection():
    cfg = Config.to_dict()
    assert cfg["l1_protection_pct"] == 0.001
    assert cfg["enable_l1_protection"] is True
    summary = Config.strategy_summary()
    assert summary["l1_protection_pct"] == 0.001
    assert summary["enable_l1_protection"] is True
    print("PASS: config exports L1 protection fields")


if __name__ == "__main__":
    test_ladder_levels_unchanged()
    test_l1_partial_arms_protection()
    test_protection_fires_at_entry()
    test_protection_fires_at_plus_10pct()
    test_protection_fires_on_small_drawdown()
    test_stop_loss_priority_at_full_sl()
    test_hold_above_protection_floor()
    test_protection_disabled()
    test_config_exports_l1_protection()
    print("\nAll L1 protection tests passed.")
