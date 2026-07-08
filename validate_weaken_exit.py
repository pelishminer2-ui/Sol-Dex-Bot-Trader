"""Validate global trend-weakening exit at >=2% profit."""

import time
from collections import deque
from unittest.mock import patch

from config import Config, DEFAULT_WEAKEN_EXIT_MIN_PROFIT_PCT
from fee_estimator import compute_take_profit_levels, get_fee_budget
from price_feed import PriceFeed
from strategy import MomentumStrategy, Position, SignalType


def _make_position(
    entry_price: float = 1.0,
    token_raw: int = 10000,
    size_sol: float = 0.05,
    tp_levels_hit=None,
) -> Position:
    tp_levels = compute_take_profit_levels(size_sol)
    return Position(
        mint="TestMint",
        symbol="TEST",
        entry_price=entry_price,
        entry_time=time.time() - 120,
        size_sol=size_sol,
        token_amount_raw=token_raw,
        initial_token_amount_raw=token_raw,
        remaining_token_amount_raw=token_raw,
        tp_levels=tp_levels,
        tp_portions=list(Config.TAKE_PROFIT_PORTIONS),
        target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
        fee_budget_sol=get_fee_budget(size_sol),
        momentum_at_entry=0.05,
        tp_levels_hit=list(tp_levels_hit or []),
    )


def _seed_slowing_history(feed: PriceFeed, mint: str, base: float = 1.0):
    """Rising then fading momentum: strong prior window, weak recent window."""
    now = time.time()
    history = deque(maxlen=500)
    for ts in range(60, 30, -5):
        price = base * (1.0 + 0.04 * (60 - ts) / 30)
        history.append((now - ts, price))
    for i, ts in enumerate(range(25, 0, -5)):
        price = base * 1.04 * (1.0 - 0.002 * i)
        history.append((now - ts, price))
    feed._history[mint] = history


def _seed_continued_history(feed: PriceFeed, mint: str, base: float = 1.0):
    """Sustained momentum through recent window."""
    now = time.time()
    history = deque(maxlen=500)
    for ts in range(60, 0, -5):
        progress = (60 - ts) / 60
        price = base * (1.0 + 0.08 * progress)
        history.append((now - ts, price))
    feed._history[mint] = history


def test_weaken_exit_at_2_5pct_with_slowing():
    strategy = MomentumStrategy()
    feed = PriceFeed()
    mint = "TestMint"
    _seed_slowing_history(feed, mint)

    pos = _make_position(size_sol=0.10)
    current_price = 1.025  # +2.5% pnl, no ladder hits yet

    assert pos.pnl_pct(current_price) >= Config.WEAKEN_EXIT_MIN_PROFIT_PCT
    assert strategy._detect_momentum_weakening(mint, pos, feed, current_price) is True

    with patch.object(Config, "MIN_NET_WIN_SOL", 0.0), patch.object(
        Config, "INSTANT_PROFIT_EXIT_ENABLED", False
    ):
        signal = strategy.evaluate_exit(pos, current_price, price_feed=feed, mint=mint)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_WEAKEN
    print("PASS: +2.5% pnl + slowing momentum -> full weaken exit")


def test_no_weaken_exit_at_2pct_with_strong_momentum():
    strategy = MomentumStrategy()
    feed = PriceFeed()
    mint = "TestMint"
    _seed_continued_history(feed, mint)

    pos = _make_position()
    current_price = 1.02  # exactly +2% pnl

    assert pos.pnl_pct(current_price) >= Config.WEAKEN_EXIT_MIN_PROFIT_PCT
    assert strategy._detect_momentum_weakening(mint, pos, feed, current_price) is False

    signal = strategy.evaluate_exit(pos, current_price, price_feed=feed, mint=mint)
    assert signal is None or signal.signal_type != SignalType.SELL_WEAKEN
    print("PASS: +2% pnl + strong momentum -> no weaken exit")


def test_weaken_exit_disabled():
    strategy = MomentumStrategy()
    feed = PriceFeed()
    mint = "TestMint"
    _seed_slowing_history(feed, mint)

    pos = _make_position()
    current_price = 1.03

    original = Config.WEAKEN_EXIT_ENABLED
    try:
        Config.WEAKEN_EXIT_ENABLED = False
        signal = strategy.evaluate_exit(pos, current_price, price_feed=feed, mint=mint)
        assert signal is None or signal.signal_type != SignalType.SELL_WEAKEN
    finally:
        Config.WEAKEN_EXIT_ENABLED = original
    print("PASS: weaken exit disabled -> no weaken signal")


def test_config_defaults():
    assert Config.WEAKEN_EXIT_ENABLED is True
    cfg = Config.to_dict()
    assert cfg["spread_defaults"]["weaken_exit_min_profit_pct"] == DEFAULT_WEAKEN_EXIT_MIN_PROFIT_PCT
    assert cfg["weaken_exit_enabled"] is True
    print("PASS: config defaults for weaken exit")


def test_coexists_with_ladder_slowdown():
    """L2 slowdown takes precedence over global weaken between 2% and 5% profit."""
    strategy = MomentumStrategy()
    feed = PriceFeed()
    mint = "TestMint"
    _seed_slowing_history(feed, mint)

    pos = _make_position(tp_levels_hit=[0, 1])
    pos.remaining_token_amount_raw = 7500
    pos.initial_token_amount_raw = 10000
    current_price = 1.03  # +3% — below instant exit, above weaken threshold

    signal = strategy.evaluate_exit(pos, current_price, price_feed=feed, mint=mint)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SLOWDOWN
    print("PASS: L2 slowdown takes precedence over global weaken below instant threshold")


def main():
    test_config_defaults()
    test_weaken_exit_at_2_5pct_with_slowing()
    test_no_weaken_exit_at_2pct_with_strong_momentum()
    test_weaken_exit_disabled()
    test_coexists_with_ladder_slowdown()
    print("\nAll weaken exit tests passed.")


if __name__ == "__main__":
    main()
