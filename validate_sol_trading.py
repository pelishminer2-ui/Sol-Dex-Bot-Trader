"""Validate SOL self-trading via WSOL (default) or liquid-staking proxy."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from config import (
    DEFAULT_SOL_TRADE_MINT,
    JITOSOL_MINT,
    SOL_MINT,
    is_sol_trade_mint,
    is_wsol_trade_mint,
    sol_trading_enabled,
)
from scanner import MoverCandidate
from sol_trading import (
    fetch_sol_trade_candidate,
    sol_entry_qualifies,
    sol_trend_exit_cold,
    wsol_entry_qualifies,
    wsol_entry_skip_reason,
)
from strategy import MomentumStrategy, Position, SignalType


MSOL = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"
WSOL = SOL_MINT


def _hot_snapshot(h1: float = 0.8, h4: float = 0.2) -> dict:
    return {
        "data_available": True,
        "sol_trend_1h_pct": h1,
        "sol_trend_4h_pct": h4,
        "sol_price_usd": 150.0,
    }


def test_wsol_mint_enabled():
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", WSOL
    ):
        assert sol_trading_enabled() is True
        assert is_sol_trade_mint(WSOL) is True
        assert is_wsol_trade_mint(WSOL) is True
    print("PASS: WSOL mint enabled for SOL trading")


def test_msol_proxy_still_supported():
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", MSOL
    ):
        assert sol_trading_enabled() is True
        assert is_sol_trade_mint(MSOL) is True
        assert is_wsol_trade_mint(MSOL) is False
    print("PASS: mSOL proxy still supported")


def test_default_mint_is_jitosol():
    assert DEFAULT_SOL_TRADE_MINT == JITOSOL_MINT
    print("PASS: default SOL_TRADE_MINT is JitoSOL")


def test_jitosol_proxy_enabled():
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", JITOSOL_MINT
    ):
        assert sol_trading_enabled() is True
        assert is_sol_trade_mint(JITOSOL_MINT) is True
        assert is_wsol_trade_mint(JITOSOL_MINT) is False
    print("PASS: JitoSOL mint enabled for SOL trading")


def test_wsol_entry_qualifies_on_momentum():
    feed = MagicMock()
    feed.get_momentum.return_value = 0.006
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", WSOL
    ), patch.object(
        __import__("config").Config, "ENTRY_MOMENTUM_PCT", 0.005
    ), patch.object(
        __import__("config").Config, "HOT_MARKET_MODE_ENABLED", False
    ):
        assert wsol_entry_qualifies(feed, 150.0) is True
        feed.get_momentum.return_value = 0.003
        assert wsol_entry_qualifies(feed, 150.0) is False
    print("PASS: WSOL entry gate uses memecoin momentum")


def test_proxy_entry_qualifies_on_hot_1h():
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", MSOL
    ), patch.object(
        __import__("config").Config, "SOL_TRADE_MIN_MOMENTUM_1H_PCT", 0.5
    ), patch.object(
        __import__("config").Config, "HOT_MARKET_MODE_ENABLED", False
    ):
        assert sol_entry_qualifies(_hot_snapshot(0.6, 0.1)) is True
        assert sol_entry_qualifies(_hot_snapshot(0.3, 0.1)) is False
    print("PASS: proxy SOL entry gate uses 1h momentum")


def test_wsol_strategy_buy_on_momentum():
    candidate = MoverCandidate(
        mint=WSOL,
        symbol="WSOL",
        name="WSOL (SOL exposure)",
        pair_address="",
        dex="orca",
        price_usd=150.0,
        liquidity_usd=1_000_000.0,
        volume_24h_usd=500_000.0,
        momentum_pct=0.006,
        price_change_5m=0.0,
        price_change_1h=0.006,
        pool_created_at=None,
        scanned_at=0.0,
        source="sol_trade",
    )
    strategy = MomentumStrategy()
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", WSOL
    ), patch.object(
        __import__("config").Config, "ENTRY_MOMENTUM_PCT", 0.005
    ), patch.object(
        __import__("config").Config, "HOT_MARKET_MODE_ENABLED", False
    ):
        signal = strategy.evaluate_entry(
            candidate, 150.0, 0.006, sol_trend_snapshot=_hot_snapshot()
        )
    assert signal == SignalType.BUY
    print("PASS: strategy buys WSOL when memecoin momentum met")


def test_wsol_strategy_instant_exit_at_5pct():
    strategy = MomentumStrategy()
    pos = Position(
        mint=WSOL,
        symbol="WSOL",
        entry_price=100.0,
        entry_time=0.0,
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", WSOL
    ), patch.object(
        __import__("config").Config, "INSTANT_PROFIT_EXIT_ENABLED", True
    ), patch.object(
        __import__("config").Config, "INSTANT_PROFIT_EXIT_PCT", 0.05
    ):
        exit_sig = strategy.evaluate_exit(
            pos, 105.5, sol_trend_snapshot=_hot_snapshot()
        )
    assert exit_sig is not None
    assert exit_sig.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: WSOL instant exit at +5% (memecoin standard)")


def test_proxy_instant_exit_at_3pct():
    strategy = MomentumStrategy()
    pos = Position(
        mint=MSOL,
        symbol="SOL",
        entry_price=100.0,
        entry_time=0.0,
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", MSOL
    ), patch.object(
        __import__("config").Config, "SOL_TRADE_INSTANT_EXIT_PCT", 0.03
    ), patch.object(
        __import__("config").Config, "INSTANT_PROFIT_EXIT_ENABLED", True
    ), patch.object(
        __import__("config").Config, "INSTANT_PROFIT_EXIT_PCT", 0.05
    ):
        exit_sig = strategy.evaluate_exit(
            pos, 103.5, sol_trend_snapshot=_hot_snapshot()
        )
    assert exit_sig is not None
    assert exit_sig.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: proxy SOL trade instant exit at +3%")


def test_trend_cold_exit_proxy_only():
    with patch.object(__import__("config").Config, "SOL_TRADE_EXIT_ON_TREND_COLD", True), patch.object(
        __import__("config").Config, "SOL_TRADE_EXIT_COLD_1H_PCT", 0.0
    ), patch.object(
        __import__("config").Config, "HOT_MARKET_MODE_ENABLED", False
    ):
        assert sol_trend_exit_cold(_hot_snapshot(-0.2, 0.5)) is True
        assert sol_trend_exit_cold(_hot_snapshot(0.5, 0.5)) is False
    print("PASS: SOL trend-cold exit (proxy)")


def test_fetch_wsol_candidate_mocked():
    feed = MagicMock()
    feed.update.return_value = {WSOL: 150.0}
    feed.get_momentum.return_value = 0.006
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", WSOL
    ), patch(
        "sol_trading._best_pair_for_mint",
        return_value={
            "chainId": "solana",
            "baseToken": {"symbol": "SOL", "name": "Wrapped SOL"},
            "liquidity": {"usd": 5000000},
            "volume": {"h24": 1000000},
            "priceChange": {"h1": 1.0},
            "pairAddress": "pair",
            "dexId": "orca",
        },
    ):
        candidate = fetch_sol_trade_candidate(
            feed, sol_snapshot=_hot_snapshot()
        )
    assert candidate is not None
    assert candidate.source == "sol_trade"
    assert candidate.symbol == "WSOL"
    assert candidate.mint == WSOL
    print("PASS: fetch WSOL trade candidate")


def test_wsol_skip_reason():
    feed = MagicMock()
    feed.get_momentum.return_value = 0.002
    with patch.object(__import__("config").Config, "ENABLE_SOL_TRADING", True), patch.object(
        __import__("config").Config, "SOL_TRADE_MINT", WSOL
    ), patch.object(
        __import__("config").Config, "ENTRY_MOMENTUM_PCT", 0.005
    ):
        reason = wsol_entry_skip_reason(feed, 150.0)
        assert reason and "WSOL" in reason
    print("PASS: WSOL entry skip reason")


def main() -> int:
    test_wsol_mint_enabled()
    test_msol_proxy_still_supported()
    test_default_mint_is_jitosol()
    test_jitosol_proxy_enabled()
    test_wsol_entry_qualifies_on_momentum()
    test_proxy_entry_qualifies_on_hot_1h()
    test_wsol_strategy_buy_on_momentum()
    test_wsol_strategy_instant_exit_at_5pct()
    test_proxy_instant_exit_at_3pct()
    test_trend_cold_exit_proxy_only()
    test_fetch_wsol_candidate_mocked()
    test_wsol_skip_reason()
    print("\nAll SOL trading tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
