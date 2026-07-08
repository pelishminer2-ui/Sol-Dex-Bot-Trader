"""Track session and recent trade activity for minimum-funding waiver logic."""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from config import Config

logger = logging.getLogger(__name__)

_TRADE_ACTIONS = frozenset({"buy", "sell", "sell_partial"})


class TradeActivityTracker:
    """Thread-safe session trade count and last-trade timestamp (paper or live)."""

    def __init__(
        self,
        journal_path: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._lock = threading.RLock()
        self._journal_path = journal_path
        self._clock = clock or time.time
        self._session_trade_count = 0
        self._session_active = False
        self._last_trade_at: Optional[float] = None
        self.refresh_from_journal()

    def _journal_file(self) -> Path:
        if self._journal_path is not None:
            return self._journal_path
        return Path(Config.TRADE_JOURNAL_PATH)

    def refresh_from_journal(self) -> None:
        """Reload last trade timestamp from trades.jsonl (survives session end / restart)."""
        journal_at = self._scan_journal_last_trade_at()
        if journal_at is None:
            return
        with self._lock:
            if self._last_trade_at is None or journal_at > self._last_trade_at:
                self._last_trade_at = journal_at

    def start_session(self) -> None:
        with self._lock:
            self._session_trade_count = 0
            self._session_active = True
        self.refresh_from_journal()

    def end_session(self) -> None:
        with self._lock:
            self._session_active = False
        self.refresh_from_journal()

    def record_trade(self, event: dict) -> None:
        action = event.get("action", "")
        if action not in _TRADE_ACTIONS:
            return
        ts = event.get("timestamp")
        try:
            trade_at = float(ts) if ts is not None else self._clock()
        except (TypeError, ValueError):
            trade_at = self._clock()

        with self._lock:
            if self._session_active:
                self._session_trade_count += 1
            self._last_trade_at = trade_at

    def get_session_trade_count(self) -> int:
        with self._lock:
            if self._session_active:
                return self._session_trade_count
        try:
            from paper_session import paper_session_manager

            return paper_session_manager.get_session_trade_count()
        except Exception:
            return 0

    def session_has_trades(self) -> bool:
        return self.get_session_trade_count() > 0

    def get_last_trade_at(self) -> Optional[float]:
        return self._latest_trade_at()

    def has_trades_in_last_hour(self, hours: Optional[float] = None) -> bool:
        """True when trades.jsonl contains a trade within the min-fund waiver window."""
        window_hours = float(
            Config.MIN_FUND_WAIVER_HOURS if hours is None else hours
        )
        if window_hours <= 0:
            return False
        cutoff = self._clock() - window_hours * 3600.0
        path = self._journal_file()
        if not path.exists():
            return False
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return False
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("action") not in _TRADE_ACTIONS:
                continue
            ts = event.get("timestamp")
            if ts is None:
                continue
            try:
                trade_at = float(ts)
            except (TypeError, ValueError):
                continue
            if trade_at >= cutoff:
                return True
            break
        return False

    def min_fund_waived(self) -> bool:
        if self.has_trades_in_last_hour():
            return True
        if Config.MIN_FUND_WAIVER_AFTER_SESSION_TRADE:
            if self.session_has_trades():
                return True
            try:
                from paper_session import paper_session_manager

                if paper_session_manager.session_has_trades():
                    return True
            except Exception:
                pass
            if self._pnl_session_has_recent_trades():
                return True
        last_at = self._latest_trade_at()
        if last_at is None:
            return False
        hours = max(float(Config.MIN_FUND_WAIVER_HOURS), 0.0)
        if hours <= 0:
            return False
        return (self._clock() - last_at) < hours * 3600.0

    def _pnl_session_has_recent_trades(self) -> bool:
        """True when session_pnl.json shows trades within the waiver window."""
        try:
            from pnl_tracker import pnl_tracker
        except Exception:
            return False

        pnl = pnl_tracker.get_running_pnl()
        trade_count = int(pnl.get("trade_count", 0))
        if trade_count <= 0:
            return False

        hours = max(float(Config.MIN_FUND_WAIVER_HOURS), 0.0)
        if hours <= 0:
            return False
        cutoff = self._clock() - hours * 3600.0

        for trade in pnl.get("recent_trades") or []:
            ts = trade.get("timestamp")
            if ts is not None and float(ts) >= cutoff:
                return True

        history = pnl.get("running_pnl_history") or []
        for point in reversed(history):
            ts = point.get("timestamp")
            if ts is None:
                continue
            if float(ts) >= cutoff:
                return True
            break

        started = pnl.get("started_at")
        if started is not None and float(started) >= cutoff:
            return True
        return False

    def waiver_block_detail(self) -> str:
        """Explain why min-fund waiver did not apply (for start-block error messages)."""
        reasons: list[str] = []
        hours = Config.MIN_FUND_WAIVER_HOURS

        if not Config.MIN_FUND_WAIVER_AFTER_SESSION_TRADE:
            reasons.append("MIN_FUND_WAIVER_AFTER_SESSION_TRADE=false")
        else:
            session_trades = self.get_session_trade_count()
            if session_trades <= 0:
                try:
                    from paper_session import paper_session_manager

                    session_trades = paper_session_manager.get_session_trade_count()
                except Exception:
                    pass
            if session_trades <= 0:
                reasons.append("no trades in current/last paper session")
            pnl_trades = 0
            try:
                from pnl_tracker import pnl_tracker

                pnl_trades = int(pnl_tracker.get_running_pnl().get("trade_count", 0))
            except Exception:
                pass
            if pnl_trades <= 0:
                reasons.append("session_pnl has no trades")
            elif not self._pnl_session_has_recent_trades():
                reasons.append(
                    f"session_pnl trades older than {hours:g}h waiver window"
                )

        path = self._journal_file()
        if not path.exists():
            reasons.append(f"journal missing at {path}")
        elif not self.has_trades_in_last_hour():
            reasons.append(f"no journal trades within last {hours:g}h")

        last_at = self._latest_trade_at()
        if last_at is None:
            reasons.append("no last trade timestamp found")
        else:
            age_min = (self._clock() - last_at) / 60.0
            if age_min >= hours * 60.0:
                reasons.append(f"last trade {age_min:.0f}m ago (>{hours:g}h window)")

        return "; ".join(reasons) if reasons else "waiver conditions not met"

    def _latest_trade_at(self) -> Optional[float]:
        self.refresh_from_journal()
        with self._lock:
            memory_at = self._last_trade_at
        candidates = [memory_at]
        try:
            from paper_session import paper_session_manager

            candidates.append(paper_session_manager.get_last_trade_at())
        except Exception:
            pass
        journal_at = self._scan_journal_last_trade_at()
        if journal_at is not None:
            candidates.append(journal_at)
        times = [t for t in candidates if t is not None]
        return max(times) if times else None

    def status_fields(self) -> dict:
        session_trades = self.get_session_trade_count()
        waived = self.min_fund_waived()
        journal_recent = self.has_trades_in_last_hour()
        return {
            "session_has_trades": session_trades > 0 or self.session_has_trades(),
            "session_trade_count": session_trades,
            "last_trade_at": self._latest_trade_at(),
            "min_fund_waived": waived,
            "min_fund_waiver_active": waived,
            "journal_trades_in_waiver_window": journal_recent,
        }

    def _scan_journal_last_trade_at(self) -> Optional[float]:
        path = self._journal_file()
        if not path.exists():
            return None
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return None
        last_at: Optional[float] = None
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("action") not in _TRADE_ACTIONS:
                continue
            ts = event.get("timestamp")
            if ts is None:
                continue
            try:
                last_at = float(ts)
            except (TypeError, ValueError):
                continue
            break
        return last_at


trade_activity = TradeActivityTracker()
