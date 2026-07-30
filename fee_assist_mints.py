"""Chart-assist / fee-volume mint list (mint-primary).

These legs are scheduled rotation / volume assists — they count toward volume
and tax row presence, but must NOT count as real W/L for session win-rate,
consecutive-loss pause, setup_learning centroids, or tax/session loss totals.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import Config, resolve_data_path

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_entries: Dict[str, dict] = {}
_loaded = False
_file_mtime: float = -1.0

# Seed when data/fee_assist_mints.json is missing (Bonga / VETT / COC).
DEFAULT_FEE_ASSIST_SEED: Dict[str, dict] = {
    "2ggeqB3ZhbGCqGi7q4BkoTPZP1sb2WMVsfdFSpLGVETT": {
        "symbol": "$VETT",
        "note": "scheduled rotation / chart-assist fee volume",
    },
    "7YoAymCyauHAXus3snMEKcLgRx546MrHuBW3EuUNKKQs": {
        "symbol": "BONGA",
        "note": "scheduled rotation / chart-assist fee volume",
    },
    "6M8z5Wzmhk93ns6BaQzCuMYvkEpFcx9CDXsgFwK58NPf": {
        "symbol": "COC",
        "note": "scheduled rotation / chart-assist fee volume",
    },
}


def _store_path() -> Path:
    raw = getattr(Config, "FEE_ASSIST_MINTS_PATH", "data/fee_assist_mints.json")
    return resolve_data_path(str(raw))


def _path_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime) if path.exists() else 0.0
    except OSError:
        return 0.0


def _ensure_loaded() -> None:
    global _loaded, _file_mtime
    with _lock:
        path = _store_path()
        mtime = _path_mtime(path)
        if _loaded and mtime == _file_mtime:
            return
        _load_unlocked()
        _file_mtime = mtime
        _loaded = True


def _load_unlocked() -> None:
    global _entries
    path = _store_path()
    if not path.exists():
        _entries = {
            mint: {
                "mint": mint,
                "symbol": str(meta.get("symbol") or ""),
                "note": str(meta.get("note") or ""),
                "added_at": 0.0,
            }
            for mint, meta in DEFAULT_FEE_ASSIST_SEED.items()
        }
        try:
            _save_unlocked()
        except OSError as exc:
            logger.warning("fee_assist_mints seed write failed: %s", exc)
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("fee_assist_mints load failed: %s", exc)
        _entries = {}
        return
    if not isinstance(raw, dict):
        _entries = {}
        return
    mints = raw.get("mints")
    cleaned: Dict[str, dict] = {}
    if isinstance(mints, dict):
        for mint, rec in mints.items():
            key = str(mint or "").strip()
            if not key:
                continue
            if not isinstance(rec, dict):
                rec = {}
            cleaned[key] = {
                "mint": key,
                "symbol": str(rec.get("symbol") or ""),
                "note": str(rec.get("note") or ""),
                "added_at": float(rec.get("added_at") or 0.0),
            }
    _entries = cleaned


def _save_unlocked() -> None:
    global _file_mtime
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": time.time(),
        "mints": dict(sorted(_entries.items())),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    _file_mtime = _path_mtime(path)


def reload_fee_assist_mints() -> int:
    global _loaded, _file_mtime
    with _lock:
        _load_unlocked()
        _file_mtime = _path_mtime(_store_path())
        _loaded = True
        return len(_entries)


def is_fee_assist_mint(mint: str) -> bool:
    """True when this exact mint is a chart-assist / fee-volume trade."""
    mint = (mint or "").strip()
    if not mint:
        return False
    _ensure_loaded()
    with _lock:
        return mint in _entries


def fee_assist_record(mint: str) -> Optional[dict]:
    mint = (mint or "").strip()
    if not mint:
        return None
    _ensure_loaded()
    with _lock:
        rec = _entries.get(mint)
        return dict(rec) if rec else None


def fee_assist_mints() -> frozenset[str]:
    _ensure_loaded()
    with _lock:
        return frozenset(_entries.keys())


def list_fee_assist_mints() -> List[dict]:
    _ensure_loaded()
    with _lock:
        return [dict(rec) for _, rec in sorted(_entries.items())]


def annotate_fee_assist_journal(journal: Optional[dict]) -> dict:
    """Tag a sell/buy journal when mint is on the fee-assist list. Mutates + returns."""
    if not isinstance(journal, dict):
        return journal or {}
    mint = str(journal.get("mint") or "").strip()
    if mint and is_fee_assist_mint(mint):
        journal["fee_assist"] = True
        journal["trade_kind"] = "fee_volume_assist"
        if not journal.get("fee_assist_note"):
            rec = fee_assist_record(mint) or {}
            journal["fee_assist_note"] = rec.get("note") or "chart-assist fee volume"
    return journal


def journal_is_fee_assist(journal: Optional[dict]) -> bool:
    if not isinstance(journal, dict):
        return False
    if journal.get("fee_assist") or str(journal.get("trade_kind") or "") == "fee_volume_assist":
        return True
    return is_fee_assist_mint(str(journal.get("mint") or ""))


def status_snapshot() -> Dict[str, Any]:
    rows = list_fee_assist_mints()
    return {
        "count": len(rows),
        "mints": rows,
        "path": str(_store_path()),
    }
