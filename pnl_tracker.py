"""Unified running P&L tracker for paper and live trading sessions."""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import Config

logger = logging.getLogger(__name__)

MAX_RECENT_TRADES = 10
MAX_HISTORY_POINTS = 500


@dataclass
class PnlSession:
    mode: str = ""
    started_at: Optional[float] = None
    active: bool = False
    total_profit_sol: float = 0.0
    total_losses_sol: float = 0.0
    total_trade_count: int = 0
    running_pnl_history: List[Dict[str, float]] = field(default_factory=list)
    recent_trades: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def net_pnl_sol(self) -> float:
        return self.total_profit_sol - self.total_losses_sol


class PnlTracker:
    """Thread-safe cumulative session P&L for paper and live modes."""

    def __init__(
        self,
        path: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._lock = threading.RLock()
        self._path = path or Path(Config.SESSION_PNL_PATH)
        self._clock = clock or time.time
        self._session = PnlSession()
        self._last_session = PnlSession()
        self._load()

    def start_session(self, mode: str) -> None:
        now = self._clock()
        with self._lock:
            if self._session.active:
                self._last_session = self._snapshot(self._session, active=False)
            self._session = PnlSession(mode=mode, started_at=now, active=True)
            self._persist()

    def end_session(self) -> None:
        with self._lock:
            if self._session.active:
                self._last_session = self._snapshot(self._session, active=False)
            self._session.active = False
            self._persist()

    def record_from_journal(self, journal: dict) -> None:
        action = journal.get("action", "")
        if action not in ("sell", "sell_partial"):
            return

        pnl_sol = journal.get("net_pnl_sol")
        if pnl_sol is None:
            pnl_sol = journal.get("pnl_sol")
        if pnl_sol is None:
            return
        try:
            pnl = float(pnl_sol)
        except (TypeError, ValueError):
            return

        with self._lock:
            if not self._session.active:
                return

            self._session.total_trade_count += 1
            if pnl >= 0:
                self._session.total_profit_sol += pnl
            else:
                self._session.total_losses_sol += abs(pnl)

            ts = journal.get("timestamp") or self._clock()
            cumulative_net = self._session.net_pnl_sol
            self._session.running_pnl_history.append(
                {"timestamp": ts, "cumulative_net": cumulative_net}
            )
            if len(self._session.running_pnl_history) > MAX_HISTORY_POINTS:
                self._session.running_pnl_history = (
                    self._session.running_pnl_history[-MAX_HISTORY_POINTS:]
                )

            symbol = journal.get("symbol", "") or "?"
            sign = "+" if pnl >= 0 else ""
            reason = str(journal.get("reason") or "")
            tp_level = journal.get("tp_level")
            ladder_marks: List[str] = []
            if tp_level == 1 or "l1" in reason.lower() or reason == "sell_take_profit_l1":
                ladder_marks.append("L1")
            if tp_level == 2 or "l2" in reason.lower() or reason == "sell_take_profit_l2":
                ladder_marks.append("L2")
            if reason == "sell_instant_5pct":
                ladder_marks.append("+5%")
            if "ladder_slowdown_after_l2" in reason:
                ladder_marks = ["L1", "L2"]
            elif "ladder_slowdown_after_l3" in reason:
                ladder_marks = ["L1", "L2", "L3"]
            marks_suffix = ""
            if ladder_marks:
                marks_suffix = " ✓ " + " ".join(ladder_marks)
            self._session.recent_trades.insert(
                0,
                {
                    "symbol": symbol,
                    "pnl_sol": pnl,
                    "label": f"{sign}{pnl:.4f} SOL {symbol}{marks_suffix}",
                    "timestamp": ts,
                    "l1_hit": "L1" in ladder_marks,
                    "l2_hit": "L2" in ladder_marks,
                    "instant_hit": "+5%" in ladder_marks,
                    "reason": reason or None,
                    "tp_level": tp_level,
                },
            )
            if len(self._session.recent_trades) > MAX_RECENT_TRADES:
                self._session.recent_trades = self._session.recent_trades[:MAX_RECENT_TRADES]

            self._persist()

    def _session_start_ts(self) -> Optional[float]:
        """Current or last session start from pnl_tracker / paper session."""
        pnl = self.get_running_pnl()
        started = pnl.get("started_at")
        if started is not None:
            return float(started)
        try:
            from paper_session import paper_session_manager

            paper_start = paper_session_manager.get_session_start_time()
            if paper_start is not None:
                return float(paper_start)
        except Exception:
            pass
        return None

    @staticmethod
    def _journal_pnl_sol(event: dict) -> Optional[float]:
        pnl = event.get("net_pnl_sol")
        if pnl is None:
            pnl = event.get("pnl_sol")
        if pnl is not None:
            try:
                return float(pnl)
            except (TypeError, ValueError):
                return None
        basis = event.get("sol_in_basis")
        if basis is None:
            basis = event.get("sol_in")
        if basis is None:
            basis = event.get("size_sol")
        pct = event.get("pnl_pct")
        if basis is None or pct is None:
            return None
        try:
            sol_basis = float(basis)
            if sol_basis <= 0:
                return None
            return sol_basis * float(pct)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _journal_sol_in(event: dict) -> float:
        for key in ("sol_in", "size_sol"):
            val = event.get(key)
            if val is None:
                continue
            try:
                amount = float(val)
            except (TypeError, ValueError):
                continue
            if amount > 0:
                return amount
        return 0.0

    def _load_session_journal_stats(
        self, session_start: float, mode: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Per-mint realized PnL and capital deployed from trades.jsonl in session."""
        path = Path(Config.TRADE_JOURNAL_PATH)
        if not path.exists():
            return {}
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return {}

        paper_mode = (mode or "").lower() == "paper"
        by_mint: Dict[str, Dict[str, Any]] = {}
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = event.get("timestamp")
            if ts is None:
                continue
            try:
                trade_at = float(ts)
            except (TypeError, ValueError):
                continue
            if trade_at < session_start:
                continue

            is_paper = bool(event.get("paper_trade") or event.get("dry_run"))
            if paper_mode and not is_paper:
                continue
            if mode and not paper_mode and is_paper:
                continue

            mint = event.get("mint") or ""
            if not mint:
                continue
            action = event.get("action", "")
            symbol = str(event.get("symbol") or "?")
            bucket = by_mint.setdefault(
                mint,
                {
                    "symbol": symbol,
                    "mint": mint,
                    "sol_invested": 0.0,
                    "realized_sol": 0.0,
                    "unrealized_sol": 0.0,
                    "usd_gain": 0.0,
                    "has_usd": False,
                },
            )
            if symbol and symbol != "?":
                bucket["symbol"] = symbol

            if action == "buy":
                bucket["sol_invested"] += self._journal_sol_in(event)
            elif action in ("sell", "sell_partial"):
                pnl = self._journal_pnl_sol(event)
                if pnl is not None:
                    bucket["realized_sol"] += pnl
                pnl_usd = event.get("pnl_usd")
                if pnl_usd is not None:
                    try:
                        bucket["usd_gain"] += float(pnl_usd)
                        bucket["has_usd"] = True
                    except (TypeError, ValueError):
                        pass
                basis = event.get("sol_in_basis")
                if basis is not None:
                    try:
                        basis_f = float(basis)
                        if basis_f > 0:
                            bucket["sol_invested"] += basis_f
                    except (TypeError, ValueError):
                        pass
        return by_mint

    @staticmethod
    def _position_unrealized_sol(pos: Dict[str, Any]) -> float:
        sol_invested = float(pos.get("sol_invested") or pos.get("size_sol") or 0.0)
        if sol_invested <= 0:
            return 0.0
        pct_gain = float(pos.get("pnl_pct") or 0.0)
        remaining = pos.get("remaining_pct")
        remaining_frac = float(remaining) if remaining is not None else 1.0
        return sol_invested * pct_gain * remaining_frac

    def get_session_top_gainers(
        self,
        open_positions: Optional[List[Dict[str, Any]]] = None,
        sol_price_usd: Optional[float] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Top session symbols by combined realized + unrealized PnL (user trades only)."""
        session_start = self._session_start_ts()
        if session_start is None:
            return []

        running = self.get_running_pnl()
        mode = running.get("mode")
        by_mint = self._load_session_journal_stats(session_start, mode)

        for pos in open_positions or []:
            mint = pos.get("mint") or ""
            if not mint:
                continue
            entry_time = pos.get("entry_time")
            if entry_time is not None:
                try:
                    if float(entry_time) < session_start:
                        continue
                except (TypeError, ValueError):
                    pass
            symbol = str(pos.get("symbol") or "?")
            bucket = by_mint.setdefault(
                mint,
                {
                    "symbol": symbol,
                    "mint": mint,
                    "sol_invested": 0.0,
                    "realized_sol": 0.0,
                    "unrealized_sol": 0.0,
                    "usd_gain": 0.0,
                    "has_usd": False,
                },
            )
            if symbol and symbol != "?":
                bucket["symbol"] = symbol
            unrealized = self._position_unrealized_sol(pos)
            if unrealized > 0:
                bucket["unrealized_sol"] += unrealized
            if bucket["sol_invested"] <= 0:
                pos_invested = float(pos.get("sol_invested") or pos.get("size_sol") or 0.0)
                if pos_invested > 0:
                    remaining = pos.get("remaining_pct")
                    remaining_frac = float(remaining) if remaining is not None else 1.0
                    bucket["sol_invested"] += pos_invested * remaining_frac

        by_symbol: Dict[str, Dict[str, Any]] = {}
        for mint, stats in by_mint.items():
            realized = float(stats["realized_sol"])
            unrealized = float(stats["unrealized_sol"])
            if realized == 0 and unrealized == 0:
                continue
            symbol = stats["symbol"]
            sym_key = symbol.upper()
            agg = by_symbol.setdefault(
                sym_key,
                {
                    "symbol": symbol,
                    "mint": mint,
                    "sol_invested": 0.0,
                    "realized_sol": 0.0,
                    "unrealized_sol": 0.0,
                    "usd_gain": 0.0,
                    "has_usd": False,
                },
            )
            agg["sol_invested"] += float(stats["sol_invested"])
            agg["realized_sol"] += float(stats["realized_sol"])
            agg["unrealized_sol"] += float(stats["unrealized_sol"])
            if stats.get("has_usd"):
                agg["usd_gain"] += float(stats.get("usd_gain") or 0.0)
                agg["has_usd"] = True
            total_gain = agg["realized_sol"] + agg["unrealized_sol"]
            if total_gain > (agg.get("_best_gain") or float("-inf")):
                agg["mint"] = mint
                agg["_best_gain"] = total_gain

        ranked: List[Dict[str, Any]] = []
        for agg in by_symbol.values():
            sol_invested = float(agg["sol_invested"])
            realized = float(agg["realized_sol"])
            unrealized = float(agg["unrealized_sol"])
            sol_gain = realized + unrealized
            if sol_invested > 0:
                pct_gain = sol_gain / sol_invested
            elif sol_gain != 0:
                pct_gain = sol_gain
            else:
                pct_gain = 0.0

            if agg.get("has_usd"):
                usd_gain = float(agg["usd_gain"])
            elif sol_price_usd is not None:
                usd_gain = sol_gain * sol_price_usd
            else:
                usd_gain = None

            ranked.append(
                {
                    "symbol": agg["symbol"],
                    "mint": agg["mint"],
                    "pct_gain": pct_gain,
                    "sol_gain_est": sol_gain,
                    "usd_gain_est": usd_gain,
                    "realized_sol": realized,
                    "unrealized_sol": unrealized,
                    "sol_invested": sol_invested,
                    "source": "session_trades",
                }
            )

        ranked.sort(
            key=lambda item: (item["sol_gain_est"], item["pct_gain"]),
            reverse=True,
        )
        winners = [item for item in ranked if item["sol_gain_est"] > 0]
        if len(winners) >= limit:
            return winners[:limit]
        losers = [item for item in ranked if item["sol_gain_est"] <= 0]
        losers.sort(key=lambda item: item["sol_gain_est"], reverse=True)
        return (winners + losers)[:limit]

    def get_running_pnl(self) -> dict:
        with self._lock:
            if self._session.active:
                session = self._session
                active = True
            elif self._last_session.started_at is not None:
                session = self._last_session
                active = False
            else:
                session = self._session
                active = False

            return {
                "profit_sol": session.total_profit_sol,
                "losses_sol": session.total_losses_sol,
                "net_pnl_sol": session.net_pnl_sol,
                "trade_count": session.total_trade_count,
                "mode": session.mode or None,
                "active": active,
                "started_at": session.started_at,
                "recent_trades": list(session.recent_trades),
                "running_pnl_history": list(session.running_pnl_history),
            }

    @staticmethod
    def _snapshot(session: PnlSession, active: bool) -> PnlSession:
        return PnlSession(
            mode=session.mode,
            started_at=session.started_at,
            active=active,
            total_profit_sol=session.total_profit_sol,
            total_losses_sol=session.total_losses_sol,
            total_trade_count=session.total_trade_count,
            running_pnl_history=list(session.running_pnl_history),
            recent_trades=list(session.recent_trades),
        )

    def _persist(self) -> None:
        with self._lock:
            payload = {
                "session": self._session_to_dict(self._session),
                "last_session": self._session_to_dict(self._last_session),
            }
        try:
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to persist session P&L: %s", exc)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load session P&L file: %s", exc)
            return

        with self._lock:
            if "session" in data:
                self._session = self._dict_to_session(data["session"])
            if "last_session" in data:
                self._last_session = self._dict_to_session(data["last_session"])

    @staticmethod
    def _session_to_dict(session: PnlSession) -> dict:
        return {
            "mode": session.mode,
            "started_at": session.started_at,
            "active": session.active,
            "total_profit_sol": session.total_profit_sol,
            "total_losses_sol": session.total_losses_sol,
            "total_trade_count": session.total_trade_count,
            "running_pnl_history": list(session.running_pnl_history),
            "recent_trades": list(session.recent_trades),
        }

    @staticmethod
    def _dict_to_session(data: dict) -> PnlSession:
        return PnlSession(
            mode=data.get("mode", ""),
            started_at=data.get("started_at"),
            active=bool(data.get("active", False)),
            total_profit_sol=float(data.get("total_profit_sol", 0.0)),
            total_losses_sol=float(data.get("total_losses_sol", 0.0)),
            total_trade_count=int(data.get("total_trade_count", 0)),
            running_pnl_history=list(data.get("running_pnl_history", [])),
            recent_trades=list(data.get("recent_trades", [])),
        )


pnl_tracker = PnlTracker()
