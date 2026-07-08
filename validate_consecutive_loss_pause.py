"""Validate consecutive-loss entry pause (paper timed, live indefinite)."""

import time
from unittest.mock import patch

from config import (
    DEFAULT_CONSECUTIVE_LOSS_PAUSE_MINUTES,
    DEFAULT_CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY,
    DEFAULT_MAX_CONSECUTIVE_LOSSES,
    Config,
)
from risk import RiskManager


def test_config_defaults():
    assert DEFAULT_MAX_CONSECUTIVE_LOSSES == 3
    assert DEFAULT_CONSECUTIVE_LOSS_PAUSE_MINUTES == 25
    assert DEFAULT_CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY is True
    assert Config.CONSECUTIVE_LOSS_PAUSE_MINUTES == DEFAULT_CONSECUTIVE_LOSS_PAUSE_MINUTES
    assert Config.CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY is True
    print("PASS: consecutive loss pause config defaults")


def test_paper_pause_triggers_after_threshold():
    risk = RiskManager()
    with patch.object(Config, "MAX_CONSECUTIVE_LOSSES", 3), patch.object(
        Config, "CONSECUTIVE_LOSS_PAUSE_MINUTES", 25
    ), patch.object(Config, "CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY", True):
        for _ in range(3):
            risk.record_trade_outcome(-0.01, dry_run=True)
        assert risk.state.consecutive_losses == 3
        assert risk.state.consecutive_loss_pause_until > time.time()
        can, reason = risk.can_enter(0, 1.0, dry_run=True)
        assert not can
        assert "consecutive losses" in reason
        assert "remaining" in reason
        status = risk.consecutive_loss_pause_status(dry_run=True)
        assert status["active"] is True
        assert status["timed_pause"] is True
        assert status["remaining_sec"] > 0
        print(f"PASS: paper pause triggers after threshold — {reason}")


def test_paper_pause_expires_and_resets_counter():
    risk = RiskManager()
    with patch.object(Config, "MAX_CONSECUTIVE_LOSSES", 3), patch.object(
        Config, "CONSECUTIVE_LOSS_PAUSE_MINUTES", 25
    ), patch.object(Config, "CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY", True):
        for _ in range(3):
            risk.record_trade_outcome(-0.01, dry_run=True)
        risk.state.consecutive_loss_pause_until = time.time() - 1
        can, _ = risk.can_enter(0, 1.0, dry_run=True)
        assert can
        assert risk.state.consecutive_losses == 0
        assert risk.state.consecutive_loss_pause_until == 0.0
        status = risk.consecutive_loss_pause_status(dry_run=True)
        assert status["active"] is False
        print("PASS: paper pause expires and resets consecutive loss counter")


def test_live_indefinite_pause_until_restart():
    risk = RiskManager()
    with patch.object(Config, "MAX_CONSECUTIVE_LOSSES", 3), patch.object(
        Config, "CONSECUTIVE_LOSS_PAUSE_MINUTES", 25
    ), patch.object(Config, "CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY", True):
        for _ in range(3):
            risk.record_trade_outcome(-0.01, dry_run=False)
        assert risk.state.consecutive_losses == 3
        assert risk.state.consecutive_loss_pause_until == 0.0
        can, reason = risk.can_enter(0, 1.0, dry_run=False)
        assert not can
        assert "consecutive losses" in reason
        assert "Stop/Start" in reason
        assert "remaining" not in reason
        status = risk.consecutive_loss_pause_status(dry_run=False)
        assert status["active"] is True
        assert status["timed_pause"] is False
        assert status["remaining_sec"] == 0
        # Simulated restart: fresh RiskManager clears block
        fresh = RiskManager()
        can_after, _ = fresh.can_enter(0, 1.0, dry_run=False)
        assert can_after
        print(f"PASS: live indefinite pause until restart — {reason}")


def test_live_not_timed_even_after_long_wait():
    risk = RiskManager()
    with patch.object(Config, "MAX_CONSECUTIVE_LOSSES", 3), patch.object(
        Config, "CONSECUTIVE_LOSS_PAUSE_MINUTES", 25
    ), patch.object(Config, "CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY", True):
        for _ in range(3):
            risk.record_trade_outcome(-0.01, dry_run=False)
        # No timer set — still blocked after simulated elapsed time
        can, _ = risk.can_enter(0, 1.0, dry_run=False)
        assert not can
        assert risk.state.consecutive_losses == 3
        print("PASS: live pause does not auto-expire")


def test_winning_trade_clears_pause():
    risk = RiskManager()
    with patch.object(Config, "MAX_CONSECUTIVE_LOSSES", 3), patch.object(
        Config, "CONSECUTIVE_LOSS_PAUSE_MINUTES", 25
    ), patch.object(Config, "CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY", True):
        for _ in range(3):
            risk.record_trade_outcome(-0.01, dry_run=True)
        risk.record_trade_outcome(0.02, dry_run=True)
        assert risk.state.consecutive_losses == 0
        assert risk.state.consecutive_loss_pause_until == 0.0
        can, _ = risk.can_enter(0, 1.0, dry_run=True)
        assert can
        print("PASS: winning trade clears consecutive loss pause")


def test_pause_disabled_when_max_consecutive_losses_zero():
    risk = RiskManager()
    with patch.object(Config, "MAX_CONSECUTIVE_LOSSES", 0):
        for _ in range(5):
            risk.record_trade_outcome(-0.01, dry_run=True)
        can, _ = risk.can_enter(0, 1.0, dry_run=True)
        assert can
        assert risk.state.consecutive_loss_pause_until == 0.0
        print("PASS: pause disabled when MAX_CONSECUTIVE_LOSSES=0")


def main():
    test_config_defaults()
    test_paper_pause_triggers_after_threshold()
    test_paper_pause_expires_and_resets_counter()
    test_live_indefinite_pause_until_restart()
    test_live_not_timed_even_after_long_wait()
    test_winning_trade_clears_pause()
    test_pause_disabled_when_max_consecutive_losses_zero()
    print("\nAll consecutive loss pause validation tests passed.")


if __name__ == "__main__":
    main()
