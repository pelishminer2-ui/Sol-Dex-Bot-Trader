"""Proxy entry gates — WBTC, JitoSOL, and WETH daily USD gain + +3.25% feasibility."""

from __future__ import annotations

from typing import Optional, Tuple

from config import (
    Config,
    is_jitosol_trade_mint,
    is_wbtc_watchlist_mint,
    is_weth_trade_mint,
    jitosol_min_expected_gain_pct,
    wbtc_min_expected_gain_pct,
    weth_min_expected_gain_pct,
)

_EPS = 1e-6


def _day_gate_passes(
    *,
    min_usd: float,
    require_positive_day: bool,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    if require_positive_day:
        if day_pct_gain is None or day_pct_gain <= _EPS:
            return False
    if day_usd_gain is None:
        return False
    return day_usd_gain >= min_usd - _EPS


def _day_gate_skip_reason(
    *,
    label: str,
    min_usd: float,
    require_positive_day: bool,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    if _day_gate_passes(
        min_usd=min_usd,
        require_positive_day=require_positive_day,
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    ):
        return None
    if require_positive_day and (
        day_pct_gain is None or day_pct_gain <= _EPS
    ):
        pct_str = f"{day_pct_gain * 100:.3f}%" if day_pct_gain is not None else "n/a"
        return f"{label}: not positive for day (24h {pct_str})"
    gain_str = f"${day_usd_gain:.2f}" if day_usd_gain is not None else "n/a"
    return f"{label}: day USD gain {gain_str} < ${min_usd:.0f}"


def _entry_rule_summary(
    *,
    label: str,
    min_usd: float,
    require_positive_day: bool,
    target_pct: float,
    sustain_minutes: Optional[int] = None,
) -> str:
    parts: list[str] = []
    if require_positive_day:
        parts.append("positive 24h day")
    parts.append(f"24h USD gain >= ${min_usd:.0f}")
    if sustain_minutes is not None and sustain_minutes > 0:
        parts.append(f"sustain {sustain_minutes}min at/above threshold")
    parts.append(f"+{target_pct * 100:.2f}% instant target feasible after fees/impact")
    return f"buy when " + " + ".join(parts)


def proxy_instant_gain_feasible_from_quotes(
    trade_size_sol: float,
    fee_budget_sol: float,
    *,
    label: str,
    target_pct: float,
    buy_impact_pct: float = 0.0,
    sell_preview_impact_pct: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """Quote-time gate: round-trip impact + fee budget vs instant target."""
    from fee_estimator import estimate_exit_net_sol
    from risk import round_trip_impact_pct

    if trade_size_sol <= 0:
        return False, f"{label}: trade size is zero"

    sell_impact = abs(float(sell_preview_impact_pct or 0.0))
    rt_frac = round_trip_impact_pct(buy_impact_pct, sell_impact) / 100.0
    if rt_frac >= target_pct - _EPS:
        return False, (
            f"{label}: round-trip impact {rt_frac * 100:.2f}% leaves no room "
            f"for +{target_pct * 100:.2f}% instant target"
        )

    net_at_target = estimate_exit_net_sol(
        trade_size_sol, 1.0, target_pct, 0.0, fee_budget_sol
    )
    if net_at_target <= _EPS:
        return False, (
            f"{label}: +{target_pct * 100:.2f}% nets {net_at_target:.4f} SOL after fees "
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


# --- WBTC ---


def wbtc_entry_rule_summary() -> str:
    return _entry_rule_summary(
        label="WBTC",
        min_usd=Config.WBTC_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.WBTC_REQUIRE_POSITIVE_DAY,
        target_pct=wbtc_min_expected_gain_pct(),
        sustain_minutes=Config.WBTC_DAY_GAIN_SUSTAIN_MINUTES,
    )


def wbtc_day_gate_passes(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    return _day_gate_passes(
        min_usd=Config.WBTC_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.WBTC_REQUIRE_POSITIVE_DAY,
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def wbtc_day_gate_skip_reason(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    return _day_gate_skip_reason(
        label="WBTC",
        min_usd=Config.WBTC_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.WBTC_REQUIRE_POSITIVE_DAY,
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def wbtc_instant_gain_feasible_from_quotes(
    trade_size_sol: float,
    fee_budget_sol: float,
    *,
    buy_impact_pct: float = 0.0,
    sell_preview_impact_pct: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    return proxy_instant_gain_feasible_from_quotes(
        trade_size_sol,
        fee_budget_sol,
        label="WBTC",
        target_pct=wbtc_min_expected_gain_pct(),
        buy_impact_pct=buy_impact_pct,
        sell_preview_impact_pct=sell_preview_impact_pct,
    )


def wbtc_entry_skip_reason(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    from wbtc_entry_gate import wbtc_entry_skip_reason as _wbtc_entry_skip_reason

    return _wbtc_entry_skip_reason(
        candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    )


def wbtc_entry_qualifies(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    return wbtc_entry_skip_reason(
        candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    ) is None


# --- JitoSOL ---


def jitosol_entry_rule_summary() -> str:
    return _entry_rule_summary(
        label="JitoSOL",
        min_usd=Config.JITOSOL_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.JITOSOL_REQUIRE_POSITIVE_DAY,
        target_pct=jitosol_min_expected_gain_pct(),
    )


def jitosol_day_gate_passes(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    return _day_gate_passes(
        min_usd=Config.JITOSOL_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.JITOSOL_REQUIRE_POSITIVE_DAY,
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def jitosol_day_gate_skip_reason(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    return _day_gate_skip_reason(
        label="JitoSOL",
        min_usd=Config.JITOSOL_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.JITOSOL_REQUIRE_POSITIVE_DAY,
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def jitosol_instant_gain_feasible_from_quotes(
    trade_size_sol: float,
    fee_budget_sol: float,
    *,
    buy_impact_pct: float = 0.0,
    sell_preview_impact_pct: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    return proxy_instant_gain_feasible_from_quotes(
        trade_size_sol,
        fee_budget_sol,
        label="JitoSOL",
        target_pct=jitosol_min_expected_gain_pct(),
        buy_impact_pct=buy_impact_pct,
        sell_preview_impact_pct=sell_preview_impact_pct,
    )


def jitosol_entry_skip_reason(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    if not is_jitosol_trade_mint(candidate.mint):
        return None
    gain, pct = _resolve_day_fields(
        candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    )
    return jitosol_day_gate_skip_reason(day_usd_gain=gain, day_pct_gain=pct)


def jitosol_entry_qualifies(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    return jitosol_entry_skip_reason(
        candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    ) is None


# --- WETH ---


def weth_entry_rule_summary() -> str:
    return _entry_rule_summary(
        label="WETH",
        min_usd=Config.WETH_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.WETH_REQUIRE_POSITIVE_DAY,
        target_pct=weth_min_expected_gain_pct(),
    )


def weth_day_gate_passes(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    return _day_gate_passes(
        min_usd=Config.WETH_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.WETH_REQUIRE_POSITIVE_DAY,
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def weth_day_gate_skip_reason(
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    return _day_gate_skip_reason(
        label="WETH",
        min_usd=Config.WETH_MIN_DAILY_GAIN_USD,
        require_positive_day=Config.WETH_REQUIRE_POSITIVE_DAY,
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def weth_instant_gain_feasible_from_quotes(
    trade_size_sol: float,
    fee_budget_sol: float,
    *,
    buy_impact_pct: float = 0.0,
    sell_preview_impact_pct: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    return proxy_instant_gain_feasible_from_quotes(
        trade_size_sol,
        fee_budget_sol,
        label="WETH",
        target_pct=weth_min_expected_gain_pct(),
        buy_impact_pct=buy_impact_pct,
        sell_preview_impact_pct=sell_preview_impact_pct,
    )


def weth_entry_skip_reason(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    if not is_weth_trade_mint(candidate.mint):
        return None
    gain, pct = _resolve_day_fields(
        candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    )
    return weth_day_gate_skip_reason(day_usd_gain=gain, day_pct_gain=pct)


def weth_entry_qualifies(
    candidate,
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    return weth_entry_skip_reason(
        candidate, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
    ) is None


def proxy_entry_rank_score(candidate) -> float:
    """
    Rank proxy candidates: favor positive 24h USD drift, deep liquidity, low impact.
  """
    score = 0.0
    day_gain = getattr(candidate, "day_usd_gain", None)
    if day_gain is not None and day_gain > 0:
        score += min(day_gain / 100.0, 5.0)
    liq = getattr(candidate, "liquidity_usd", None) or 0.0
    if liq > 0:
        score += min(liq / 100000.0, 3.0)
    impact = getattr(candidate, "entry_price_impact_pct", None)
    if impact is not None:
        score -= max(impact, 0.0) * 0.1
    return score
