"""WBTC pinned-watchlist entry gates — positive day, $75 gain, +3.25% feasibility."""

from __future__ import annotations

from typing import Optional, Tuple

from config import Config, is_wbtc_watchlist_mint, wbtc_min_expected_gain_pct

_EPS = 1e-6


def wbtc_entry_rule_summary() -> str:
    """Human-readable WBTC entry policy for GUI / status probes."""
    parts: list[str] = []
    if Config.WBTC_REQUIRE_POSITIVE_DAY:
        parts.append("positive 24h day")
    parts.append(f"24h USD gain >= ${Config.WBTC_MIN_DAILY_GAIN_USD:.0f}")
    target = wbtc_min_expected_gain_pct()
    parts.append(f"+{target * 100:.2f}% instant target feasible after fees/impact")
    return "buy when " + " + ".join(parts)


def wbtc_day_gate_passes(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    """True when WBTC is positive for the day and up at least the USD threshold."""
    if Config.WBTC_REQUIRE_POSITIVE_DAY:
        if day_pct_gain is None or day_pct_gain <= _EPS:
            return False
    if day_usd_gain is None:
        return False
    return day_usd_gain >= Config.WBTC_MIN_DAILY_GAIN_USD - _EPS


def wbtc_day_gate_skip_reason(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    if wbtc_day_gate_passes(day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain):
        return None
    if Config.WBTC_REQUIRE_POSITIVE_DAY and (
        day_pct_gain is None or day_pct_gain <= _EPS
    ):
        pct_str = f"{day_pct_gain * 100:.3f}%" if day_pct_gain is not None else "n/a"
        return f"WBTC: not positive for day (24h {pct_str})"
    threshold = Config.WBTC_MIN_DAILY_GAIN_USD
    gain_str = f"${day_usd_gain:.2f}" if day_usd_gain is not None else "n/a"
    return f"WBTC: day USD gain {gain_str} < ${threshold:.0f}"


def wbtc_instant_gain_feasible_from_quotes(
    trade_size_sol: float,
    fee_budget_sol: float,
    *,
    buy_impact_pct: float = 0.0,
    sell_preview_impact_pct: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Quote-time gate: round-trip impact must leave room for the instant target and
    the +target% move must net positive SOL after fees (same standard as exits).
    """
    from fee_estimator import estimate_exit_net_sol
    from risk import round_trip_impact_pct

    if trade_size_sol <= 0:
        return False, "WBTC: trade size is zero"

    target = wbtc_min_expected_gain_pct()
    sell_impact = abs(float(sell_preview_impact_pct or 0.0))
    rt_frac = round_trip_impact_pct(buy_impact_pct, sell_impact) / 100.0
    if rt_frac >= target - _EPS:
        return False, (
            f"WBTC: round-trip impact {rt_frac * 100:.2f}% leaves no room "
            f"for +{target * 100:.2f}% instant target"
        )

    net_at_target = estimate_exit_net_sol(
        trade_size_sol, 1.0, target, 0.0, fee_budget_sol
    )
    if net_at_target <= _EPS:
        return False, (
            f"WBTC: +{target * 100:.2f}% nets {net_at_target:.4f} SOL after fees "
            f"(instant target not feasible)"
        )
    return True, None


def _resolve_day_fields(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> tuple[Optional[float], Optional[float]]:
    gain = day_usd_gain
    if gain is None:
        gain = getattr(candidate, "day_usd_gain", None)
    pct = day_pct_gain
    if pct is None:
        pct = getattr(candidate, "day_pct_gain", None)
    return gain, pct


def wbtc_entry_skip_reason(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    """Strategy-level WBTC entry skip reason (day gate; quote gate runs in bot)."""
    if not is_wbtc_watchlist_mint(candidate.mint):
        return None
    gain, pct = _resolve_day_fields(
        candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    )
    return wbtc_day_gate_skip_reason(day_usd_gain=gain, day_pct_gain=pct)


def wbtc_entry_qualifies(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    return wbtc_entry_skip_reason(
        candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    ) is None
