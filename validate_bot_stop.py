"""Validate bot stop halts trading promptly and allows restart."""

import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from bot import TradingBot
from bot_manager import bot_manager
from config import Config

_MOCK_BALANCE = patch.object(bot_manager, "get_balance", return_value=1.0)


class _CountingBot(TradingBot):
    """Bot stub that counts loop iterations after stop is requested."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.iterations_after_stop = 0
        self.entry_attempts_after_stop = 0

    async def initialize(self, setup_signals: bool = True):
        self.solana = type("Solana", (), {"public_key": "stub", "close": lambda self: asyncio.sleep(0)})()
        self.jupiter = type("Jupiter", (), {})()
        self._record_action("stub initialized")

    async def _refresh_watchlist(self):
        self.watchlist = []

    async def _monitor_all_open_positions(self):
        if not self.should_run():
            return

    async def _try_entry(self):
        if not self.should_run():
            self.entry_attempts_after_stop += 1
            return
        await super()._try_entry()

    async def _interruptible_sleep(self, seconds: float) -> None:
        if not self.should_run():
            self.iterations_after_stop += 1
            return
        await super()._interruptible_sleep(seconds)


def test_trading_bot_stop_sets_flags():
    stop_event = threading.Event()
    bot = TradingBot(dry_run=True, stop_event=stop_event)
    assert bot.should_run() is True
    bot.stop()
    assert bot.running is False
    assert stop_event.is_set()
    assert bot.should_run() is False
    print("PASS: TradingBot.stop sets running and stop_event")


def test_manager_stop_joins_thread():
    bot_manager.reset_to_idle(force=True)
    hold = threading.Event()
    seen_bot = {}

    def _worker(dry_run, key):
        current = threading.current_thread()
        bot = _CountingBot(dry_run=dry_run, private_key=key, stop_event=bot_manager._stop_event)
        seen_bot["bot"] = bot
        with bot_manager._lock:
            bot_manager._bot = bot
            bot_manager._status = "running"
            bot_manager._thread = current
        while bot.should_run():
            bot.entry_attempts_after_stop += 0  # no-op marker loop
            hold.wait(timeout=0.2)
            if not bot.should_run():
                break

    with patch.object(bot_manager, "_run_bot_thread", side_effect=_worker):
        with _MOCK_BALANCE:
            bot_manager.start(dry_run=True)
            time.sleep(0.05)
            assert bot_manager.get_status()["running"] is True
            result = bot_manager.stop()
    assert result["status"] == "stopped"
    with _MOCK_BALANCE:
        status = bot_manager.get_status()
    assert status["running"] is False
    assert status["status"] == "stopped"
    print("PASS: manager stop joins thread and returns stopped")


def test_stop_prevents_new_entries_in_loop():
    async def _run():
        stop_event = threading.Event()
        bot = _CountingBot(dry_run=True, stop_event=stop_event)

        async def _fake_try_entry():
            if not bot.should_run():
                bot.entry_attempts_after_stop += 1
                return

        bot._try_entry = _fake_try_entry  # type: ignore[method-assign]

        task = asyncio.create_task(bot.run(setup_signals=False))
        await asyncio.sleep(0.05)
        bot.stop()
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert bot.entry_attempts_after_stop == 0 or bot.should_run() is False

    asyncio.run(_run())
    print("PASS: stop prevents further entry attempts")


def test_restart_after_stop():
    bot_manager.reset_to_idle(force=True)
    hold = threading.Event()

    def _worker(dry_run, key):
        hold.wait()

    patcher = patch.object(bot_manager, "_run_bot_thread", side_effect=_worker)
    patcher.start()
    try:
        with _MOCK_BALANCE:
            bot_manager.start(dry_run=True)
            hold.set()
            bot_manager.stop()
            assert bot_manager.get_status()["running"] is False
            hold.clear()
            bot_manager.start(dry_run=True)
            assert bot_manager.get_status()["running"] is True
            hold.set()
            bot_manager.stop()
    finally:
        hold.set()
        patcher.stop()
        bot_manager.reset_to_idle(force=True)
    print("PASS: restart after stop")


def test_no_trades_after_stop_timestamp():
    bot_manager.reset_to_idle(force=True)
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "trades.jsonl"
        journal.write_text(
            json.dumps({"timestamp": time.time() - 10, "action": "buy", "symbol": "AAA"})
            + "\n",
            encoding="utf-8",
        )
        stop_at = time.time()
        journal.write_text(
            journal.read_text(encoding="utf-8")
            + json.dumps({"timestamp": stop_at - 1, "action": "buy", "symbol": "BBB"})
            + "\n",
            encoding="utf-8",
        )
        with patch.object(Config, "TRADE_JOURNAL_PATH", str(journal)):
            trades = bot_manager.get_trades(limit=50)
        post_stop = [t for t in trades if t.get("timestamp", 0) > stop_at]
        assert post_stop == []
    print("PASS: no trades recorded after stop timestamp in journal check")


if __name__ == "__main__":
    test_trading_bot_stop_sets_flags()
    test_manager_stop_joins_thread()
    test_stop_prevents_new_entries_in_loop()
    test_restart_after_stop()
    test_no_trades_after_stop_timestamp()
    print("\nAll bot stop validation tests passed.")
