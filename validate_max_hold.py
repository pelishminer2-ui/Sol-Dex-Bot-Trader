"""Validate non-WBTC max hold exits (15 min default).

At max hold: stop loss if worst mark/quote/trough hits threshold, else forced
time exit (green -> SELL_MAX_HOLD_PROFIT, red above stop -> SELL_TIME).
"""

import time
from contextlib import contextmanager
from unittest.mock import patch

from config import Config, DEFAULT_WATCHLIST_MINT
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
    )


def _hold_past_max() -> int:
    return Config.MAX_HOLD_MINUTES_NON_WBTC * 60 + 1


def test_config_defaults():
    assert Config.MAX_HOLD_MINUTES_NON_WBTC == 15
    assert Config.MAX_HOLD_ENABLED is True
    print("PASS: max hold config defaults")


def test_non_wbtc_positive_max_hold_profit_exit():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=_hold_past_max()):
            signal = strategy.evaluate_exit(pos, current_price=1.005)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_MAX_HOLD_PROFIT
    assert signal.signal_type != SignalType.SELL_SL
    print("PASS: non-WBTC 15m positive -> max hold profit exit")


def test_non_wbtc_shallow_loss_max_hold_time_exit():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=_hold_past_max()):
            signal = strategy.evaluate_exit(pos, current_price=0.995)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_TIME
    assert signal.signal_type != SignalType.SELL_SL
    print("PASS: non-WBTC 15m shallow loss -> time exit not SL")


def test_non_wbtc_deep_loss_still_stop_loss():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    with patch("strategy.time.time", return_value=_hold_past_max()):
        signal = strategy.evaluate_exit(pos, current_price=0.98)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: non-WBTC 15m deep loss -> stop loss")


def test_positive_mark_bad_trough_triggers_stop_at_max_hold():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    pos.trough_pnl_pct = -0.02
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=_hold_past_max()):
            signal = strategy.evaluate_exit(pos, current_price=1.003)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    assert signal.signal_type != SignalType.SELL_MAX_HOLD_PROFIT
    print("PASS: bad trough at 15m -> stop loss before time exit")


def test_positive_quote_max_hold_profit_exit():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=_hold_past_max()):
            signal = strategy.evaluate_exit(
                pos,
                current_price=0.999,
                executable_pnl_pct=0.002,
            )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_MAX_HOLD_PROFIT
    print("PASS: positive quote at 15m -> max hold profit exit")


def test_wbtc_exempt_at_20m():
    strategy = MomentumStrategy()
    pos = _make_position(mint=DEFAULT_WATCHLIST_MINT)
    pos.symbol = "WBTC"
    pos.entry_time = 0
    t = 20 * 60 + 1
    with _without_instant_exit():
        with patch.object(Config, "MIN_NET_WIN_SOL", 0.0):
            with patch("strategy.time.time", return_value=t):
                signal = strategy.evaluate_exit(pos, current_price=1.005)
    assert signal is None
    print("PASS: WBTC at 20m -> no max hold exit")


def test_disabled_max_hold():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    original = Config.MAX_HOLD_ENABLED
    Config.MAX_HOLD_ENABLED = False
    try:
        with _without_instant_exit():
            with patch("strategy.time.time", return_value=_hold_past_max()):
                signal = strategy.evaluate_exit(pos, current_price=1.005)
        assert signal is None
    finally:
        Config.MAX_HOLD_ENABLED = original
    print("PASS: MAX_HOLD_ENABLED=false skips max hold")


def main():
    test_config_defaults()
    test_non_wbtc_positive_max_hold_profit_exit()
    test_non_wbtc_shallow_loss_max_hold_time_exit()
    test_non_wbtc_deep_loss_still_stop_loss()
    test_positive_mark_bad_trough_triggers_stop_at_max_hold()
    test_positive_quote_max_hold_profit_exit()
    test_wbtc_exempt_at_20m()
    test_disabled_max_hold()
    print("\nAll max hold tests passed.")


if __name__ == "__main__":
    main()
