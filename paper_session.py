"""24-hour paper trading session with cumulative P&L tracking."""

import csv
import io
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import Config, normalize_paper_balance_sol
from pnl_tracker import pnl_tracker

logger = logging.getLogger(__name__)

PAPER_EXPORT_COLUMNS = [
    "timestamp",
    "token_symbol",
    "action",
    "contract_address",
    "pnl_sol",
    "sol_in",
    "sol_out",
    "reason",
]


@dataclass
class PaperSession:
    start_time: Optional[float] = None
    active: bool = False
    simulated_balance: float = 0.0
    stop_reason: Optional[str] = None
    trade_count: int = 0
    last_trade_at: Optional[float] = None


class PaperSessionManager:
    """Thread-safe paper session state for dry-run trading."""

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._lock = threading.RLock()
        self._clock = clock or time.time
        self._path = Path(Config.PAPER_SESSION_STATE_PATH)
        self._target_balance_sol = Config.PAPER_SIMULATED_BALANCE_SOL
        self._session = PaperSession()
        self._last_session = PaperSession()
        self._load()

    def get_target_balance(self) -> float:
        with self._lock:
            return self._target_balance_sol

    def set_target_balance(self, amount: float) -> float:
        """Set the configured paper balance (persists; used on session start / reset)."""
        normalized = normalize_paper_balance_sol(amount)
        with self._lock:
            self._target_balance_sol = normalized
            Config.PAPER_SIMULATED_BALANCE_SOL = normalized
        self._persist()
        return normalized

    def reset_balance(self) -> float:
        """Reset running paper balance to the configured target amount."""
        with self._lock:
            target = self._target_balance_sol
            if self._session.active:
                self._session.simulated_balance = target
            elif self._last_session.start_time is not None:
                self._last_session.simulated_balance = target
        self._persist()
        return target

    def _persist(self) -> None:
        with self._lock:
            payload = {
                "target_balance_sol": self._target_balance_sol,
                "session": self._session_to_dict(self._session),
                "last_session": self._session_to_dict(self._last_session),
            }
        try:
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to persist paper session state: %s", exc)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        with self._lock:
            if "target_balance_sol" in data:
                try:
                    self._target_balance_sol = normalize_paper_balance_sol(
                        float(data["target_balance_sol"])
                    )
                    Config.PAPER_SIMULATED_BALANCE_SOL = self._target_balance_sol
                except (TypeError, ValueError):
                    pass
            if "session" in data:
                self._session = self._dict_to_session(data["session"])
            if "last_session" in data:
                self._last_session = self._dict_to_session(data["last_session"])

    @staticmethod
    def _session_to_dict(session: PaperSession) -> dict:
        return {
            "start_time": session.start_time,
            "active": session.active,
            "simulated_balance": session.simulated_balance,
            "stop_reason": session.stop_reason,
            "trade_count": session.trade_count,
            "last_trade_at": session.last_trade_at,
        }

    @staticmethod
    def _dict_to_session(data: dict) -> PaperSession:
        return PaperSession(
            start_time=data.get("start_time"),
            active=bool(data.get("active", False)),
            simulated_balance=float(data.get("simulated_balance", 0.0)),
            stop_reason=data.get("stop_reason"),
            trade_count=int(data.get("trade_count", 0)),
            last_trade_at=data.get("last_trade_at"),
        )

    def _duration_sec(self) -> float:
        return max(float(Config.PAPER_SESSION_HOURS), 0.0) * 3600.0

    @staticmethod
    def _copy_session(session: PaperSession, active: bool) -> PaperSession:
        return PaperSession(
            start_time=session.start_time,
            active=active,
            simulated_balance=session.simulated_balance,
            stop_reason=session.stop_reason,
            trade_count=session.trade_count,
            last_trade_at=session.last_trade_at,
        )

    def start_session(self) -> None:
        now = self._clock()
        with self._lock:
            self._session = PaperSession(
                start_time=now,
                active=True,
                simulated_balance=self._target_balance_sol,
                stop_reason=None,
                trade_count=0,
                last_trade_at=None,
            )
        self._persist()

    def end_session(self, stop_reason: Optional[str] = None) -> None:
        with self._lock:
            if not self._session.active:
                return
            if stop_reason:
                self._session.stop_reason = stop_reason
            self._last_session = self._copy_session(self._session, active=False)
            self._session.active = False
        self._persist()

    def get_simulated_balance(self) -> float:
        with self._lock:
            if self._session.active:
                return self._session.simulated_balance
            if self._last_session.start_time is not None:
                return self._last_session.simulated_balance
            return self._target_balance_sol

    def record_buy(self, sol_in: float) -> None:
        with self._lock:
            if not self._session.active:
                return
            self._session.simulated_balance = max(
                0.0, self._session.simulated_balance - float(sol_in)
            )
            self._session.trade_count += 1
            self._session.last_trade_at = self._clock()
        self._persist()

    def session_has_trades(self) -> bool:
        with self._lock:
            if self._session.active:
                return self._session.trade_count > 0
            return self._last_session.trade_count > 0

    def get_session_trade_count(self) -> int:
        with self._lock:
            if self._session.active:
                return self._session.trade_count
            return self._last_session.trade_count

    def get_last_trade_at(self) -> Optional[float]:
        with self._lock:
            if self._session.active and self._session.last_trade_at is not None:
                return self._session.last_trade_at
            if self._last_session.last_trade_at is not None:
                return self._last_session.last_trade_at
            return None

    def record_sell(self, sol_out: float) -> None:
        with self._lock:
            if not self._session.active:
                return
            self._session.simulated_balance += float(sol_out)
            self._session.trade_count += 1
            self._session.last_trade_at = self._clock()
        self._persist()

    def is_balance_insufficient_for_entry(self, trade_size: float) -> bool:
        with self._lock:
            if not self._session.active:
                return False
            balance = self._session.simulated_balance
            return (
                balance < Config.MIN_SOL_RESERVE + trade_size
                or balance <= Config.MIN_SOL_RESERVE
            )

    def is_active(self) -> bool:
        with self._lock:
            return self._session.active

    def is_session_expired(self) -> bool:
        with self._lock:
            if not self._session.active or self._session.start_time is None:
                return False
            if self._duration_sec() <= 0:
                return False
            elapsed = self._clock() - self._session.start_time
            return elapsed >= self._duration_sec()

    def remaining_sec(self) -> float:
        with self._lock:
            if not self._session.active or self._session.start_time is None:
                return 0.0
            duration = self._duration_sec()
            if duration <= 0:
                return 0.0
            return max(0.0, duration - (self._clock() - self._session.start_time))

    def record_paper_pnl(self, pnl_sol: float, symbol: str = "") -> None:
        """Record a paper sell P&L event (delegates to unified pnl_tracker)."""
        with self._lock:
            if not self._session.active:
                return
        pnl_tracker.record_from_journal(
            {
                "action": "sell",
                "pnl_sol": pnl_sol,
                "symbol": symbol,
                "timestamp": self._clock(),
            }
        )

    def _paper_pnl_fields(self) -> dict:
        pnl = pnl_tracker.get_running_pnl()
        if pnl.get("mode") != "paper":
            return {
                "paper_session_profit_sol": 0.0,
                "paper_session_losses_sol": 0.0,
                "paper_session_net_pnl_sol": 0.0,
                "paper_session_trade_count": 0,
            }
        return {
            "paper_session_profit_sol": pnl["profit_sol"],
            "paper_session_losses_sol": pnl["losses_sol"],
            "paper_session_net_pnl_sol": pnl["net_pnl_sol"],
            "paper_session_trade_count": pnl["trade_count"],
        }

    def _session_start_time(self) -> Optional[float]:
        with self._lock:
            if self._session.active and self._session.start_time is not None:
                return self._session.start_time
            if self._last_session.start_time is not None:
                return self._last_session.start_time
            return None

    def get_session_start_time(self) -> Optional[float]:
        """Public accessor for current or last paper session start timestamp."""
        return self._session_start_time()

    @staticmethod
    def _is_paper_trade(trade: dict) -> bool:
        return bool(trade.get("paper_trade") or trade.get("dry_run"))

    @staticmethod
    def _trade_to_preview_row(trade: dict) -> dict:
        action = trade.get("action", "")
        return {
            "timestamp": trade.get("timestamp"),
            "symbol": trade.get("symbol", "") or "?",
            "action": action,
            "pnl_sol": trade.get("pnl_sol"),
            "mint": trade.get("mint", ""),
            "contract_address": trade.get("mint", ""),
        }

    def read_session_trades(
        self, session_start: Optional[float] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Paper trades from the journal for the current (or given) session start."""
        start = session_start if session_start is not None else self._session_start_time()
        if start is None:
            return []

        path = Path(Config.TRADE_JOURNAL_PATH)
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return []

        rows: List[Dict[str, Any]] = []
        for line in reversed(lines):
            try:
                trade = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not self._is_paper_trade(trade):
                continue
            ts = trade.get("timestamp")
            if ts is None or float(ts) < start:
                continue
            rows.append(self._trade_to_preview_row(trade))
            if len(rows) >= limit:
                break
        return rows

    def export_session_csv(self) -> Optional[str]:
        """CSV of all paper exit trades in the current session (empty if no session)."""
        start = self._session_start_time()
        if start is None:
            return None

        path = Path(Config.TRADE_JOURNAL_PATH)
        if not path.exists():
            return None

        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return None

        export_rows: List[Dict[str, Any]] = []
        for line in lines:
            try:
                trade = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not self._is_paper_trade(trade):
                continue
            ts = trade.get("timestamp")
            if ts is None or float(ts) < start:
                continue
            action = trade.get("action", "")
            if action not in ("sell", "sell_partial"):
                continue
            export_rows.append(
                {
                    "timestamp": ts,
                    "token_symbol": trade.get("symbol", ""),
                    "action": action,
                    "contract_address": trade.get("mint", ""),
                    "pnl_sol": trade.get("pnl_sol", ""),
                    "sol_in": trade.get("sol_in_basis", trade.get("sol_in", "")),
                    "sol_out": trade.get("sol_out", ""),
                    "reason": trade.get("reason", ""),
                }
            )

        if not export_rows:
            return None

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=PAPER_EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(export_rows)
        return buf.getvalue()

    def _session_status_label(self, session: PaperSession, active: bool) -> str:
        if active:
            return "active"
        if session.start_time is not None:
            return "ended"
        return "not_started"

    def _stats_from(self, session: PaperSession, active: bool) -> dict:
        remaining = self.remaining_sec() if active else 0.0
        pnl = self._paper_pnl_fields()
        stats = {
            "paper_session_active": active,
            "paper_session_status": self._session_status_label(session, active),
            "paper_session_started_at": session.start_time,
            "paper_session_remaining_sec": remaining,
            "paper_simulated_balance_sol": session.simulated_balance,
            "paper_target_balance_sol": self._target_balance_sol,
            "paper_stop_reason": session.stop_reason,
            "recent_paper_trades": self.read_session_trades(session.start_time),
        }
        stats.update(pnl)
        stats["profit_sol"] = pnl["paper_session_profit_sol"]
        stats["losses_sol"] = pnl["paper_session_losses_sol"]
        stats["net_pnl_sol"] = pnl["paper_session_net_pnl_sol"]
        stats["trade_count"] = pnl["paper_session_trade_count"]
        return stats

    def get_session_stats(self) -> dict:
        with self._lock:
            if self._session.active:
                return self._stats_from(self._session, active=True)
            if self._last_session.start_time is not None:
                return self._stats_from(self._last_session, active=False)
            return {
                "paper_session_active": False,
                "paper_session_status": "not_started",
                "paper_session_started_at": None,
                "paper_session_remaining_sec": 0.0,
                "paper_simulated_balance_sol": self._target_balance_sol,
                "paper_target_balance_sol": self._target_balance_sol,
                "paper_stop_reason": None,
                "paper_session_profit_sol": 0.0,
                "paper_session_losses_sol": 0.0,
                "paper_session_net_pnl_sol": 0.0,
                "paper_session_trade_count": 0,
                "profit_sol": 0.0,
                "losses_sol": 0.0,
                "net_pnl_sol": 0.0,
                "trade_count": 0,
                "recent_paper_trades": [],
            }


paper_session_manager = PaperSessionManager()
