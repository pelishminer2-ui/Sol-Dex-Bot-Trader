"""Scheduled $1 USD mint rotation: buy every 12h, hold 2h, rotate A→B→C.

Positions are tagged `profile["scheduled_rotation"]=True` so they do not consume
normal companion / max-position slots. Live-only by default.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from config import Config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

PositionLike = Any  # Position dataclass or persisted dict


def is_scheduled_rotation_position(position: PositionLike) -> bool:
    """True when position is tagged as a scheduled rotation leg.

    Accepts live ``Position`` objects (profile dict) and API/persisted dicts
    that flatten ``scheduled_rotation`` / ``scanner_source`` to the top level.
    """
    if position is None:
        return False
    profile = getattr(position, "profile", None)
    if profile is None and isinstance(position, dict):
        profile = position.get("profile") or {}
        # Flattened API / UI payloads (see bot_manager._position_to_dict).
        if position.get("scheduled_rotation"):
            return True
        src = str(position.get("scanner_source") or "").strip().lower()
        if src == "scheduled_rotation":
            return True
    if not isinstance(profile, dict):
        return False
    if profile.get("scheduled_rotation"):
        return True
    return str(profile.get("scanner_source") or "").strip().lower() == "scheduled_rotation"


def trading_open_mints(positions: Sequence[PositionLike]) -> List[str]:
    """Open mints that count toward normal max-position / companion accounting."""
    out: List[str] = []
    for p in positions or []:
        if is_scheduled_rotation_position(p):
            continue
        mint = getattr(p, "mint", None)
        if mint is None and isinstance(p, dict):
            mint = p.get("mint")
        if mint:
            out.append(str(mint))
    return out


def trading_open_count(positions: Sequence[PositionLike]) -> int:
    return len(trading_open_mints(positions))


def rotation_open_positions(positions: Sequence[PositionLike]) -> List[PositionLike]:
    return [p for p in (positions or []) if is_scheduled_rotation_position(p)]


def is_scheduled_rotation_mint(mint: str) -> bool:
    if not mint or not Config.SCHEDULED_ROTATION_ENABLED:
        return False
    return mint in Config.SCHEDULED_ROTATION_MINTS


def _state_path() -> Path:
    return Path(Config.SCHEDULED_ROTATION_STATE_PATH)


def _default_state() -> Dict[str, Any]:
    now = time.time()
    return {
        "next_due_ts": now,
        "next_mint_index": 0,
        "open_rotation": None,
        "last_buy_ts": None,
        "last_sell_ts": None,
        "last_skip_reason": None,
        "last_skip_ts": None,
        "updated_at": now,
    }


def load_state() -> Dict[str, Any]:
    path = _state_path()
    with _lock:
        if not path.exists():
            state = _default_state()
            _save_unlocked(state)
            return state
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state root must be object")
            state = _default_state()
            state.update(raw)
            return state
        except Exception as exc:
            logger.warning("scheduled_rotation load failed (%s); resetting", exc)
            state = _default_state()
            _save_unlocked(state)
            return state


def _save_unlocked(state: Dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_at"] = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_state(state: Dict[str, Any]) -> None:
    with _lock:
        _save_unlocked(state)


def mint_at_index(index: int) -> Optional[str]:
    mints = list(Config.SCHEDULED_ROTATION_MINTS)
    if not mints:
        return None
    return mints[int(index) % len(mints)]


def sync_open_from_positions(positions: Sequence[PositionLike]) -> Dict[str, Any]:
    """Reconcile disk state with any tagged rotation positions after restart."""
    state = load_state()
    open_rots = rotation_open_positions(positions)
    if open_rots:
        p = open_rots[0]
        mint = getattr(p, "mint", None) or (p.get("mint") if isinstance(p, dict) else None)
        entry_time = getattr(p, "entry_time", None)
        if entry_time is None and isinstance(p, dict):
            entry_time = p.get("entry_time")
        symbol = getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else "")
        state["open_rotation"] = {
            "mint": mint,
            "entry_ts": float(entry_time or time.time()),
            "symbol": symbol or "",
        }
    elif state.get("open_rotation"):
        # Position gone (sold / closed elsewhere) — clear open leg.
        state["open_rotation"] = None
        if state.get("last_sell_ts") is None:
            state["last_sell_ts"] = time.time()
    save_state(state)
    return state


def mark_buy(
    *,
    mint: str,
    symbol: str,
    entry_ts: Optional[float] = None,
    mint_index: int,
) -> Dict[str, Any]:
    state = load_state()
    now = float(entry_ts if entry_ts is not None else time.time())
    mints = list(Config.SCHEDULED_ROTATION_MINTS)
    n = max(len(mints), 1)
    state["open_rotation"] = {
        "mint": mint,
        "entry_ts": now,
        "symbol": symbol or "",
    }
    state["last_buy_ts"] = now
    state["next_mint_index"] = (int(mint_index) + 1) % n
    state["next_due_ts"] = now + float(Config.SCHEDULED_ROTATION_INTERVAL_SEC)
    state["last_skip_reason"] = None
    save_state(state)
    return state


def mark_sell(*, mint: Optional[str] = None) -> Dict[str, Any]:
    state = load_state()
    open_leg = state.get("open_rotation") or {}
    if mint and open_leg.get("mint") and open_leg.get("mint") != mint:
        return state
    state["open_rotation"] = None
    state["last_sell_ts"] = time.time()
    save_state(state)
    return state


def mark_skip(reason: str, *, retry_sec: Optional[float] = None) -> Dict[str, Any]:
    """Defer next attempt without advancing mint index (failed Jupiter / liquidity)."""
    state = load_state()
    now = time.time()
    delay = float(
        retry_sec
        if retry_sec is not None
        else Config.SCHEDULED_ROTATION_RETRY_SEC
    )
    state["last_skip_reason"] = str(reason)[:240]
    state["last_skip_ts"] = now
    # Don't push past the regular cadence if already far in the future.
    next_due = float(state.get("next_due_ts") or 0)
    retry_at = now + delay
    if next_due <= now:
        state["next_due_ts"] = retry_at
    else:
        state["next_due_ts"] = min(next_due, retry_at)
    save_state(state)
    return state


def status_snapshot(positions: Optional[Sequence[PositionLike]] = None) -> Dict[str, Any]:
    state = load_state()
    if positions is not None:
        state = sync_open_from_positions(positions)
    mints = list(Config.SCHEDULED_ROTATION_MINTS)
    idx = int(state.get("next_mint_index") or 0)
    next_mint = mint_at_index(idx) if mints else None
    next_due = float(state.get("next_due_ts") or 0)
    return {
        "enabled": bool(Config.SCHEDULED_ROTATION_ENABLED),
        "live_only": bool(Config.SCHEDULED_ROTATION_LIVE_ONLY),
        "interval_sec": float(Config.SCHEDULED_ROTATION_INTERVAL_SEC),
        "hold_sec": float(Config.SCHEDULED_ROTATION_HOLD_SEC),
        "size_usd": float(Config.SCHEDULED_ROTATION_SIZE_USD),
        "mints": mints,
        "next_mint_index": idx,
        "next_mint": next_mint,
        "next_due_ts": next_due,
        "next_due_in_sec": max(0.0, next_due - time.time()),
        "open_rotation": state.get("open_rotation"),
        "last_buy_ts": state.get("last_buy_ts"),
        "last_sell_ts": state.get("last_sell_ts"),
        "last_skip_reason": state.get("last_skip_reason"),
        "state_path": str(_state_path()),
    }
