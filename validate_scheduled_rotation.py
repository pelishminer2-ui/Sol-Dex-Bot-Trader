"""Validate scheduled mint rotation helpers and slot exemption."""

from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config import Config
from scheduled_rotation import (
    is_scheduled_rotation_position,
    load_state,
    mark_buy,
    mark_sell,
    mark_skip,
    status_snapshot,
    trading_open_mints,
)
from strategy import MomentumStrategy, Position, SignalType


def _pos(mint: str, *, rotation: bool = False, entry_time: float | None = None) -> Position:
    profile = {}
    if rotation:
        profile = {"scheduled_rotation": True, "scanner_source": "scheduled_rotation"}
    return Position(
        mint=mint,
        symbol=mint[:4],
        entry_price=1.0,
        entry_time=entry_time if entry_time is not None else time.time(),
        size_sol=0.01,
        token_amount_raw=1_000_000,
        initial_token_amount_raw=1_000_000,
        remaining_token_amount_raw=1_000_000,
        token_decimals=6,
        profile=profile,
    )


def test_slot_exemption():
    rot = _pos("rotMint111", rotation=True)
    wbtc = _pos(Config.watchlist_mints()[0] if Config.watchlist_mints() else "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh")
    mints = trading_open_mints([rot, wbtc])
    assert len(mints) == 1
    assert mints[0] == wbtc.mint
    assert is_scheduled_rotation_position(rot) is True
    assert is_scheduled_rotation_position(wbtc) is False
    print("PASS: slot_exemption")


def test_can_open_more_with_rotation():
    strategy = MomentumStrategy()
    strategy.positions = [_pos("rotMint222", rotation=True)]
    assert strategy.can_open_more() is True
    print("PASS: can_open_more_with_rotation")


def test_two_hour_exit_and_skip_15m():
    strategy = MomentumStrategy()
    # After 20 minutes: normal memecoin would max-hold; rotation must still hold.
    pos = _pos("rotMint333", rotation=True, entry_time=time.time() - 20 * 60)
    pos.remaining_token_amount_raw = 1_000_000
    sig = strategy.evaluate_exit(pos, current_price=1.0)
    assert sig is None, f"expected hold at 20m, got {sig}"

    # After 2h+: mandatory time exit
    pos2 = _pos("rotMint444", rotation=True, entry_time=time.time() - 2 * 3600 - 5)
    pos2.remaining_token_amount_raw = 1_000_000
    sig2 = strategy.evaluate_exit(pos2, current_price=1.0)
    assert sig2 is not None and sig2.signal_type == SignalType.SELL_TIME
    print("PASS: two_hour_exit_and_skip_15m")


def test_early_instant_profit_before_2h():
    """Rotation sells at +3.25% instantly — does not wait for 2h hold."""
    strategy = MomentumStrategy()
    pos = _pos("rotMint555", rotation=True, entry_time=time.time() - 10 * 60)
    pos.remaining_token_amount_raw = 1_000_000
    # +3.5% mark PnL (above INSTANT_EXIT_3PCT 3.25%)
    sig = strategy.evaluate_exit(pos, current_price=1.035)
    assert sig is not None and sig.signal_type == SignalType.SELL_INSTANT_PROFIT, (
        f"expected instant profit before 2h, got {sig}"
    )
    # Peak-based: mark flat but peak already hit +3.25%
    pos2 = _pos("rotMint666", rotation=True, entry_time=time.time() - 5 * 60)
    pos2.remaining_token_amount_raw = 1_000_000
    pos2.peak_pnl_pct = 0.04
    sig2 = strategy.evaluate_exit(pos2, current_price=1.0)
    assert sig2 is not None and sig2.signal_type == SignalType.SELL_INSTANT_PROFIT, (
        f"expected peak instant profit, got {sig2}"
    )
    # Quote-based path
    pos3 = _pos("rotMint777", rotation=True, entry_time=time.time() - 5 * 60)
    pos3.remaining_token_amount_raw = 1_000_000
    sig3 = strategy.evaluate_exit(
        pos3, current_price=1.0, executable_pnl_pct=0.033
    )
    assert sig3 is not None and sig3.signal_type == SignalType.SELL_INSTANT_PROFIT, (
        f"expected quote instant profit, got {sig3}"
    )
    print("PASS: early_instant_profit_before_2h")


def test_no_stop_loss_on_rotation():
    """Fee-drag / -1.5% must NOT stop-loss a scheduled rotation leg."""
    from config import stop_loss_applies_for_mint

    mints = list(Config.SCHEDULED_ROTATION_MINTS)
    assert mints, "expected configured rotation mints"
    for mint in mints:
        assert stop_loss_applies_for_mint(mint) is False, mint

    strategy = MomentumStrategy()
    mint = mints[0]
    pos = Position(
        mint=mint,
        symbol="ROT",
        entry_price=1.0,
        entry_time=time.time() - 30,
        size_sol=0.01,
        token_amount_raw=1_000_000,
        initial_token_amount_raw=1_000_000,
        remaining_token_amount_raw=1_000_000,
        token_decimals=6,
        profile={"scheduled_rotation": True, "scanner_source": "scheduled_rotation"},
    )
    # Deep red on mark + quote + trough — still no SL for rotation
    pos.trough_pnl_pct = -0.10
    sig = strategy.evaluate_exit(pos, current_price=0.90, executable_pnl_pct=-0.08)
    assert sig is None or sig.signal_type != SignalType.SELL_SL, (
        f"rotation must not SL, got {sig}"
    )
    # Instant profit still works
    pos2 = Position(
        mint=mint,
        symbol="ROT",
        entry_price=1.0,
        entry_time=time.time() - 60,
        size_sol=0.01,
        token_amount_raw=1_000_000,
        initial_token_amount_raw=1_000_000,
        remaining_token_amount_raw=1_000_000,
        token_decimals=6,
        profile={"scheduled_rotation": True, "scanner_source": "scheduled_rotation"},
    )
    sig2 = strategy.evaluate_exit(pos2, current_price=1.04)
    assert sig2 is not None and sig2.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: no_stop_loss_on_rotation")


def test_state_round_trip():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "scheduled_rotation_state.json"
        with patch.object(Config, "SCHEDULED_ROTATION_STATE_PATH", str(path)):
            st = load_state()
            assert path.exists()
            assert "next_due_ts" in st
            mints = list(Config.SCHEDULED_ROTATION_MINTS)
            assert mints
            mark_buy(mint=mints[0], symbol="A", mint_index=0)
            st2 = load_state()
            assert st2["open_rotation"]["mint"] == mints[0]
            assert st2["next_mint_index"] == 1 % max(len(mints), 1)
            mark_sell(mint=mints[0])
            st3 = load_state()
            assert st3["open_rotation"] is None
            mark_skip("no_route")
            st4 = load_state()
            assert st4["last_skip_reason"] == "no_route"
            snap = status_snapshot()
            assert snap["enabled"] is True
            assert snap["size_usd"] == Config.SCHEDULED_ROTATION_SIZE_USD
            assert snap["hold_sec"] == Config.SCHEDULED_ROTATION_HOLD_SEC
            raw = json.loads(path.read_text(encoding="utf-8"))
            assert "next_due_ts" in raw
    print("PASS: state_round_trip")


if __name__ == "__main__":
    test_slot_exemption()
    test_can_open_more_with_rotation()
    test_two_hour_exit_and_skip_15m()
    test_early_instant_profit_before_2h()
    test_no_stop_loss_on_rotation()
    test_state_round_trip()
    print("ALL PASS")
