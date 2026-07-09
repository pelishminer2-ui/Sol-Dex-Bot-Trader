"""Market regime detector — hot / neutral / cold adaptive entry gates."""

from __future__ import annotations

import logging
from typing import Any, Optional

from config import Config

logger = logging.getLogger(__name__)

REGIME_HOT = "hot"
REGIME_NEUTRAL = "neutral"
REGIME_COLD = "cold"

_TARGET_WIN_RATES = {
    REGIME_HOT: lambda: Config.HOT_MARKET_TARGET_WIN_RATE,
    REGIME_NEUTRAL: lambda: Config.NEUTRAL_MARKET_TARGET_WIN_RATE,
    REGIME_COLD: lambda: Config.COLD_MARKET_TARGET_WIN_RATE,
}

_snapshot: dict[str, Any] = {
    "market_regime": REGIME_NEUTRAL,
    "target_win_rate": 0.55,
    "scanner_passing_count": 0,
    "gmgn_volume_usd": 0.0,
    "sol_trend_1h_pct": None,
    "sol_trend_4h_pct": None,
    "regime_gates": {},
    "hot_market_mode_enabled": False,
    "updated_at": 0.0,
}


def reset_market_regime_for_tests() -> None:
    """Reset cached regime state (validation scripts)."""
    global _snapshot
    _snapshot = {
        "market_regime": REGIME_NEUTRAL,
        "target_win_rate": Config.NEUTRAL_MARKET_TARGET_WIN_RATE,
        "scanner_passing_count": 0,
        "gmgn_volume_usd": 0.0,
        "sol_trend_1h_pct": None,
        "sol_trend_4h_pct": None,
        "regime_gates": _static_regime_gates(REGIME_NEUTRAL),
        "hot_market_mode_enabled": Config.HOT_MARKET_MODE_ENABLED,
        "updated_at": 0.0,
    }


def _baseline_gates() -> dict[str, float]:
    """Classic static Steady baseline gates (no regime adaptation)."""
    return {
        "entry_momentum_pct": Config.ENTRY_MOMENTUM_PCT,
        "min_momentum_pct": Config.MIN_MOMENTUM_PCT,
        "min_volume_24h_usd": Config.MIN_VOLUME_24H_USD,
        "non_watchlist_min_volume_24h_usd": Config.NON_WATCHLIST_MIN_VOLUME_24H_USD,
    }


def _static_regime_gates(regime: str) -> dict[str, float]:
    """ENTRY-SELECTION gate values for a regime (never touches exits).

    The adaptive layer is active only when Steady's hot-market mode AND the
    self-adjust master switch are both on. When active, the Steady preset
    self-tunes its own entry floors by regime:
      * HOT     -> loosest  (more trades on the momentum pops)
      * NEUTRAL -> moderately tight
      * COLD    -> tightest / most selective (fewer, higher-quality trades)
    These are entry momentum + volume floors ONLY. Stops, instant-profit
    targets, the 15-min hold, forced sells, and the spike/instant-dump gate are
    untouched in every regime.
    """
    if not (Config.HOT_MARKET_MODE_ENABLED and Config.STEADY_TRADE_AUTO_ADJUST):
        return _baseline_gates()
    if regime == REGIME_HOT:
        vol = Config.HOT_MARKET_MIN_VOLUME_24H_USD
        return {
            "entry_momentum_pct": Config.HOT_MARKET_ENTRY_MOMENTUM_PCT,
            "min_momentum_pct": Config.HOT_MARKET_MIN_MOMENTUM_PCT,
            "min_volume_24h_usd": vol,
            "non_watchlist_min_volume_24h_usd": vol,
        }
    if regime == REGIME_COLD:
        vol = Config.COLD_MARKET_MIN_VOLUME_24H_USD
        return {
            "entry_momentum_pct": Config.COLD_MARKET_ENTRY_MOMENTUM_PCT,
            "min_momentum_pct": Config.COLD_MARKET_MIN_MOMENTUM_PCT,
            "min_volume_24h_usd": vol,
            "non_watchlist_min_volume_24h_usd": vol,
        }
    vol = Config.NEUTRAL_MARKET_MIN_VOLUME_24H_USD
    return {
        "entry_momentum_pct": Config.NEUTRAL_MARKET_ENTRY_MOMENTUM_PCT,
        "min_momentum_pct": Config.NEUTRAL_MARKET_MIN_MOMENTUM_PCT,
        "min_volume_24h_usd": vol,
        "non_watchlist_min_volume_24h_usd": vol,
    }


def get_regime_target_win_rate(regime: str) -> float:
    """Regime WR target for session closed-loop entry tuning (entry only)."""
    fn = _TARGET_WIN_RATES.get(regime)
    if fn is None:
        return Config.NEUTRAL_MARKET_TARGET_WIN_RATE
    return float(fn())


def get_regime_gates() -> dict[str, float]:
    """Current effective entry/scanner gates (respects Steady self-adjust)."""
    if not (Config.HOT_MARKET_MODE_ENABLED and Config.STEADY_TRADE_AUTO_ADJUST):
        return _static_regime_gates(REGIME_NEUTRAL)
    gates = _snapshot.get("regime_gates")
    if gates:
        return dict(gates)
    regime = _snapshot.get("market_regime", REGIME_NEUTRAL)
    return _static_regime_gates(regime)


def count_scanner_passing(watchlist: list) -> int:
    """Non-watchlist movers that already passed scanner filters."""
    from watchlist_scanner import is_pinned_watchlist_mint

    return sum(
        1 for candidate in watchlist if not is_pinned_watchlist_mint(candidate.mint)
    )


def sum_gmgn_volume_usd(watchlist: list) -> float:
    return sum(
        float(candidate.volume_24h_usd or 0)
        for candidate in watchlist
        if getattr(candidate, "source", "") == "gmgn"
    )


def detect_market_regime(
    sol_snapshot: Optional[dict],
    watchlist: list,
) -> str:
    """
    Classify market as hot, neutral, or cold.

    Hot: SOL 1h >= +0.5% AND 4h >= 0%, plus scanner passing >= N candidates.
    Cold: SOL 1h/4h below macro trend thresholds (risk-off).
    Neutral: everything else (STEADY_TRADE baseline gates).
    """
    if not Config.HOT_MARKET_MODE_ENABLED:
        return REGIME_NEUTRAL

    snap = sol_snapshot or {}
    h1 = snap.get("sol_trend_1h_pct")
    h4 = snap.get("sol_trend_4h_pct")
    passing = count_scanner_passing(watchlist)
    gmgn_vol = sum_gmgn_volume_usd(watchlist)

    # Align cold regime with SOL macro trend filter (not merely h1 < 0).
    if h1 is not None and h1 < Config.SOL_MIN_CHANGE_1H_PCT:
        return REGIME_COLD
    if h4 is not None and h4 < Config.SOL_MIN_CHANGE_4H_PCT:
        return REGIME_COLD

    sol_hot = h1 is not None and h1 >= Config.HOT_MARKET_SOL_MIN_1H_PCT
    if h4 is not None:
        sol_hot = sol_hot and h4 >= Config.HOT_MARKET_SOL_MIN_4H_PCT
    scanner_hot = passing >= Config.HOT_MARKET_MIN_SCANNER_CANDIDATES
    gmgn_ok = True
    min_gmgn = Config.HOT_MARKET_MIN_GMGN_VOLUME_USD
    if min_gmgn > 0:
        gmgn_ok = gmgn_vol >= min_gmgn

    if sol_hot and scanner_hot and gmgn_ok:
        return REGIME_HOT

    return REGIME_NEUTRAL


def update_market_regime(
    sol_snapshot: Optional[dict],
    watchlist: list,
) -> dict[str, Any]:
    """Recompute and cache market regime from latest scan + SOL trend."""
    import time

    global _snapshot
    regime = detect_market_regime(sol_snapshot, watchlist)
    gates = _static_regime_gates(regime)
    passing = count_scanner_passing(watchlist)
    gmgn_vol = sum_gmgn_volume_usd(watchlist)
    snap = sol_snapshot or {}

    prev = _snapshot.get("market_regime")
    _snapshot = {
        "market_regime": regime,
        "target_win_rate": _TARGET_WIN_RATES[regime](),
        "scanner_passing_count": passing,
        "gmgn_volume_usd": gmgn_vol,
        "sol_trend_1h_pct": snap.get("sol_trend_1h_pct"),
        "sol_trend_4h_pct": snap.get("sol_trend_4h_pct"),
        "regime_gates": gates,
        "hot_market_mode_enabled": Config.HOT_MARKET_MODE_ENABLED,
        "updated_at": time.time(),
    }
    if regime != prev:
        logger.info(
            "Market regime -> %s (SOL 1h=%s 4h=%s, %d scanner candidates, gates entry=%.2f%% mom=%.1f%% vol=$%.0fk)",
            regime,
            f"{snap.get('sol_trend_1h_pct'):+.2f}%" if snap.get("sol_trend_1h_pct") is not None else "—",
            f"{snap.get('sol_trend_4h_pct'):+.2f}%" if snap.get("sol_trend_4h_pct") is not None else "—",
            passing,
            gates["entry_momentum_pct"] * 100,
            gates["min_momentum_pct"] * 100,
            gates["min_volume_24h_usd"] / 1000,
        )
        if Config.HOT_MARKET_MODE_ENABLED and Config.STEADY_TRADE_AUTO_ADJUST:
            direction = {
                REGIME_HOT: "HOT -> looser gates (more trades)",
                REGIME_NEUTRAL: "NEUTRAL -> tighter gates (balanced)",
                REGIME_COLD: "COLD -> tighter gates (most selective)",
            }.get(regime, f"{regime} gates")
            logger.info(
                "Steady Trade auto-adjust: %s — entry=%.2f%% mom=%.1f%% vol=$%.0fk "
                "(entry selection only; exits unchanged)",
                direction,
                gates["entry_momentum_pct"] * 100,
                gates["min_momentum_pct"] * 100,
                gates["min_volume_24h_usd"] / 1000,
            )
    return dict(_snapshot)


def get_market_regime_snapshot() -> dict[str, Any]:
    """Cached regime for status API and effective gate lookups."""
    if not _snapshot.get("regime_gates"):
        _snapshot["regime_gates"] = _static_regime_gates(
            _snapshot.get("market_regime", REGIME_NEUTRAL)
        )
    return dict(_snapshot)
