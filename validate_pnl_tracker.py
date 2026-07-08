"""Validate unified running P&L tracker for paper and live sessions."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from app import app
from bot_manager import bot_manager
from pnl_tracker import PnlTracker


def _fresh_tracker() -> PnlTracker:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    path = Path(tmp.name)
    tmp.close()
    return PnlTracker(path=path)


def _sell_journal(pnl_sol: float, symbol: str = "JOBY", action: str = "sell") -> dict:
    return {
        "action": action,
        "pnl_sol": pnl_sol,
        "symbol": symbol,
        "timestamp": 1_700_000_000.0,
    }


def test_paper_sell_increments_running_pnl():
    tracker = _fresh_tracker()
    tracker.start_session("paper")
    tracker.record_from_journal(_sell_journal(0.02, "JOBY"))
    tracker.record_from_journal(_sell_journal(-0.01, "BONK", action="sell_partial"))

    pnl = tracker.get_running_pnl()
    assert abs(pnl["profit_sol"] - 0.02) < 1e-9
    assert abs(pnl["losses_sol"] - 0.01) < 1e-9
    assert abs(pnl["net_pnl_sol"] - 0.01) < 1e-9
    assert pnl["trade_count"] == 2
    assert pnl["mode"] == "paper"
    assert len(pnl["recent_trades"]) == 2
    assert pnl["recent_trades"][0]["symbol"] == "BONK"
    print("PASS: paper sell increments running P&L")


def test_live_sell_increments_running_pnl():
    tracker = _fresh_tracker()
    tracker.start_session("live")
    tracker.record_from_journal(_sell_journal(0.05, "WIF"))

    pnl = tracker.get_running_pnl()
    assert pnl["mode"] == "live"
    assert abs(pnl["profit_sol"] - 0.05) < 1e-9
    assert pnl["trade_count"] == 1
    print("PASS: live sell increments running P&L")


def test_net_equals_profit_minus_losses():
    tracker = _fresh_tracker()
    tracker.start_session("paper")
    tracker.record_from_journal(_sell_journal(0.1))
    tracker.record_from_journal(_sell_journal(-0.03))
    tracker.record_from_journal(_sell_journal(0.02))

    pnl = tracker.get_running_pnl()
    assert abs(pnl["net_pnl_sol"] - (pnl["profit_sol"] - pnl["losses_sol"])) < 1e-9
    assert abs(pnl["net_pnl_sol"] - 0.09) < 1e-9
    print("PASS: net equals profit minus losses")


def test_reset_on_new_start():
    tracker = _fresh_tracker()
    tracker.start_session("paper")
    tracker.record_from_journal(_sell_journal(0.1))
    tracker.end_session()
    tracker.start_session("live")

    pnl = tracker.get_running_pnl()
    assert pnl["trade_count"] == 0
    assert pnl["profit_sol"] == 0.0
    assert pnl["losses_sol"] == 0.0
    assert pnl["mode"] == "live"
    print("PASS: reset on new start")


def test_persistence_while_running():
    tracker = _fresh_tracker()
    tracker.start_session("paper")
    tracker.record_from_journal(_sell_journal(0.03, "PEPE"))

    reloaded = PnlTracker(path=tracker._path)
    pnl = reloaded.get_running_pnl()
    assert pnl["active"] is True
    assert pnl["trade_count"] == 1
    assert abs(pnl["profit_sol"] - 0.03) < 1e-9
    print("PASS: persistence while running")


def test_running_pnl_history_appends():
    tracker = _fresh_tracker()
    tracker.start_session("paper")
    tracker.record_from_journal(_sell_journal(0.01))
    tracker.record_from_journal(_sell_journal(0.02))

    history = tracker.get_running_pnl()["running_pnl_history"]
    assert len(history) == 2
    assert abs(history[-1]["cumulative_net"] - 0.03) < 1e-9
    print("PASS: running P&L history appends")


def test_status_api_includes_running_pnl():
    tracker = _fresh_tracker()
    with patch("bot_manager.pnl_tracker", tracker), patch("app.pnl_tracker", tracker):
        tracker.start_session("live")
        tracker.record_from_journal(_sell_journal(0.04, "SOL"))

        client = app.test_client()
        with patch.object(bot_manager, "_status", "running"):
            with patch.object(bot_manager, "_dry_run", False):
                status = client.get("/api/bot/status").get_json()

        assert "running_pnl" in status
        assert status["running_pnl"]["mode"] == "live"
        assert status["running_pnl"]["trade_count"] == 1

        pnl_only = client.get("/api/pnl").get_json()
        assert pnl_only["trade_count"] == 1
    print("PASS: status API includes running_pnl")


def test_bot_start_resets_pnl_tracker():
    tracker = _fresh_tracker()
    with patch("bot_manager.pnl_tracker", tracker):
        tracker.start_session("paper")
        tracker.record_from_journal(_sell_journal(0.2))

        with patch.object(bot_manager, "_status", "stopped"):
            with patch.object(bot_manager, "_run_bot_thread"):
                with patch.object(bot_manager, "_resolve_private_key", return_value="fake-key"):
                    with patch.object(bot_manager, "get_balance", return_value=1.0):
                        bot_manager.start(dry_run=False)

        pnl = tracker.get_running_pnl()
        assert pnl["trade_count"] == 0
        assert pnl["mode"] == "live"
        bot_manager.stop()
    print("PASS: bot start resets pnl tracker")


def main():
    test_paper_sell_increments_running_pnl()
    test_live_sell_increments_running_pnl()
    test_net_equals_profit_minus_losses()
    test_reset_on_new_start()
    test_persistence_while_running()
    test_running_pnl_history_appends()
    test_status_api_includes_running_pnl()
    test_bot_start_resets_pnl_tracker()
    print("\nAll running P&L validation tests passed.")


if __name__ == "__main__":
    main()
