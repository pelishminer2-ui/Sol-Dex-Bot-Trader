"""Validate momentum slowdown early exit after L2 ladder level."""

import time
from collections import deque
from contextlib import contextmanager

from config import Config
from fee_estimator import compute_take_profit_levels, get_fee_budget
from price_feed import PriceFeed
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
    for i, ts in enumerate(range(60, 30, -5)):
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


def test_slowdown_triggers_full_exit_after_l2():
    strategy = MomentumStrategy()
    feed = PriceFeed()
    mint = "TestMint"
    _seed_slowing_history(feed, mint)

    pos = _make_position(token_raw=5000, tp_levels_hit=[0, 1])
    pos.remaining_token_amount_raw = 5000
    pos.initial_token_amount_raw = 10000
    pos.l1_protection_armed = True

    current_price = 1.045
    assert strategy.evaluate_momentum_slowdown(mint, pos, feed, current_price) is True

    signal = strategy.evaluate_exit(pos, current_price, price_feed=feed, mint=mint)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SLOWDOWN
    assert signal.slowdown_after_level == 2
    print("PASS: slowing momentum triggers full exit after L2")


def test_continued_momentum_proceeds_to_l2():
    strategy = MomentumStrategy()
    feed = PriceFeed()
    mint = "TestMint"
    _seed_continued_history(feed, mint)

    pos = _make_position(token_raw=5000, tp_levels_hit=[0])
    pos.remaining_token_amount_raw = 5000
    pos.initial_token_amount_raw = 10000

    assert strategy.evaluate_momentum_slowdown(mint, pos, feed, 1.035) is False

    with _without_instant_exit():
        signal = strategy.evaluate_exit(pos, 1.041, price_feed=feed, mint=mint)
        assert signal is not None
        assert signal.signal_type == SignalType.SELL_TP_PARTIAL
        assert signal.tp_level_index == 1
    print("PASS: continued momentum proceeds to L2")


def test_l1_only_no_slowdown_exit():
    strategy = MomentumStrategy()
    feed = PriceFeed()
    mint = "TestMint"
    _seed_slowing_history(feed, mint)

    pos = _make_position(token_raw=5000, tp_levels_hit=[0])
    pos.remaining_token_amount_raw = 5000
    pos.initial_token_amount_raw = 10000
    pos.l1_protection_armed = True

    current_price = 1.01
    assert strategy.evaluate_momentum_slowdown(mint, pos, feed, current_price) is False

    signal = strategy.evaluate_exit(pos, current_price, price_feed=feed, mint=mint)
    assert signal is None
    print("PASS: L1 only — no slowdown or weaken exit below 2% profit")


def test_config_defaults():
    assert 2 in Config.LADDER_EARLY_EXIT_LEVELS
    assert Config.MOMENTUM_SLOWDOWN_PCT == 0.5
    cfg = Config.to_dict()
    assert 2 in cfg["ladder_early_exit_levels"]
    assert cfg["momentum_slowdown_pct"] == 0.5
    print("PASS: config defaults for slowdown rule")


def main():
    test_config_defaults()
    test_slowdown_triggers_full_exit_after_l2()
    test_continued_momentum_proceeds_to_l2()
    test_l1_only_no_slowdown_exit()
    print("\nAll momentum slowdown tests passed.")


if __name__ == "__main__":
    main()
