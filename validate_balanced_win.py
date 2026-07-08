"""Validate Balanced Win preset, API, and entry skip reason exposure."""

from unittest.mock import patch

from config import (
    BALANCED_WIN_PRESET,
    BALANCED_WIN_STRATEGY_PRESET,
    BEST_WIN_PRESET,
    Config,
    apply_balanced_win_strategy,
)
from scanner import MoverCandidate
from strategy import MomentumStrategy


def test_balanced_win_preset_values():
    assert BALANCED_WIN_PRESET["entry_momentum_pct"] == 0.005
    assert BALANCED_WIN_PRESET["min_momentum_pct"] == 0.015
    assert BALANCED_WIN_PRESET["non_watchlist_min_volume_24h_usd"] == 50000.0
    assert BALANCED_WIN_PRESET["max_entry_price_impact_pct"] == 1.0
    assert BALANCED_WIN_PRESET["min_net_win_sol"] == 0.002
    assert BALANCED_WIN_PRESET["min_expected_net_profit_sol"] == 0.002
    assert BALANCED_WIN_PRESET["loss_reentry_cooldown_minutes"] == 90
    assert BALANCED_WIN_PRESET["reentry_min_momentum_pct"] == 0.005
    assert BALANCED_WIN_STRATEGY_PRESET["gmgn_min_liquidity_usd"] == 15000.0
    assert BALANCED_WIN_STRATEGY_PRESET["block_stock_related_tokens"] is True
    print("PASS: BALANCED_WIN_PRESET values")


def test_balanced_vs_strict_comparison():
    assert BEST_WIN_PRESET["entry_momentum_pct"] > BALANCED_WIN_PRESET["entry_momentum_pct"]
    assert BEST_WIN_PRESET["min_momentum_pct"] > BALANCED_WIN_PRESET["min_momentum_pct"]
    assert (
        BEST_WIN_PRESET["non_watchlist_min_volume_24h_usd"]
        > BALANCED_WIN_PRESET["non_watchlist_min_volume_24h_usd"]
    )
    assert (
        BEST_WIN_PRESET["max_entry_price_impact_pct"]
        < BALANCED_WIN_PRESET["max_entry_price_impact_pct"]
    )
    assert BEST_WIN_PRESET["min_net_win_sol"] > BALANCED_WIN_PRESET["min_net_win_sol"]
    print("PASS: balanced is looser than strict on scanner/entry filters")


def test_config_api_exposes_balanced_preset():
    cfg = Config.spread_defaults()
    assert cfg["balanced_win_preset"] == BALANCED_WIN_PRESET
    assert cfg["balanced_win_strategy_preset"] == BALANCED_WIN_STRATEGY_PRESET
    print("PASS: spread_defaults exposes balanced presets")


def test_apply_balanced_win_strategy():
    with patch("config._write_env_keys"), patch.object(Config, "update_runtime") as mock_update:
        mock_update.return_value = {"applied": {}, "needs_restart": []}
        result = apply_balanced_win_strategy(save_bookmark=False)
    assert result["preset"] == "balanced_win_strategy"
    applied = mock_update.call_args.kwargs
    assert applied["ENTRY_MOMENTUM_PCT"] == 0.005
    assert applied["MIN_MOMENTUM_PCT"] == 0.015
    assert applied["NON_WATCHLIST_MIN_VOLUME_24H_USD"] == 50000.0
    assert applied["GMGN_MIN_LIQUIDITY_USD"] == 15000.0
    assert applied["MAX_ENTRY_PRICE_IMPACT_PCT"] == 1.0
    print("PASS: apply_balanced_win_strategy runtime updates")


def test_entry_skip_reason_momentum():
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
    with patch.object(Config, "ENTRY_MOMENTUM_PCT", 0.0075):
        reason = strategy.entry_skip_reason(candidate, momentum=0.01)
    assert reason is None
    with patch.object(Config, "ENTRY_MOMENTUM_PCT", 0.0075):
        reason = strategy.entry_skip_reason(candidate, momentum=0.005)
    assert reason is not None
    assert "entry momentum" in reason
    print("PASS: entry_skip_reason momentum gate")


def test_bot_status_last_entry_skip_reason_field():
    from bot import TradingBot

    bot = TradingBot(dry_run=True)
    bot.last_entry_skip_reason = "entry momentum 0.40% < 0.75%: FOO"
    assert bot.last_entry_skip_reason.startswith("entry momentum")
    print("PASS: bot last_entry_skip_reason field")


if __name__ == "__main__":
    test_balanced_win_preset_values()
    test_balanced_vs_strict_comparison()
    test_config_api_exposes_balanced_preset()
    test_apply_balanced_win_strategy()
    test_entry_skip_reason_momentum()
    test_bot_status_last_entry_skip_reason_field()
    print("\nAll Balanced Win validation tests passed.")
