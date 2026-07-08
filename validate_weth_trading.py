"""Validate WETH trading at memecoin standards."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from config import WETH_MINT, is_weth_trade_mint, weth_trading_enabled
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType
from weth_trading import (
    fetch_weth_trade_candidate,
    merge_weth_trade_watchlist,
    probe_weth_trade_status,
    weth_entry_qualifies,
    weth_entry_skip_reason,
)


def test_weth_disabled_by_default_env():
    with patch.object(__import__("config").Config, "ENABLE_WETH_TRADING", False):
        assert weth_trading_enabled() is False
        assert is_weth_trade_mint(WETH_MINT) is False
    print("PASS: WETH disabled when ENABLE_WETH_TRADING=false")


def test_weth_enabled():
    with patch.object(__import__("config").Config, "ENABLE_WETH_TRADING", True), patch.object(
        __import__("config").Config, "WETH_MINT", WETH_MINT
    ):
        assert weth_trading_enabled() is True
        assert is_weth_trade_mint(WETH_MINT) is True
        assert is_weth_trade_mint("other") is False
    print("PASS: WETH mint recognized when enabled")


def test_entry_qualifies_on_momentum():
    feed = MagicMock()
    feed.get_momentum.return_value = 0.008
    with patch.object(__import__("config").Config, "ENABLE_WETH_TRADING", True), patch.object(
        __import__("config").Config, "WETH_MINT", WETH_MINT
    ), patch.object(
        __import__("config").Config, "ENTRY_MOMENTUM_PCT", 0.005
    ), patch.object(
        __import__("config").Config, "HOT_MARKET_MODE_ENABLED", False
    ):
        assert weth_entry_qualifies(feed, 3500.0) is True
        feed.get_momentum.return_value = 0.002
        assert weth_entry_qualifies(feed, 3500.0) is False
    print("PASS: WETH entry uses memecoin momentum gate")


def test_entry_skip_reason():
    feed = MagicMock()
    feed.get_momentum.return_value = 0.001
    with patch.object(__import__("config").Config, "ENABLE_WETH_TRADING", True), patch.object(
        __import__("config").Config, "WETH_MINT", WETH_MINT
    ), patch.object(
        __import__("config").Config, "ENTRY_MOMENTUM_PCT", 0.005
    ):
        reason = weth_entry_skip_reason(feed, 3500.0)
        assert reason and "WETH" in reason and "momentum" in reason
    print("PASS: WETH entry skip reason")


def test_strategy_buy_on_momentum():
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
        pool_created_at=None,
        scanned_at=0.0,
        source="weth_trade",
    )
    strategy = MomentumStrategy()
    with patch.object(__import__("config").Config, "ENABLE_WETH_TRADING", True), patch.object(
        __import__("config").Config, "WETH_MINT", WETH_MINT
    ), patch.object(
        __import__("config").Config, "ENTRY_MOMENTUM_PCT", 0.005
    ), patch.object(
        __import__("config").Config, "HOT_MARKET_MODE_ENABLED", False
    ):
        signal = strategy.evaluate_entry(candidate, 3500.0, 0.008)
    assert signal == SignalType.BUY
    print("PASS: strategy buys WETH on momentum")


def test_fetch_and_merge_mocked():
    feed = MagicMock()
    feed.update.return_value = {WETH_MINT: 3600.0}
    feed.get_momentum.return_value = 0.006
    pair = {
        "chainId": "solana",
        "baseToken": {"symbol": "WETH", "name": "Wrapped Ether"},
        "liquidity": {"usd": 8000000},
        "volume": {"h24": 2000000},
        "priceChange": {"h1": 0.8},
        "pairAddress": "pair",
        "dexId": "orca",
    }
    with patch.object(__import__("config").Config, "ENABLE_WETH_TRADING", True), patch.object(
        __import__("config").Config, "WETH_MINT", WETH_MINT
    ), patch("weth_trading._best_pair_for_mint", return_value=pair):
        status = probe_weth_trade_status(feed)
        assert status["enabled"] is True
        assert status["mint"] == WETH_MINT
        assert status["symbol"] == "WETH"
        candidate = fetch_weth_trade_candidate(feed, status=status)
        assert candidate is not None
        assert candidate.source == "weth_trade"
        merged = merge_weth_trade_watchlist([], feed)
        assert merged and merged[0].mint == WETH_MINT
    print("PASS: fetch/merge WETH candidate")


def main() -> int:
    test_weth_disabled_by_default_env()
    test_weth_enabled()
    test_entry_qualifies_on_momentum()
    test_entry_skip_reason()
    test_strategy_buy_on_momentum()
    test_fetch_and_merge_mocked()
    print("\nAll WETH trading tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
