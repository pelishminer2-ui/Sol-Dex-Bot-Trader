"""WBTC pinned-watchlist entry gates — day gain, sustain timer, +3.25% feasibility."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional, Tuple

from config import Config, resolve_data_path
from proxy_entry_gate import (
    wbtc_day_gate_passes,
    wbtc_day_gate_skip_reason,
    wbtc_entry_rule_summary,
    wbtc_instant_gain_feasible_from_quotes,
)

logger = logging.getLogger(__name__)

_EPS = 1e-6


def _sustain_path() -> Path:
    path = getattr(Config, "WBTC_ENTRY_SUSTAIN_PATH", None)
    if path:
        return Path(path) if isinstance(path, Path) else resolve_data_path(str(path))
    return resolve_data_path("data/wbtc_entry_sustain.json")


def _load_sustain_state() -> dict:
    path = _sustain_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("WBTC sustain state unreadable (%s): %s", path, exc)
        return {}


def _save_sustain_state(state: dict) -> None:
    path = _sustain_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _reset_sustain_state(*, now: Optional[float] = None) -> None:
    ts = now if now is not None else time.time()
    _save_sustain_state({"first_met_at": None, "last_checked_at": ts})


def _touch_sustain_state(first_met_at: float, *, now: Optional[float] = None) -> None:
    ts = now if now is not None else time.time()
    _save_sustain_state({"first_met_at": first_met_at, "last_checked_at": ts})


def wbtc_sustain_gate_passes(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
    now: Optional[float] = None,
) -> bool:
    """
    True when day gate has been satisfied continuously for
    WBTC_DAY_GAIN_SUSTAIN_MINUTES (default 30).
    """
    ts = now if now is not None else time.time()
    if not wbtc_day_gate_passes(day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain):
        state = _load_sustain_state()
        if state.get("first_met_at") is not None:
            _reset_sustain_state(now=ts)
        return False

    state = _load_sustain_state()
    first_met = state.get("first_met_at")
    if first_met is None:
        _touch_sustain_state(ts, now=ts)
        return False

    sustain_sec = Config.WBTC_DAY_GAIN_SUSTAIN_MINUTES * 60
    return (ts - float(first_met)) >= sustain_sec - _EPS


def wbtc_sustain_skip_reason(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
    now: Optional[float] = None,
) -> Optional[str]:
    """Human-readable reason when sustain gate blocks WBTC entry."""
    day_reason = wbtc_day_gate_skip_reason(
        day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    )
    if day_reason:
        return day_reason

    if wbtc_sustain_gate_passes(
        day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain, now=now
    ):
        return None

    ts = now if now is not None else time.time()
    state = _load_sustain_state()
    first_met = state.get("first_met_at")
    required = Config.WBTC_DAY_GAIN_SUSTAIN_MINUTES
    if first_met is None:
        return (
            f"WBTC: day gate met — sustain timer started "
            f"({required}min continuous required)"
        )

    elapsed_min = max(0.0, (ts - float(first_met)) / 60.0)
    return (
        f"WBTC: day gate sustained {elapsed_min:.0f}min < "
        f"{required}min required"
    )


def wbtc_entry_skip_reason(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    """Combined day + sustain gate for WBTC watchlist mint."""
    from config import is_wbtc_watchlist_mint

    if not is_wbtc_watchlist_mint(candidate.mint):
        return None

    gain = day_usd_gain
    if gain is None:
        gain = getattr(candidate, "day_usd_gain", None)
    pct = day_pct_gain
    if pct is None:
        pct = getattr(candidate, "day_pct_gain", None)

    return wbtc_sustain_skip_reason(day_usd_gain=gain, day_pct_gain=pct)


def wbtc_entry_qualifies(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    return (
        wbtc_entry_skip_reason(
            candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
        )
        is None
    )


__all__ = [
    "wbtc_day_gate_passes",
    "wbtc_day_gate_skip_reason",
    "wbtc_entry_qualifies",
    "wbtc_entry_rule_summary",
    "wbtc_entry_skip_reason",
    "wbtc_instant_gain_feasible_from_quotes",
    "wbtc_sustain_gate_passes",
    "wbtc_sustain_skip_reason",
]
