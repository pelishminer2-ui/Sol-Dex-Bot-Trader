"""Closed-loop session entry tightening when win rate trails regime target.

Entry selection only — never touches exits, stops, or profit targets.
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


def _append_log(entry: dict) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as exc:
        logger.warning("session_entry_tuning log failed: %s", exc)


def maybe_auto_tighten(target_win_rate: float) -> dict[str, Any]:
    """
    When session WR trails the regime target after enough trades, bump entry
    selectivity (win-lean + liquidity). Returns action summary (may be empty).
    """
    if not Config.SESSION_AUTO_TIGHTEN_ENABLED:
        return {"action": "disabled"}

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

    new_win_lean = effective_setup_learning_min_win_lean()
    base_liq = _state.get("base_min_liquidity_usd") or Config.MIN_LIQUIDITY_USD
    new_min_liq = min(
        float(base_liq) + new_liq_bump,
        Config.SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD,
    )
    base_spike = _state.get("base_spike_min_liquidity_usd") or Config.SPIKE_MIN_LIQUIDITY_USD
    new_spike_liq = min(
        float(base_spike) + new_liq_bump,
        Config.SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD,
    )
    base_gmgn = _state.get("base_gmgn_min_liquidity_usd") or Config.GMGN_MIN_LIQUIDITY_USD
    new_gmgn_liq = min(
        float(base_gmgn or new_min_liq) + new_liq_bump,
        Config.SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD,
    )

    applied = Config.update_runtime(
        SETUP_LEARNING_MIN_WIN_LEAN=new_win_lean,
        MIN_LIQUIDITY_USD=new_min_liq,
        SPIKE_MIN_LIQUIDITY_USD=new_spike_liq,
        GMGN_MIN_LIQUIDITY_USD=new_gmgn_liq,
    )

    summary = {
        "action": "tightened",
        "trade_count": trades,
        "win_rate": round(wr, 4),
        "target_win_rate": target_win_rate,
        "tighten_level": level,
        "win_lean": new_win_lean,
        "min_liquidity_usd": new_min_liq,
        "spike_min_liquidity_usd": new_spike_liq,
        "gmgn_min_liquidity_usd": new_gmgn_liq,
        "runtime_applied": applied.get("applied", {}),
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
        new_win_lean,
        new_min_liq,
    )
    return summary


def status_snapshot(target_win_rate: Optional[float] = None) -> dict[str, Any]:
    wr = session_win_rate()
    return {
        "enabled": Config.SESSION_AUTO_TIGHTEN_ENABLED,
        "trade_count": session_trade_count(),
        "win_rate": wr,
        "target_win_rate": target_win_rate,
        "tighten_level": int(_state.get("tighten_level", 0)),
        "win_lean_effective": effective_setup_learning_min_win_lean(),
        "win_lean_bump": float(_state.get("win_lean_bump", 0.0)),
        "liquidity_bump_usd": float(_state.get("liquidity_bump_usd", 0.0)),
    }
