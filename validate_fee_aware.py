"""Validate fee-aware exit gates, loss re-entry cooldown, and config defaults."""

from unittest.mock import patch

from config import (
    BEST_WIN_PRESET,
    DEFAULT_LOSS_REENTRY_COOLDOWN_MINUTES,
    DEFAULT_LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES,
    DEFAULT_MIN_EXPECTED_NET_PROFIT_SOL,
    DEFAULT_MIN_NET_WIN_SOL,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_WEAKEN_EXIT_MIN_PROFIT_PCT,
    Config,
)
from fee_estimator import estimate_partial_net_win_sol
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType


def _candidate(mint: str = "mint123", symbol: str = "TEST") -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
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


def test_config_fee_aware_defaults():
    assert DEFAULT_MIN_EXPECTED_NET_PROFIT_SOL == 0.003
    assert DEFAULT_MIN_NET_WIN_SOL == 0.003
    assert DEFAULT_LOSS_REENTRY_COOLDOWN_MINUTES == 120
    assert DEFAULT_LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES == 240
    assert DEFAULT_STOP_LOSS_PCT == 0.015
    assert DEFAULT_WEAKEN_EXIT_MIN_PROFIT_PCT == 0.01
    cfg = Config.to_dict()
    assert cfg["best_win_preset"]["min_net_win_sol"] == 0.003
    assert cfg["loss_reentry_cooldown_minutes"] == Config.LOSS_REENTRY_COOLDOWN_MINUTES
    assert cfg["best_win_preset"]["loss_reentry_repeat_cooldown_minutes"] == 240
    assert cfg["win_focused_preset"]["min_net_win_sol"] == 0.003
    assert cfg["best_win_preset"] == BEST_WIN_PRESET
    assert cfg["tight_losses_preset"]["min_net_win_sol"] == 0.003
    print("PASS: fee-aware config defaults")


def test_partial_l1_blocked_when_net_below_min():
    """0.10 SOL L1 at +3% should net ~0.0015 — below 0.002 min win."""
    est = estimate_partial_net_win_sol(0.10, 0, 0.03)
    assert est < Config.MIN_NET_WIN_SOL

    strategy = MomentumStrategy()
    pos = strategy.open_position(_candidate(), 1.0, 0.10, 0.05, token_amount_raw=100000)
    with patch.object(Config, "MIN_NET_WIN_SOL", 0.002):
        signal = strategy.evaluate_exit(pos, 1.03)
    assert signal is None
    print("PASS: partial L1 blocked when est net below MIN_NET_WIN_SOL")


def test_loss_reentry_cooldown_blocks_entry():
    strategy = MomentumStrategy()
    mint = "lossmint123456789"
    with patch.object(Config, "LOSS_REENTRY_COOLDOWN_MINUTES", 90):
        strategy.record_loss_reentry_cooldown(mint)
        assert strategy.is_on_loss_reentry_cooldown(mint)
        result = strategy.evaluate_entry(_candidate(mint=mint), 1.0, 0.05)
    assert result == SignalType.NONE
    print("PASS: loss re-entry cooldown blocks entry")


def test_l1_protection_still_fires():
    strategy = MomentumStrategy()
    pos = strategy.open_position(_candidate(), 1.0, 0.10, 0.05, token_amount_raw=100000)
    strategy.apply_partial_tp(pos, 0, 50000, 1.03)
    with patch.object(Config, "MIN_NET_WIN_SOL", 0.002):
        signal = strategy.evaluate_exit(pos, 1.0)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_L1_PROTECTION
    print("PASS: L1 protection fires despite min net win gate")


if __name__ == "__main__":
    test_config_fee_aware_defaults()
    test_partial_l1_blocked_when_net_below_min()
    test_loss_reentry_cooldown_blocks_entry()
    test_l1_protection_still_fires()
    print("\nAll fee-aware tests passed.")
