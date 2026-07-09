"""Smart re-chase retry for loss-blocked tickers — escalating pause / user action."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import Config, resolve_data_path
from scanner import MoverCandidate
from setup_learner import normalize_setup_features
from similarity import _candidate_vector, _cosine_similarity

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.85
_lock = threading.Lock()


class ReentryRetryManager:
    def __init__(self, path: Path):
        self.path = path
        self.mints: Dict[str, dict] = {}
        self.denied_signatures: List[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.mints = raw.get("mints", {})
            self.denied_signatures = raw.get("denied_signatures", [])
        except Exception as exc:
            logger.warning("reentry_retry load failed: %s", exc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"mints": self.mints, "denied_signatures": self.denied_signatures}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _now(self) -> float:
        return time.time()

    def is_active(self) -> bool:
        return Config.reentry_retry_is_active()

    def _mint_record(self, mint: str, symbol: str = "") -> dict:
        rec = self.mints.get(mint)
        if rec is None:
            rec = {
                "mint": mint,
                "symbol": symbol,
                "failed_attempts": 0,
                "retry_window_until": 0.0,
                "block_until": 0.0,
                "pending_user_action": False,
                "retry_entry_active": False,
                "signature": {},
                "user_decision": None,
            }
            self.mints[mint] = rec
        elif symbol and not rec.get("symbol"):
            rec["symbol"] = symbol
        return rec

    def loss_signature_from_candidate(self, candidate: MoverCandidate) -> dict:
        return normalize_setup_features(
            {
                "momentum_pct": candidate.momentum_pct,
                "liquidity_usd": candidate.liquidity_usd,
                "volume_24h_usd": candidate.volume_24h_usd,
                "price_change_5m": candidate.price_change_5m,
                "price_change_1h": candidate.price_change_1h,
                "price_change_6h": getattr(candidate, "price_change_6h", 0.0),
                "price_change_24h": getattr(candidate, "price_change_24h", 0.0),
                "entry_price_impact_pct": 0.0,
                "is_pumpfun_route": candidate.source == "pumpfun",
                "hold_time_sec": 0.0,
                "scanner_source": candidate.source,
            }
        )

    def _matches_denied_pattern(self, candidate: MoverCandidate) -> bool:
        vec = _candidate_vector(candidate)
        for denied in self.denied_signatures:
            if _cosine_similarity(vec, denied) >= SIMILARITY_THRESHOLD:
                return True
        return False

    def entry_denied_for_candidate(
        self, candidate: MoverCandidate
    ) -> Tuple[bool, Optional[str]]:
        if not self.is_active():
            return False, None
        rec = self.mints.get(candidate.mint, {})
        if rec.get("pending_user_action") and not rec.get("user_decision"):
            return (
                True,
                f"re-chase pending user decision: {candidate.symbol}",
            )
        decision = rec.get("user_decision")
        if decision and not decision.get("allow"):
            return True, f"re-chase denied by user: {candidate.symbol}"
        if self._matches_denied_pattern(candidate):
            return True, f"re-chase denied (similar pattern): {candidate.symbol}"
        return False, None

    def bypasses_loss_block(self, mint: str) -> bool:
        if not self.is_active():
            return False
        now = self._now()
        rec = self.mints.get(mint, {})
        if rec.get("pending_user_action") and not rec.get("user_decision"):
            return False
        if rec.get("block_until", 0) > now:
            return False
        if rec.get("retry_entry_active"):
            return True
        return rec.get("retry_window_until", 0) > now

    def can_open_retry_window(self, mint: str) -> bool:
        if not self.is_active():
            return False
        now = self._now()
        rec = self.mints.get(mint, {})
        if rec.get("pending_user_action") and not rec.get("user_decision"):
            return False
        if rec.get("block_until", 0) > now:
            return False
        if rec.get("retry_entry_active"):
            return False
        if rec.get("retry_window_until", 0) > now:
            return False
        if rec.get("failed_attempts", 0) >= Config.REENTRY_RETRY_MAX_ATTEMPTS:
            return False
        decision = rec.get("user_decision")
        if decision and not decision.get("allow"):
            return False
        return True

    def open_retry_window(self, mint: str, symbol: str, signature: dict) -> bool:
        if not self.can_open_retry_window(mint):
            return False
        rec = self._mint_record(mint, symbol)
        rec["retry_window_until"] = self._now() + Config.REENTRY_RETRY_WINDOW_MINUTES * 60
        rec["signature"] = signature or {}
        self._save()
        logger.info(
            "Reentry retry: opened %d-min window for %s (%s)",
            Config.REENTRY_RETRY_WINDOW_MINUTES,
            symbol or mint[:8],
            mint[:8],
        )
        return True

    def is_retry_entry_pending(self, mint: str) -> bool:
        return self.bypasses_loss_block(mint)

    def mark_retry_entry(self, mint: str) -> None:
        rec = self._mint_record(mint)
        rec["retry_entry_active"] = True
        rec["retry_window_until"] = 0.0
        self._save()

    def record_retry_outcome(
        self,
        mint: str,
        *,
        symbol: str = "",
        won: bool,
        loss_signature: Optional[dict] = None,
    ) -> bool:
        rec = self.mints.get(mint)
        if not rec or not rec.get("retry_entry_active"):
            return False
        rec["retry_entry_active"] = False
        if won:
            self.mints.pop(mint, None)
            self._save()
            logger.info("Reentry retry: %s won — cleared retry state", symbol or mint[:8])
            return True
        rec["failed_attempts"] = rec.get("failed_attempts", 0) + 1
        rec["block_until"] = self._now() + Config.REENTRY_RETRY_BLOCK_HOURS * 3600
        if loss_signature:
            rec["signature"] = loss_signature
        if rec["failed_attempts"] >= Config.REENTRY_RETRY_MAX_ATTEMPTS:
            rec["pending_user_action"] = True
            logger.warning(
                "Reentry retry: %s failed %d times — user action required",
                symbol or mint[:8],
                rec["failed_attempts"],
            )
        else:
            logger.info(
                "Reentry retry: %s failed attempt %d — blocked %sh",
                symbol or mint[:8],
                rec["failed_attempts"],
                Config.REENTRY_RETRY_BLOCK_HOURS,
            )
        self._save()
        return True

    def get_pending_actions(self) -> List[dict]:
        pending: List[dict] = []
        for rec in self.mints.values():
            if not rec.get("pending_user_action") or rec.get("user_decision"):
                continue
            sig = rec.get("signature") or {}
            pending.append(
                {
                    "mint": rec["mint"],
                    "symbol": rec.get("symbol", ""),
                    "attempt_count": rec.get("failed_attempts", 0),
                    "momentum_pct": sig.get("momentum_pct"),
                    "is_pumpfun_route": bool(sig.get("is_pumpfun_route")),
                    "message": (
                        f"{rec.get('symbol') or rec['mint'][:8]} failed "
                        f"{rec.get('failed_attempts', 0)} retry entries — allow similar trades?"
                    ),
                }
            )
        return pending

    def apply_decision(
        self, mint: str, *, allow: bool, deny_similar_pattern: bool = False
    ) -> Dict[str, Any]:
        rec = self._mint_record(mint)
        rec["user_decision"] = {
            "allow": allow,
            "deny_similar": deny_similar_pattern,
            "decided_at": self._now(),
        }
        rec["pending_user_action"] = False
        if not allow and deny_similar_pattern and rec.get("signature"):
            self.denied_signatures.append(rec["signature"])
        if allow:
            rec["failed_attempts"] = 0
            rec["block_until"] = 0.0
        self._save()
        return {
            "mint": mint,
            "symbol": rec.get("symbol"),
            "allow": allow,
            "deny_similar_pattern": deny_similar_pattern,
        }

    def status_snapshot(self) -> dict:
        pending = self.get_pending_actions()
        return {
            "enabled": Config.REENTRY_RETRY_ENABLED,
            "active": self.is_active(),
            "window_minutes": Config.REENTRY_RETRY_WINDOW_MINUTES,
            "block_hours": Config.REENTRY_RETRY_BLOCK_HOURS,
            "max_attempts": Config.REENTRY_RETRY_MAX_ATTEMPTS,
            "pending_count": len(pending),
        }


def _store_path() -> Path:
    return resolve_data_path(Config.REENTRY_RETRY_STATE_PATH)


reentry_retry_manager = ReentryRetryManager(_store_path())
