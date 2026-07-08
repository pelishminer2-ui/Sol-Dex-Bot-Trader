"""Validate Jul 6 session-learned strategy changes."""

import time
from unittest.mock import patch

from config import (
    BEST_WIN_PRESET,
    DEFAULT_WATCHLIST_MINT,
    Config,
    effective_stop_loss_pct,
)
from gmgn_scanner import parse_gmgn_token
from scanner import MoverCandidate, merge_candidates
from strategy import MomentumStrategy, SignalType


def _candidate(
    mint: str = "mint123456789",
    symbol: str = "TEST",
    *,
    source: str = "dexscreener",
) -> MoverCandidate:
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
        source=source,
    )


def test_best_win_preset_session_values():
    assert BEST_WIN_PRESET["entry_momentum_pct"] == 0.0075
    assert BEST_WIN_PRESET["stop_loss_pct"] == 0.015
    assert BEST_WIN_PRESET["min_volume_24h_usd"] == 75000.0
    assert BEST_WIN_PRESET["loss_reentry_cooldown_minutes"] == 120
    assert BEST_WIN_PRESET["loss_reentry_repeat_cooldown_minutes"] == 240
    assert BEST_WIN_PRESET["reentry_min_momentum_pct"] == 0.015
    assert BEST_WIN_PRESET["max_entry_price_impact_pct"] == 0.50
    print("PASS: BEST_WIN_PRESET session values")


def test_gmgn_merge_filters_low_liquidity():
    low = MoverCandidate(
        mint="GmgnLowLiq",
        symbol="LOW",
        name="LOW",
        pair_address="",
        dex="pump",
        price_usd=1.0,
        liquidity_usd=18000.0,
        volume_24h_usd=100000.0,
        momentum_pct=0.08,
        price_change_5m=0.08,
        price_change_1h=0.05,
        source="gmgn",
    )
    merged = merge_candidates([low])
    assert merged == []
    print("PASS: merge_candidates drops GMGN below $25k liquidity")


def test_parse_gmgn_token_rejects_between_15k_and_20k_liquidity():
    with patch.object(Config, "MIN_LIQUIDITY_USD", 15000.0), patch.object(
        Config, "GMGN_MIN_LIQUIDITY_USD", None
    ):
        token = {
            "address": "GmgnMint1111111111111111111111111111111",
            "symbol": "GMGN",
            "name": "GMGN Token",
            "price": 0.01,
            "liquidity": 18000,
            "volume": 100000,
            "price_change_percent5m": 2.5,
            "price_change_percent1h": 1.5,
        }
        assert parse_gmgn_token(token) is None
    print("PASS: parse_gmgn_token rejects liquidity below $25k floor")


def test_per_mint_stop_loss_split():
    with patch.object(Config, "STOP_LOSS_PCT", 0.015), patch.object(
        Config, "WBTC_STOP_LOSS_PCT", 0.02
    ):
        assert effective_stop_loss_pct("randommint") == 0.015
        assert effective_stop_loss_pct(DEFAULT_WATCHLIST_MINT) == 0.02
    print("PASS: per-mint stop loss (1.5% memecoin, 2% WBTC)")


def test_wbtc_skips_30m_negative_forced_exit():
    strategy = MomentumStrategy()
    candidate = _candidate(mint=DEFAULT_WATCHLIST_MINT, symbol="WBTC")
    pos = strategy.open_position(
        candidate, 1.0, 0.10, 0.0, token_amount_raw=1_000_000
    )
    pos.entry_time = time.time() - 31 * 60
    with patch.object(Config, "ENABLE_LADDER_TIME_EXITS", True), patch.object(
        Config, "LADDER_MISSED_NEGATIVE_DCA_MINUTES", 30
    ), patch.object(Config, "TIME_STOP_MINUTES", 120), patch.object(
        Config, "STOP_LOSS_PCT", 0.015
    ), patch.object(Config, "WBTC_STOP_LOSS_PCT", 0.02):
        signal = strategy.evaluate_exit(pos, 0.999)
    assert signal is None
    print("PASS: WBTC skips sell_ladder_missed_30m_negative")


def test_memecoin_still_gets_30m_negative_exit():
    strategy = MomentumStrategy()
    pos = strategy.open_position(
        _candidate(), 1.0, 0.10, 0.05, token_amount_raw=1_000_000
    )
    pos.entry_time = time.time() - 31 * 60
    with patch.object(Config, "ENABLE_LADDER_TIME_EXITS", True), patch.object(
        Config, "LADDER_MISSED_NEGATIVE_DCA_MINUTES", 30
    ), patch.object(Config, "TIME_STOP_MINUTES", 120), patch.object(
        Config, "MAX_BUYS_PER_MINT", 1
    ):
        signal = strategy.evaluate_exit(pos, 0.99)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_LADDER_MISSED_30M
    print("PASS: non-WBTC still exits on 30m ladder miss when negative")


def test_repeat_loss_cooldown_uses_240_minutes():
    strategy = MomentumStrategy()
    mint = "repeatlossmint12345"
    with patch.object(Config, "LOSS_REENTRY_COOLDOWN_MINUTES", 120), patch.object(
        Config, "LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES", 240
    ):
        strategy.record_loss_reentry_cooldown(mint)
        assert strategy.is_on_loss_reentry_cooldown(mint)
        strategy._loss_reentry_until.clear()
        strategy.record_loss_reentry_cooldown(mint)
        until = strategy._loss_reentry_until[mint]
        assert until >= time.time() + 239 * 60
        assert strategy._loss_session_count[mint] == 2
    print("PASS: repeat loss cooldown uses 240 min on 2nd session loss")


if __name__ == "__main__":
    test_best_win_preset_session_values()
    test_gmgn_merge_filters_low_liquidity()
    test_parse_gmgn_token_rejects_between_15k_and_20k_liquidity()
    test_per_mint_stop_loss_split()
    test_wbtc_skips_30m_negative_forced_exit()
    test_memecoin_still_gets_30m_negative_exit()
    test_repeat_loss_cooldown_uses_240_minutes()
    print("\nAll session-learning validation tests passed.")
