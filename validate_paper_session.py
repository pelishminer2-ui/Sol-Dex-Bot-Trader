"""Validate 24-hour paper session timer and P&L tracking."""

import asyncio
import json
import os
import tempfile
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

from app import app
from bot import TradingBot
from bot_manager import bot_manager
from config import Config
from paper_session import PaperSession, PaperSessionManager, paper_session_manager
from pnl_tracker import PnlSession, PnlTracker, pnl_tracker


def _reset_manager() -> None:
    with paper_session_manager._lock:
        paper_session_manager._session = PaperSession()
        paper_session_manager._last_session = PaperSession()
    with pnl_tracker._lock:
        pnl_tracker._session = PnlSession()
        pnl_tracker._last_session = PnlSession()


def test_session_starts_on_paper_bot_start():
    _reset_manager()
    with patch.object(bot_manager, "_status", "stopped"):
        with patch.object(bot_manager, "_run_bot_thread"):
            bot_manager.start(dry_run=True)
            assert paper_session_manager.is_active()
            stats = paper_session_manager.get_session_stats()
            assert stats["paper_session_active"] is True
            assert stats["paper_session_started_at"] is not None
            # Default PAPER_SESSION_HOURS=0 => continuous (unlimited).
            if Config.PAPER_SESSION_HOURS <= 0:
                assert stats.get("paper_session_unlimited") is True
                assert stats["paper_session_remaining_sec"] is None
            else:
                assert stats["paper_session_remaining_sec"] > 0
            bot_manager.stop()
    print("PASS: session starts on paper bot start")


def test_pnl_accumulates_on_paper_sells():
    _reset_manager()
    pnl_tracker.start_session("paper")
    paper_session_manager.start_session()
    paper_session_manager.record_paper_pnl(0.05)
    paper_session_manager.record_paper_pnl(-0.02)
    paper_session_manager.record_paper_pnl(0.01)
    stats = paper_session_manager.get_session_stats()
    assert abs(stats["paper_session_profit_sol"] - 0.06) < 1e-9
    assert abs(stats["paper_session_losses_sol"] - 0.02) < 1e-9
    assert abs(stats["paper_session_net_pnl_sol"] - 0.04) < 1e-9
    assert stats["paper_session_trade_count"] == 3
    print("PASS: PnL accumulates on paper sells")


def test_session_resets_on_new_paper_start():
    _reset_manager()
    pnl_tracker.start_session("paper")
    paper_session_manager.start_session()
    paper_session_manager.record_paper_pnl(0.1)
    paper_session_manager.end_session()
    pnl_tracker.end_session()
    pnl_tracker.start_session("paper")
    paper_session_manager.start_session()
    stats = paper_session_manager.get_session_stats()
    assert stats["paper_session_trade_count"] == 0
    assert stats["paper_session_profit_sol"] == 0.0
    assert stats["paper_session_active"] is True
    print("PASS: session resets on new paper start")


def test_expired_session_detection():
    t = [1000.0]
    mgr = PaperSessionManager(clock=lambda: t[0])
    with patch.object(Config, "PAPER_SESSION_HOURS", 0.001):
        mgr.start_session()
        assert mgr.is_session_expired() is False
        t[0] += 4.0
        assert mgr.is_session_expired() is True
        assert mgr.remaining_sec() == 0.0
    print("PASS: expired session detection")


def test_expired_session_triggers_stop():
    t = [1000.0]
    mgr = PaperSessionManager(clock=lambda: t[0])

    async def _run():
        bot = TradingBot(dry_run=True)
        bot.running = True
        bot.solana = MagicMock()
        bot.jupiter = MagicMock()
        bot.strategy = MagicMock()
        bot.strategy.get_open_position = MagicMock(return_value=None)
        bot._close_for_session_expiry = AsyncMock()

        with patch("bot.paper_session_manager", mgr):
            with patch.object(Config, "PAPER_SESSION_HOURS", 0.001):
                mgr.start_session()
                t[0] += 10.0
                if bot.dry_run and mgr.is_session_expired():
                    await bot._close_for_session_expiry()
                    bot.running = False

        assert bot.running is False
        bot._close_for_session_expiry.assert_awaited_once()

    asyncio.run(_run())
    print("PASS: expired session triggers stop")


def test_live_mode_ignores_paper_session():
    _reset_manager()

    class _Fee:
        skipped = True

        def to_dict(self):
            return {"skipped": True}

    with patch.object(bot_manager, "_status", "stopped"):
        with patch.object(bot_manager, "_run_bot_thread"):
            with patch.object(bot_manager, "_resolve_private_key", return_value="fake-key"):
                with patch.object(bot_manager, "get_balance", return_value=1.0):
                    with patch("live_start_fee.collect_live_start_fee", return_value=_Fee()):
                        bot_manager.start(dry_run=False)
                        assert paper_session_manager.is_active() is False
                        stats = bot_manager.get_status()
                        assert stats["paper_session_active"] is False
                        assert stats["paper_session_started_at"] is None
                        bot_manager.stop()
    print("PASS: live mode ignores paper session timer")


def test_status_api_includes_paper_session_fields():
    _reset_manager()
    client = app.test_client()
    pnl_tracker.start_session("paper")
    paper_session_manager.start_session()
    paper_session_manager.record_paper_pnl(0.03)
    paper_session_manager.record_paper_pnl(-0.01)

    alive = threading.Event()

    def _hang():
        alive.wait()

    live_thread = threading.Thread(target=_hang, daemon=True)
    live_thread.start()
    try:
        with patch.object(bot_manager, "_status", "running"):
            with patch.object(bot_manager, "_dry_run", True):
                with patch.object(bot_manager, "_thread", live_thread):
                    status = client.get("/api/bot/status").get_json()
    finally:
        alive.set()
        live_thread.join(timeout=2)

    for key in (
        "paper_session_active",
        "paper_session_started_at",
        "paper_session_remaining_sec",
        "paper_session_profit_sol",
        "paper_session_losses_sol",
        "paper_session_net_pnl_sol",
        "paper_session_trade_count",
        "running_pnl",
    ):
        assert key in status, f"missing {key}"

    assert status["paper_session_active"] is True
    assert abs(status["paper_session_profit_sol"] - 0.03) < 1e-9
    assert abs(status["paper_session_losses_sol"] - 0.01) < 1e-9
    assert status["paper_session_trade_count"] == 2
    assert status["running_pnl"]["trade_count"] == 2
    assert status["running_pnl"]["mode"] == "paper"
    assert "recent_paper_trades" in status
    assert status["paper_session_status"] == "active"

    session_start = paper_session_manager._session_start_time()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "action": "sell",
                    "mint": "PaperMint123456789",
                    "symbol": "PAPER",
                    "pnl_sol": 0.03,
                    "paper_trade": True,
                    "timestamp": session_start + 1,
                }
            )
            + "\n"
        )
        journal_path = f.name

    try:
        with patch.object(Config, "TRADE_JOURNAL_PATH", journal_path):
            session = client.get("/api/paper/session").get_json()
            assert session["paper_session_active"] is True
            assert session["trade_count"] == 2
            assert len(session["recent_paper_trades"]) >= 1
            assert session["recent_paper_trades"][0]["symbol"] == "PAPER"

            export = client.get("/api/paper/export")
            assert export.status_code == 200
            assert "token_symbol" in export.get_data(as_text=True)
    finally:
        os.unlink(journal_path)

    paper_session_manager.end_session()
    last = paper_session_manager.get_session_stats()
    assert last["paper_session_active"] is False
    assert last["paper_session_trade_count"] == 2
    print("PASS: status API includes paper session fields")


def test_config_includes_paper_session_hours():
    cfg = Config.to_dict()
    assert "paper_session_hours" in cfg
    assert cfg["paper_session_hours"] == Config.PAPER_SESSION_HOURS
    print("PASS: config includes paper_session_hours")


def main():
    test_session_starts_on_paper_bot_start()
    test_pnl_accumulates_on_paper_sells()
    test_session_resets_on_new_paper_start()
    test_expired_session_detection()
    test_expired_session_triggers_stop()
    test_live_mode_ignores_paper_session()
    test_status_api_includes_paper_session_fields()
    test_config_includes_paper_session_hours()
    print("\nAll paper session validation tests passed.")


if __name__ == "__main__":
    main()
