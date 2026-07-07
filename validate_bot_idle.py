"""Validate bot idle / stale-state recovery behavior."""

import threading
import time
from unittest.mock import patch

from bot_manager import STARTING_TIMEOUT_SEC, bot_manager

_MOCK_BALANCE = patch.object(bot_manager, "get_balance", return_value=1.0)


class _FakeBot:
    running = True

    def stop(self):
        self.running = False


def test_can_start_when_idle():
    bot_manager.reset_to_idle(force=True)
    with _MOCK_BALANCE:
        status = bot_manager.get_status()
    assert status["can_start"] is True
    assert status["running"] is False
    print("PASS: can_start true when idle")


def test_can_start_when_stopping():
    """Stuck stopping state must allow Start (UI uses can_start)."""
    bot_manager.reset_to_idle(force=True)
    hold = threading.Event()

    def _linger(dry_run, key):
        hold.wait()

    patcher = patch.object(bot_manager, "_run_bot_thread", side_effect=_linger)
    patcher.start()
    try:
        with _MOCK_BALANCE:
            bot_manager.start(dry_run=True)
        with patch.object(bot_manager, "STOP_JOIN_TIMEOUT_SEC", 0.01):
            bot_manager.stop()
        with _MOCK_BALANCE:
            status = bot_manager.get_status()
        assert status["status"] == "stopping"
        assert status["running"] is True
        assert status["can_start"] is True
    finally:
        hold.set()
        patcher.stop()
        bot_manager.force_reset()
    print("PASS: can_start true when stopping")


def test_can_start_false_when_running():
    bot_manager.reset_to_idle(force=True)
    hold, patcher = _start_with_hanging_thread()
    try:
        with _MOCK_BALANCE:
            status = bot_manager.get_status()
        assert status["running"] is True
        assert status["can_start"] is False
    finally:
        hold.set()
        bot_manager.stop()
        patcher.stop()
    print("PASS: can_start false when running")


def test_fresh_start_is_idle():
    bot_manager.reset_to_idle(force=True)
    with _MOCK_BALANCE:
        status = bot_manager.get_status()
    assert status["status"] == "stopped"
    assert status["running"] is False
    assert bot_manager.is_running() is False
    print("PASS: fresh start is idle")


def _start_with_hanging_thread():
    """Start bot with a worker thread that stays alive until released."""
    hold = threading.Event()

    def _hang(dry_run, key):
        with bot_manager._lock:
            bot_manager._status = "running"
        hold.wait()

    patcher = patch.object(bot_manager, "_run_bot_thread", side_effect=_hang)
    patcher.start()
    bot_manager.start(dry_run=True)
    return hold, patcher


def test_start_stop_cycle():
    bot_manager.reset_to_idle(force=True)
    hold, patcher = _start_with_hanging_thread()
    try:
        assert bot_manager.is_running() is True
        hold.set()
        bot_manager.stop()
    finally:
        patcher.stop()
    with _MOCK_BALANCE:
        status = bot_manager.get_status()
    assert status["running"] is False
    assert status["status"] == "stopped"
    print("PASS: start/stop cycle")


def test_second_start_while_running():
    bot_manager.reset_to_idle(force=True)
    hold, patcher = _start_with_hanging_thread()
    try:
        try:
            bot_manager.start(dry_run=True)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "already running" in str(exc).lower()
    finally:
        hold.set()
        bot_manager.stop()
        patcher.stop()
    print("PASS: second start blocked while running")


def test_stale_running_flag_recovered():
    bot_manager.reset_to_idle(force=True)
    with bot_manager._lock:
        bot_manager._status = "running"
        bot_manager._thread = threading.Thread(target=lambda: None, daemon=True)
    with _MOCK_BALANCE:
        assert bot_manager.get_status()["running"] is False
        assert bot_manager.get_status()["status"] == "stopped"
    with patch.object(bot_manager, "_run_bot_thread", side_effect=lambda d, k: None):
        bot_manager.start(dry_run=True)
        bot_manager.stop()
    print("PASS: stale running flag recovered")


def test_force_reset_clears_stale():
    bot_manager.reset_to_idle(force=True)
    with bot_manager._lock:
        bot_manager._status = "starting"
        bot_manager._thread = None
    result = bot_manager.force_reset()
    assert result["status"] == "stopped"
    with _MOCK_BALANCE:
        assert bot_manager.get_status()["running"] is False
    print("PASS: force reset clears stale state")


def test_orphan_thread_allows_start():
    """Idle status with a lingering worker thread must not block the next start."""
    bot_manager.reset_to_idle(force=True)
    hold = threading.Event()

    def _linger(dry_run, key):
        hold.wait()

    orphan = threading.Thread(target=_linger, args=(True, None), daemon=True, name="OrphanBot")
    with bot_manager._lock:
        bot_manager._status = "stopped"
        bot_manager._thread = orphan
    orphan.start()

    with _MOCK_BALANCE:
        status = bot_manager.get_status()
    assert status["status"] == "running"
    assert status["running"] is True

    with patch.object(bot_manager, "_run_bot_thread", side_effect=lambda d, k: None):
        result = bot_manager.start(dry_run=True)
    assert result["status"] == "starting"

    hold.set()
    orphan.join(timeout=5)
    bot_manager.reset_to_idle(force=True)
    print("PASS: orphan thread does not block start")


def test_start_failure_resets_to_idle():
    bot_manager.reset_to_idle(force=True)
    try:
        bot_manager.start(dry_run=False)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "wallet" in str(exc).lower() or "private key" in str(exc).lower()

    with _MOCK_BALANCE:
        status = bot_manager.get_status()
    assert status["status"] == "stopped"
    assert status["running"] is False

    with patch.object(bot_manager, "_run_bot_thread", side_effect=lambda d, k: None):
        bot_manager.start(dry_run=True)
        bot_manager.stop()
    print("PASS: start failure resets to idle")


def test_stuck_starting_times_out():
    bot_manager.reset_to_idle(force=True)
    hold = threading.Event()

    def _stuck_starting(dry_run, key):
        hold.wait()

    patcher = patch.object(bot_manager, "_run_bot_thread", side_effect=_stuck_starting)
    patcher.start()
    bot_manager.start(dry_run=True)

    with bot_manager._lock:
        bot_manager._status = "starting"
        bot_manager._started_at = time.time() - STARTING_TIMEOUT_SEC - 1

    with patch.object(bot_manager, "stop", side_effect=lambda: bot_manager.reset_to_idle(force=True)):
        bot_manager._reconcile_stale_state()

    with _MOCK_BALANCE:
        status = bot_manager.get_status()
    assert status["running"] is False

    hold.set()
    patcher.stop()
    bot_manager.reset_to_idle(force=True)
    print("PASS: stuck starting times out")


def test_old_thread_does_not_clobber_new_session():
    """A dying thread must not clear state for a newly started bot."""
    bot_manager.reset_to_idle(force=True)
    gate_a = threading.Event()
    gate_b = threading.Event()
    phase = {"value": "A"}

    def _worker(dry_run, key):
        current = threading.current_thread()
        with bot_manager._lock:
            bot_manager._loop = None
            bot_manager._bot = _FakeBot()
            bot_manager._status = "running"
        if phase["value"] == "A":
            gate_a.wait()
        else:
            gate_b.wait()

    patcher = patch.object(bot_manager, "_run_bot_thread", side_effect=_worker)
    patcher.start()

    bot_manager.start(dry_run=True)
    with bot_manager._lock:
        thread_a = bot_manager._thread
    assert thread_a is not None

    bot_manager.stop()
    gate_a.set()
    thread_a.join(timeout=5)

    phase["value"] = "B"
    bot_manager.start(dry_run=True)
    with bot_manager._lock:
        thread_b = bot_manager._thread
        assert thread_b is not None
        assert bot_manager._status in ("starting", "running")

    gate_b.set()
    bot_manager.stop()
    patcher.stop()
    bot_manager.reset_to_idle(force=True)
    print("PASS: old thread does not clobber new session")


def test_stop_timeout_keeps_thread_tracked():
    """If join times out, manager must not drop the thread reference."""
    bot_manager.reset_to_idle(force=True)
    hold = threading.Event()

    def _linger(dry_run, key):
        hold.wait()

    patcher = patch.object(bot_manager, "_run_bot_thread", side_effect=_linger)
    patcher.start()
    try:
        with _MOCK_BALANCE:
            bot_manager.start(dry_run=True)
            assert bot_manager.is_running() is True

        with patch.object(bot_manager, "STOP_JOIN_TIMEOUT_SEC", 0.01):
            result = bot_manager.stop()

        assert result["status"] == "stopping"
        with bot_manager._lock:
            assert bot_manager._thread is not None
            assert bot_manager._thread.is_alive()

        with _MOCK_BALANCE:
            status = bot_manager.get_status()
        assert status["running"] is True
        assert status["status"] in ("running", "stopping")
    finally:
        hold.set()
        patcher.stop()
        bot_manager.force_reset()
    print("PASS: stop timeout keeps thread tracked")


def test_bot_started_at_while_running():
    """bot_started_at must be set while running and cleared when stopped."""
    bot_manager.reset_to_idle(force=True)
    hold, patcher = _start_with_hanging_thread()
    try:
        with _MOCK_BALANCE:
            status = bot_manager.get_status()
        assert status["running"] is True
        assert status["bot_started_at"] is not None
        assert status["started_at"] == status["bot_started_at"]
        assert status["bot_uptime_sec"] is not None
        assert status["bot_uptime_sec"] >= 0
    finally:
        hold.set()
        bot_manager.stop()
        patcher.stop()

    with _MOCK_BALANCE:
        status = bot_manager.get_status()
    assert status["running"] is False
    assert status["bot_started_at"] is None
    assert status["started_at"] is None
    assert status["bot_uptime_sec"] is None
    print("PASS: bot_started_at while running")


def test_bot_started_at_restored_from_persisted_state():
    """Lost in-memory started_at is restored from runtime state file while running."""
    bot_manager.reset_to_idle(force=True)
    hold, patcher = _start_with_hanging_thread()
    try:
        with bot_manager._lock:
            saved = bot_manager._started_at
            bot_manager._started_at = None
        with _MOCK_BALANCE:
            status = bot_manager.get_status()
        assert status["running"] is True
        assert status["bot_started_at"] == saved
        with bot_manager._lock:
            assert bot_manager._started_at == saved
    finally:
        hold.set()
        bot_manager.stop()
        patcher.stop()
    print("PASS: bot_started_at restored from persisted state")


if __name__ == "__main__":
    test_can_start_when_idle()
    test_can_start_when_stopping()
    test_can_start_false_when_running()
    test_fresh_start_is_idle()
    test_start_stop_cycle()
    test_second_start_while_running()
    test_stale_running_flag_recovered()
    test_force_reset_clears_stale()
    test_orphan_thread_allows_start()
    test_start_failure_resets_to_idle()
    test_stuck_starting_times_out()
    test_old_thread_does_not_clobber_new_session()
    test_stop_timeout_keeps_thread_tracked()
    test_bot_started_at_while_running()
    test_bot_started_at_restored_from_persisted_state()
    print("\nAll bot idle validation tests passed.")
