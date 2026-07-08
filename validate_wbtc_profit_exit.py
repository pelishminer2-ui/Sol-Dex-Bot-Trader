"""Validate WBTC profit-only voluntary exit gates."""

import time
from unittest.mock import patch

from config import (
    DEFAULT_WATCHLIST_MINT,
    DEFAULT_WBTC_PROFIT_ONLY_EXITS,
    Config,
    wbtc_profit_gate_applies,
)
from fee_estimator import estimate_exit_net_sol, estimate_partial_net_win_sol
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType


WBTC_MINT = DEFAULT_WATCHLIST_MINT


def _wbtc_candidate() -> MoverCandidate:
    return MoverCandidate(
        mint=WBTC_MINT,
        symbol="WBTC",
        name="Wrapped BTC",
        pair_address="",
        dex="test",
        price_usd=60000.0,
        liquidity_usd=500000.0,
        volume_24h_usd=1000000.0,
        momentum_pct=0.01,
        price_change_5m=0.01,
        price_change_1h=0.01,
        source="test",
    )


def test_config_default_wbtc_profit_only():
    assert DEFAULT_WBTC_PROFIT_ONLY_EXITS is True
    assert Config.WBTC_PROFIT_ONLY_EXITS is True
    assert Config.to_dict()["wbtc_profit_only_exits"] is True
    print("PASS: WBTC_PROFIT_ONLY_EXITS defaults true")


def test_wbtc_profit_gate_signal_classification():
    assert not wbtc_profit_gate_applies(WBTC_MINT, "sell_instant_5pct")
    assert wbtc_profit_gate_applies(WBTC_MINT, "sell_ladder_missed_10m_positive")
    assert not wbtc_profit_gate_applies(WBTC_MINT, "sell_stop_loss")
    assert not wbtc_profit_gate_applies(WBTC_MINT, "sell_l1_protection")
    assert not wbtc_profit_gate_applies("othermint", "sell_instant_5pct")
    print("PASS: WBTC voluntary vs risk exit classification")


def test_wbtc_ladder_10m_blocked_on_small_green():
    """+0.06% gross on 0.10 SOL is green but net-negative after fees — must hold."""
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    pos.entry_time = time.time() - 11 * 60
    est = estimate_exit_net_sol(0.10, 1.0, 0.0006, 0.0, pos.fee_budget_sol)
    assert est < Config.MIN_NET_WIN_SOL

    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "MIN_NET_WIN_SOL", 0.002
    ):
        signal = strategy.evaluate_exit(pos, 1.0006)
    assert signal is None
    print("PASS: WBTC 10m positive exit blocked when net below min")


def test_wbtc_l1_partial_blocked_at_plus_3pct():
    """L1 at +3% gross nets below MIN_NET_WIN_SOL on 0.10 SOL — hold."""
    est = estimate_partial_net_win_sol(0.10, 0, 0.03)
    assert est < 0.002

    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "MIN_NET_WIN_SOL", 0.002
    ):
        signal = strategy.evaluate_exit(pos, 1.03)
    assert signal is None
    print("PASS: WBTC L1 partial blocked at +3% when net below min")


def test_wbtc_stop_loss_still_fires():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "MIN_NET_WIN_SOL", 0.002
    ):
        signal = strategy.evaluate_exit(pos, 0.978)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    assert not signal.needs_quote_check
    print("PASS: WBTC stop-loss fires despite profit-only mode")


def test_wbtc_l1_protection_still_fires():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    strategy.apply_partial_tp(pos, 0, 50_000, 1.05)
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "MIN_NET_WIN_SOL", 0.002
    ):
        signal = strategy.evaluate_exit(pos, 1.0)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_L1_PROTECTION
    print("PASS: WBTC L1 protection fires despite profit-only mode")


def test_wbtc_voluntary_exit_flags_quote_check():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    pos.entry_time = time.time() - 11 * 60
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "MIN_NET_WIN_SOL", 0.0
    ), patch.object(Config, "INSTANT_PROFIT_EXIT_ENABLED", False):
        signal = strategy.evaluate_exit(pos, 1.05)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_LADDER_MISSED_10M
    assert signal.needs_quote_check is True
    print("PASS: WBTC voluntary exit sets needs_quote_check")


def test_wbtc_instant_exit_skips_quote_gate():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "MIN_NET_WIN_SOL", 0.002
    ), patch.object(Config, "INSTANT_PROFIT_EXIT_PCT", 0.05):
        signal = strategy.evaluate_exit(pos, 1.05)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    assert signal.needs_quote_check is False
    print("PASS: WBTC instant +5% not gated by profit-only quote check")


def test_non_wbtc_unaffected_when_min_net_zero():
    strategy = MomentumStrategy()
    other = MoverCandidate(
        mint="othermint123456789",
        symbol="MEME",
        name="Meme",
        pair_address="",
        dex="test",
        price_usd=1.0,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.05,
        source="test",
    )
    pos = strategy.open_position(other, 1.0, 0.10, 0.05, token_amount_raw=100_000)
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "MIN_NET_WIN_SOL", 0.0
    ), patch.object(Config, "INSTANT_PROFIT_EXIT_PCT", 0.05):
        signal = strategy.evaluate_exit(pos, 1.06)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    assert signal.needs_quote_check is False
    print("PASS: non-WBTC not gated when MIN_NET_WIN_SOL=0")


if __name__ == "__main__":
    test_config_default_wbtc_profit_only()
    test_wbtc_profit_gate_signal_classification()
    test_wbtc_ladder_10m_blocked_on_small_green()
    test_wbtc_l1_partial_blocked_at_plus_3pct()
    test_wbtc_stop_loss_still_fires()
    test_wbtc_l1_protection_still_fires()
    test_wbtc_voluntary_exit_flags_quote_check()
    test_wbtc_instant_exit_skips_quote_gate()
    test_non_wbtc_unaffected_when_min_net_zero()
    print("\nAll WBTC profit-exit tests passed.")
