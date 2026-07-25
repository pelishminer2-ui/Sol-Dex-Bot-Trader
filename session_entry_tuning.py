"""Closed-loop session entry tightening/loosening (entry selection only).

Tighten: when session WR trails regime target after enough trades, bump
win-lean + liquidity floors.

Loosen (market pickup): when Hot Market Mode regime stays hot for
SESSION_AUTO_LOOSEN_HOT_HOLD_SEC, step those bumps back toward session base /
Steady defaults every SESSION_AUTO_LOOSEN_COOLDOWN_SEC. While hot with bumps
still present, further auto-tighten is paused.

Never touches exits, stops, profit targets, 15-min hold, or forced sells.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from config import Config, resolve_data_path

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: dict[str, Any] = {
    "wins": 0,
    "losses": 0,
    "tighten_level": 0,
    "win_lean_bump": 0.0,
    "liquidity_bump_usd": 0.0,
    "last_tighten_trade_count": 0,
    "last_tighten_at": 0.0,
    "last_loosen_at": 0.0,
    "base_win_lean": None,
    "base_min_liquidity_usd": None,
    "base_spike_min_liquidity_usd": None,
    "base_gmgn_min_liquidity_usd": None,
}


def _log_path() -> Path:
    return resolve_data_path("data/entry_tuning_log.jsonl")


def reset_session() -> None:
    """Clear per-bot-session counters (call on bot start)."""
    global _state
    with _lock:
        _state = {
            "wins": 0,
            "losses": 0,
            "tighten_level": 0,
            "win_lean_bump": 0.0,
            "liquidity_bump_usd": 0.0,
            "last_tighten_trade_count": 0,
            "last_tighten_at": 0.0,
            "last_loosen_at": 0.0,
            "base_win_lean": Config.SETUP_LEARNING_MIN_WIN_LEAN,
            "base_min_liquidity_usd": Config.MIN_LIQUIDITY_USD,
            "base_spike_min_liquidity_usd": Config.SPIKE_MIN_LIQUIDITY_USD,
            "base_gmgn_min_liquidity_usd": Config.GMGN_MIN_LIQUIDITY_USD,
        }


def record_exit(net_pnl_sol: float) -> None:
    """Track a completed round-trip for session win-rate."""
    with _lock:
        if net_pnl_sol >= 0:
            _state["wins"] = int(_state.get("wins", 0)) + 1
        else:
            _state["losses"] = int(_state.get("losses", 0)) + 1


def session_trade_count() -> int:
    with _lock:
        return int(_state.get("wins", 0)) + int(_state.get("losses", 0))


def session_win_rate() -> Optional[float]:
    with _lock:
        total = int(_state.get("wins", 0)) + int(_state.get("losses", 0))
        if total <= 0:
            return None
        return int(_state.get("wins", 0)) / total


def effective_setup_learning_min_win_lean() -> float:
    """Runtime win-lean threshold including session auto-tighten bump."""
    with _lock:
        base = _state.get("base_win_lean")
        bump = float(_state.get("win_lean_bump", 0.0))
    if base is None:
        base = Config.SETUP_LEARNING_MIN_WIN_LEAN
    cap = Config.SESSION_AUTO_TIGHTEN_WIN_LEAN_CAP
    return min(float(base) + bump, cap)


def has_session_tighten_bumps() -> bool:
    """True when session auto-tighten still has entry floors above base."""
    with _lock:
        return (
            float(_state.get("win_lean_bump", 0.0)) > 1e-12
            or float(_state.get("liquidity_bump_usd", 0.0)) > 1e-12
            or int(_state.get("tighten_level", 0)) > 0
        )


def apply_runtime_win_lean(new_lean: float) -> None:
    """Sync session base when win-lean is changed via runtime config API."""
    with _lock:
        base = _state.get("base_win_lean")
        bump = float(_state.get("win_lean_bump", 0.0))
        if base is None:
            base = Config.SETUP_LEARNING_MIN_WIN_LEAN
        current = float(base) + bump
        new_lean = float(new_lean)
        _state["base_win_lean"] = new_lean
        if new_lean < current:
            _state["win_lean_bump"] = 0.0


def _append_log(entry: dict) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as exc:
        logger.warning("session_entry_tuning log failed: %s", exc)


def _apply_entry_floors(new_bump: float, new_liq_bump: float) -> dict[str, Any]:
    """Write effective win-lean + liquidity floors to runtime Config (entry only)."""
    with _lock:
        base_lean = _state.get("base_win_lean")
        if base_lean is None:
            base_lean = Config.SETUP_LEARNING_MIN_WIN_LEAN
        base_liq = _state.get("base_min_liquidity_usd") or Config.MIN_LIQUIDITY_USD
        base_spike = _state.get("base_spike_min_liquidity_usd") or Config.SPIKE_MIN_LIQUIDITY_USD
        base_gmgn = _state.get("base_gmgn_min_liquidity_usd") or Config.GMGN_MIN_LIQUIDITY_USD

    lean_cap = Config.SESSION_AUTO_TIGHTEN_WIN_LEAN_CAP
    liq_cap = Config.SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD
    new_win_lean = min(float(base_lean) + new_bump, lean_cap)
    new_min_liq = min(float(base_liq) + new_liq_bump, liq_cap)
    new_spike_liq = min(float(base_spike) + new_liq_bump, liq_cap)
    new_gmgn_liq = min(float(base_gmgn or new_min_liq) + new_liq_bump, liq_cap)

    applied = Config.update_runtime(
        SETUP_LEARNING_MIN_WIN_LEAN=new_win_lean,
        MIN_LIQUIDITY_USD=new_min_liq,
        SPIKE_MIN_LIQUIDITY_USD=new_spike_liq,
        GMGN_MIN_LIQUIDITY_USD=new_gmgn_liq,
    )
    return {
        "win_lean": new_win_lean,
        "min_liquidity_usd": new_min_liq,
        "spike_min_liquidity_usd": new_spike_liq,
        "gmgn_min_liquidity_usd": new_gmgn_liq,
        "runtime_applied": applied.get("applied", {}),
    }


def maybe_auto_tighten(
    target_win_rate: float,
    *,
    market_regime: Optional[str] = None,
) -> dict[str, Any]:
    """
    When session WR trails the regime target after enough trades, bump entry
    selectivity (win-lean + liquidity). Returns action summary (may be empty).

    While market is hot and session bumps remain, tighten is paused so pickup
    loosen can unwind toward Steady/session base.
    """
    if not Config.SESSION_AUTO_TIGHTEN_ENABLED:
        return {"action": "disabled"}

    if (
        market_regime == "hot"
        and Config.SESSION_AUTO_LOOSEN_ENABLED
        and has_session_tighten_bumps()
    ):
        return {
            "action": "paused_hot_loosen",
            "market_regime": market_regime,
            "tighten_level": int(_state.get("tighten_level", 0)),
            "win_lean_bump": float(_state.get("win_lean_bump", 0.0)),
            "liquidity_bump_usd": float(_state.get("liquidity_bump_usd", 0.0)),
        }

    min_trades = Config.SESSION_AUTO_TIGHTEN_MIN_TRADES
    wr = session_win_rate()
    trades = session_trade_count()
    if wr is None or trades < min_trades:
        return {
            "action": "hold",
            "trade_count": trades,
            "win_rate": wr,
            "target_win_rate": target_win_rate,
            "min_trades": min_trades,
        }

    if wr >= target_win_rate:
        return {
            "action": "ok",
            "trade_count": trades,
            "win_rate": wr,
            "target_win_rate": target_win_rate,
        }

    with _lock:
        last_count = int(_state.get("last_tighten_trade_count", 0))
        if trades <= last_count:
            return {
                "action": "already_tightened_at_count",
                "trade_count": trades,
                "win_rate": wr,
                "target_win_rate": target_win_rate,
                "tighten_level": _state.get("tighten_level", 0),
            }

        level = int(_state.get("tighten_level", 0)) + 1
        lean_step = Config.SESSION_AUTO_TIGHTEN_WIN_LEAN_STEP
        liq_step = Config.SESSION_AUTO_TIGHTEN_LIQUIDITY_STEP_USD
        lean_cap = Config.SESSION_AUTO_TIGHTEN_WIN_LEAN_CAP
        liq_cap = Config.SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD

        base_lean = _state.get("base_win_lean")
        if base_lean is None:
            base_lean = Config.SETUP_LEARNING_MIN_WIN_LEAN
        new_bump = min(
            float(_state.get("win_lean_bump", 0.0)) + lean_step,
            max(0.0, lean_cap - float(base_lean)),
        )
        new_liq_bump = min(
            float(_state.get("liquidity_bump_usd", 0.0)) + liq_step,
            max(0.0, liq_cap - float(_state.get("base_min_liquidity_usd") or Config.MIN_LIQUIDITY_USD)),
        )

        _state["tighten_level"] = level
        _state["win_lean_bump"] = new_bump
        _state["liquidity_bump_usd"] = new_liq_bump
        _state["last_tighten_trade_count"] = trades
        _state["last_tighten_at"] = time.time()

    floors = _apply_entry_floors(new_bump, new_liq_bump)

    summary = {
        "action": "tightened",
        "trade_count": trades,
        "win_rate": round(wr, 4),
        "target_win_rate": target_win_rate,
        "tighten_level": level,
        **floors,
        "ts": time.time(),
    }
    _append_log(summary)
    logger.warning(
        "Session auto-tighten L%d: WR %.1f%% < target %.1f%% after %d trades — "
        "win_lean=%.3f min_liq=$%.0f (entry only)",
        level,
        wr * 100,
        target_win_rate * 100,
        trades,
        floors["win_lean"],
        floors["min_liquidity_usd"],
    )
    return summary


def maybe_auto_loosen(regime_snapshot: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """
    When market "picks up" (hot regime sustained), step session tighten bumps
    back toward session base / Steady defaults.

    Market pickup definition (code):
      market_regime_snapshot["market_regime"] == "hot"
      (same as Hot Market Mode: SOL 1h/4h hot thresholds + scanner candidates)
      AND hot_since is at least SESSION_AUTO_LOOSEN_HOT_HOLD_SEC ago.

    Then every SESSION_AUTO_LOOSEN_COOLDOWN_SEC, reduce one tighten level
    (win_lean_bump and liquidity_bump_usd by configured steps, floor at 0).
    Entry filters only — never weakens spike/instant-dump or any exit.
    """
    if not Config.SESSION_AUTO_LOOSEN_ENABLED:
        return {"action": "disabled"}

    snap = regime_snapshot or {}
    regime = snap.get("market_regime")
    hot_since = snap.get("hot_since")
    now = time.time()

    if not has_session_tighten_bumps():
        return {
            "action": "nothing_to_loosen",
            "market_regime": regime,
            "tighten_level": int(_state.get("tighten_level", 0)),
        }

    if regime != "hot" or not hot_since:
        return {
            "action": "hold_not_hot",
            "market_regime": regime,
            "hot_since": hot_since,
            "tighten_level": int(_state.get("tighten_level", 0)),
            "win_lean_bump": float(_state.get("win_lean_bump", 0.0)),
            "liquidity_bump_usd": float(_state.get("liquidity_bump_usd", 0.0)),
        }

    hot_for = now - float(hot_since)
    hold_need = float(Config.SESSION_AUTO_LOOSEN_HOT_HOLD_SEC)
    if hot_for < hold_need:
        return {
            "action": "hold_hot_warming",
            "market_regime": regime,
            "hot_for_sec": round(hot_for, 1),
            "hot_hold_sec": hold_need,
            "tighten_level": int(_state.get("tighten_level", 0)),
        }

    with _lock:
        last_loosen = float(_state.get("last_loosen_at", 0.0) or 0.0)
        cooldown = float(Config.SESSION_AUTO_LOOSEN_COOLDOWN_SEC)
        if last_loosen > 0 and (now - last_loosen) < cooldown:
            return {
                "action": "cooldown",
                "market_regime": regime,
                "cooldown_remaining_sec": round(cooldown - (now - last_loosen), 1),
                "tighten_level": int(_state.get("tighten_level", 0)),
            }

        lean_step = float(Config.SESSION_AUTO_LOOSEN_WIN_LEAN_STEP)
        liq_step = float(Config.SESSION_AUTO_LOOSEN_LIQUIDITY_STEP_USD)
        prev_level = int(_state.get("tighten_level", 0))
        prev_bump = float(_state.get("win_lean_bump", 0.0))
        prev_liq = float(_state.get("liquidity_bump_usd", 0.0))

        new_bump = max(0.0, prev_bump - lean_step)
        new_liq_bump = max(0.0, prev_liq - liq_step)
        new_level = max(0, prev_level - 1)
        # If bumps already at floor but level was inflated while capped, clear level.
        if new_bump <= 0 and new_liq_bump <= 0:
            new_level = 0

        _state["win_lean_bump"] = new_bump
        _state["liquidity_bump_usd"] = new_liq_bump
        _state["tighten_level"] = new_level
        _state["last_loosen_at"] = now

    floors = _apply_entry_floors(new_bump, new_liq_bump)
    summary = {
        "action": "loosened",
        "market_regime": regime,
        "hot_for_sec": round(hot_for, 1),
        "tighten_level": new_level,
        "prev_tighten_level": prev_level,
        "win_lean_bump": new_bump,
        "liquidity_bump_usd": new_liq_bump,
        **floors,
        "ts": now,
    }
    _append_log(summary)
    logger.warning(
        "Session auto-loosen L%d->L%d after %.0fs hot: win_lean=%.3f min_liq=$%.0f "
        "(entry only; exits unchanged)",
        prev_level,
        new_level,
        hot_for,
        floors["win_lean"],
        floors["min_liquidity_usd"],
    )
    return summary


def status_snapshot(target_win_rate: Optional[float] = None) -> dict[str, Any]:
    wr = session_win_rate()
    return {
        "enabled": Config.SESSION_AUTO_TIGHTEN_ENABLED,
        "loosen_enabled": Config.SESSION_AUTO_LOOSEN_ENABLED,
        "trade_count": session_trade_count(),
        "win_rate": wr,
        "target_win_rate": target_win_rate,
        "tighten_level": int(_state.get("tighten_level", 0)),
        "win_lean_effective": effective_setup_learning_min_win_lean(),
        "win_lean_bump": float(_state.get("win_lean_bump", 0.0)),
        "liquidity_bump_usd": float(_state.get("liquidity_bump_usd", 0.0)),
        "last_loosen_at": float(_state.get("last_loosen_at", 0.0) or 0.0),
        "has_tighten_bumps": has_session_tighten_bumps(),
    }
