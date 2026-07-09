"""Validate smart re-chase retry state machine and config."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from config import (
    DEFAULT_REENTRY_RETRY_BLOCK_HOURS,
    DEFAULT_REENTRY_RETRY_ENABLED,
    DEFAULT_REENTRY_RETRY_MAX_ATTEMPTS,
    DEFAULT_REENTRY_RETRY_WINDOW_MINUTES,
    Config,
)
from reentry_retry import ReentryRetryManager, reentry_retry_manager
from scanner import MoverCandidate


def _candidate(
    symbol: str = "NARC", mint: str = "mint11111111111111111111111111111111"
) -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="raydium",
        price_usd=0.001,
        liquidity_usd=50000.0,
        volume_24h_usd=120000.0,
        momentum_pct=0.05,
        price_change_5m=0.02,
        price_change_1h=0.03,
        source="pumpfun",
    )


def test_config_defaults():
    assert DEFAULT_REENTRY_RETRY_ENABLED is True
    assert DEFAULT_REENTRY_RETRY_WINDOW_MINUTES == 60
    assert DEFAULT_REENTRY_RETRY_BLOCK_HOURS == 2
    assert DEFAULT_REENTRY_RETRY_MAX_ATTEMPTS == 2
    assert Config.REENTRY_RETRY_ENABLED is True
    assert Config.reentry_retry_is_active() is True
    print("PASS: reentry retry config defaults active immediately")


def test_open_window_bypasses_loss_block():
    with tempfile.TemporaryDirectory() as tmp:
        store = ReentryRetryManager(Path(tmp) / "state.json")
        cand = _candidate()
        sig = store.loss_signature_from_candidate(cand)
        with patch.object(Config, "REENTRY_RETRY_ENABLED", True):
            assert store.can_open_retry_window(cand.mint)
            assert store.open_retry_window(cand.mint, cand.symbol, sig)
            assert store.bypasses_loss_block(cand.mint)
    print("PASS: retry window bypasses loss block")


def test_failed_retry_blocks_then_pending_user_action():
    with tempfile.TemporaryDirectory() as tmp:
        store = ReentryRetryManager(Path(tmp) / "state2.json")
        cand = _candidate(mint="mint22222222222222222222222222222222")
        sig = store.loss_signature_from_candidate(cand)
        with patch.object(Config, "REENTRY_RETRY_ENABLED", True), patch.object(
            Config, "REENTRY_RETRY_MAX_ATTEMPTS", 2
        ), patch.object(Config, "REENTRY_RETRY_BLOCK_HOURS", 2):
            store.open_retry_window(cand.mint, cand.symbol, sig)
            store.mark_retry_entry(cand.mint)
            assert store.record_retry_outcome(cand.mint, symbol=cand.symbol, won=False)
            assert not store.bypasses_loss_block(cand.mint)
            assert store.can_open_retry_window(cand.mint) is False

            rec = store.mints[cand.mint]
            rec["block_until"] = 0.0
            store.open_retry_window(cand.mint, cand.symbol, sig)
            store.mark_retry_entry(cand.mint)
            assert store.record_retry_outcome(cand.mint, symbol=cand.symbol, won=False)
            pending = store.get_pending_actions()
            assert len(pending) == 1
            assert pending[0]["mint"] == cand.mint
    print("PASS: two failed retries require user action")


def test_user_allow_clears_block():
    with tempfile.TemporaryDirectory() as tmp:
        store = ReentryRetryManager(Path(tmp) / "state3.json")
        mint = "mint33333333333333333333333333333333"
        rec = store._mint_record(mint, "NARC")
        rec["pending_user_action"] = True
        rec["failed_attempts"] = 2
        store.apply_decision(mint, allow=True, deny_similar_pattern=False)
        assert store.can_open_retry_window(mint)
    print("PASS: user allow clears retry block")


def test_user_deny_similar_blocks_pattern():
    with tempfile.TemporaryDirectory() as tmp:
        store = ReentryRetryManager(Path(tmp) / "state4.json")
        mint = "mint44444444444444444444444444444444"
        sig = store.loss_signature_from_candidate(_candidate(mint=mint))
        rec = store._mint_record(mint, "NARC")
        rec["signature"] = sig
        rec["pending_user_action"] = True
        store.apply_decision(mint, allow=False, deny_similar_pattern=True)
        denied, reason = store.entry_denied_for_candidate(_candidate(mint=mint))
        assert denied
        assert reason
    print("PASS: deny similar blocks matching candidates")


def test_win_clears_retry_state():
    with tempfile.TemporaryDirectory() as tmp:
        store = ReentryRetryManager(Path(tmp) / "state5.json")
        mint = "mint55555555555555555555555555555555"
        store.mark_retry_entry(mint)
        store.mints[mint]["retry_entry_active"] = True
        assert store.record_retry_outcome(mint, symbol="NARC", won=True)
        assert mint not in store.mints
    print("PASS: retry win clears state")


def test_manager_singleton_active():
    with patch.object(Config, "REENTRY_RETRY_ENABLED", True):
        snap = reentry_retry_manager.status_snapshot()
        assert snap["enabled"] is True
        assert snap["active"] is True
    print("PASS: manager status snapshot")


def main():
    test_config_defaults()
    test_open_window_bypasses_loss_block()
    test_failed_retry_blocks_then_pending_user_action()
    test_user_allow_clears_block()
    test_user_deny_similar_blocks_pattern()
    test_win_clears_retry_state()
    test_manager_singleton_active()
    print("\nAll reentry retry validations passed.")


if __name__ == "__main__":
    main()
