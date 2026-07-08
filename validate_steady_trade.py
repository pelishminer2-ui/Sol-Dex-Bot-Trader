"""Validate Steady Trade preset, API, and entry gate exposure."""

from unittest.mock import patch

from config import (
    BEST_WIN_PRESET,
    STEADY_TRADE_PRESET,
    STEADY_TRADE_STRATEGY_PRESET,
    Config,
    apply_steady_trade_strategy,
)
from scanner import MoverCandidate
from strategy import MomentumStrategy


def test_steady_trade_preset_values():
    assert STEADY_TRADE_PRESET["entry_momentum_pct"] == 0.004
    assert STEADY_TRADE_PRESET["min_momentum_pct"] == 0.015
    assert STEADY_TRADE_PRESET["non_watchlist_min_volume_24h_usd"] == 45000.0
    assert STEADY_TRADE_PRESET["max_entry_price_impact_pct"] == 1.0
    assert STEADY_TRADE_PRESET["min_net_win_sol"] == 0.002
    assert STEADY_TRADE_PRESET["loss_reentry_cooldown_minutes"] == 120
    assert STEADY_TRADE_PRESET["reentry_min_momentum_pct"] == 0.015
    assert STEADY_TRADE_STRATEGY_PRESET["hot_market_mode_enabled"] is True
    print("PASS: STEADY_TRADE_PRESET values")


def test_steady_between_balanced_and_best_win():
    assert STEADY_TRADE_PRESET["entry_momentum_pct"] == 0.004
    assert BEST_WIN_PRESET["entry_momentum_pct"] > STEADY_TRADE_PRESET["entry_momentum_pct"]
    assert BEST_WIN_PRESET["min_momentum_pct"] > STEADY_TRADE_PRESET["min_momentum_pct"]
    assert (
        BEST_WIN_PRESET["non_watchlist_min_volume_24h_usd"]
        > STEADY_TRADE_PRESET["non_watchlist_min_volume_24h_usd"]
    )
    print("PASS: steady is looser than Best Win on scanner/entry filters")


def test_config_api_exposes_steady_preset():
    cfg = Config.spread_defaults()
    assert cfg["steady_trade_preset"] == STEADY_TRADE_PRESET
    assert cfg["steady_trade_strategy_preset"] == STEADY_TRADE_STRATEGY_PRESET
    print("PASS: spread_defaults exposes steady presets")


def test_apply_steady_trade_strategy():
    with patch("config._write_env_keys"), patch(
        "config._write_hot_market_env_defaults"
    ), patch.object(Config, "update_runtime") as mock_update:
        mock_update.return_value = {"applied": {}, "needs_restart": []}
        result = apply_steady_trade_strategy(save_bookmark=False)
    assert result["preset"] == "steady_trade_strategy"
    applied = mock_update.call_args.kwargs
    assert applied["ENTRY_MOMENTUM_PCT"] == 0.004
    assert applied["MIN_MOMENTUM_PCT"] == 0.015
    assert applied["NON_WATCHLIST_MIN_VOLUME_24H_USD"] == 45000.0
    assert applied["HOT_MARKET_MODE_ENABLED"] is True
    print("PASS: apply_steady_trade_strategy runtime updates")


def test_entry_skip_reason_uses_effective_momentum():
    strategy = MomentumStrategy()
    candidate = MoverCandidate(
        mint="mint123456789",
        symbol="TEST",
        name="Test",
        pair_address="pair",
        dex="raydium",
        price_usd=1.0,
        liquidity_usd=50000,
        volume_24h_usd=100000,
        momentum_pct=0.01,
        price_change_5m=0.01,
        price_change_1h=0.01,
    )
    with patch.object(Config, "HOT_MARKET_MODE_ENABLED", True), patch(
        "config.get_regime_gates",
        create=True,
    ), patch("market_regime.get_regime_gates", return_value={"entry_momentum_pct": 0.0075}):
        reason = strategy.entry_skip_reason(candidate, momentum=0.006)
    assert reason is not None
    assert "0.75%" in reason
    print("PASS: entry_skip_reason uses effective entry momentum")


if __name__ == "__main__":
    test_steady_trade_preset_values()
    test_steady_between_balanced_and_best_win()
    test_config_api_exposes_steady_preset()
    test_apply_steady_trade_strategy()
    test_entry_skip_reason_uses_effective_momentum()
    print("\nAll Steady Trade validation tests passed.")
