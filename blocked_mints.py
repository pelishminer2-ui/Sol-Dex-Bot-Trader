"""Permanent no-trade blocklist: mint-primary + optional ticker blocks.

Solana reuses tickers. Default is mint-only so unrelated contracts sharing a
name stay tradeable. Explicit entries under ``symbols`` block that ticker
case-insensitively for entry and dip re-entry (in addition to mint blocks).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config import Config, resolve_data_path

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_logged_skipped: set[str] = set()
# mint -> {mint, symbol, reason, blocked_at, ...}
_entries: Dict[str, dict] = {}
# SYMBOL_UPPER -> {symbol, reason, blocked_at, note}
_symbol_entries: Dict[str, dict] = {}
_loaded = False
_file_mtime: float = -1.0


def _store_path() -> Path:
    raw = getattr(Config, "BLOCKED_MINTS_PATH", "data/blocked_mints.json")
    return resolve_data_path(str(raw))


def _norm_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _path_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime) if path.exists() else 0.0
    except OSError:
        return 0.0


def _ensure_loaded() -> None:
    """Load from disk; auto-reload when blocked_mints.json mtime changes (hot-apply)."""
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
    global _entries, _symbol_entries
    path = _store_path()
    if not path.exists():
        _entries = {}
        _symbol_entries = {}
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("blocked_mints load failed: %s", exc)
        _entries = {}
        _symbol_entries = {}
        return
    if not isinstance(raw, dict):
        _entries = {}
        _symbol_entries = {}
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
                "reason": str(rec.get("reason") or "permanent_block"),
                "blocked_at": float(rec.get("blocked_at") or 0.0),
                "note": str(rec.get("note") or ""),
            }
    _entries = cleaned

    symbols = raw.get("symbols")
    cleaned_sym: Dict[str, dict] = {}
    if isinstance(symbols, dict):
        for sym, rec in symbols.items():
            key = _norm_symbol(str(sym or ""))
            if not key:
                continue
            if not isinstance(rec, dict):
                rec = {}
            cleaned_sym[key] = {
                "symbol": key,
                "reason": str(rec.get("reason") or "permanent_ticker_block"),
                "blocked_at": float(rec.get("blocked_at") or 0.0),
                "note": str(rec.get("note") or ""),
            }
    _symbol_entries = cleaned_sym


def _save_unlocked() -> None:
    global _file_mtime
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "updated_at": time.time(),
        "mints": dict(sorted(_entries.items())),
        "symbols": dict(sorted(_symbol_entries.items())),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    _file_mtime = _path_mtime(path)


def reload_blocked_mints() -> int:
    """Force re-read from disk (tests / hot tools). Returns mint+symbol count."""
    global _loaded, _file_mtime
    with _lock:
        _load_unlocked()
        _file_mtime = _path_mtime(_store_path())
        _loaded = True
        return len(_entries) + len(_symbol_entries)


def is_mint_permanently_blocked(mint: str) -> bool:
    """True when this exact mint is on the permanent no-trade list."""
    mint = (mint or "").strip()
    if not mint:
        return False
    _ensure_loaded()
    with _lock:
        return mint in _entries


def is_symbol_permanently_blocked(symbol: str) -> bool:
    """True when this ticker (case-insensitive) is on the permanent block list."""
    key = _norm_symbol(symbol)
    if not key:
        return False
    _ensure_loaded()
    with _lock:
        return key in _symbol_entries


def is_permanently_blocked(mint: str = "", symbol: str = "") -> bool:
    """True when mint and/or symbol hits a permanent block."""
    if is_mint_permanently_blocked(mint):
        return True
    if is_symbol_permanently_blocked(symbol):
        return True
    return False


def blocked_mint_record(mint: str) -> Optional[dict]:
    mint = (mint or "").strip()
    if not mint:
        return None
    _ensure_loaded()
    with _lock:
        rec = _entries.get(mint)
        return dict(rec) if rec else None


def blocked_symbol_record(symbol: str) -> Optional[dict]:
    key = _norm_symbol(symbol)
    if not key:
        return None
    _ensure_loaded()
    with _lock:
        rec = _symbol_entries.get(key)
        return dict(rec) if rec else None


def permanently_blocked_mints() -> frozenset[str]:
    _ensure_loaded()
    with _lock:
        return frozenset(_entries.keys())


def permanently_blocked_symbols() -> frozenset[str]:
    _ensure_loaded()
    with _lock:
        return frozenset(_symbol_entries.keys())


def block_mint_permanently(
    mint: str,
    *,
    symbol: str = "",
    reason: str = "permanent_block",
    note: str = "",
) -> dict:
    """Add or refresh a mint-primary permanent block. Symbol is metadata only."""
    mint = (mint or "").strip()
    if not mint:
        raise ValueError("mint is required")
    _ensure_loaded()
    with _lock:
        prev = _entries.get(mint) or {}
        rec = {
            "mint": mint,
            "symbol": (symbol or prev.get("symbol") or "").strip(),
            "reason": (reason or prev.get("reason") or "permanent_block").strip(),
            "blocked_at": float(prev.get("blocked_at") or time.time()),
            "note": (note or prev.get("note") or "").strip(),
        }
        if not prev:
            rec["blocked_at"] = time.time()
        _entries[mint] = rec
        _save_unlocked()
        _logged_skipped.discard(mint)
        logger.warning(
            "Permanent mint block: %s (%s) — %s",
            mint,
            rec["symbol"] or "?",
            rec["reason"],
        )
        return dict(rec)


def block_symbol_permanently(
    symbol: str,
    *,
    reason: str = "permanent_ticker_block",
    note: str = "",
) -> dict:
    """Add or refresh a case-insensitive ticker permanent block."""
    key = _norm_symbol(symbol)
    if not key:
        raise ValueError("symbol is required")
    _ensure_loaded()
    with _lock:
        prev = _symbol_entries.get(key) or {}
        rec = {
            "symbol": key,
            "reason": (reason or prev.get("reason") or "permanent_ticker_block").strip(),
            "blocked_at": float(prev.get("blocked_at") or time.time()),
            "note": (note or prev.get("note") or "").strip(),
        }
        if not prev:
            rec["blocked_at"] = time.time()
        _symbol_entries[key] = rec
        _save_unlocked()
        _logged_skipped.discard(f"sym:{key}")
        logger.warning(
            "Permanent ticker block: %s — %s",
            key,
            rec["reason"],
        )
        return dict(rec)


def unblock_mint_permanently(mint: str) -> dict:
    """Remove a permanent mint block (admin / unblock API)."""
    mint = (mint or "").strip()
    if not mint:
        return {"mint": mint, "cleared": False, "reason": "empty_mint"}
    _ensure_loaded()
    with _lock:
        prev = _entries.pop(mint, None)
        if prev is None:
            return {"mint": mint, "cleared": False, "reason": "not_found"}
        _save_unlocked()
        _logged_skipped.discard(mint)
        logger.info(
            "Cleared permanent mint block: %s (%s)",
            mint,
            prev.get("symbol") or "?",
        )
        return {
            "mint": mint,
            "symbol": prev.get("symbol") or None,
            "cleared": True,
            "had_block": True,
        }


def unblock_symbol_permanently(symbol: str) -> dict:
    """Remove a permanent ticker block."""
    key = _norm_symbol(symbol)
    if not key:
        return {"symbol": symbol, "cleared": False, "reason": "empty_symbol"}
    _ensure_loaded()
    with _lock:
        prev = _symbol_entries.pop(key, None)
        if prev is None:
            return {"symbol": key, "cleared": False, "reason": "not_found"}
        _save_unlocked()
        _logged_skipped.discard(f"sym:{key}")
        logger.info("Cleared permanent ticker block: %s", key)
        return {
            "symbol": key,
            "cleared": True,
            "had_block": True,
        }


def permanent_block_skip_reason(mint: str, symbol: str = "") -> Optional[str]:
    """Human-readable skip when mint or ticker is permanently blocked."""
    mint = (mint or "").strip()
    sym = (symbol or "").strip()
    if mint and is_mint_permanently_blocked(mint):
        rec = blocked_mint_record(mint) or {}
        label = (sym or rec.get("symbol") or mint[:8]).strip()
        reason = rec.get("reason") or "permanent_block"
        return f"permanently blocked mint: {label} ({reason})"
    if sym and is_symbol_permanently_blocked(sym):
        rec = blocked_symbol_record(sym) or {}
        reason = rec.get("reason") or "permanent_ticker_block"
        return f"permanently blocked ticker: {_norm_symbol(sym)} ({reason})"
    return None


def log_skipped_blocked_mint(mint: str, symbol: str = "") -> None:
    mint = (mint or "").strip()
    sym_key = _norm_symbol(symbol)
    log_key = mint or (f"sym:{sym_key}" if sym_key else "")
    if not log_key or log_key in _logged_skipped:
        return
    reason = permanent_block_skip_reason(mint, symbol)
    if reason:
        _logged_skipped.add(log_key)
        logger.info("%s", reason)


def filter_permanently_blocked_candidates(candidates: Iterable) -> list:
    """Drop candidates whose mint or ticker is on the permanent blocklist."""
    kept = []
    for candidate in candidates:
        mint = getattr(candidate, "mint", "") or ""
        symbol = getattr(candidate, "symbol", "") or ""
        if is_permanently_blocked(mint, symbol):
            log_skipped_blocked_mint(mint, symbol or mint[:8])
            continue
        kept.append(candidate)
    return kept


def list_blocked_mints() -> List[dict]:
    _ensure_loaded()
    with _lock:
        return [dict(rec) for _, rec in sorted(_entries.items())]


def list_blocked_symbols() -> List[dict]:
    _ensure_loaded()
    with _lock:
        return [dict(rec) for _, rec in sorted(_symbol_entries.items())]


def status_snapshot() -> Dict[str, Any]:
    rows = list_blocked_mints()
    sym_rows = list_blocked_symbols()
    return {
        "count": len(rows),
        "mints": rows,
        "symbol_count": len(sym_rows),
        "symbols": sym_rows,
        "path": str(_store_path()),
    }


def reset_logged_skips() -> None:
    _logged_skipped.clear()
