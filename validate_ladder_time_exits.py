"""Validate ladder-missed time exit and DCA rules."""

import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from config import Config
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
    entry_price: float = 1.0,
    token_raw: int = 10000,
    size_sol: float = 0.05,
    buy_count: int = 1,
) -> Position:
    tp_levels = compute_take_profit_levels(size_sol)
    return Position(
        mint="TestMint",
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
        buy_count=buy_count,
    )


def test_config_defaults():
    assert Config.LADDER_MISSED_POSITIVE_EXIT_MINUTES == 10
    assert Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES == 30
    assert Config.MAX_BUYS_PER_MINT == 3
    assert Config.ENABLE_LADDER_TIME_EXITS is True
    print("PASS: ladder time exit config defaults")


def test_10m_positive_full_exit_no_ladder():
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.10)
    pos.entry_time = 0
    t = Config.LADDER_MISSED_POSITIVE_EXIT_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch.object(Config, "MIN_NET_WIN_SOL", 0.0):
            with patch("strategy.time.time", return_value=t):
                signal = strategy.evaluate_exit(pos, current_price=1.005)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_LADDER_MISSED_10M
    print("PASS: 10m positive + no ladder -> full exit")


def test_10m_negative_hold():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    t = Config.LADDER_MISSED_POSITIVE_EXIT_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t):
            signal = strategy.evaluate_exit(pos, current_price=0.995)
    assert signal is None
    print("PASS: 10m negative + no ladder -> hold")


def test_30m_negative_sell_when_dca_blocked():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    t = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t):
            signal = strategy.evaluate_exit(
                pos,
                current_price=0.99,
                can_afford_dca=False,
                jupiter_route_ok=False,
            )
    assert signal is not None
    # Non-WBTC max hold (15m) fires before 30m ladder timeout.
    assert signal.signal_type == SignalType.SELL_TIME
    print("PASS: 30m negative + no ladder + DCA blocked -> max hold time exit")


def test_30m_negative_dca_when_favorable():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    feed = MagicMock()
    feed.get_window_momentum.side_effect = lambda *a, **k: 0.006
    feed.get_peak_momentum_since.return_value = 0.01
    feed.momentum_declining_streak.return_value = False
    t = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t):
            with patch.object(strategy, "_detect_momentum_weakening", return_value=False):
                signal = strategy.evaluate_exit(
                    pos,
                    current_price=0.992,
                    price_feed=feed,
                    mint=pos.mint,
                    can_afford_dca=True,
                    jupiter_route_ok=True,
                )
    assert signal is not None
    # Max hold at 15m preempts 30m ladder DCA for non-WBTC.
    assert signal.signal_type == SignalType.SELL_TIME
    print("PASS: 30m negative + recovering momentum -> max hold time exit (not DCA)")


def test_30m_negative_sell_on_liquidity_drop():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    t = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t):
            signal = strategy.evaluate_exit(
                pos,
                current_price=0.992,
                can_afford_dca=True,
                jupiter_route_ok=True,
                current_liquidity_usd=40000.0,
            )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_TIME
    print("PASS: 30m negative + liquidity drop -> max hold time exit")


def test_30m_negative_sell_on_weak_momentum():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    feed = MagicMock()
    t = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t):
            with patch.object(strategy, "_detect_momentum_weakening", return_value=True):
                signal = strategy.evaluate_exit(
                    pos,
                    current_price=0.992,
                    price_feed=feed,
                    mint=pos.mint,
                    can_afford_dca=True,
                    jupiter_route_ok=True,
                )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_TIME
    print("PASS: 30m negative + weak momentum -> max hold time exit")


def test_30m_negative_max_buys_sells():
    strategy = MomentumStrategy()
    pos = _make_position(buy_count=3)
    pos.entry_time = 0
    t = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t):
            signal = strategy.evaluate_exit(
                pos,
                current_price=0.99,
                can_afford_dca=True,
                jupiter_route_ok=True,
            )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_TIME
    print("PASS: 30m negative + max buys reached -> max hold time exit")


def test_stop_loss_before_ladder_time_exit():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    t = Config.LADDER_MISSED_POSITIVE_EXIT_MINUTES * 60 + 1
    with patch("strategy.time.time", return_value=t):
        signal = strategy.evaluate_exit(pos, current_price=0.98)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: stop loss priority over 10m ladder exit")


def test_ladder_hit_skips_10m_exit():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.tp_levels_hit = [0]
    pos.entry_time = 0
    t = Config.LADDER_MISSED_POSITIVE_EXIT_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t):
            signal = strategy.evaluate_exit(pos, current_price=1.02)
    assert signal is None
    print("PASS: partial ladder hit skips 10m positive exit")


def test_apply_dca_increments_buy_count():
    strategy = MomentumStrategy()
    pos = _make_position(size_sol=0.05, token_raw=10000)
    pos.entry_time = 0.0
    with patch("strategy.time.time", return_value=1000.0):
        strategy.apply_dca_to_position(pos, 0.05, 5000, 0.95)
    assert pos.buy_count == 2
    assert pos.size_sol == 0.10
    assert pos.remaining_token_amount_raw == 15000
    assert pos.initial_token_amount_raw == 15000
    assert len(pos.tp_levels) == 2
    assert pos.entry_time == 1000.0
    print("PASS: apply_dca increments buy_count and rescales ladder")


def test_dca_resets_ladder_timeout_clock():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    t = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60 + 1
    with patch("strategy.time.time", return_value=t):
        strategy.apply_dca_to_position(pos, 0.05, 5000, 0.95)
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t + 60):
            # Blended entry ~0.983 after DCA; stay below +0.10% L1 and above -1.5% SL
            signal = strategy.evaluate_exit(pos, current_price=0.969)
    assert signal is None
    print("PASS: DCA resets entry_time so 30m rule does not re-fire immediately")


def test_disabled_ladder_time_exits():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    original = Config.ENABLE_LADDER_TIME_EXITS
    Config.ENABLE_LADDER_TIME_EXITS = False
    try:
        t = Config.LADDER_MISSED_POSITIVE_EXIT_MINUTES * 60 + 1
        with _without_instant_exit():
            with patch("strategy.time.time", return_value=t):
                signal = strategy.evaluate_exit(pos, current_price=1.0005)
        assert signal is None
    finally:
        Config.ENABLE_LADDER_TIME_EXITS = original
    print("PASS: ENABLE_LADDER_TIME_EXITS=false skips 10m rule")


def test_wbtc_30m_negative_ladder_exit_fires():
    from config import DEFAULT_WATCHLIST_MINT

    strategy = MomentumStrategy()
    pos = _make_position()
    pos.mint = DEFAULT_WATCHLIST_MINT
    pos.symbol = "WBTC"
    pos.entry_time = 0
    t = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60 + 1
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=t):
            signal = strategy.evaluate_exit(
                pos,
                current_price=0.992,
                can_afford_dca=False,
                jupiter_route_ok=False,
            )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_LADDER_MISSED_30M
    print("PASS: WBTC 30m negative ladder exit fires (no defer)")


def main():
    test_config_defaults()
    test_10m_positive_full_exit_no_ladder()
    test_10m_negative_hold()
    test_30m_negative_sell_when_dca_blocked()
    test_30m_negative_dca_when_favorable()
    test_30m_negative_sell_on_liquidity_drop()
    test_30m_negative_sell_on_weak_momentum()
    test_30m_negative_max_buys_sells()
    test_wbtc_30m_negative_ladder_exit_fires()
    test_stop_loss_before_ladder_time_exit()
    test_ladder_hit_skips_10m_exit()
    test_apply_dca_increments_buy_count()
    test_dca_resets_ladder_timeout_clock()
    test_disabled_ladder_time_exits()
    print("\nAll ladder time exit tests passed.")


if __name__ == "__main__":
    main()
