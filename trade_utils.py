"""Helpers for trade journal entries and paper-trade amount formatting."""

from typing import Optional

from config import SOL_MINT, USDC_MINT, USDT_MINT
from jupiter import SwapQuote

_ACTION_LABELS = {
    "buy": "BUY",
    "sell": "SELL",
    "sell_partial": "SELL_PARTIAL",
}
_STABLE_MINTS = frozenset({USDC_MINT, USDT_MINT})
STABLE_DECIMALS = 6


def format_trade_cli(event: dict) -> str:
    """Single-line CLI summary for dashboard trade feed."""
    action = event.get("action", "?")
    symbol = event.get("symbol") or "?"
    paper = event.get("paper_trade") or event.get("dry_run")
    mode = " (paper)" if paper else ""

    if action == "buy":
        sol_in = event.get("sol_in") or event.get("size_sol")
        tokens = event.get("token_amount")
        price = event.get("entry_price")
        parts = [f"BUY {symbol}"]
        if sol_in is not None:
            parts.append(f"{float(sol_in):.4f} SOL")
        if tokens is not None:
            parts.append(f"→ {float(tokens):,.4f} tokens")
        if price is not None:
            parts.append(f"@ ${float(price):.8f}")
        return " ".join(parts) + mode

    label = _ACTION_LABELS.get(action, action.upper())
    if action == "sell_partial" and event.get("tp_level") is not None:
        label = f"SELL_PARTIAL L{event['tp_level']}"

    pnl_sol = event.get("net_pnl_sol")
    if pnl_sol is None:
        pnl_sol = event.get("pnl_sol")
    pnl_pct = event.get("pnl_pct")
    sol_out = event.get("sol_out")
    reason = event.get("reason") or ""

    parts = [f"{label} {symbol}"]
    if sol_out is not None:
        parts.append(f"{float(sol_out):.4f} SOL out")
    if pnl_sol is not None:
        sign = "+" if float(pnl_sol) >= 0 else ""
        parts.append(f"{sign}{float(pnl_sol):.4f} SOL net")
    if pnl_pct is not None:
        sign = "+" if float(pnl_pct) >= 0 else ""
        parts.append(f"({sign}{float(pnl_pct) * 100:.2f}%)")
    if reason:
        parts.append(f"— {reason}")
    return " ".join(parts) + mode

SOL_DECIMALS = 9


def lamports_to_sol(lamports: int) -> float:
    return lamports / (10**SOL_DECIMALS)


def raw_to_ui(raw: int, decimals: int) -> float:
    if decimals < 0:
        return float(raw)
    return raw / (10**decimals)


def quote_sol_flow(
    quote: SwapQuote,
    sol_price_usd: Optional[float] = None,
) -> tuple[float, float]:
    """Return (sol_in, sol_out) from a Jupiter quote.

    SOL legs use lamports. USDC/USDT legs convert via sol_price_usd when provided
    so WSOL↔stable paper/live sizing stays SOL-equivalent.
    """
    if quote.input_mint == SOL_MINT:
        sol_in = lamports_to_sol(quote.in_amount)
    elif quote.input_mint in _STABLE_MINTS and sol_price_usd and sol_price_usd > 0:
        sol_in = (quote.in_amount / (10**STABLE_DECIMALS)) / float(sol_price_usd)
    else:
        sol_in = 0.0

    if quote.output_mint == SOL_MINT:
        sol_out = lamports_to_sol(quote.out_amount)
    elif quote.output_mint in _STABLE_MINTS and sol_price_usd and sol_price_usd > 0:
        sol_out = (quote.out_amount / (10**STABLE_DECIMALS)) / float(sol_price_usd)
    else:
        sol_out = 0.0
    return sol_in, sol_out


def estimate_token_ui(
    token_raw: int,
    decimals: Optional[int],
    sol_in: float,
    entry_price_usd: float,
    sol_price_usd: Optional[float],
) -> float:
    if decimals is not None and decimals >= 0:
        return raw_to_ui(token_raw, decimals)
    if entry_price_usd > 0 and sol_price_usd and sol_price_usd > 0:
        return (sol_in * sol_price_usd) / entry_price_usd
    return float(token_raw)


def entry_sol_basis(position_size_sol: float, sold_raw: int, initial_raw: int) -> float:
    if initial_raw <= 0:
        return position_size_sol
    return position_size_sol * (sold_raw / initial_raw)


def build_buy_journal(
    *,
    candidate,
    entry_price: float,
    quote: SwapQuote,
    trade_size: float,
    momentum: float,
    signature: str,
    dry_run: bool,
    sol_price_usd: Optional[float],
    token_decimals: Optional[int],
    reason: Optional[str] = None,
    buy_count: Optional[int] = None,
    estimated_fees_sol: Optional[float] = None,
    fee_breakdown: Optional[dict] = None,
) -> dict:
    sol_in, _ = quote_sol_flow(quote)
    if sol_in <= 0:
        sol_in = trade_size
    token_raw = quote.out_amount
    token_ui = estimate_token_ui(
        token_raw, token_decimals, sol_in, entry_price, sol_price_usd
    )
    tokens_usd = token_ui * entry_price if entry_price > 0 else None
    event = {
        "action": "buy",
        "mint": candidate.mint,
        "symbol": candidate.symbol,
        "name": candidate.name,
        "entry_price": entry_price,
        "sol_in": sol_in,
        "size_sol": trade_size,
        "token_amount_raw": token_raw,
        "token_amount": token_ui,
        "tokens_usd_value": tokens_usd,
        "momentum": momentum,
        "price_impact_pct": quote.price_impact_pct,
        "signature": signature,
        "dry_run": dry_run,
        "paper_trade": dry_run,
    }
    if reason:
        event["reason"] = reason
    if buy_count is not None:
        event["buy_count"] = buy_count
    if estimated_fees_sol is not None:
        event["estimated_fees_sol"] = float(estimated_fees_sol)
    if fee_breakdown:
        event["fee_breakdown"] = fee_breakdown
        event["fee_estimate_source"] = fee_breakdown.get("sell_fee_source", "fallback")
    event["route_labels"] = _route_labels_from_quote(quote)
    event["cli_line"] = format_trade_cli(event)
    return event


def _route_labels_from_quote(quote: SwapQuote) -> list:
    from fee_estimator import extract_route_labels

    return extract_route_labels(quote.raw)


def build_sell_journal(
    *,
    position,
    quote: SwapQuote,
    token_raw: int,
    current_price: float,
    pnl_pct: float,
    reason: str,
    signature: str,
    dry_run: bool,
    action: str = "sell",
    tp_level: Optional[int] = None,
    tp_level_pct: Optional[float] = None,
    remaining_token_raw: int = 0,
    sol_price_usd: Optional[float],
    token_decimals: Optional[int],
    estimated_fees_sol: Optional[float] = None,
    actual_fees_sol: Optional[float] = None,
) -> dict:
    _, sol_out = quote_sol_flow(quote)
    sol_basis = entry_sol_basis(position.size_sol, token_raw, position.initial_token_amount_raw)
    gross_pnl_sol = sol_out - sol_basis
    fees = float(estimated_fees_sol or 0.0)
    actual = float(actual_fees_sol if actual_fees_sol is not None else fees)
    net_pnl_sol = gross_pnl_sol - actual
    token_ui = estimate_token_ui(
        token_raw, token_decimals, sol_basis, position.entry_price, sol_price_usd
    )
    pnl_usd = net_pnl_sol * sol_price_usd if sol_price_usd else None
    event = {
        "action": action,
        "mint": position.mint,
        "symbol": position.symbol,
        "reason": reason,
        "entry_price": position.entry_price,
        "exit_price": current_price,
        "sol_in_basis": sol_basis,
        "sol_out": sol_out,
        "gross_pnl_sol": gross_pnl_sol,
        "estimated_fees_sol": fees,
        "actual_fees_sol": actual,
        "net_pnl_sol": net_pnl_sol,
        "pnl_sol": net_pnl_sol,
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "token_amount_raw": token_raw,
        "token_amount": token_ui,
        "tokens_sold": token_ui,
        "remaining_token_raw": remaining_token_raw,
        "price_impact_pct": quote.price_impact_pct,
        "signature": signature,
        "dry_run": dry_run,
        "paper_trade": dry_run,
    }
    if tp_level is not None:
        event["tp_level"] = tp_level
    if tp_level_pct is not None:
        event["tp_level_pct"] = tp_level_pct
    event["route_labels"] = _route_labels_from_quote(quote)
    event["cli_line"] = format_trade_cli(event)
    return event
