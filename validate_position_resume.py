"""Validate open-position persistence and resume across restarts."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config import (
    Config,
    is_wbtc_watchlist_mint,
    stop_loss_applies_for_mint,
    wbtc_hold_until_profit_mode,
)
from position_store import (
    clear_open_positions,
    has_open_positions,
    load_open_positions,
    position_from_dict,
    position_to_dict,
    save_open_positions,
)
from strategy import MomentumStrategy, Position, SignalType
from scanner import MoverCandidate

WBTC_MINT = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"


def _sample_position(**overrides) -> Position:
    data = {
        "mint": "So11111111111111111111111111111111111111112",
        "symbol": "TEST",
        "entry_price": 0.001,
        "entry_time": time.time() - 60,
        "size_sol": 0.05,
        "token_amount_raw": 50_000_000,
        "initial_token_amount_raw": 50_000_000,
        "remaining_token_amount_raw": 50_000_000,
        "token_decimals": 6,
        "tp_levels_hit": [],
        "tp_levels": [0.0325, 0.05],
        "tp_portions": [0.5, 0.5],
        "target_net_profit_sol": 0.01,
        "fee_budget_sol": 0.001,
        "estimated_fees_sol": 0.001,
        "fees_allocated_sol": 0.0,
        "realized_net_pnl_sol": 0.0,
        "momentum_at_entry": 12.5,
        "l1_protection_armed": False,
        "peak_pnl_pct": 0.01,
        "trough_pnl_pct": -0.005,
        "profile": {
            "momentum_pct": 12.5,
            "liquidity_usd": 80000,
            "volume_24h_usd": 120000,
            "price_change_5m": 5.0,
            "price_change_1h": 10.0,
        },
        "buy_count": 1,
    }
    data.update(overrides)
    return position_from_dict(data)


def test_round_trip_serialization():
    pos = _sample_position()
    restored = position_from_dict(position_to_dict(pos))
    assert restored.mint == pos.mint
    assert restored.symbol == pos.symbol
    assert abs(restored.entry_price - pos.entry_price) < 1e-12
    assert restored.remaining_token_amount_raw == pos.remaining_token_amount_raw
    assert restored.peak_pnl_pct == pos.peak_pnl_pct
    assert restored.profile["liquidity_usd"] == 80000
    print("PASS: round_trip_serialization")


def test_save_load_filter_by_mode():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_positions.json"
        with patch.object(Config, "OPEN_POSITIONS_STATE_PATH", str(path)):
            clear_open_positions()
            save_open_positions([_sample_position()], dry_run=True)
            assert has_open_positions(dry_run=True)
            assert not has_open_positions(dry_run=False)
            loaded = load_open_positions(dry_run=True)
            assert len(loaded) == 1
            assert loaded[0].symbol == "TEST"
            # Wrong mode filter returns empty without wiping file.
            assert load_open_positions(dry_run=False) == []
            assert path.exists()
    print("PASS: save_load_filter_by_mode")


def test_strategy_persist_on_open_and_close():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_positions.json"
        with patch.object(Config, "OPEN_POSITIONS_STATE_PATH", str(path)):
            clear_open_positions()
            strategy = MomentumStrategy()
            strategy._persist_dry_run = True
            candidate = MoverCandidate(
                mint="Mint111111111111111111111111111111111111111",
                symbol="MOON",
                name="Moon",
                pair_address="pair",
                dex="raydium",
                price_usd=0.002,
                liquidity_usd=50000,
                volume_24h_usd=90000,
                momentum_pct=20.0,
            )
            strategy.open_position(candidate, 0.002, 0.04, 20.0, token_amount_raw=1000)
            assert path.exists()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["dry_run"] is True
            assert len(data["positions"]) == 1
            pos = strategy.get_open_positions()[0]
            strategy.close_position(pos, 0.0021, SignalType.SELL_TIME)
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["positions"] == []
    print("PASS: strategy_persist_on_open_and_close")


def test_restore_into_strategy():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_positions.json"
        with patch.object(Config, "OPEN_POSITIONS_STATE_PATH", str(path)):
            save_open_positions(
                [_sample_position(symbol="A"), _sample_position(mint="B" * 32, symbol="B")],
                dry_run=True,
            )
            strategy = MomentumStrategy()
            restored = load_open_positions(dry_run=True)
            assert strategy.restore_positions(restored) == 2
            assert len(strategy.get_open_positions()) == 2
            assert {p.symbol for p in strategy.get_open_positions()} == {"A", "B"}
    print("PASS: restore_into_strategy")


def test_refuse_cross_mode_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_positions.json"
        with patch.object(Config, "OPEN_POSITIONS_STATE_PATH", str(path)):
            save_open_positions(
                [_sample_position(mint=WBTC_MINT, symbol="WBTC")],
                dry_run=False,
            )
            # Paper empty save must not wipe live books.
            save_open_positions([], dry_run=True)
            loaded = load_open_positions(dry_run=False)
            assert len(loaded) == 1
            assert loaded[0].symbol == "WBTC"
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["dry_run"] is False
            assert len(data["positions"]) == 1
    print("PASS: refuse_cross_mode_overwrite")


def test_stop_and_force_reset_preserve_books():
    from bot_manager import BotManager

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_positions.json"
        runtime = Path(tmp) / "bot_runtime_state.json"
        with patch.object(Config, "OPEN_POSITIONS_STATE_PATH", str(path)):
            save_open_positions(
                [_sample_position(mint=WBTC_MINT, symbol="WBTC")],
                dry_run=False,
            )
            mgr = BotManager()
            mgr._runtime_state_path = runtime
            mgr._dry_run = False
            mgr._private_key = "k"
            mgr._public_key = "p"
            mgr._status = "running"
            mgr.force_reset()
            assert path.exists()
            assert has_open_positions(dry_run=False)
            assert mgr._private_key is None
            st = mgr.get_status()
            assert st["open_positions_count"] == 1
            assert st["needs_resume"] is True
            assert "parked" in (st.get("activity_status") or "").lower()
            positions = mgr.get_positions()
            assert len(positions) == 1
            assert positions[0]["symbol"] == "WBTC"
    print("PASS: stop_and_force_reset_preserve_books")


def test_start_blocks_wrong_mode_and_skips_fee_on_resume():
    from bot_manager import BotManager

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_positions.json"
        fee_path = Path(tmp) / "live_start_fee_paid.json"
        with patch.object(Config, "OPEN_POSITIONS_STATE_PATH", str(path)):
            with patch.object(Config, "LIVE_START_FEE_PAID_PATH", str(fee_path)):
                with patch.object(Config, "FEE_ENABLED", True):
                    save_open_positions(
                        [_sample_position(mint=WBTC_MINT, symbol="WBTC")],
                        dry_run=False,
                    )
                    mgr = BotManager()
                    mgr.reset_to_idle(force=True)
                    # Paper start must refuse while live books are parked.
                    try:
                        mgr.start(dry_run=True)
                        raise AssertionError("expected RuntimeError for wrong mode")
                    except RuntimeError as exc:
                        assert "live" in str(exc).lower()

                    mgr._private_key = "unit-test-key"
                    mgr._public_key = "UnitTestPubkey"
                    with patch("bot_manager.Config.has_user_rpc", return_value=True):
                        with patch(
                            "bot_manager.Config.user_rpc_url",
                            return_value="https://mainnet.helius-rpc.com/?api-key=x",
                        ):
                            with patch.object(mgr, "get_balance", return_value=5.0):
                                with patch(
                                    "bot_manager.RiskManager.can_start_trading",
                                    return_value=(True, ""),
                                ):
                                    with patch.object(
                                        mgr, "_run_bot_thread", lambda *a, **k: None
                                    ):
                                        with patch(
                                            "live_start_fee._collect_async"
                                        ) as mock_chain:
                                            result = mgr.start(dry_run=False)
                    assert result["resuming_open_positions"] is True
                    assert result["live_start_fee"]["skipped"] is True
                    assert result["live_start_fee"]["reason"] == "resume_open_positions"
                    mock_chain.assert_not_called()
                    mgr.reset_to_idle(force=True)
                    mgr._private_key = None
                    mgr._public_key = None
    print("PASS: start_blocks_wrong_mode_and_skips_fee_on_resume")


def test_bot_restore_preserves_wbtc_exit_rules():
    from bot import TradingBot

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_positions.json"
        with patch.object(Config, "OPEN_POSITIONS_STATE_PATH", str(path)):
            pos = _sample_position(
                mint=WBTC_MINT,
                symbol="WBTC",
                entry_price=66553.0,
                peak_pnl_pct=0.008,
                trough_pnl_pct=-0.04,
                profile={"scanner_source": "watchlist_mint", "liquidity_usd": 1e6},
            )
            save_open_positions([pos], dry_run=True)
            bot = TradingBot(dry_run=True, private_key=None, stop_event=None)
            asyncio.run(bot._restore_persisted_positions())
            opens = bot.strategy.get_open_positions()
            assert len(opens) == 1
            restored = opens[0]
            assert restored.symbol == "WBTC"
            assert restored.peak_pnl_pct == 0.008
            assert is_wbtc_watchlist_mint(restored.mint)
            assert not stop_loss_applies_for_mint(restored.mint)
            assert wbtc_hold_until_profit_mode(restored.mint)
    print("PASS: bot_restore_preserves_wbtc_exit_rules")


def test_get_token_balance_raw_returns_none_on_rpc_failure():
    from solana_client import SolanaClient

    client = SolanaClient.__new__(SolanaClient)
    client.public_key = MagicMock()
    client.client = MagicMock()
    client.client.get_token_accounts_by_owner_json_parsed = AsyncMock(
        side_effect=RuntimeError("429 Too Many Requests")
    )
    result = asyncio.run(client.get_token_balance_raw(WBTC_MINT))
    assert result is None, f"expected None on RPC failure, got {result!r}"
    print("PASS: get_token_balance_raw returns None on RPC failure (not 0)")


def test_live_restore_keeps_unknown_drops_zero_reconciles_known():
    from bot import TradingBot

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "open_positions.json"
        with patch.object(Config, "OPEN_POSITIONS_STATE_PATH", str(path)):
            stored = _sample_position(
                mint=WBTC_MINT,
                symbol="WBTC",
                token_amount_raw=11782,
                initial_token_amount_raw=11782,
                remaining_token_amount_raw=11782,
            )
            save_open_positions([stored], dry_run=False)

            bot = TradingBot(dry_run=False, private_key=None, stop_event=None)
            bot.solana = MagicMock()

            # Unknown must keep book (never treat as not-held).
            bot.solana.get_token_balance_raw = AsyncMock(return_value=None)
            asyncio.run(bot._restore_persisted_positions())
            opens = bot.strategy.get_open_positions()
            assert len(opens) == 1
            assert opens[0].remaining_token_amount_raw == 11782
            disk = load_open_positions(dry_run=False)
            assert len(disk) == 1
            assert disk[0].remaining_token_amount_raw == 11782

            # Confirmed zero drops.
            save_open_positions([stored], dry_run=False)
            bot.strategy.positions = []
            bot.solana.get_token_balance_raw = AsyncMock(return_value=0)
            asyncio.run(bot._restore_persisted_positions())
            assert bot.strategy.get_open_positions() == []
            assert load_open_positions(dry_run=False) == []

            # Known > 0 reconciles qty.
            save_open_positions([stored], dry_run=False)
            bot.strategy.positions = []
            bot.solana.get_token_balance_raw = AsyncMock(return_value=12000)
            asyncio.run(bot._restore_persisted_positions())
            opens = bot.strategy.get_open_positions()
            assert len(opens) == 1
            assert opens[0].remaining_token_amount_raw == 12000
            assert opens[0].token_amount_raw == 12000
    print("PASS: live_restore keeps unknown, drops zero, reconciles known")


if __name__ == "__main__":
    test_round_trip_serialization()
    test_save_load_filter_by_mode()
    test_strategy_persist_on_open_and_close()
    test_restore_into_strategy()
    test_refuse_cross_mode_overwrite()
    test_stop_and_force_reset_preserve_books()
    test_start_blocks_wrong_mode_and_skips_fee_on_resume()
    test_bot_restore_preserves_wbtc_exit_rules()
    test_get_token_balance_raw_returns_none_on_rpc_failure()
    test_live_restore_keeps_unknown_drops_zero_reconciles_known()
    print("\nAll position resume validation tests passed.")
