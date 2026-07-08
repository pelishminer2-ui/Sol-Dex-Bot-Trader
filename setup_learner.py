"""Persistent setup learning from completed trades (wins and losses)."""

import json
import logging
import math
import time
from pathlib import Path
from typing import Dict, List, Optional

from config import Config, resolve_data_path
from scanner import MoverCandidate

logger = logging.getLogger(__name__)

DEFAULT_STORE_PATH = resolve_data_path("data/setup_learning.json")
STORE_VERSION = 2

FEATURE_KEYS = [
    "momentum_pct",
    "liquidity_usd",
    "volume_24h_usd",
    "price_change_5m",
    "price_change_1h",
    "price_change_6h",
    "price_change_24h",
    "entry_price_impact_pct",
    "is_pumpfun_route",
    "hold_time_sec",
    "scanner_source",
]

_SOURCE_ENCODING = {
    "dexscreener": 0.1,
    "pumpfun": 0.2,
    "birdeye": 0.3,
    "gmgn": 0.4,
    "watchlist_mint": 0.5,
    "sol_trade": 0.6,
    "weth_trade": 0.7,
}


def _float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _encode_scanner_source(source: Optional[str]) -> float:
    if not source:
        return 0.0
    return _SOURCE_ENCODING.get(str(source).strip().lower(), 0.05)


def _is_pumpfun_route(route_labels) -> bool:
    if not route_labels:
        return False
    for label in route_labels:
        text = str(label)
        if "Pump.fun" in text or "pump.fun" in text.lower():
            return True
    return False


def normalize_setup_features(features: dict) -> Dict[str, float]:
    """Normalize raw setup features into a comparable vector."""
    return {
        "momentum_pct": _float(features.get("momentum_pct")),
        "liquidity_usd": math.log10(max(_float(features.get("liquidity_usd"), 1.0), 1.0)),
        "volume_24h_usd": math.log10(max(_float(features.get("volume_24h_usd"), 1.0), 1.0)),
        "price_change_5m": _float(features.get("price_change_5m")),
        "price_change_1h": _float(features.get("price_change_1h")),
        "price_change_6h": _float(features.get("price_change_6h")),
        "price_change_24h": _float(features.get("price_change_24h")),
        "entry_price_impact_pct": _float(features.get("entry_price_impact_pct")),
        "is_pumpfun_route": 1.0 if features.get("is_pumpfun_route") else 0.0,
        "hold_time_sec": min(_float(features.get("hold_time_sec")) / 3600.0, 24.0),
        "scanner_source": _encode_scanner_source(features.get("scanner_source")),
    }


def _candidate_vector(candidate: MoverCandidate) -> Dict[str, float]:
    return normalize_setup_features(
        {
            "momentum_pct": candidate.momentum_pct,
            "liquidity_usd": candidate.liquidity_usd,
            "volume_24h_usd": candidate.volume_24h_usd,
            "price_change_5m": candidate.price_change_5m,
            "price_change_1h": candidate.price_change_1h,
            "price_change_6h": candidate.price_change_6h,
            "price_change_24h": candidate.price_change_24h,
            "entry_price_impact_pct": 0.0,
            "is_pumpfun_route": candidate.source == "pumpfun",
            "hold_time_sec": 0.0,
            "scanner_source": candidate.source,
        }
    )


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in FEATURE_KEYS)
    norm_a = math.sqrt(sum(a.get(k, 0.0) ** 2 for k in FEATURE_KEYS))
    norm_b = math.sqrt(sum(b.get(k, 0.0) ** 2 for k in FEATURE_KEYS))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _mean_centroid(rows: List[dict]) -> Optional[Dict[str, float]]:
    if not rows:
        return None
    vecs = [row.get("features", {}) for row in rows]
    return {
        key: sum(vec.get(key, 0.0) for vec in vecs) / len(vecs)
        for key in FEATURE_KEYS
    }


def _merge_centroids(
    old: Dict[str, float],
    old_count: int,
    new: Dict[str, float],
    new_count: int,
) -> Dict[str, float]:
    total = old_count + new_count
    if total <= 0:
        return dict(new)
    return {
        key: (old.get(key, 0.0) * old_count + new.get(key, 0.0) * new_count) / total
        for key in FEATURE_KEYS
    }


def _discovery_score(candidate: MoverCandidate) -> float:
    mom = max(candidate.momentum_pct, 0.0)
    vol = math.log10(max(candidate.volume_24h_usd, 1.0))
    return mom * 0.7 + vol * 0.001


def features_from_position_profile(
    profile: dict,
    *,
    hold_time_sec: Optional[float] = None,
    entry_price_impact_pct: Optional[float] = None,
    route_labels=None,
    scanner_source: Optional[str] = None,
) -> dict:
    """Build raw setup features from a position profile and optional entry metadata."""
    features = dict(profile or {})
    if hold_time_sec is not None:
        features["hold_time_sec"] = hold_time_sec
    if entry_price_impact_pct is not None:
        features["entry_price_impact_pct"] = entry_price_impact_pct
    if scanner_source is not None:
        features["scanner_source"] = scanner_source
    if route_labels is not None:
        features["is_pumpfun_route"] = _is_pumpfun_route(route_labels)
    elif "is_pumpfun_route" not in features:
        features["is_pumpfun_route"] = False
    return features


def _exit_reason_category(reason: str) -> str:
    lower = (reason or "").lower()
    if "stop_loss" in lower or lower.startswith("sell_sl"):
        return "stop_loss"
    if "take_profit" in lower or "instant" in lower:
        return "take_profit"
    if "slowdown" in lower or "weaken" in lower:
        return "momentum_exit"
    if "ladder" in lower:
        return "ladder"
    if "time" in lower or "session" in lower or "manual" in lower:
        return "time_or_manual"
    return "other"


class SetupLearner:
    """Learn entry setups from completed trades and rank new candidates."""

    def __init__(self, store_path: Optional[Path] = None):
        self.store_path = Path(store_path or DEFAULT_STORE_PATH)
        self.history: List[dict] = []
        self.patterns: Optional[dict] = None
        self.trades_since_condense: int = 0
        self._bootstrapped = False
        self.load()

    @property
    def has_patterns(self) -> bool:
        if not self.patterns:
            return False
        return bool(
            self.patterns.get("win_centroid") or self.patterns.get("loss_centroid")
        )

    @property
    def learning_active(self) -> bool:
        if not Config.SETUP_LEARNING_ENABLED:
            return False
        if len(self.history) >= Config.SETUP_LEARNING_MIN_TRADES:
            return True
        if self.has_patterns:
            condensed = int(self.patterns.get("win_count", 0)) + int(
                self.patterns.get("loss_count", 0)
            )
            return condensed >= Config.SETUP_LEARNING_MIN_TRADES
        return False

    def _wins(self) -> List[dict]:
        return [row for row in self.history if row.get("win")]

    def _losses(self) -> List[dict]:
        return [row for row in self.history if not row.get("win")]

    def _age_cutoff(self) -> float:
        return time.time() - Config.SETUP_LEARNING_MAX_AGE_DAYS * 86400.0

    def _filter_by_age(self, rows: List[dict]) -> List[dict]:
        cutoff = self._age_cutoff()
        return [row for row in rows if _float(row.get("recorded_at"), 0.0) >= cutoff]

    def _apply_age_filter(self) -> None:
        self.history = self._filter_by_age(self.history)

    def _should_condense(self) -> bool:
        return (
            self.trades_since_condense >= Config.SETUP_LEARNING_CONDENSE_EVERY
            or len(self.history) > Config.SETUP_LEARNING_RAW_HISTORY
        )

    def _update_patterns_from_history(self, source_history: List[dict]) -> None:
        wins = [row for row in source_history if row.get("win")]
        losses = [row for row in source_history if not row.get("win")]
        win_centroid = _mean_centroid(wins)
        loss_centroid = _mean_centroid(losses)
        win_count = len(wins)
        loss_count = len(losses)

        if self.patterns:
            if win_centroid and self.patterns.get("win_centroid"):
                prev_count = int(self.patterns.get("win_count", 0))
                win_centroid = _merge_centroids(
                    self.patterns["win_centroid"],
                    prev_count,
                    win_centroid,
                    win_count,
                )
                win_count += prev_count
            elif self.patterns.get("win_centroid"):
                win_centroid = self.patterns["win_centroid"]
                win_count = int(self.patterns.get("win_count", 0))

            if loss_centroid and self.patterns.get("loss_centroid"):
                prev_count = int(self.patterns.get("loss_count", 0))
                loss_centroid = _merge_centroids(
                    self.patterns["loss_centroid"],
                    prev_count,
                    loss_centroid,
                    loss_count,
                )
                loss_count += prev_count
            elif self.patterns.get("loss_centroid"):
                loss_centroid = self.patterns["loss_centroid"]
                loss_count = int(self.patterns.get("loss_count", 0))

        self.patterns = {
            "win_centroid": win_centroid,
            "loss_centroid": loss_centroid,
            "win_count": win_count,
            "loss_count": loss_count,
            "condensed_at": time.time(),
            "condensed_from_trades": len(source_history),
        }

    def _trim_history(self) -> None:
        self.history = self.history[-Config.SETUP_LEARNING_RAW_HISTORY :]
        max_hist = Config.SETUP_LEARNING_MAX_HISTORY
        if len(self.history) > max_hist:
            self.history = self.history[-max_hist:]

    def _condense(self) -> None:
        self._apply_age_filter()
        if not self.history:
            self.trades_since_condense = 0
            self.save()
            return
        source = list(self.history)
        self._update_patterns_from_history(source)
        self._trim_history()
        self.trades_since_condense = 0
        logger.info(
            "Setup learning condensed %d trades into patterns (raw history=%d)",
            len(source),
            len(self.history),
        )
        self.save()

    def load(self) -> None:
        if self.store_path.exists():
            try:
                payload = json.loads(self.store_path.read_text(encoding="utf-8"))
                self.history = list(payload.get("history", []))
                self.patterns = payload.get("patterns")
                self.trades_since_condense = int(payload.get("trades_since_condense", 0))
                self._bootstrapped = bool(payload.get("bootstrapped"))
                version = int(payload.get("version", 1))
                if version < STORE_VERSION and self.history and not self.has_patterns:
                    self._migrate_v1_store()
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not load setup learning store: %s", exc)
                self.history = []
                self.patterns = None
                self.trades_since_condense = 0
                self._bootstrapped = False
        else:
            self.history = []
            self.patterns = None
            self.trades_since_condense = 0
            self._bootstrapped = False
        if not self.history and not self._bootstrapped:
            self._bootstrap_from_journal()

    def _migrate_v1_store(self) -> None:
        logger.info(
            "Migrating setup learning store v1 -> v%d (%d trades)",
            STORE_VERSION,
            len(self.history),
        )
        self._update_patterns_from_history(list(self.history))
        self._trim_history()
        self.trades_since_condense = 0
        self.save()

    def save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._apply_age_filter()
        max_hist = Config.SETUP_LEARNING_MAX_HISTORY
        if len(self.history) > max_hist:
            self.history = self.history[-max_hist:]
        payload = {
            "version": STORE_VERSION,
            "last_updated": time.time(),
            "bootstrapped": self._bootstrapped,
            "history": self.history,
            "patterns": self.patterns,
            "trades_since_condense": self.trades_since_condense,
        }
        self.store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def reset_patterns(self) -> None:
        """Clear condensed win/loss centroids; raw history is kept."""
        self.patterns = None
        self.trades_since_condense = 0
        self.save()
        logger.info("Setup learning patterns reset")

    def record_completed_trade(
        self,
        features: dict,
        net_pnl_sol: float,
        *,
        pnl_pct: Optional[float] = None,
        exit_reason: Optional[str] = None,
        mint: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> None:
        if not Config.SETUP_LEARNING_ENABLED:
            return
        win = net_pnl_sol > 0
        row = {
            "recorded_at": time.time(),
            "mint": mint,
            "symbol": symbol,
            "features": normalize_setup_features(features),
            "net_pnl_sol": float(net_pnl_sol),
            "pnl_pct": float(pnl_pct if pnl_pct is not None else 0.0),
            "win": win,
            "exit_reason": exit_reason,
            "exit_reason_category": _exit_reason_category(exit_reason or ""),
        }
        self.history.append(row)
        self.trades_since_condense += 1
        if self._should_condense():
            self._condense()
        else:
            self.save()
        logger.info(
            "Setup learning recorded %s %s net=%.4f SOL (history=%d, patterns=%s)",
            "WIN" if win else "LOSS",
            symbol or mint or "?",
            net_pnl_sol,
            len(self.history),
            "yes" if self.has_patterns else "no",
        )

    def _avg_similarity(self, candidate_vec: Dict[str, float], rows: List[dict]) -> float:
        if not rows:
            return 0.0
        sims = [
            _cosine_similarity(candidate_vec, row.get("features", {}))
            for row in rows
        ]
        return sum(sims) / len(sims)

    def _raw_similarity_score(self, candidate_vec: Dict[str, float]) -> float:
        win_boost = self._avg_similarity(candidate_vec, self._wins())
        loss_penalty = self._avg_similarity(candidate_vec, self._losses())
        return (
            Config.SETUP_LEARNING_WIN_WEIGHT * win_boost
            - Config.SETUP_LEARNING_LOSS_WEIGHT * loss_penalty
        )

    def _centroid_similarity_score(self, candidate_vec: Dict[str, float]) -> float:
        if not self.has_patterns:
            return 0.0
        win_centroid = self.patterns.get("win_centroid") or {}
        loss_centroid = self.patterns.get("loss_centroid") or {}
        win_boost = (
            _cosine_similarity(candidate_vec, win_centroid) if win_centroid else 0.0
        )
        loss_penalty = (
            _cosine_similarity(candidate_vec, loss_centroid) if loss_centroid else 0.0
        )
        return (
            Config.SETUP_LEARNING_WIN_WEIGHT * win_boost
            - Config.SETUP_LEARNING_LOSS_WEIGHT * loss_penalty
        )

    def score_candidate(self, candidate: MoverCandidate) -> float:
        if not self.learning_active:
            return _discovery_score(candidate)
        candidate_vec = _candidate_vector(candidate)
        base_momentum = max(candidate.momentum_pct, 0.0)

        if self.has_patterns:
            centroid_score = self._centroid_similarity_score(candidate_vec)
            raw_score = self._raw_similarity_score(candidate_vec)
            blend = Config.SETUP_LEARNING_CENTROID_WEIGHT
            similarity = blend * centroid_score + (1.0 - blend) * raw_score
        else:
            similarity = self._raw_similarity_score(candidate_vec)

        return similarity + base_momentum * 0.4

    def win_lean_score(self, candidate: MoverCandidate) -> Optional[float]:
        """
        Lean of a candidate toward learned WINS vs LOSSES.

        Returns similarity(win) - similarity(loss) in [-1, 1]. Positive means the
        setup looks more like past winners than losers. Returns ``None`` when the
        learner has insufficient data (learning inactive) so callers can fall back
        to allowing the entry instead of blocking everything.
        """
        if not Config.SETUP_LEARNING_ENABLED:
            return None
        if not self.learning_active:
            return None
        candidate_vec = _candidate_vector(candidate)
        if self.has_patterns:
            win_centroid = self.patterns.get("win_centroid") or {}
            loss_centroid = self.patterns.get("loss_centroid") or {}
            win = (
                _cosine_similarity(candidate_vec, win_centroid)
                if win_centroid
                else 0.0
            )
            loss = (
                _cosine_similarity(candidate_vec, loss_centroid)
                if loss_centroid
                else 0.0
            )
        else:
            wins = self._wins()
            losses = self._losses()
            if not wins and not losses:
                return None
            win = self._avg_similarity(candidate_vec, wins)
            loss = self._avg_similarity(candidate_vec, losses)
        return win - loss

    def rank(self, candidates: List[MoverCandidate]) -> List[MoverCandidate]:
        if not candidates:
            return []
        if not Config.SETUP_LEARNING_ENABLED or not self.learning_active:
            return sorted(candidates, key=_discovery_score, reverse=True)
        return sorted(candidates, key=self.score_candidate, reverse=True)

    def get_stats(self) -> dict:
        wins = self._wins()
        losses = self._losses()
        avg_win_score = None
        avg_loss_score = None
        if wins:
            avg_win_score = sum(
                row.get("features", {}).get("momentum_pct", 0.0) for row in wins
            ) / len(wins)
        if losses:
            avg_loss_score = sum(
                row.get("features", {}).get("momentum_pct", 0.0) for row in losses
            ) / len(losses)
        last_updated = None
        if self.store_path.exists():
            try:
                payload = json.loads(self.store_path.read_text(encoding="utf-8"))
                last_updated = payload.get("last_updated")
            except (OSError, json.JSONDecodeError):
                pass
        patterns = self.patterns or {}
        return {
            "enabled": Config.SETUP_LEARNING_ENABLED,
            "learning_active": self.learning_active,
            "trades_learned": len(self.history),
            "win_count": len(wins),
            "loss_count": len(losses),
            "min_trades": Config.SETUP_LEARNING_MIN_TRADES,
            "max_history": Config.SETUP_LEARNING_MAX_HISTORY,
            "raw_history": Config.SETUP_LEARNING_RAW_HISTORY,
            "condense_every": Config.SETUP_LEARNING_CONDENSE_EVERY,
            "centroid_weight": Config.SETUP_LEARNING_CENTROID_WEIGHT,
            "win_weight": Config.SETUP_LEARNING_WIN_WEIGHT,
            "loss_weight": Config.SETUP_LEARNING_LOSS_WEIGHT,
            "avg_win_setup_score": avg_win_score,
            "avg_loss_setup_score": avg_loss_score,
            "last_updated": last_updated,
            "store_path": str(self.store_path),
            "has_patterns": self.has_patterns,
            "win_centroid_trades": int(patterns.get("win_count", 0)),
            "loss_centroid_trades": int(patterns.get("loss_count", 0)),
            "last_condensed_at": patterns.get("condensed_at"),
            "raw_history_count": len(self.history),
            "trades_since_condense": self.trades_since_condense,
        }

    def _bootstrap_from_journal(self, limit: int = 100) -> None:
        journal_path = Path(Config.TRADE_JOURNAL_PATH)
        if not journal_path.exists():
            self._bootstrapped = True
            return
        try:
            lines = journal_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Setup learning bootstrap skipped: %s", exc)
            self._bootstrapped = True
            return

        open_buys: Dict[str, dict] = {}
        completed: List[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            action = event.get("action")
            mint = event.get("mint")
            if not mint:
                continue
            if action == "buy":
                open_buys[mint] = event
            elif action in ("sell", "sell_partial"):
                remaining = event.get("remaining_token_raw")
                if action == "sell_partial" and remaining not in (0, None):
                    continue
                buy = open_buys.pop(mint, None)
                completed.append((buy, event))

        if not completed:
            self._bootstrapped = True
            return

        for buy, sell in completed[-limit:]:
            features = self._features_from_journal_events(buy, sell)
            net = sell.get("net_pnl_sol")
            if net is None:
                net = sell.get("pnl_sol", 0.0)
            recorded_at = _float(sell.get("timestamp"), time.time())
            if recorded_at < self._age_cutoff():
                recorded_at = time.time()
            self.history.append(
                {
                    "recorded_at": recorded_at,
                    "mint": sell.get("mint"),
                    "symbol": sell.get("symbol"),
                    "features": normalize_setup_features(features),
                    "net_pnl_sol": float(net or 0.0),
                    "pnl_pct": float(sell.get("pnl_pct") or 0.0),
                    "win": float(net or 0.0) > 0,
                    "exit_reason": sell.get("reason"),
                    "exit_reason_category": _exit_reason_category(sell.get("reason", "")),
                    "bootstrapped": True,
                }
            )

        self._bootstrapped = True
        if self.history:
            if self._should_condense():
                self._condense()
            else:
                self.save()
            logger.info(
                "Setup learning bootstrapped %d trades from journal",
                len(self.history),
            )

    @staticmethod
    def _features_from_journal_events(buy: Optional[dict], sell: dict) -> dict:
        buy = buy or {}
        hold_sec = 0.0
        buy_ts = buy.get("timestamp")
        sell_ts = sell.get("timestamp")
        if buy_ts and sell_ts:
            hold_sec = max(0.0, float(sell_ts) - float(buy_ts))
        route_labels = buy.get("route_labels") or []
        return {
            "momentum_pct": buy.get("momentum") or buy.get("momentum_pct") or 0.0,
            "liquidity_usd": buy.get("liquidity_usd") or 0.0,
            "volume_24h_usd": buy.get("volume_24h_usd") or 0.0,
            "price_change_5m": buy.get("price_change_5m") or 0.0,
            "price_change_1h": buy.get("price_change_1h") or 0.0,
            "price_change_6h": buy.get("price_change_6h") or 0.0,
            "price_change_24h": buy.get("price_change_24h") or 0.0,
            "entry_price_impact_pct": buy.get("price_impact_pct") or 0.0,
            "is_pumpfun_route": _is_pumpfun_route(route_labels),
            "scanner_source": buy.get("scanner_source") or buy.get("source"),
            "hold_time_sec": hold_sec,
        }
