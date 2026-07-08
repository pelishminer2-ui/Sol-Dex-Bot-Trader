"""Validate stop-loss never-miss: mark/quote/trough lag, priority over max hold."""
import time
from contextlib import contextmanager
from unittest.mock import patch

from config import Config, DEFAULT_WATCHLIST_MINT, effective_stop_loss_pct
from fee_estimator import compute_take_profit_levels, get_fee_budget
from strategy import MomentumStrategy, Position, SignalType


@contextmanager
def _without_instant_exit():
    original = Config.INSTANT_PROFIT_EXIT_ENABLED
    Config.INSTANT_PROFIT_EXIT_ENABLED = False
    try:
        yield
    finally:
        Config.INSTANT_PROFIT_EXIT_ENABLED = original


def _make_position(
    *,
    mint: str = "TestMint",
    entry_price: float = 1.0,
    token_raw: int = 10000,
    size_sol: float = 0.05,
    trough_pnl_pct: float = 0.0,
) -> Position:
    tp_levels = compute_take_profit_levels(size_sol)
    return Position(
        mint=mint,
        symbol="TEST",
        entry_price=entry_price,
        entry_time=time.time(),
        size_sol=size_sol,
        token_amount_raw=token_raw,
        initial_token_amount_raw=token_raw,
        remaining_token_amount_raw=token_raw,
        tp_levels=tp_levels,
        tp_portions=list(Config.TAKE_PROFIT_PORTIONS),
        target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
        fee_budget_sol=get_fee_budget(size_sol),
        profile={"liquidity_usd": 50000.0, "momentum_pct": 0.01},
        trough_pnl_pct=trough_pnl_pct,
    )


def test_config_stop_loss_never_miss_default():
    assert Config.STOP_LOSS_NEVER_MISS is True
    assert Config.LOSS_FRESH_QUOTE_PCT == 0.01
    print("PASS: STOP_LOSS_NEVER_MISS defaults true")


def test_stop_at_configured_threshold_not_emergency():
    strategy = MomentumStrategy()
    pos = _make_position()
    signal = strategy._evaluate_stop_loss(
        pos, mark_pnl=-0.015, executable_pnl_pct=-0.005
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    # exactly at 1.5% — not waiting for 3% emergency
    signal2 = strategy._evaluate_stop_loss(pos, mark_pnl=-0.014)
    assert signal2 is None
    print("PASS: stop fires at configured 1.5%, not emergency 3%")


def test_mark_lag_quote_triggers_stop():
    strategy = MomentumStrategy()
    pos = _make_position()
    signal = strategy._evaluate_stop_loss(
        pos,
        mark_pnl=-0.005,
        executable_pnl_pct=-0.016,
        trough_pnl=0.0,
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: quote lag triggers stop when mark looks safe")


def test_trough_triggers_stop_when_mark_and_quote_stale():
    strategy = MomentumStrategy()
    pos = _make_position(trough_pnl_pct=-0.02)
    signal = strategy._evaluate_stop_loss(
        pos,
        mark_pnl=0.0,
        executable_pnl_pct=0.0,
        trough_pnl=pos.trough_pnl_pct,
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: trough triggers stop when mark and quote stale")


def test_stop_before_max_hold_on_red_trough():
    strategy = MomentumStrategy()
    pos = _make_position(trough_pnl_pct=-0.02)
    pos.entry_time = 0
    hold_sec = Config.MAX_HOLD_MINUTES_NON_WBTC * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=hold_sec):
            signal = strategy.evaluate_exit(
                pos,
                current_price=1.002,
                executable_pnl_pct=0.001,
            )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: stop loss before max hold on red trough")


def test_stop_before_instant_on_red():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.peak_pnl_pct = 0.06
    signal = strategy.evaluate_exit(pos, current_price=0.98)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: stop loss before instant profit on red position")


def test_wbtc_stop_at_two_percent():
    strategy = MomentumStrategy()
    pos = _make_position(mint=DEFAULT_WATCHLIST_MINT)
    stop = effective_stop_loss_pct(DEFAULT_WATCHLIST_MINT)
    assert stop == Config.WBTC_STOP_LOSS_PCT
    signal = strategy._evaluate_stop_loss(pos, mark_pnl=-stop)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    signal2 = strategy._evaluate_stop_loss(pos, mark_pnl=-(stop - 0.001))
    assert signal2 is None
    print("PASS: WBTC stop at 2%")


def main():
    test_config_stop_loss_never_miss_default()
    test_stop_at_configured_threshold_not_emergency()
    test_mark_lag_quote_triggers_stop()
    test_trough_triggers_stop_when_mark_and_quote_stale()
    test_stop_before_max_hold_on_red_trough()
    test_stop_before_instant_on_red()
    test_wbtc_stop_at_two_percent()
    print("\nAll stop-loss never-miss tests passed.")


if __name__ == "__main__":
    main()
