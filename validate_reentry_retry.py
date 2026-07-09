"""Validate smart re-chase retry state machine (entry-only)."""

import time
from unittest.mock import patch

from config import Config
from reentry_retry import ReentryRetryManager
from scanner import MoverCandidate


def _manager(clock) -> ReentryRetryManager:
    mgr = ReentryRetryManager(clock=clock)
    mgr._path = mgr._path.with_suffix(".validate.json")
    mgr._data = {"version": 1, "mints": {}, "pattern_decisions": []}
    return mgr


def _candidate(mint: str = "Mint1111111111111111111111111111111111") -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol="NARC",
        name="NARC",
        pair_address="",
        dex="pumpfun",
        price_usd=0.001,
        liquidity_usd=50000.0,
        volume_24h_usd=120000.0,
        momentum_pct=3.5,
        price_change_5m=0.12,
        price_change_1h=0.25,
        source="pumpfun",
    )


def test_inactive_before_effective_after():
    t0 = Config.reentry_retry_effective_after_ts() - 3600
    clock = lambda: t0
    mgr = _manager(clock)
    with patch.object(Config, "REENTRY_RETRY_ENABLED", True):
        assert not mgr.is_active(clock)
    print("PASS: inactive before effective_after")


def test_block_then_retry_window():
    t0 = Config.reentry_retry_effective_after_ts() + 10
    now = {"t": t0}
    clock = lambda: now["t"]
    mgr = _manager(clock)
    mint = "Mint1111111111111111111111111111111111"
    sig = mgr.loss_signature_from_candidate(_candidate(mint))

    with patch.object(Config, "REENTRY_RETRY_ENABLED", True):
        assert mgr.is_active(clock)
        assert mgr.can_open_retry_window(mint, clock)
        assert mgr.open_retry_window(mint, "NARC", sig, clock=clock)
        assert mgr.bypasses_loss_block(mint, clock)
        assert mgr.is_retry_entry_pending(mint, clock)

        mgr.mark_retry_entry(mint, clock)
        assert not mgr.bypasses_loss_block(mint, clock)

        handled = mgr.record_retry_outcome(
            mint, symbol="NARC", won=False, loss_signature=sig, clock=clock
        )
        assert handled
        assert not mgr.bypasses_loss_block(mint, clock)
        row = mgr._data["mints"][mint]
        assert row["attempt_count"] == 1
        assert row["block_until"] > now["t"]

        now["t"] += Config.REENTRY_RETRY_BLOCK_HOURS * 3600 + 1
        assert mgr.can_open_retry_window(mint, clock)
        assert mgr.open_retry_window(mint, "NARC", sig, clock=clock)
        mgr.mark_retry_entry(mint, clock)
        handled2 = mgr.record_retry_outcome(
            mint, symbol="NARC", won=False, loss_signature=sig, clock=clock
        )
        assert handled2
        assert mgr._data["mints"][mint]["pending_user_action"] is True
        pending = mgr.get_pending_actions(clock)
        assert len(pending) == 1
        assert pending[0]["symbol"] == "NARC"
    print("PASS: block -> 1h retry -> fail -> 2h block -> fail -> user action")


def test_user_deny_similar_pattern():
    t0 = Config.reentry_retry_effective_after_ts() + 10
    clock = lambda: t0
    mgr = _manager(clock)
    mint = "Mint2222222222222222222222222222222222"
    sig = mgr.loss_signature_from_candidate(_candidate(mint))
    mgr._data["mints"][mint] = {
        "mint": mint,
        "symbol": "NARC",
        "pending_user_action": True,
        "loss_signature": sig,
        "attempt_count": 2,
    }

    with patch.object(Config, "REENTRY_RETRY_ENABLED", True):
        denied, reason = mgr.entry_denied_for_candidate(_candidate(mint), clock)
        assert denied and "pending" in (reason or "")

        mgr.apply_decision(mint, allow=False, deny_similar_pattern=True, clock=clock)
        similar = _candidate("Mint3333333333333333333333333333333333")
        denied2, reason2 = mgr.entry_denied_for_candidate(similar, clock)
        assert denied2 and "similar" in (reason2 or "").lower()
    print("PASS: user deny + similar pattern blocks alike tickers")


def test_user_allow_clears_state():
    t0 = Config.reentry_retry_effective_after_ts() + 10
    clock = lambda: t0
    mgr = _manager(clock)
    mint = "Mint4444444444444444444444444444444444"
    sig = mgr.loss_signature_from_candidate(_candidate(mint))
    mgr._data["mints"][mint] = {
        "mint": mint,
        "symbol": "NARC",
        "pending_user_action": True,
        "loss_signature": sig,
        "attempt_count": 2,
        "user_decision": None,
    }

    with patch.object(Config, "REENTRY_RETRY_ENABLED", True):
        result = mgr.apply_decision(mint, allow=True, deny_similar_pattern=False, clock=clock)
        assert result["decision"] == "allow"
        row = mgr._data["mints"][mint]
        assert row["attempt_count"] == 0
        assert not row["pending_user_action"]
        assert mgr.can_open_retry_window(mint, clock)
    print("PASS: user allow resets retry state")


def test_retry_win_clears_attempts():
    t0 = Config.reentry_retry_effective_after_ts() + 10
    clock = lambda: t0
    mgr = _manager(clock)
    mint = "Mint5555555555555555555555555555555555"
    mgr._data["mints"][mint] = {
        "mint": mint,
        "symbol": "NARC",
        "retry_entry_used": True,
        "attempt_count": 1,
    }
    with patch.object(Config, "REENTRY_RETRY_ENABLED", True):
        assert mgr.record_retry_outcome(mint, symbol="NARC", won=True, clock=clock)
        assert mgr._data["mints"][mint]["attempt_count"] == 0
    print("PASS: retry win clears attempt count")


if __name__ == "__main__":
    test_inactive_before_effective_after()
    test_block_then_retry_window()
    test_user_deny_similar_pattern()
    test_user_allow_clears_state()
    test_retry_win_clears_attempts()
    print("ALL PASS: validate_reentry_retry")
