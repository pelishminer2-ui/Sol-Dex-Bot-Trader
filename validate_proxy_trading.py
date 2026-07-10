"""Validate proxy mainstream trading: per-asset day gates and 15m green time exits."""

import time
from contextlib import contextmanager
from unittest.mock import patch

from config import (
    DEFAULT_JITOSOL_MIN_DAILY_GAIN_USD,
    DEFAULT_WATCHLIST_MINT,
    DEFAULT_WBTC_MIN_DAILY_GAIN_USD,
    DEFAULT_WETH_MIN_DAILY_GAIN_USD,
    JITOSOL_MINT,
    WETH_MINT,
    Config,
    is_proxy_mainstream_mint,
)
from fee_estimator import compute_take_profit_levels, get_fee_budget
from proxy_entry_gate import (
    jitosol_day_gate_passes,
    weth_day_gate_passes,
    weth_entry_rule_summary,
)
from scanner import MoverCandidate
from strategy import MomentumStrategy, Position, SignalType


@contextmanager
def _without_instant_exit():
    original = Config.INSTANT_PROFIT_EXIT_ENABLED
    Config.INSTANT_PROFIT_EXIT_ENABLED = False
    try:
        yield
    finally:
        Config.INSTANT_PROFIT_EXIT_ENABLED = original


def _make_position(*, mint: str, entry_price: float = 1.0) -> Position:
    tp_levels = compute_take_profit_levels(0.05)
    return Position(
        mint=mint,
        symbol="PROXY",
        entry_price=entry_price,
        entry_time=time.time(),
        size_sol=0.05,
        token_amount_raw=10000,
        initial_token_amount_raw=10000,
        remaining_token_amount_raw=10000,
        tp_levels=tp_levels,
        tp_portions=list(Config.TAKE_PROFIT_PORTIONS),
        target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
        fee_budget_sol=get_fee_budget(0.05),
        profile={"liquidity_usd": 500000.0},
    )


def _hold_past_max() -> int:
    return Config.MAX_HOLD_MINUTES_NON_WBTC * 60 + 1


def test_config_per_asset_day_gate_defaults():
    assert DEFAULT_WBTC_MIN_DAILY_GAIN_USD == 301.0
    assert DEFAULT_JITOSOL_MIN_DAILY_GAIN_USD == 50.0
    assert DEFAULT_WETH_MIN_DAILY_GAIN_USD == 150.0
    assert Config.WBTC_MIN_DAILY_GAIN_USD == 301.0
    assert Config.JITOSOL_MIN_DAILY_GAIN_USD == 50.0
    assert Config.WETH_MIN_DAILY_GAIN_USD == 150.0
    print("PASS: per-asset day gate defaults (WBTC $301, JitoSOL $50, WETH $150)")


def test_proxy_mainstream_mint_recognition():
    assert is_proxy_mainstream_mint(DEFAULT_WATCHLIST_MINT) is True
    with patch.object(Config, "ENABLE_SOL_TRADING", True), patch.object(
        Config, "SOL_TRADE_MINT", JITOSOL_MINT
    ):
        assert is_proxy_mainstream_mint(JITOSOL_MINT) is True
    with patch.object(Config, "ENABLE_WETH_TRADING", True):
        assert is_proxy_mainstream_mint(WETH_MINT) is True
    assert is_proxy_mainstream_mint("random_memecoin") is False
    print("PASS: proxy mainstream mint recognition")


def test_weth_day_gate_requires_150():
    assert weth_day_gate_passes(day_usd_gain=200.0, day_pct_gain=0.01) is True
    assert weth_day_gate_passes(day_usd_gain=140.0, day_pct_gain=0.01) is False
    summary = weth_entry_rule_summary()
    assert "$150" in summary
    print("PASS: WETH $150 day gate")


def test_jitosol_day_gate_requires_50():
    assert jitosol_day_gate_passes(day_usd_gain=55.0, day_pct_gain=0.01) is True
    assert jitosol_day_gate_passes(day_usd_gain=15.0, day_pct_gain=0.01) is False
    print("PASS: JitoSOL $50 day gate")


def test_wbtc_no_proxy_green_exit_at_15m():
    strategy = MomentumStrategy()
    pos = _make_position(mint=DEFAULT_WATCHLIST_MINT)
    pos.symbol = "WBTC"
    pos.entry_time = 0
    with _without_instant_exit():
        with patch("strategy.time.time", return_value=_hold_past_max()):
            signal = strategy.evaluate_exit(pos, current_price=1.002)
    assert signal is None
    print("PASS: WBTC 15m green — no forced proxy exit (hold until fee-positive)")


def test_jitosol_green_proxy_exit_at_15m():
    strategy = MomentumStrategy()
    pos = _make_position(mint=JITOSOL_MINT)
    pos.symbol = "JitoSOL"
    pos.entry_time = 0
    with _without_instant_exit():
        with patch.object(Config, "ENABLE_SOL_TRADING", True), patch.object(
            Config, "SOL_TRADE_MINT", JITOSOL_MINT
        ), patch("strategy.time.time", return_value=_hold_past_max()):
            signal = strategy.evaluate_exit(pos, current_price=1.002)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_PROXY_GREEN_HOLD
    print("PASS: JitoSOL 15m green -> proxy green hold exit")


def test_proxy_red_not_forced_at_15m():
    strategy = MomentumStrategy()
    pos = _make_position(mint=WETH_MINT)
    pos.symbol = "WETH"
    pos.entry_time = 0
    with _without_instant_exit():
        with patch.object(Config, "ENABLE_WETH_TRADING", True), patch(
            "strategy.time.time", return_value=_hold_past_max()
        ):
            signal = strategy.evaluate_exit(pos, current_price=0.999)
    assert signal is None
    print("PASS: proxy red at 15m -> no forced time exit")


def test_strategy_weth_blocks_below_150():
    strategy = MomentumStrategy()
    candidate = MoverCandidate(
        mint=WETH_MINT,
        symbol="WETH",
        name="Wrapped Ether",
        pair_address="pair",
        dex="orca",
        price_usd=3500.0,
        liquidity_usd=5_000_000.0,
        volume_24h_usd=1_000_000.0,
        momentum_pct=0.008,
        price_change_5m=0.0,
        price_change_1h=0.008,
        source="weth_trade",
        day_usd_gain=140.0,
        day_pct_gain=0.01,
    )
    with patch.object(Config, "ENABLE_WETH_TRADING", True), patch.object(
        Config, "WETH_MINT", WETH_MINT
    ):
        assert strategy.evaluate_entry(candidate, 3500.0, 0.008) == SignalType.NONE
    print("PASS: strategy blocks WETH below $150 day gain")


def test_strategy_weth_allows_at_150():
    strategy = MomentumStrategy()
    candidate = MoverCandidate(
        mint=WETH_MINT,
        symbol="WETH",
        name="Wrapped Ether",
        pair_address="pair",
        dex="orca",
        price_usd=3500.0,
        liquidity_usd=5_000_000.0,
        volume_24h_usd=1_000_000.0,
        momentum_pct=0.008,
        price_change_5m=0.0,
        price_change_1h=0.008,
        source="weth_trade",
        day_usd_gain=200.0,
        day_pct_gain=0.01,
    )
    with patch.object(Config, "ENABLE_WETH_TRADING", True), patch.object(
        Config, "WETH_MINT", WETH_MINT
    ):
        assert strategy.evaluate_entry(candidate, 3500.0, 0.008) == SignalType.BUY
    print("PASS: strategy allows WETH at $150+ day gain")


def main():
    test_config_per_asset_day_gate_defaults()
    test_proxy_mainstream_mint_recognition()
    test_weth_day_gate_requires_150()
    test_jitosol_day_gate_requires_50()
    test_wbtc_no_proxy_green_exit_at_15m()
    test_jitosol_green_proxy_exit_at_15m()
    test_proxy_red_not_forced_at_15m()
    test_strategy_weth_blocks_below_150()
    test_strategy_weth_allows_at_150()
    print("\nAll proxy trading validations passed.")


if __name__ == "__main__":
    main()
