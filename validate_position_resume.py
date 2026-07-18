"""Validate open-position persistence and resume across restarts."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from config import Config
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


if __name__ == "__main__":
    test_round_trip_serialization()
    test_save_load_filter_by_mode()
    test_strategy_persist_on_open_and_close()
    test_restore_into_strategy()
    print("\nAll position resume validation tests passed.")
