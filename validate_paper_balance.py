"""Validate paper simulated balance tracking and auto-stop on depletion."""

import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from bot import TradingBot
from bot_manager import bot_manager
from config import Config
from paper_session import PaperSession, PaperSessionManager, paper_session_manager
from pnl_tracker import PnlSession, PnlTracker, pnl_tracker
from risk import RiskManager
from trade_activity import trade_activity


def _reset_manager() -> None:
    with paper_session_manager._lock:
        paper_session_manager._target_balance_sol = 0.75
        Config.PAPER_SIMULATED_BALANCE_SOL = 0.75
        paper_session_manager._session = PaperSession()
        paper_session_manager._last_session = PaperSession()
    with pnl_tracker._lock:
        pnl_tracker._session = PnlSession()
        pnl_tracker._last_session = PnlSession()


def test_session_starts_with_initial_balance():
    _reset_manager()
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        paper_session_manager.set_target_balance(0.75)
        paper_session_manager.start_session()
        assert abs(paper_session_manager.get_simulated_balance() - 0.75) < 1e-9
        stats = paper_session_manager.get_session_stats()
        assert abs(stats["paper_simulated_balance_sol"] - 0.75) < 1e-9
        assert stats["paper_stop_reason"] is None
    print("PASS: session starts with initial balance")


def test_buy_and_sell_update_running_balance():
    _reset_manager()
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        paper_session_manager.start_session()
        paper_session_manager.record_buy(0.05)
        assert abs(paper_session_manager.get_simulated_balance() - 0.70) < 1e-9
        paper_session_manager.record_sell(0.048)
        assert abs(paper_session_manager.get_simulated_balance() - 0.748) < 1e-9
    print("PASS: buy and sell update running balance")


def test_insufficient_balance_detection():
    _reset_manager()
    mgr = PaperSessionManager()
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
            with patch.object(Config, "TRADE_SIZE_SOL", 0.05):
                mgr.start_session()
                trade_size = RiskManager().compute_trade_size(0.0, dry_run=True)
                assert trade_size > 0
                assert mgr.is_balance_insufficient_for_entry(trade_size) is False

                # Drain until next entry would fail
                for _ in range(14):
                    mgr.record_buy(0.05)
                assert mgr.is_balance_insufficient_for_entry(trade_size) is True
    print("PASS: insufficient balance detection")


def test_balance_depletion_ends_session_preserves_stats():
    _reset_manager()
    pnl_tracker.start_session("paper")
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        paper_session_manager.start_session()
        paper_session_manager.record_paper_pnl(0.02)
        paper_session_manager.record_paper_pnl(-0.01)
        paper_session_manager.record_buy(0.75)

        paper_session_manager.end_session(stop_reason="balance_depleted")
        stats = paper_session_manager.get_session_stats()
        assert stats["paper_session_active"] is False
        assert stats["paper_session_status"] == "ended"
        assert stats["paper_stop_reason"] == "balance_depleted"
        assert stats["paper_session_trade_count"] == 2
        assert abs(stats["paper_session_profit_sol"] - 0.02) < 1e-9
        assert abs(stats["paper_session_losses_sol"] - 0.01) < 1e-9
        assert stats["paper_simulated_balance_sol"] <= Config.MIN_SOL_RESERVE + 0.05
    print("PASS: balance depletion ends session and preserves stats")


def test_entry_attempt_triggers_bot_stop():
    _reset_manager()

    async def _run():
        bot = TradingBot(dry_run=True)
        bot.running = True
        bot.solana = MagicMock()
        bot.solana.get_balance = AsyncMock(return_value=0.0)
        bot.jupiter = MagicMock()
        bot.strategy = MagicMock()
        bot.strategy.positions = []
        bot.strategy.can_open_more = MagicMock(return_value=True)
        bot._stop_for_paper_balance_depletion = AsyncMock(
            wraps=bot._stop_for_paper_balance_depletion
        )

        candidate = MagicMock()
        candidate.symbol = "TEST"
        candidate.mint = "TestMint"
        candidate.liquidity_usd = 100_000.0
        candidate.source = "dex"

        with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
            with patch.object(Config, "TRADE_SIZE_SOL", 0.05):
                paper_session_manager.set_target_balance(0.75)
                paper_session_manager.start_session()
                paper_session_manager.record_buy(0.74)
                result = await bot._execute_entry(candidate, 1.0, 0.01)
                assert result is False
                bot._stop_for_paper_balance_depletion.assert_awaited_once()
                assert bot.running is False

        stats = paper_session_manager.get_session_stats()
        assert stats["paper_stop_reason"] == "balance_depleted"

    asyncio.run(_run())
    print("PASS: entry attempt triggers bot stop on depletion")


def test_status_api_includes_balance_fields():
    _reset_manager()
    from app import app

    client = app.test_client()
    pnl_tracker.start_session("paper")
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        paper_session_manager.start_session()
        paper_session_manager.record_buy(0.10)
        paper_session_manager.end_session(stop_reason="balance_depleted")

        with patch.object(bot_manager, "_status", "stopped"):
            with patch.object(bot_manager, "_dry_run", True):
                status = client.get("/api/bot/status").get_json()
                session = client.get("/api/paper/session").get_json()

    assert "paper_simulated_balance_sol" in status
    assert "paper_stop_reason" in status
    assert status["paper_stop_reason"] == "balance_depleted"
    assert abs(status["paper_simulated_balance_sol"] - 0.65) < 1e-9
    assert session["paper_stop_reason"] == "balance_depleted"
    assert session["paper_session_trade_count"] == 0 or session["paper_session_status"] == "ended"
    print("PASS: status API includes balance fields")


def test_end_session_idempotent_after_balance_stop():
    _reset_manager()
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        paper_session_manager.start_session()
        paper_session_manager.record_paper_pnl(0.03)
        paper_session_manager.end_session(stop_reason="balance_depleted")
        first = paper_session_manager.get_session_stats()
        paper_session_manager.end_session(stop_reason="session_expired")
        second = paper_session_manager.get_session_stats()
        assert second["paper_stop_reason"] == "balance_depleted"
        assert second["paper_session_trade_count"] == first["paper_session_trade_count"]
    print("PASS: end_session idempotent after balance stop")


def test_entry_allowed_when_balance_drops_below_min_fund():
    """0.7476 SOL after one trade must not block the next affordable entry."""
    _reset_manager()
    risk = RiskManager()

    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
            with patch.object(Config, "TRADE_SIZE_SOL", 0.05):
                paper_session_manager.start_session()
                paper_session_manager.record_buy(0.0024)
                balance = paper_session_manager.get_simulated_balance()
                assert balance < Config.MIN_FUND_SOL
                assert abs(balance - 0.7476) < 1e-9

                ok, reason = risk.can_open_position(0, 0.0, dry_run=True)
                assert ok is True, reason
                trade_size = risk.compute_trade_size(0.0, dry_run=True)
                assert trade_size > 0
                assert paper_session_manager.is_balance_insufficient_for_entry(trade_size) is False

    print("PASS: entry allowed when balance drops below MIN_FUND_SOL during session")


def test_paper_session_hours_default_continuous():
    # Code default is 0 (continuous). Local .env may override — pin for this test.
    with patch.object(Config, "PAPER_SESSION_HOURS", 0):
        mgr = PaperSessionManager()
        mgr.start_session()
        assert mgr.is_unlimited() is True
        assert mgr.is_session_expired() is False
    with patch.object(Config, "PAPER_SESSION_HOURS", 24):
        mgr2 = PaperSessionManager()
        mgr2.start_session()
        remaining = mgr2.remaining_sec()
        assert abs(remaining - 24 * 3600) < 1.0
    print("PASS: paper session defaults to continuous (0 hours)")


def test_min_fund_waived_after_paper_buy():
    """Paper session buy waives MIN_FUND_SOL for restart while session has trades."""
    _reset_manager()
    risk = RiskManager()
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
            paper_session_manager.start_session()
            paper_session_manager.record_buy(0.05)
            assert paper_session_manager.get_simulated_balance() < Config.MIN_FUND_SOL
            ok, reason = risk.can_start_trading(None, dry_run=True)
            assert ok is True, reason
    print("PASS: paper buy waives min fund for session restart")


def _reset_trade_activity() -> None:
    with trade_activity._lock:
        trade_activity._session_trade_count = 0
        trade_activity._session_active = False
        trade_activity._last_trade_at = None


def test_journal_live_trade_waives_paper_restart():
    """Live journal trade within waiver window allows paper restart below MIN_FUND_SOL."""
    _reset_manager()
    _reset_trade_activity()
    now = time.time()
    journal = Path(tempfile.mktemp(suffix=".jsonl"))
    journal.write_text(
        json.dumps(
            {
                "action": "buy",
                "mint": "LiveMint",
                "timestamp": now - 300,
                "dry_run": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    risk = RiskManager()
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.50):
        with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
            with patch.object(Config, "TRADE_SIZE_SOL", 0.05):
                with patch.object(trade_activity, "_journal_path", journal):
                    with patch.object(trade_activity, "_clock", lambda: now):
                        trade_activity.refresh_from_journal()
                        assert trade_activity.min_fund_waived() is True
                        ok, reason = risk.can_start_trading(None, dry_run=True)
                        assert ok is True, reason
    journal.unlink(missing_ok=True)
    _reset_trade_activity()
    print("PASS: live journal trade waives paper restart below MIN_FUND_SOL")


def test_paper_bot_start_with_waiver_below_min_fund():
    """bot_manager.start(paper) succeeds when waiver active and balance below MIN_FUND_SOL."""
    _reset_manager()
    _reset_trade_activity()
    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
            with patch.object(Config, "TRADE_SIZE_SOL", 0.05):
                paper_session_manager.start_session()
                paper_session_manager.record_buy(0.05)
                paper_session_manager.end_session()
                assert paper_session_manager.get_simulated_balance() < Config.MIN_FUND_SOL
                with patch.object(bot_manager, "_status", "stopped"):
                    with patch.object(bot_manager, "_run_bot_thread"):
                        result = bot_manager.start(dry_run=True)
                        assert result["dry_run"] is True
                        bot_manager.stop()
    _reset_trade_activity()
    print("PASS: paper bot start with waiver below MIN_FUND_SOL")


def test_set_and_reset_paper_balance():
    """User can set configured paper balance and reset running balance to it."""
    _reset_manager()
    paper_session_manager.set_target_balance(1.25)
    assert abs(paper_session_manager.get_target_balance() - 1.25) < 1e-9
    assert abs(paper_session_manager.get_simulated_balance() - 1.25) < 1e-9

    paper_session_manager.start_session()
    paper_session_manager.record_buy(0.05)
    assert abs(paper_session_manager.get_simulated_balance() - 1.20) < 1e-9

    # Mid-session Set should apply immediately to the running wallet.
    paper_session_manager.set_target_balance(2.0)
    assert abs(paper_session_manager.get_simulated_balance() - 2.0) < 1e-9

    reset_to = paper_session_manager.reset_balance()
    assert abs(reset_to - 2.0) < 1e-9
    assert abs(paper_session_manager.get_simulated_balance() - 2.0) < 1e-9
    print("PASS: set and reset paper balance")


def test_api_set_paper_balance():
    from app import app

    _reset_manager()
    client = app.test_client()
    resp = client.post("/api/paper/balance", json={"amount": 2.0})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert abs(data["paper_target_balance_sol"] - 2.0) < 1e-9
    resp_high = client.post("/api/paper/balance", json={"amount": 6.0})
    assert resp_high.status_code == 400
    print("PASS: POST /api/paper/balance sets target balance and rejects above max")


def test_api_reset_paper_balance():
    from app import app

    _reset_manager()
    paper_session_manager.set_target_balance(1.5)
    paper_session_manager.start_session()
    paper_session_manager.record_buy(0.10)
    client = app.test_client()
    resp = client.post("/api/paper/balance/reset", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert abs(data["paper_simulated_balance_sol"] - 1.5) < 1e-9
    print("PASS: POST /api/paper/balance/reset restores configured balance")


def test_paper_balance_reset_on_stop():
    """Paper mode Stop Bot resets simulated balance to configured target."""
    from app import app

    _reset_manager()
    bot_manager.reset_to_idle(force=True)
    paper_session_manager.set_target_balance(2.0)
    paper_session_manager.start_session()
    paper_session_manager.record_buy(0.25)
    assert abs(paper_session_manager.get_simulated_balance() - 1.75) < 1e-9

    hold = threading.Event()

    def _worker(dry_run, key):
        hold.wait(timeout=5.0)

    with patch.object(bot_manager, "_run_bot_thread", side_effect=_worker):
        with patch.object(bot_manager, "get_balance", return_value=2.0):
            bot_manager.start(dry_run=True)
            hold.set()
            client = app.test_client()
            resp = client.post("/api/bot/stop", json={})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data.get("paper_balance_reset_on_stop") is True
            assert abs(data["paper_simulated_balance_sol"] - 2.0) < 1e-9

    assert abs(paper_session_manager.get_simulated_balance() - 2.0) < 1e-9
    print("PASS: paper balance resets to target on Stop Bot")


def test_restore_config_bookmark_api():
    from app import app
    from config import Config, ensure_config_bookmark, get_config_bookmark_info

    ensure_config_bookmark()
    info = get_config_bookmark_info()
    expected = info.get("values") or {}
    client = app.test_client()
    resp = client.post("/api/config/restore-bookmark", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["label"] == info.get("label")
    if "trade_size_sol" in expected:
        assert abs(Config.TRADE_SIZE_SOL - expected["trade_size_sol"]) < 1e-9
    if "stop_loss_pct" in expected:
        assert abs(Config.STOP_LOSS_PCT - expected["stop_loss_pct"]) < 1e-9
    if "min_liquidity_usd" in expected:
        assert abs(Config.MIN_LIQUIDITY_USD - expected["min_liquidity_usd"]) < 1e-9
    print("PASS: POST /api/config/restore-bookmark applies bookmark values")


def main():
    test_session_starts_with_initial_balance()
    test_buy_and_sell_update_running_balance()
    test_insufficient_balance_detection()
    test_entry_allowed_when_balance_drops_below_min_fund()
    test_min_fund_waived_after_paper_buy()
    test_journal_live_trade_waives_paper_restart()
    test_paper_bot_start_with_waiver_below_min_fund()
    test_paper_session_hours_default_continuous()
    test_balance_depletion_ends_session_preserves_stats()
    test_entry_attempt_triggers_bot_stop()
    test_status_api_includes_balance_fields()
    test_end_session_idempotent_after_balance_stop()
    test_set_and_reset_paper_balance()
    test_api_set_paper_balance()
    test_api_reset_paper_balance()
    test_paper_balance_reset_on_stop()
    test_restore_config_bookmark_api()
    print("\nAll paper balance validation tests passed.")


if __name__ == "__main__":
    main()
