"""Persist open positions to disk so paper/live books survive process restarts."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import Config
from strategy import Position

logger = logging.getLogger(__name__)

_lock = threading.RLock()


def _state_path() -> Path:
    return Path(Config.OPEN_POSITIONS_STATE_PATH)


def position_to_dict(position: Position) -> Dict[str, Any]:
    return {
        "mint": position.mint,
        "symbol": position.symbol,
        "entry_price": float(position.entry_price),
        "entry_time": float(position.entry_time),
        "size_sol": float(position.size_sol),
        "token_amount_raw": int(position.token_amount_raw),
        "initial_token_amount_raw": int(position.initial_token_amount_raw),
        "remaining_token_amount_raw": int(position.remaining_token_amount_raw),
        "token_decimals": position.token_decimals,
        "tp_levels_hit": list(position.tp_levels_hit),
        "tp_levels": list(position.tp_levels),
        "tp_portions": list(position.tp_portions),
        "target_net_profit_sol": float(position.target_net_profit_sol),
        "fee_budget_sol": float(position.fee_budget_sol),
        "estimated_fees_sol": float(position.estimated_fees_sol),
        "fees_allocated_sol": float(position.fees_allocated_sol),
        "realized_net_pnl_sol": float(position.realized_net_pnl_sol),
        "momentum_at_entry": float(position.momentum_at_entry),
        "l1_protection_armed": bool(position.l1_protection_armed),
        "peak_pnl_pct": float(position.peak_pnl_pct),
        "trough_pnl_pct": float(position.trough_pnl_pct),
        "profile": dict(position.profile or {}),
        "buy_count": int(position.buy_count),
    }


def position_from_dict(data: Dict[str, Any]) -> Position:
    decimals = data.get("token_decimals")
    return Position(
        mint=str(data["mint"]),
        symbol=str(data.get("symbol") or data["mint"][:8]),
        entry_price=float(data["entry_price"]),
        entry_time=float(data.get("entry_time") or time.time()),
        size_sol=float(data.get("size_sol") or 0.0),
        token_amount_raw=int(data.get("token_amount_raw") or 0),
        initial_token_amount_raw=int(
            data.get("initial_token_amount_raw")
            or data.get("token_amount_raw")
            or 0
        ),
        remaining_token_amount_raw=int(
            data.get("remaining_token_amount_raw")
            or data.get("token_amount_raw")
            or 0
        ),
        token_decimals=int(decimals) if decimals is not None else None,
        tp_levels_hit=[int(x) for x in (data.get("tp_levels_hit") or [])],
        tp_levels=[float(x) for x in (data.get("tp_levels") or [])],
        tp_portions=[float(x) for x in (data.get("tp_portions") or [])],
        target_net_profit_sol=float(data.get("target_net_profit_sol") or 0.0),
        fee_budget_sol=float(data.get("fee_budget_sol") or 0.0),
        estimated_fees_sol=float(data.get("estimated_fees_sol") or 0.0),
        fees_allocated_sol=float(data.get("fees_allocated_sol") or 0.0),
        realized_net_pnl_sol=float(data.get("realized_net_pnl_sol") or 0.0),
        momentum_at_entry=float(data.get("momentum_at_entry") or 0.0),
        l1_protection_armed=bool(data.get("l1_protection_armed")),
        peak_pnl_pct=float(data.get("peak_pnl_pct") or 0.0),
        trough_pnl_pct=float(data.get("trough_pnl_pct") or 0.0),
        profile=dict(data.get("profile") or {}),
        buy_count=max(1, int(data.get("buy_count") or 1)),
    )


def save_open_positions(
    positions: List[Position],
    *,
    dry_run: bool,
) -> None:
    """Serialize the current open-position book to disk (atomic replace)."""
    path = _state_path()
    payload = {
        "version": 1,
        "updated_at": time.time(),
        "dry_run": bool(dry_run),
        "mode": "paper" if dry_run else "live",
        "positions": [position_to_dict(p) for p in positions],
    }
    text = json.dumps(payload, indent=2)
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.error("Failed to persist open positions: %s", exc)


def load_open_positions(
    *,
    dry_run: Optional[bool] = None,
) -> List[Position]:
    """Load open positions from disk. Optionally filter by paper/live mode."""
    path = _state_path()
    with _lock:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load open positions: %s", exc)
            return []
    if dry_run is not None and "dry_run" in data and bool(data["dry_run"]) != bool(dry_run):
        logger.info(
            "Skipping persisted positions (stored mode=%s, requested dry_run=%s)",
            data.get("mode"),
            dry_run,
        )
        return []
    raw = data.get("positions") or []
    out: List[Position] = []
    for item in raw:
        try:
            out.append(position_from_dict(item))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping corrupt persisted position: %s", exc)
    return out


def has_open_positions(*, dry_run: Optional[bool] = None) -> bool:
    return bool(load_open_positions(dry_run=dry_run))


def clear_open_positions() -> None:
    path = _state_path()
    with _lock:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.error("Failed to clear open positions file: %s", exc)


def peek_runtime_mode() -> Optional[bool]:
    """Return stored dry_run flag if a state file exists, else None."""
    path = _state_path()
    with _lock:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if "dry_run" not in data:
        return None
    return bool(data["dry_run"])
