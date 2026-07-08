"""Validate instant +3.25% / +5% full profit exits (no ladder partials)."""

import time
from unittest.mock import patch

from config import Config
from fee_estimator import get_fee_budget
from strategy import MomentumStrategy, Position, SignalType


def _make_position(
    entry_price: float = 1.0,
    token_raw: int = 10000,
    size_sol: float = 0.05,
    tp_levels_hit=None,
) -> Position:
    return Position(
        mint="TestMint",
        symbol="TEST",
        entry_price=entry_price,
        entry_time=time.time() - 120,
        size_sol=size_sol,
        token_amount_raw=token_raw,
        initial_token_amount_raw=token_raw,
        remaining_token_amount_raw=token_raw,
        tp_levels=[],
        tp_portions=[],
        target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
        fee_budget_sol=get_fee_budget(size_sol),
        momentum_at_entry=0.05,
        tp_levels_hit=list(tp_levels_hit or []),
    )


def test_instant_exit_at_325pct():
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.10)
    current_price = 1.0326  # slightly above +3.25% to avoid float edge

    assert pos.pnl_pct(current_price) >= Config.INSTANT_EXIT_3PCT - 1e-9
    signal = strategy.evaluate_exit(pos, current_price)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: +3.25% pnl -> instant full exit")


def test_instant_exit_at_5pct():
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.10)
    current_price = 1.05

    signal = strategy.evaluate_exit(pos, current_price)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: +5% pnl -> instant full exit")


def test_peak_pnl_triggers_instant_exit():
    """Peak was +5% between polls; current dipped to +3% — still sell."""
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.peak_pnl_pct = 0.05
    current_price = 1.03

    signal = strategy.evaluate_exit(pos, current_price)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: peak +5% / current +3% -> instant full exit via peak tracking")


def test_quote_pnl_triggers_when_mark_lags():
    """Mark shows +2% but Jupiter sell quote net is +6% — instant must fire."""
    strategy = MomentumStrategy()
    pos = _make_position()
    current_price = 1.02  # +2% mark — below 3.25%

    signal = strategy.evaluate_exit(
        pos, current_price, executable_pnl_pct=0.06
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    assert pos.peak_pnl_pct >= 0.06
    print("PASS: quote +6% triggers instant exit when mark lags at +2%")


def test_quote_pnl_triggers_325_when_mark_lags():
    """Mark flat; executable quote at +3.5% triggers +3.25% tier."""
    strategy = MomentumStrategy()
    pos = _make_position()
    current_price = 1.01

    signal = strategy.evaluate_exit(
        pos, current_price, executable_pnl_pct=0.035
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: quote +3.5% triggers +3.25% instant when mark lags")


def test_no_instant_exit_below_325pct():
    strategy = MomentumStrategy()
    pos = _make_position()
    current_price = 1.03  # +3%, below 3.25%

    assert pos.pnl_pct(current_price) < Config.INSTANT_EXIT_3PCT
    with patch.object(Config, "MIN_NET_WIN_SOL", 0.0):
        signal = strategy.evaluate_exit(pos, current_price)
    assert signal is None
    print("PASS: +3% -> hold (below instant 3.25%)")


def test_between_325_and_5_instant_fires():
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.10)
    current_price = 1.04  # +4%

    with patch.object(Config, "MIN_NET_WIN_SOL", 0.0):
        signal = strategy.evaluate_exit(pos, current_price)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    assert signal.signal_type != SignalType.SELL_TP_PARTIAL
    print("PASS: +4% -> instant full exit (not ladder partial)")


def test_instant_exit_disabled():
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.10)
    current_price = 1.06

    original = Config.INSTANT_PROFIT_EXIT_ENABLED
    try:
        Config.INSTANT_PROFIT_EXIT_ENABLED = False
        with patch.object(Config, "MIN_NET_WIN_SOL", 0.0):
            signal = strategy.evaluate_exit(pos, current_price)
        assert signal is None or signal.signal_type != SignalType.SELL_INSTANT_PROFIT
    finally:
        Config.INSTANT_PROFIT_EXIT_ENABLED = original
    print("PASS: instant exit disabled -> no instant sell at +6%")


def test_stop_loss_before_instant():
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.10)
    current_price = 0.98

    with patch.object(Config, "STOP_LOSS_PCT", 0.02):
        signal = strategy.evaluate_exit(pos, current_price)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: stop-loss checked before instant exit")


def test_five_pct_checked_before_three_pct():
    """At +5%, instant full exit fires (higher target path)."""
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.10)
    current_price = 1.05

    signal = strategy.evaluate_exit(pos, current_price)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: +5% instant exit")


def test_config_defaults():
    assert Config.INSTANT_EXIT_3PCT == 0.0325
    assert Config.INSTANT_PROFIT_EXIT_PCT == 0.05
    assert Config.INSTANT_PROFIT_EXIT_ENABLED is True
    assert Config.TAKE_PROFIT_LEVELS == []
    cfg = Config.to_dict()
    assert cfg["instant_exit_3pct"] == 0.0325
    assert cfg["instant_profit_exit_pct"] == 0.05
    assert cfg["take_profit_levels"] == []
    print("PASS: config defaults for instant profit exit")


def test_no_ladder_partial_exit():
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.10)
    current_price = 1.04

    with patch.object(Config, "MIN_NET_WIN_SOL", 0.0):
        signal = strategy.evaluate_exit(pos, current_price)
    assert signal is not None
    assert signal.signal_type != SignalType.SELL_TP_PARTIAL
    print("PASS: no ladder partial exits")


def test_exit_impact_never_defers():
    from risk import RiskManager

    defer, counts, forced = RiskManager.should_defer_exit_for_impact(
        "mint",
        "SYM",
        5.0,
        is_stop_loss=False,
        defer_counts={},
        signal_name="sell_instant_5pct",
    )
    assert defer is False
    assert counts == {}
    print("PASS: high exit impact does not defer sells")


def main():
    test_config_defaults()
    test_instant_exit_at_325pct()
    test_instant_exit_at_5pct()
    test_peak_pnl_triggers_instant_exit()
    test_quote_pnl_triggers_when_mark_lags()
    test_quote_pnl_triggers_325_when_mark_lags()
    test_no_instant_exit_below_325pct()
    test_between_325_and_5_instant_fires()
    test_instant_exit_disabled()
    test_stop_loss_before_instant()
    test_five_pct_checked_before_three_pct()
    test_no_ladder_partial_exit()
    test_exit_impact_never_defers()
    print("\nAll instant profit exit tests passed.")


if __name__ == "__main__":
    main()
