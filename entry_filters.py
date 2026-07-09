"""
Entry-selection win-rate filters (learned-pattern driven).

These gates only tighten ENTRY SELECTION. They never touch stop-loss, profit
exits, the 15-minute max hold, forced-exit logic, or learning recording. The
same gates apply identically in paper and live because both run through
``strategy.evaluate_entry`` / ``evaluate_dip_reentry``.

--------------------------------------------------------------------------------
Pop vs drop — the whole point of momentum trading is catching high-momentum
"quick pops". We therefore do NOT blanket-block on momentum magnitude. Instead:

  * Absolute ceiling (``MAX_ENTRY_MOMENTUM_PCT`` / ``MAX_ENTRY_PRICE_CHANGE_5M_PCT``)
    only rejects clearly-absurd bonding-curve artifacts (default 50,000 stored
    scale). It almost never fires.
  * For genuinely high momentum (>= ``HIGH_MOMENTUM_QUALITY_PCT``) we run a
    "pop vs drop" discriminator that blocks ONLY the instant-dump signatures and
    lets quality pops through so the bot can catch (and learn from) them.

Journal evidence used to design the discriminator (data/setup_learning.json, 50
completed round-trips):

  bucket (momentum_pct)   n   winrate   avg net SOL
  0-50                    33   48.5%     +0.00158   <- profitable core
  50-100                   7   28.6%     -0.00671
  1000-5000                7    0.0%     -0.00148
  5000-10000               1    0.0%     -0.00036
  10000+                   2    0.0%     -0.00073

  * EVERY entry with momentum_pct >= 500 (n=10) lost, but with TINY net losses
    (stop-outs / fee bleed, not blowups) -> these are avoidable, not fatal.
  * 9 of 10 high-momentum losers shared an instant-dump signature:
      - STALE spike: the whole move sat in a 6h/24h window while max(5m,1h) ~ 0
        (ANSEM +24,618% 6h with 5m/1h flat; manlet +1,030% 24h; drooling; Jotchua),
        OR
      - REVERSAL: a fresh 5m/1h spike with BOTH 6h AND 24h already negative
        (Cupsey, KINS, ANSEM 5m/1h spikes with 6h & 24h < 0).
  * 8 of 10 were NOT Pump.fun route; all 18 wins in the file were Pump.fun.
  * There were ZERO high-momentum winners in the journal, so we cannot yet learn
    a positive "good high-momentum" signature — the discriminator is a prior that
    blocks the known dump signatures and *allows* quality attempts so learning can
    fill that gap. If a sell-preview round-trip impact is supplied it is the
    strongest flat-book signal (can't exit above stop => instant loss).

Levers implemented here:
  1. spike_trap_reason  — absurd ceiling + high-momentum pop-vs-drop discriminator.
  2. win_lean_reason    — lean toward the learned WIN centroid over LOSS (normal
                          momentum only; high-momentum quality pops bypass it
                          because the raw cosine is dominated by momentum scale).
  3. pop_vs_drop_score  — continuous [-1, 1] runner-vs-dump score (logging/tuning).
"""

import logging
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)


def effective_setup_learning_min_win_lean() -> float:
    """Win-lean floor including session auto-tighten bump (entry only)."""
    try:
        from session_entry_tuning import effective_setup_learning_min_win_lean as _effective

        return _effective()
    except Exception:
        return Config.SETUP_LEARNING_MIN_WIN_LEAN


def _f(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def is_high_momentum(candidate) -> bool:
    """True when the candidate's momentum (or 5m change) is high enough to be
    treated as a "pop" and routed through the pop-vs-drop discriminator."""
    thr = Config.HIGH_MOMENTUM_QUALITY_PCT
    if not thr or thr <= 0:
        return False
    momentum = _f(getattr(candidate, "momentum_pct", 0.0))
    change_5m = _f(getattr(candidate, "price_change_5m", 0.0))
    return momentum >= thr or change_5m >= thr


def _is_stale_spike(candidate) -> bool:
    """The move sits entirely in a longer (6h/24h) window while the recent 5m/1h
    windows are flat — the pop already ran and is not continuing NOW."""
    change_5m = _f(getattr(candidate, "price_change_5m", 0.0))
    change_1h = _f(getattr(candidate, "price_change_1h", 0.0))
    fresh_min = Config.SPIKE_FRESH_CONTINUATION_MIN_PCT
    return max(change_5m, change_1h) < fresh_min


def _is_reversing(candidate) -> bool:
    """A fresh spike but the multi-hour trend is already down: both 6h AND 24h
    negative == classic pump-and-dump reversal."""
    change_6h = _f(getattr(candidate, "price_change_6h", 0.0))
    change_24h = _f(getattr(candidate, "price_change_24h", 0.0))
    return change_6h < 0.0 and change_24h < 0.0


def pop_vs_drop_score(candidate, sell_preview_impact_pct: Optional[float] = None) -> float:
    """
    Likelihood a high-momentum candidate is a *runner* (pop that keeps going)
    vs an *instant dump* (flat-book / stale / reversing trap).

    Returns a score in roughly [-1, 1]: > 0 leans runner, < 0 leans dump. Uses
    only pre-entry candidate features plus an optional sell-preview round-trip
    impact. Weights come from the journal evidence documented at module top
    (Pump.fun route, freshness, multi-hour direction, liquidity, exit-ability).
    """
    score = 0.0

    # Route: 100% of journal wins were Pump.fun; 80% of high-mom losers were not.
    is_pumpfun = getattr(candidate, "source", "") == "pumpfun"
    score += 0.25 if is_pumpfun else -0.25

    # Freshness: reward a pop that is happening in the last 5m/1h, punish stale.
    if _is_stale_spike(candidate):
        score -= 0.35
    else:
        score += 0.25

    # Multi-hour direction: punish an already-reversing move.
    if _is_reversing(candidate):
        score -= 0.30
    else:
        score += 0.10

    # Liquidity floor (flat book => can't exit): raw pool USD on the live candidate.
    liquidity = _f(getattr(candidate, "liquidity_usd", 0.0))
    floor = Config.SPIKE_MIN_LIQUIDITY_USD
    if floor > 0 and liquidity < floor:
        score -= 0.30
    else:
        score += 0.15

    # Sell-preview round-trip impact (strongest signal when available).
    if sell_preview_impact_pct is not None:
        ceiling = Config.effective_spike_roundtrip_impact_pct()
        if sell_preview_impact_pct >= ceiling:
            score -= 0.50
        elif sell_preview_impact_pct <= ceiling * 0.5:
            score += 0.20

    return max(-1.0, min(1.0, score))


def instant_dump_reason(
    candidate, sell_preview_impact_pct: Optional[float] = None
) -> Optional[str]:
    """
    Return a skip reason when a HIGH-momentum candidate matches a known
    instant-dump signature (flat book / stale spike / already reversing), else
    ``None`` (a quality pop that should be allowed). Callers should only invoke
    this for high-momentum candidates (see ``is_high_momentum``).
    """
    symbol = getattr(candidate, "symbol", "?")
    momentum = _f(getattr(candidate, "momentum_pct", 0.0))

    # (a) Flat book: cannot round-trip out above stop -> guaranteed instant loss.
    if sell_preview_impact_pct is not None:
        ceiling = Config.effective_spike_roundtrip_impact_pct()
        if sell_preview_impact_pct >= ceiling:
            return (
                f"flat-book dump: round-trip impact {sell_preview_impact_pct * 100:.2f}% "
                f">= {ceiling * 100:.2f}% (mom {momentum:.0f}): {symbol}"
            )

    # (b) Thin liquidity: a high-momentum micro-pool is a flat-book trap.
    liquidity = _f(getattr(candidate, "liquidity_usd", 0.0))
    floor = Config.SPIKE_MIN_LIQUIDITY_USD
    if floor > 0 and liquidity < floor:
        return (
            f"flat-book dump: liquidity ${liquidity:,.0f} < ${floor:,.0f} "
            f"(mom {momentum:.0f}): {symbol}"
        )

    # (c) Stale spike: the pop already ran (nothing fresh in 5m/1h).
    if _is_stale_spike(candidate):
        change_5m = _f(getattr(candidate, "price_change_5m", 0.0))
        change_1h = _f(getattr(candidate, "price_change_1h", 0.0))
        return (
            f"stale-spike dump: mom {momentum:.0f} but 5m {change_5m:.1f}% / "
            f"1h {change_1h:.1f}% flat: {symbol}"
        )

    # (d) Reversal: fresh spike but the multi-hour trend is already down.
    if _is_reversing(candidate):
        change_6h = _f(getattr(candidate, "price_change_6h", 0.0))
        change_24h = _f(getattr(candidate, "price_change_24h", 0.0))
        return (
            f"reversing dump: mom {momentum:.0f} but 6h {change_6h:.1f}% & "
            f"24h {change_24h:.1f}% negative: {symbol}"
        )

    return None


def sol_trend_quality_override_passes(
    candidate, sell_preview_impact_pct: Optional[float] = None
) -> bool:
    """
    Quality bar that lets a proven-shape "pop" bypass the SOL 1h macro trend gate.

    This is a *loosening* lever: it only ever ALLOWS an entry the SOL 1h gate would
    otherwise block. It never blocks anything, and it never touches stop-loss,
    profit exits, the 15-minute max hold, forced exits, or learning recording.

    A candidate qualifies only when ALL of these hold (reusing the same
    instant-dump / pop-vs-drop logic used elsewhere so the definition of a
    "quality pop" stays consistent):
      * Acceptable route — Pump.fun (100% of journal wins were Pump.fun route).
      * Liquidity >= ``SPIKE_MIN_LIQUIDITY_USD`` (not a flat-book micro-pool).
      * Fresh 5m/1h momentum (not a stale spike that already ran).
      * No instant-dump signature (flat book / stale / already reversing), which
        also honours the sell-preview round-trip impact ceiling when supplied.
      * Positive pop-vs-drop lean (leans runner, not dump).
    """
    if not Config.SOL_TREND_QUALITY_OVERRIDE_ENABLED:
        return False
    if candidate is None:
        return False

    # Acceptable route: Pump.fun only.
    if getattr(candidate, "source", "") != "pumpfun":
        return False

    # Liquidity floor — a high-momentum micro-pool is a flat-book trap.
    liquidity = _f(getattr(candidate, "liquidity_usd", 0.0))
    floor = Config.SPIKE_MIN_LIQUIDITY_USD
    if floor > 0 and liquidity < floor:
        return False

    # Fresh 5m/1h continuation — do not let stale 6h/24h spikes through.
    if _is_stale_spike(candidate):
        return False

    # No known instant-dump signature (also checks sell-preview round-trip impact
    # against the stop-derived ceiling when the preview is available).
    if instant_dump_reason(candidate, sell_preview_impact_pct) is not None:
        return False

    # Must lean runner over dump.
    if pop_vs_drop_score(candidate, sell_preview_impact_pct) <= 0.0:
        return False

    return True


def spike_trap_reason(
    candidate, sell_preview_impact_pct: Optional[float] = None
) -> Optional[str]:
    """
    Return a skip reason when a candidate looks like a spike trap, else ``None``.

    Two tiers (both on the stored feature scale, same units as
    ``MoverCandidate.momentum_pct`` / ``price_change_5m``):
      1. Absolute ceiling — reject only clearly-absurd artifacts.
      2. High-momentum pop-vs-drop discriminator — for momentum at/above
         ``HIGH_MOMENTUM_QUALITY_PCT``, block only the instant-dump signatures.
    """
    if not Config.SPIKE_TRAP_FILTER_ENABLED:
        return None

    symbol = getattr(candidate, "symbol", "?")

    max_mom = Config.MAX_ENTRY_MOMENTUM_PCT
    momentum = _f(getattr(candidate, "momentum_pct", 0.0))
    if max_mom and max_mom > 0 and momentum > max_mom:
        return f"absurd momentum {momentum:.1f} > ceiling {max_mom:.1f}: {symbol}"

    max_5m = Config.MAX_ENTRY_PRICE_CHANGE_5M_PCT
    change_5m = _f(getattr(candidate, "price_change_5m", 0.0))
    if max_5m and max_5m > 0 and change_5m > max_5m:
        return f"absurd 5m change {change_5m:.1f} > ceiling {max_5m:.1f}: {symbol}"

    if is_high_momentum(candidate):
        return instant_dump_reason(candidate, sell_preview_impact_pct)

    return None


def win_lean_reason(candidate, setup_learner) -> Optional[str]:
    """
    Return a skip reason when a candidate leans toward the learned LOSS profile,
    else ``None``. Falls back to allow (returns ``None``) when the gate is
    disabled, no learner is supplied, or the learner has insufficient data.
    """
    if not Config.SETUP_LEARNING_ENTRY_GATE_ENABLED:
        return None
    if setup_learner is None:
        return None

    score = setup_learner.win_lean_score(candidate)
    if score is None:
        # Insufficient data — never block all entries on a cold learner.
        return None

    threshold = effective_setup_learning_min_win_lean()
    if score < threshold:
        symbol = getattr(candidate, "symbol", "?")
        return f"win-lean {score:.3f} < {threshold:.3f}: {symbol}"
    return None


def entry_winrate_skip_reason(
    candidate, setup_learner, sell_preview_impact_pct: Optional[float] = None
) -> Optional[str]:
    """
    Combined spike-trap + win-lean skip reason for memecoin momentum entries.
    Returns the first blocking reason, or ``None`` when the candidate passes.

    A high-momentum candidate that clears the pop-vs-drop discriminator bypasses
    the win-lean gate: the raw win-lean cosine is dominated by the (unnormalized)
    momentum feature, so it would falsely reject every high-momentum pop and
    defeat the point of momentum trading. Normal-momentum candidates still go
    through the win-lean gate as before.
    """
    reason = spike_trap_reason(candidate, sell_preview_impact_pct)
    if reason:
        return reason
    if Config.SPIKE_TRAP_FILTER_ENABLED and is_high_momentum(candidate):
        return None
    return win_lean_reason(candidate, setup_learner)
