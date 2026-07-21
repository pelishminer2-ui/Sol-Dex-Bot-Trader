"""Estimate round-trip trading fees and compute take-profit ladder levels."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config import Config, SOL_MINT

LAMPORTS_PER_SOL = 1_000_000_000

# Solana base fee: 5000 lamports per signature (conservative default).
DEFAULT_SOL_TX_FEE_LAMPORTS = 5000
ROUND_TRIP_TX_COUNT = 2  # 1 buy + 1 full sell (instant exit, no partials)

# Typical DEX fee bps when Jupiter route labels are unavailable.
DEFAULT_DEX_FEE_BPS_BY_LABEL: Dict[str, int] = {
    "raydium": 25,
    "raydium clmm": 25,
    "orca": 30,
    "whirlpool": 30,
    "meteora": 30,
    "meteora dlmm": 30,
    "lifinity": 20,
    "pump": 100,
    "pump.fun": 100,
    "pumpfun": 100,
    "phoenix": 20,
    "openbook": 20,
}


def _lamports_to_sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL


def _priority_fee_lamports() -> int:
    return int(
        getattr(Config, "SOL_PRIORITY_FEE_LAMPORTS", None)
        or Config.PRIORITY_FEE_LAMPORTS
    )


def _base_tx_fee_lamports() -> int:
    return int(getattr(Config, "SOL_TX_FEE_LAMPORTS", DEFAULT_SOL_TX_FEE_LAMPORTS))


def _fee_buffer_multiplier() -> float:
    pct = float(getattr(Config, "FEE_BUFFER_PCT", 0.0) or 0.0)
    return 1.0 + max(0.0, pct)


def _dex_fee_bps_map() -> Dict[str, int]:
    custom = getattr(Config, "DEX_FEE_BPS_BY_LABEL", None)
    if isinstance(custom, dict) and custom:
        merged = dict(DEFAULT_DEX_FEE_BPS_BY_LABEL)
        merged.update({str(k).lower(): int(v) for k, v in custom.items()})
        return merged
    return DEFAULT_DEX_FEE_BPS_BY_LABEL


def _default_dex_fee_bps() -> int:
    return int(getattr(Config, "DEFAULT_DEX_FEE_BPS", 25))


def _lookup_dex_fee_bps(label: str) -> int:
    key = (label or "").strip().lower()
    if not key:
        return _default_dex_fee_bps()
    fee_map = _dex_fee_bps_map()
    if key in fee_map:
        return fee_map[key]
    for name, bps in fee_map.items():
        if name in key or key in name:
            return bps
    return _default_dex_fee_bps()


def primary_instant_exit_pct() -> float:
    """Primary full-exit profit target when ladder partials are disabled."""
    return float(getattr(Config, "INSTANT_EXIT_3PCT", 0.0325))


def _effective_sell_legs(
    levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
) -> List[tuple[float, float]]:
    """Return (portion, level_pct) sell legs for fee estimation."""
    lvl = list(levels if levels is not None else Config.TAKE_PROFIT_LEVELS)
    shares = list(portions if portions is not None else Config.TAKE_PROFIT_PORTIONS)
    if lvl and shares:
        return list(zip(shares, lvl))
    return [(1.0, primary_instant_exit_pct())]


def _slippage_fraction() -> float:
    return Config.DEFAULT_SLIPPAGE_BPS / 10_000.0


def _quote_dict(quote: Optional[dict | Any]) -> Optional[dict]:
    if quote is None:
        return None
    if isinstance(quote, dict):
        return quote
    raw = getattr(quote, "raw", None)
    return raw if isinstance(raw, dict) else None


def extract_route_labels(quote: Optional[dict | Any]) -> List[str]:
    data = _quote_dict(quote)
    if not data:
        return []
    labels: List[str] = []
    for step in data.get("routePlan") or []:
        swap = (step or {}).get("swapInfo") or {}
        label = swap.get("label")
        if label:
            labels.append(str(label))
    return labels


def weighted_route_dex_bps(quote: Optional[dict | Any]) -> float:
    """Weighted average DEX fee bps from Jupiter route plan labels."""
    data = _quote_dict(quote)
    if not data:
        return float(_default_dex_fee_bps())
    route = data.get("routePlan") or []
    if not route:
        return float(_default_dex_fee_bps())

    total_weight = 0.0
    weighted_bps = 0.0
    for step in route:
        swap = (step or {}).get("swapInfo") or {}
        label = str(swap.get("label") or "")
        weight = float(step.get("percent") or step.get("bps") or 100)
        if weight <= 0:
            continue
        weighted_bps += _lookup_dex_fee_bps(label) * weight
        total_weight += weight
    if total_weight <= 0:
        return float(_default_dex_fee_bps())
    return weighted_bps / total_weight


def extract_platform_fee_sol(quote: Optional[dict | Any]) -> float:
    """Jupiter platformFee.amount when denominated in SOL lamports."""
    data = _quote_dict(quote)
    if not data:
        return 0.0
    platform = data.get("platformFee") or {}
    amount_raw = platform.get("amount")
    if amount_raw is None:
        return 0.0
    try:
        lamports = int(amount_raw)
    except (TypeError, ValueError):
        return 0.0
    if lamports <= 0:
        return 0.0
    input_mint = data.get("inputMint") or ""
    output_mint = data.get("outputMint") or ""
    if input_mint == SOL_MINT or output_mint == SOL_MINT:
        return _lamports_to_sol(lamports)
    return 0.0


def estimate_chain_fees_sol(tx_count: int = ROUND_TRIP_TX_COUNT) -> float:
    """Priority + base signature fees for a full ladder round trip."""
    per_tx = _priority_fee_lamports() + _base_tx_fee_lamports()
    return _lamports_to_sol(per_tx * max(1, tx_count))


def estimate_chain_fee_per_tx_sol() -> float:
    return estimate_chain_fees_sol(tx_count=1)


def estimate_dex_fee_sol(sol_notional: float, quote: Optional[dict | Any] = None) -> float:
    """DEX swap fee on a SOL-notional leg using Jupiter route or fallback bps."""
    if sol_notional <= 0:
        return 0.0
    bps = weighted_route_dex_bps(quote)
    return sol_notional * (bps / 10_000.0)


def estimate_quote_swap_fees_sol(
    quote: Optional[dict | Any],
    *,
    sol_notional: Optional[float] = None,
) -> float:
    """Per-swap DEX + platform fees for one Jupiter quote."""
    data = _quote_dict(quote)
    if not data:
        if sol_notional is None:
            return 0.0
        return estimate_dex_fee_sol(sol_notional)

    if sol_notional is None:
        try:
            in_amount = int(data.get("inAmount") or 0)
        except (TypeError, ValueError):
            in_amount = 0
        input_mint = data.get("inputMint") or ""
        if input_mint == SOL_MINT and in_amount > 0:
            sol_notional = _lamports_to_sol(in_amount)
        else:
            try:
                out_amount = int(data.get("outAmount") or 0)
            except (TypeError, ValueError):
                out_amount = 0
            output_mint = data.get("outputMint") or ""
            if output_mint == SOL_MINT and out_amount > 0:
                sol_notional = _lamports_to_sol(out_amount)

    dex_fee = estimate_dex_fee_sol(float(sol_notional or 0.0), data)
    return dex_fee + extract_platform_fee_sol(data)


def estimate_slippage_fees_sol(
    trade_size_sol: float,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
) -> float:
    """Jupiter slippage estimate on buy plus each sell leg (or one full instant exit)."""
    if trade_size_sol <= 0:
        return 0.0
    slip = _slippage_fraction()
    total = trade_size_sol * slip
    for portion, level_pct in _effective_sell_legs(tp_levels, portions):
        sell_notional = trade_size_sol * portion * (1.0 + level_pct)
        total += sell_notional * slip
    return total


def fee_breakdown_from_quotes(
    trade_size_sol: float,
    jupiter_quote_buy: Optional[dict | Any] = None,
    jupiter_quote_sell: Optional[dict | Any] = None,
    *,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Detailed fee components for logging / GUI."""
    legs = _effective_sell_legs(tp_levels, portions)

    chain_sol = estimate_chain_fees_sol()
    buy_dex = estimate_quote_swap_fees_sol(
        jupiter_quote_buy, sol_notional=trade_size_sol
    )

    sell_dex = 0.0
    sell_source = "fallback"
    if jupiter_quote_sell is not None:
        sell_dex = estimate_quote_swap_fees_sol(jupiter_quote_sell)
        sell_source = "jupiter"
    else:
        for portion, level_pct in legs:
            sell_notional = trade_size_sol * portion * (1.0 + level_pct)
            sell_dex += estimate_dex_fee_sol(sell_notional)

    slippage_sol = 0.0
    if jupiter_quote_buy is None and jupiter_quote_sell is None:
        slippage_sol = estimate_slippage_fees_sol(trade_size_sol, tp_levels, portions)
    elif jupiter_quote_sell is None:
        # Buy quote only — reserve slippage budget for unmatched sell legs.
        slip = _slippage_fraction()
        for portion, level_pct in legs:
            sell_notional = trade_size_sol * portion * (1.0 + level_pct)
            slippage_sol += sell_notional * slip
    subtotal = chain_sol + buy_dex + sell_dex + slippage_sol
    buffer_mult = _fee_buffer_multiplier()
    total = subtotal * buffer_mult

    return {
        "chain_sol": chain_sol,
        "dex_buy_sol": buy_dex,
        "dex_sell_sol": sell_dex,
        "slippage_sol": slippage_sol,
        "subtotal_sol": subtotal,
        "fee_buffer_pct": float(getattr(Config, "FEE_BUFFER_PCT", 0.0) or 0.0),
        "buffered_total_sol": total,
        "route_labels_buy": extract_route_labels(jupiter_quote_buy),
        "route_labels_sell": extract_route_labels(jupiter_quote_sell),
        "sell_fee_source": sell_source,
        "priority_fee_lamports": _priority_fee_lamports(),
        "tx_fee_lamports": _base_tx_fee_lamports(),
    }


def estimate_round_trip_fees_sol(
    trade_size_sol: float,
    jupiter_quote_buy: Optional[dict | Any] = None,
    jupiter_quote_sell: Optional[dict | Any] = None,
    *,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
) -> float:
    """Total estimated fees in SOL for one full ladder trade."""
    if Config.FEE_BUFFER_SOL is not None:
        return float(Config.FEE_BUFFER_SOL)
    breakdown = fee_breakdown_from_quotes(
        trade_size_sol,
        jupiter_quote_buy,
        jupiter_quote_sell,
        tp_levels=tp_levels,
        portions=portions,
    )
    return breakdown["buffered_total_sol"]


def estimate_round_trip_fees(trade_size_sol: float) -> float:
    """Backward-compatible alias without live Jupiter quotes."""
    return estimate_round_trip_fees_sol(trade_size_sol)


def get_fee_budget(
    trade_size_sol: float,
    jupiter_quote_buy: Optional[dict | Any] = None,
    jupiter_quote_sell: Optional[dict | Any] = None,
) -> float:
    """Round-trip fee budget stored on positions."""
    return estimate_round_trip_fees_sol(
        trade_size_sol, jupiter_quote_buy, jupiter_quote_sell
    )


def gross_profit_target(trade_size_sol: float) -> float:
    """Gross SOL gain needed before fees to hit the net profit target."""
    return Config.TARGET_NET_PROFIT_SOL + get_fee_budget(trade_size_sol)


def compute_take_profit_levels(
    trade_size_sol: float,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
) -> List[float]:
    """
    Return fixed take-profit ladder levels for any trade size.

    Empty by default — exits use instant +3.25% / +5% full sells instead of partials.
    trade_size_sol is accepted for call-site compatibility but does not scale levels.
    """
    del trade_size_sol, portions  # unused; ladder is not scaled by size
    if tp_levels is not None:
        return list(tp_levels)
    return list(Config.TAKE_PROFIT_LEVELS)


def estimate_leg_fees_sol(
    trade_size_sol: float,
    level_index: int,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
    *,
    fee_budget_sol: Optional[float] = None,
) -> float:
    """Estimated fees for one partial sell leg (includes amortized buy cost)."""
    levels = tp_levels if tp_levels is not None else Config.TAKE_PROFIT_LEVELS
    shares = portions if portions is not None else Config.TAKE_PROFIT_PORTIONS
    if level_index < 0 or level_index >= len(shares):
        return 0.0

    chain_per_tx = estimate_chain_fee_per_tx_sol()
    slip = _slippage_fraction()
    leg_count = max(len(shares), 1)
    budget = fee_budget_sol if fee_budget_sol is not None else get_fee_budget(trade_size_sol)

    buy_slippage = trade_size_sol * slip
    buy_chain = chain_per_tx
    buy_dex = estimate_dex_fee_sol(trade_size_sol)
    buy_share = (buy_slippage + buy_chain + buy_dex) / leg_count

    portion = shares[level_index]
    level_pct = levels[level_index] if level_index < len(levels) else 0.0
    sell_notional = trade_size_sol * portion * (1.0 + level_pct)
    sell_slippage = sell_notional * slip
    sell_chain = chain_per_tx
    sell_dex = estimate_dex_fee_sol(sell_notional)

    leg_fees = buy_share + sell_slippage + sell_chain + sell_dex
    return min(leg_fees, budget)


def estimate_full_exit_fees_sol(
    trade_size_sol: float,
    remaining_fraction: float,
    fees_allocated_sol: float,
    fee_budget_sol: float,
) -> float:
    """Remaining fee budget for a full exit (stop-loss, time stop, etc.)."""
    remaining = max(0.0, fee_budget_sol - fees_allocated_sol)
    if remaining_fraction <= 0:
        return 0.0
    chain_per_tx = estimate_chain_fee_per_tx_sol()
    slip = _slippage_fraction()
    sell_notional = trade_size_sol * remaining_fraction
    incremental = (
        sell_notional * slip
        + chain_per_tx
        + estimate_dex_fee_sol(sell_notional)
    )
    return min(remaining, incremental + remaining * 0.5)


def expected_gross_profit_sol(
    trade_size_sol: float,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
) -> float:
    """Expected gross SOL profit if every ladder level fills at target prices."""
    legs = _effective_sell_legs(tp_levels, portions)
    return trade_size_sol * sum(p * l for p, l in legs)


def expected_net_profit_sol(
    trade_size_sol: float,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
    *,
    fee_budget_sol: Optional[float] = None,
    jupiter_quote_buy: Optional[dict | Any] = None,
    jupiter_quote_sell: Optional[dict | Any] = None,
) -> float:
    """Expected net SOL profit after estimated round-trip fees."""
    gross = expected_gross_profit_sol(trade_size_sol, tp_levels, portions)
    fees = fee_budget_sol
    if fees is None:
        fees = estimate_round_trip_fees_sol(
            trade_size_sol, jupiter_quote_buy, jupiter_quote_sell,
            tp_levels=tp_levels, portions=portions,
        )
    return gross - fees


def l1_gross_profit_sol(
    trade_size_sol: float,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
) -> float:
    """Gross SOL profit if only L1 partial fills at its target."""
    levels = tp_levels if tp_levels is not None else Config.TAKE_PROFIT_LEVELS
    shares = portions if portions is not None else Config.TAKE_PROFIT_PORTIONS
    if not levels or not shares:
        return 0.0
    return trade_size_sol * shares[0] * levels[0]


def trade_covers_l1_fees(
    trade_size_sol: float,
    *,
    fee_budget_sol: Optional[float] = None,
    jupiter_quote_buy: Optional[dict | Any] = None,
    jupiter_quote_sell: Optional[dict | Any] = None,
) -> Tuple[bool, float, float]:
    """
    Return (ok, l1_gross_sol, required_sol) where required = L1 leg fees + MIN_NET_WIN_SOL.
    """
    levels = Config.TAKE_PROFIT_LEVELS
    portions = Config.TAKE_PROFIT_PORTIONS
    if (not levels or not portions) and Config.INSTANT_PROFIT_EXIT_ENABLED:
        instant_gross = trade_size_sol * primary_instant_exit_pct()
        leg_fees = estimate_leg_fees_sol(
            trade_size_sol,
            0,
            [primary_instant_exit_pct()],
            [1.0],
            fee_budget_sol=fee_budget_sol,
        )
        if jupiter_quote_buy is not None or jupiter_quote_sell is not None:
            buy_leg = estimate_quote_swap_fees_sol(
                jupiter_quote_buy, sol_notional=trade_size_sol
            ) + estimate_chain_fee_per_tx_sol()
            sell_leg = 0.0
            if jupiter_quote_sell is not None:
                sell_leg = (
                    estimate_quote_swap_fees_sol(jupiter_quote_sell)
                    + estimate_chain_fee_per_tx_sol()
                )
            else:
                sell_notional = trade_size_sol * (1.0 + primary_instant_exit_pct())
                sell_leg = (
                    estimate_dex_fee_sol(sell_notional, jupiter_quote_sell)
                    + estimate_chain_fee_per_tx_sol()
                )
            leg_fees = buy_leg + sell_leg
        required = leg_fees
        return instant_gross >= required, instant_gross, required

    l1_gross = l1_gross_profit_sol(trade_size_sol, levels, portions)
    leg_fees = estimate_leg_fees_sol(
        trade_size_sol,
        0,
        levels,
        portions,
        fee_budget_sol=fee_budget_sol,
    )
    if jupiter_quote_buy is not None or jupiter_quote_sell is not None:
        buy_leg = estimate_quote_swap_fees_sol(
            jupiter_quote_buy, sol_notional=trade_size_sol
        ) + estimate_chain_fee_per_tx_sol()
        sell_leg = 0.0
        if jupiter_quote_sell is not None:
            sell_leg = (
                estimate_quote_swap_fees_sol(jupiter_quote_sell)
                + estimate_chain_fee_per_tx_sol()
            )
        elif levels and portions:
            sell_notional = trade_size_sol * portions[0] * (1.0 + levels[0])
            sell_leg = (
                estimate_dex_fee_sol(sell_notional, jupiter_quote_sell)
                + estimate_chain_fee_per_tx_sol()
            )
        leg_fees = buy_leg + sell_leg
    required = leg_fees
    return l1_gross >= required, l1_gross, required


def estimate_partial_net_win_sol(
    trade_size_sol: float,
    level_index: int,
    level_pct: float,
    tp_levels: Optional[List[float]] = None,
    portions: Optional[List[float]] = None,
    *,
    fee_budget_sol: Optional[float] = None,
) -> float:
    """Estimated net SOL for one ladder partial at target level."""
    shares = portions if portions is not None else Config.TAKE_PROFIT_PORTIONS
    if level_index < 0 or level_index >= len(shares):
        return 0.0
    portion = shares[level_index]
    gross = trade_size_sol * portion * level_pct
    fees = estimate_leg_fees_sol(
        trade_size_sol,
        level_index,
        tp_levels,
        portions,
        fee_budget_sol=fee_budget_sol,
    )
    return gross - fees


def estimate_full_exit_net_sol(
    trade_size_sol: float,
    remaining_fraction: float,
    pnl_pct: float,
    fees_allocated_sol: float,
    fee_budget_sol: float,
) -> float:
    """Estimated net SOL for a full exit at current pnl_pct on the remainder."""
    if remaining_fraction <= 0:
        return 0.0
    gross = trade_size_sol * remaining_fraction * pnl_pct
    exit_fees = estimate_full_exit_fees_sol(
        trade_size_sol,
        remaining_fraction,
        fees_allocated_sol,
        fee_budget_sol,
    )
    return gross - exit_fees


def estimate_exit_net_sol(
    trade_size_sol: float,
    remaining_fraction: float,
    pnl_pct: float,
    fees_allocated_sol: float,
    fee_budget_sol: float,
) -> float:
    """Alias for full-exit net estimate (used by WBTC profit gate)."""
    return estimate_full_exit_net_sol(
        trade_size_sol,
        remaining_fraction,
        pnl_pct,
        fees_allocated_sol,
        fee_budget_sol,
    )


def preview_round_trip_with_jupiter(
    trade_size_sol: float,
    token_mint: str,
    *,
    slippage_bps: Optional[int] = None,
    timeout: int = 8,
    max_retries: int = 1,
) -> Dict[str, Any]:
    """
    Live Jupiter buy + L1 sell preview for GUI / config API.
    Returns fee breakdown; empty dict on quote failure.
    """
    if trade_size_sol <= 0 or not token_mint:
        return {}

    from jupiter_client import get_jupiter_client

    client = get_jupiter_client()
    slip = slippage_bps or Config.DEFAULT_SLIPPAGE_BPS
    lamports = int(trade_size_sol * LAMPORTS_PER_SOL)
    buy = client.get_quote(
        SOL_MINT, token_mint, lamports, slip, timeout=timeout, max_retries=max_retries
    )
    if not buy or buy.get("error"):
        return {}

    # Empty ladder (Steady Trade / Best Win instant-exit presets) → full-size sell preview.
    legs = _effective_sell_legs()
    sell_portion = float(legs[0][0]) if legs else 1.0
    try:
        token_l1 = int(int(buy.get("outAmount") or 0) * sell_portion)
    except (TypeError, ValueError, IndexError):
        token_l1 = 0

    sell = None
    if token_l1 > 0:
        sell = client.get_quote(
            token_mint,
            SOL_MINT,
            token_l1,
            slip,
            use_cache=True,
            timeout=timeout,
            max_retries=max_retries,
        )

    breakdown = fee_breakdown_from_quotes(trade_size_sol, buy, sell)
    breakdown["estimated_fees_sol"] = breakdown["buffered_total_sol"]
    breakdown["expected_ladder_gross_sol"] = expected_gross_profit_sol(trade_size_sol)
    breakdown["expected_ladder_net_sol"] = (
        breakdown["expected_ladder_gross_sol"] - breakdown["estimated_fees_sol"]
    )
    breakdown["preview_mint"] = token_mint
    breakdown["fee_source"] = "jupiter" if sell else "jupiter_buy_only"
    return breakdown
