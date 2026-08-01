"""
Short-horizon structure proxies for Solana memecoin setup learning.

Classical TA (cup/handle, flags, ascending triangle, blue-sky breakout,
double top, double bottom) is adapted to minutes-scale scanner windows
(5m/1h/6h/24h + liquidity/volume) — not multi-year OHLC. These scores are
ENTRY-selection only; they never touch stops, Instant profit, max-hold, or
forced exits.

Gold long exemplars (friend / SASS Instant-pop shape): Pump.fun-era fresh 1h pop,
6h/24h ~ flat (little overhead), expanding volume, short hold → Instant win.
Double top is bearish for this long-biased bot: treat high scores as avoid/skip.
Double bottom is bullish: prior wash + higher-low bounce — soft prefer, never skip.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional


STRUCTURE_FEATURE_KEYS = (
    "blue_sky_score",
    "flag_continuation_score",
    "cup_handle_score",
    "ascending_triangle_score",
    "volume_expansion_score",
    "double_top_score",
    "double_bottom_score",
    "structure_edge",
)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def window_to_fraction(value: Any) -> float:
    """
    Normalize a scanner window to a fraction.

    DexScreener paths store fractions; some pump/external paths leave percent
    points (e.g. 81.8). Values with abs > 2 are treated as percent points.
    """
    v = _float(value)
    if abs(v) > 2.0:
        return v / 100.0
    return v


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _logish_usd(value: Any) -> float:
    """Accept raw USD or already-log10 values (stored learning vectors use log10)."""
    x = max(_float(value), 0.0)
    if x <= 0.0:
        return 0.0
    if x < 15.0:
        return x
    return math.log10(max(x, 1.0))


def volume_expansion_score(liquidity_usd: Any, volume_24h_usd: Any) -> float:
    """Volume expanding vs pool size — proxy for breakout participation."""
    gap = _logish_usd(volume_24h_usd) - _logish_usd(liquidity_usd)
    return _clamp01(gap / 2.5)


def blue_sky_score(c5m: float, c1h: float, c6h: float, c24h: float) -> float:
    """
    Break above recent/historical resistance with little overhead supply.

    Memecoin proxy: fresh 5m/1h impulse while 6h/24h are flat/near-zero
    (friend/SASS Instant-pop shape), or short window dominates longer ones.
    """
    short = max(c5m, c1h)
    long = max(c6h, c24h, 0.0)
    if short <= 0.0:
        return 0.0
    # Already-extended tape with weak/negative 5m is overhead, not blue sky.
    if long > 0.15 and c5m <= 0.0 and short < long * 0.6:
        return 0.0
    # Strength of the fresh leg (≈20%+ fraction is a strong memecoin pop).
    strength = _clamp01(short / 0.20)
    # Share of the move that is "new" vs already-run longer windows.
    dominance = short / (short + long + 1e-9)
    # Extra credit when longer windows are essentially empty (true blue sky).
    overhead_penalty = _clamp01(long / max(short, 1e-9))
    clear = 1.0 - 0.85 * overhead_penalty
    return _clamp01(0.55 * strength * clear + 0.45 * dominance * clear)


def flag_continuation_score(c5m: float, c1h: float, c6h: float, c24h: float) -> float:
    """
    Bull/bear flag proxy: prior impulse, shallow pause, then continuation.

    Impulse in 1h/6h, 5m still directionally aligned but smaller (tight coil /
    handle of the flag) — not a full reversal.
    """
    impulse = max(c1h, c6h)
    if impulse <= 0.05:
        # Fresh 5m continuation off a small 1h base still counts lightly.
        if c5m > 0.02 and c1h > 0.0:
            return _clamp01(0.35 * _clamp01(c5m / 0.08))
        return 0.0
    # Deep pullback / failed second peak — not a tight flag (double-top territory).
    if c5m < -0.05 or c5m < -0.25 * impulse:
        return 0.0
    # Pause/coil: 5m much smaller than impulse but not deeply red.
    coil = 1.0 - _clamp01(abs(c5m) / max(impulse, 1e-9))
    direction = 1.0 if c5m >= -0.02 else 0.35
    prior = _clamp01(impulse / 0.25)
    # Prefer not already exhausted on 24h alone while 5m dead.
    fresh = 0.0 if (c5m <= 0.0 and max(c5m, c1h) < 0.02) else 1.0
    return _clamp01(0.4 * prior + 0.35 * coil + 0.25 * direction) * fresh


def cup_handle_score(c5m: float, c1h: float, c6h: float, c24h: float) -> float:
    """
    Cup & handle proxy: rounded longer base, softer mid window, shallow handle
    then recent recovery/break (5m/1h up vs 6h).
    """
    if c24h <= 0.05:
        return 0.0
    # Cup: 24h up with 6h softer than the full base (pullback into handle).
    if c6h > c24h * 0.95 and c6h > 0.15:
        # Still accelerating through all windows — more blue-sky than cup.
        return _clamp01(0.25 * _clamp01(c24h / 0.4))
    pullback = c24h - c6h
    if pullback < 0.0:
        pullback = 0.0
    handle_ok = c6h > -0.15  # not a collapsed dump mid-window
    recover = c1h >= c6h * 0.5 and c5m >= -0.02
    if not (handle_ok and recover):
        return 0.0
    base = _clamp01(c24h / 0.40)
    shape = _clamp01(pullback / max(c24h, 1e-9))
    tip = _clamp01(max(c5m, 0.0) / 0.08)
    return _clamp01(0.4 * base + 0.35 * shape + 0.25 * tip)


def ascending_triangle_score(c5m: float, c1h: float, c6h: float, c24h: float) -> float:
    """
    Ascending triangle proxy: stair-step higher lows into flat-ish resistance,
    then a 5m break attempt. Without OHLC highs we approximate with ordered
    positive windows and a fresh 5m kick.
    """
    if c5m <= 0.0 or c1h <= 0.0:
        return 0.0
    # Higher lows: longer windows still green and 1h >= portion of 6h.
    stairs = 0.0
    if c6h > 0.0 and c24h >= c6h * 0.8:
        stairs += 0.45
    if c1h >= max(c6h, 0.0) * 0.5:
        stairs += 0.25
    if c5m > 0.0 and c5m <= max(c1h, 0.05) * 1.25:
        # Break attempt without a vertical blow-off vs 1h.
        stairs += 0.30
    if c24h < 0.0 and c6h < 0.0:
        return 0.0
    return _clamp01(stairs * _clamp01(max(c1h, c5m) / 0.15))


def double_top_score(c5m: float, c1h: float, c6h: float, c24h: float) -> float:
    """
    Double-top / failed second-peak / M-top proxy (long avoid).

    Extended uptrend (6h/24h), then short-horizon rejection: 5m rolls over while
    price is still elevated — buyers fail at the same ceiling. Fresh blue-sky
    pops (flat 6h/24h) score near zero so Instant winners are not punished.
    """
    overhead = max(c6h, c24h)
    if overhead <= 0.08:
        # No extended uptrend → not a double top (friend/SASS shape).
        return 0.0
    # Rejection: 5m red or sharply weaker than the elevated 1h while longer still up.
    reject = 0.0
    if c5m < 0.0:
        reject = _clamp01((-c5m) / 0.10)
    elif c1h > 0.10 and c5m < 0.25 * c1h:
        # Second peak stalling under prior impulse.
        reject = _clamp01((0.25 * c1h - c5m) / max(0.25 * c1h, 1e-9)) * 0.6
    if reject <= 0.0:
        return 0.0
    # Failed higher-high feel: 1h no longer leading 6h/24h (stall at resistance).
    stall = 0.0
    if c1h <= overhead * 0.85:
        stall = _clamp01((overhead - max(c1h, 0.0)) / max(overhead, 1e-9))
    trend = _clamp01(overhead / 0.35)
    # Neckline-break start: 5m deeply red while 6h/24h still green.
    breakdown = _clamp01((-c5m) / 0.12) if c5m < -0.02 else 0.0
    return _clamp01(0.35 * trend + 0.35 * reject + 0.20 * stall + 0.10 * breakdown)


def double_bottom_score(c5m: float, c1h: float, c6h: float, c24h: float) -> float:
    """
    Double-bottom / W-recovery proxy (long prefer — soft boost, never a hard skip).

    Classical W after a decline: washed longer tape, bounce, second low holds
    (no fresh lower-low dump), short windows turn up. Memecoin proxy without OHLC:
      * Prior pressure: 6h and/or 24h soft/negative (not still extended up)
      * Stabilization / higher-low: 5m/1h turning up while longer is recovering
      * Not a double-top rejection (5m rolling over under elevated overhead)
      * Distinct from blue-sky (flat longer windows + fresh pop with no wash)
    Volume expansion is applied in ``structure_edge``, not here.
    """
    long_min = min(c6h, c24h)
    long_avg = 0.5 * (c6h + c24h)
    overhead = max(c6h, c24h)
    short = max(c5m, c1h)

    # Still extended uptrend → not a washed double-bottom base.
    if long_min > 0.05 and long_avg > 0.08:
        return 0.0
    # Double-top rejection territory: elevated overhead + 5m rolling over.
    if overhead > 0.15 and c5m < -0.03:
        return 0.0
    # Need a short-horizon bounce attempt (higher-low / neckline reclaim feel).
    if c5m <= 0.0 and c1h <= 0.0:
        return 0.0
    # Pure blue-sky Instant pop: strong short leg with empty/flat longer windows
    # and no actual wash — leave that to blue_sky_score.
    if long_min >= -0.02 and overhead <= 0.05 and short >= 0.15:
        return 0.0

    # Wash depth: prefer real downside in 6h/24h (W left side), not flat blue-sky.
    if long_min < -0.02:
        wash = _clamp01((-long_min) / 0.20)
    elif long_min < 0.0:
        wash = 0.25 * _clamp01((-long_min) / 0.02)
    elif long_avg <= 0.02 and overhead <= 0.04 and short < 0.12:
        # Soft recovering base only when the short leg is modest (not Instant pop).
        wash = 0.20
    else:
        wash = 0.0
    if wash < 0.12:
        return 0.0

    bounce = 0.0
    if c5m > 0.0:
        bounce += 0.50 * _clamp01(c5m / 0.08)
    if c1h > 0.0:
        bounce += 0.40 * _clamp01(c1h / 0.15)
    elif c5m > 0.02:
        bounce += 0.15  # fresh 5m kick while 1h still flat
    # Second-low hold: short windows not collapsing vs washed mid window.
    if c5m < -0.08 or (c1h < -0.10 and c5m < 0.0):
        return 0.0

    # Higher-low / reclaim: 1h lifting vs soft 6h (W right shoulder).
    higher_low = 0.0
    if c6h < 0.10 and c1h > c6h:
        higher_low = _clamp01((c1h - c6h) / 0.20)
    elif c24h < 0.0 and c1h > 0.0:
        higher_low = _clamp01(c1h / 0.12) * 0.7

    # Mild neckline-breakout feel: 5m leading the reclaim.
    breakout = _clamp01(c5m / 0.06) if c5m > 0.01 else 0.0

    return _clamp01(0.32 * wash + 0.33 * bounce + 0.22 * higher_low + 0.13 * breakout)


def compute_structure_scores(
    *,
    price_change_5m: Any = 0.0,
    price_change_1h: Any = 0.0,
    price_change_6h: Any = 0.0,
    price_change_24h: Any = 0.0,
    liquidity_usd: Any = 0.0,
    volume_24h_usd: Any = 0.0,
) -> Dict[str, float]:
    """Return structure feature scores in ~[0, 1] (structure_edge in ~[-1, 1])."""
    c5m, c1h, c6h, c24h = (
        window_to_fraction(price_change_5m),
        window_to_fraction(price_change_1h),
        window_to_fraction(price_change_6h),
        window_to_fraction(price_change_24h),
    )
    blue = blue_sky_score(c5m, c1h, c6h, c24h)
    flag = flag_continuation_score(c5m, c1h, c6h, c24h)
    cup = cup_handle_score(c5m, c1h, c6h, c24h)
    triangle = ascending_triangle_score(c5m, c1h, c6h, c24h)
    vol = volume_expansion_score(liquidity_usd, volume_24h_usd)
    double_top = double_top_score(c5m, c1h, c6h, c24h)
    double_bottom = double_bottom_score(c5m, c1h, c6h, c24h)
    # Bullish structures compete for continuation; double-bottom is positive.
    continuation = max(blue, flag, cup, triangle, double_bottom)
    # Volume participates in continuation edge; double-top subtracts (avoid).
    edge = continuation * (0.55 + 0.45 * vol) - double_top
    return {
        "blue_sky_score": blue,
        "flag_continuation_score": flag,
        "cup_handle_score": cup,
        "ascending_triangle_score": triangle,
        "volume_expansion_score": vol,
        "double_top_score": double_top,
        "double_bottom_score": double_bottom,
        "structure_edge": max(-1.0, min(1.0, edge)),
    }


def structure_scores_from_features(features: Mapping[str, Any]) -> Dict[str, float]:
    return compute_structure_scores(
        price_change_5m=features.get("price_change_5m"),
        price_change_1h=features.get("price_change_1h"),
        price_change_6h=features.get("price_change_6h"),
        price_change_24h=features.get("price_change_24h"),
        liquidity_usd=features.get("liquidity_usd"),
        volume_24h_usd=features.get("volume_24h_usd"),
    )


def structure_scores_from_candidate(candidate) -> Dict[str, float]:
    return compute_structure_scores(
        price_change_5m=getattr(candidate, "price_change_5m", 0.0),
        price_change_1h=getattr(candidate, "price_change_1h", 0.0),
        price_change_6h=getattr(candidate, "price_change_6h", 0.0),
        price_change_24h=getattr(candidate, "price_change_24h", 0.0),
        liquidity_usd=getattr(candidate, "liquidity_usd", 0.0),
        volume_24h_usd=getattr(candidate, "volume_24h_usd", 0.0),
    )


def structure_preference_score(candidate) -> float:
    """Single ranking signal: bullish structure edge minus double-top risk."""
    return float(structure_scores_from_candidate(candidate).get("structure_edge", 0.0))


def enrich_features_with_structure(features: dict) -> dict:
    """Mutate/return raw feature dict with structure scores filled in."""
    out = dict(features or {})
    scores = structure_scores_from_features(out)
    out.update(scores)
    return out
