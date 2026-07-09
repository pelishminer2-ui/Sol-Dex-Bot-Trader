"""Validate WBTC hold-until-fee-positive exit policy (no SL, no time force)."""

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
    assert Config.WBTC_STOP_LOSS_ENABLED is False
    assert Config.to_dict()["wbtc_profit_only_exits"] is True
    assert Config.to_dict()["wbtc_stop_loss_enabled"] is False
    print("PASS: WBTC hold-until-profit defaults")


def test_wbtc_profit_gate_signal_classification():
    assert wbtc_profit_gate_applies(WBTC_MINT, "sell_instant_5pct")
    assert wbtc_profit_gate_applies(WBTC_MINT, "sell_wbtc_hold_profit")
    assert wbtc_profit_gate_applies(WBTC_MINT, "sell_ladder_missed_10m_positive")
    assert not wbtc_profit_gate_applies(WBTC_MINT, "sell_stop_loss")
    assert not wbtc_profit_gate_applies(WBTC_MINT, "sell_l1_protection")
    assert not wbtc_profit_gate_applies("othermint", "sell_instant_5pct")
    print("PASS: WBTC voluntary vs risk exit classification")


def test_wbtc_no_stop_loss_when_disabled():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "WBTC_STOP_LOSS_ENABLED", False
    ):
        signal = strategy.evaluate_exit(pos, 0.978)
    assert signal is None
    print("PASS: WBTC stop-loss disabled — holds through drawdown")


def test_wbtc_no_proxy_green_exit_at_15m():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    pos.entry_time = time.time() - 16 * 60
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "INSTANT_PROFIT_EXIT_ENABLED", False
    ):
        signal = strategy.evaluate_exit(pos, 1.002)
    assert signal is None
    print("PASS: WBTC no 15m green proxy exit")


def test_wbtc_ladder_10m_blocked_on_small_green():
    """+0.06% gross on 0.10 SOL is green but net-negative after fees — must hold."""
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    pos.entry_time = time.time() - 11 * 60
    est = estimate_exit_net_sol(0.10, 1.0, 0.0006, 0.0, pos.fee_budget_sol)
    assert est < 0

    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True):
        signal = strategy.evaluate_exit(pos, 1.0006)
    assert signal is None
    print("PASS: WBTC 10m positive exit blocked when net below fee-positive")


def test_wbtc_hold_until_profitable_on_quote():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    pnl = 0.035
    est = estimate_exit_net_sol(0.10, 1.0, pnl, 0.0, pos.fee_budget_sol)
    assert est > 0

    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "INSTANT_PROFIT_EXIT_ENABLED", False
    ):
        signal = strategy.evaluate_exit(
            pos, 1.0 + pnl, executable_pnl_pct=pnl
        )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_WBTC_HOLD_PROFIT
    assert signal.needs_quote_check is True
    print("PASS: WBTC hold-until-profit fires on fee-positive quote")


def test_wbtc_instant_exit_requires_fee_positive():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "INSTANT_PROFIT_EXIT_PCT", 0.05
    ):
        signal = strategy.evaluate_exit(pos, 1.05)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    assert signal.needs_quote_check is True
    print("PASS: WBTC instant +5% requires quote fee-positive check")


def test_wbtc_stop_loss_fires_when_enabled():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _wbtc_candidate(), 1.0, 0.10, 0.01, token_amount_raw=100_000
    )
    with patch.object(Config, "WBTC_PROFIT_ONLY_EXITS", True), patch.object(
        Config, "WBTC_STOP_LOSS_ENABLED", True
    ):
        signal = strategy.evaluate_exit(pos, 0.978)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: WBTC stop-loss fires when WBTC_STOP_LOSS_ENABLED=true")


def test_non_wbtc_unaffected():
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
        Config, "INSTANT_PROFIT_EXIT_PCT", 0.05
    ):
        signal = strategy.evaluate_exit(pos, 1.06)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    assert signal.needs_quote_check is False
    print("PASS: non-WBTC not gated by WBTC hold policy")


if __name__ == "__main__":
    test_config_default_wbtc_profit_only()
    test_wbtc_profit_gate_signal_classification()
    test_wbtc_no_stop_loss_when_disabled()
    test_wbtc_no_proxy_green_exit_at_15m()
    test_wbtc_ladder_10m_blocked_on_small_green()
    test_wbtc_hold_until_profitable_on_quote()
    test_wbtc_instant_exit_requires_fee_positive()
    test_wbtc_stop_loss_fires_when_enabled()
    test_non_wbtc_unaffected()
    print("\nAll WBTC profit-exit tests passed.")
