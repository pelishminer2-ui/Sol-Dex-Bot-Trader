"""WETH on Solana — proxy mainstream asset with dollar day-gain entry gate.

Entry: positive 24h day + WETH_MIN_DAILY_GAIN_USD ($150 default) + optional
+3.25% quote feasibility. Exits: 1.5% SL, instant +3.25%/+5%, 15-min green hold.
Exempt from SOL dump filter (like WBTC/JitoSOL).
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Set

from config import Config, WETH_MINT, is_weth_trade_mint, weth_trading_enabled
from price_feed import PriceFeed
from proxy_entry_gate import (
    weth_entry_qualifies as proxy_weth_entry_qualifies,
    weth_entry_rule_summary,
)
from scanner import MoverCandidate
from watchlist_scanner import (
    _best_pair_for_mint,
    _pair_metadata,
    day_pct_gain_from_h24,
    day_usd_gain_from_h24,
)

logger = logging.getLogger(__name__)


def _weth_day_gains_from_pair(
    price_usd: Optional[float], h24_pct: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    if price_usd is None or price_usd <= 0 or h24_pct is None:
        return None, None
    return (
        day_usd_gain_from_h24(price_usd, h24_pct),
        day_pct_gain_from_h24(h24_pct),
    )


def _memecoin_exit_rule_summary() -> str:
    pct3 = Config.INSTANT_EXIT_3PCT * 100
    pct5 = Config.INSTANT_PROFIT_EXIT_PCT * 100
    sl = Config.STOP_LOSS_PCT * 100
    hold = Config.MAX_HOLD_MINUTES_NON_WBTC
    return (
        f"instant +{pct3:.2f}% / +{pct5:.0f}% full exit / SL -{sl:.1f}% / "
        f"{hold}m green profit-taking"
    )


def weth_entry_qualifies_from_status(status: Dict) -> bool:
    """WETH entry gate — positive 24h day + $150 USD gain from DexScreener h24."""
    if not weth_trading_enabled() or not is_weth_trade_mint(WETH_MINT):
        return False
    return proxy_weth_entry_qualifies(
        _status_as_candidate_fields(status),
        day_usd_gain=status.get("day_usd_gain"),
        day_pct_gain=status.get("day_pct_gain"),
    )


def _status_as_candidate_fields(status: Dict):
    """Minimal namespace for proxy_entry_gate helpers."""

    class _Fields:
        mint = status.get("mint", WETH_MINT)

    return _Fields()


def weth_entry_skip_reason_from_status(status: Dict) -> Optional[str]:
    from proxy_entry_gate import weth_entry_skip_reason

    if not weth_trading_enabled():
        return "WETH trading disabled"
    if weth_entry_qualifies_from_status(status):
        return None
    return weth_entry_skip_reason(
        _status_as_candidate_fields(status),
        day_usd_gain=status.get("day_usd_gain"),
        day_pct_gain=status.get("day_pct_gain"),
    )


def weth_entry_qualifies(price_feed: PriceFeed, current_price: float) -> bool:
    """Backward-compatible entry check using price feed + DexScreener h24."""
    status = probe_weth_trade_status(price_feed)
    return status.get("qualifies", False)


def weth_entry_skip_reason(
    price_feed: PriceFeed, current_price: float
) -> Optional[str]:
    status = probe_weth_trade_status(price_feed)
    return weth_entry_skip_reason_from_status(status)


def probe_weth_trade_status(
    price_feed: PriceFeed,
    *,
    held_mints: Optional[Set[str]] = None,
) -> Dict:
    """Status for GUI / bot polling."""
    if not weth_trading_enabled():
        return {"enabled": False, "asset_type": "weth"}

    mint = Config.WETH_MINT
    held = held_mints or set()
    prices = price_feed.update([mint])
    current_price = prices.get(mint)
    pair = _best_pair_for_mint(mint)
    meta = _pair_metadata(pair, mint)
    h24_pct = meta.get("price_change_h24_pct")
    day_usd_gain, day_pct_gain = _weth_day_gains_from_pair(current_price, h24_pct)
    qualifies = (
        weth_entry_qualifies_from_status(
            {
                "mint": mint,
                "day_usd_gain": day_usd_gain,
                "day_pct_gain": day_pct_gain,
            }
        )
        if current_price
        else False
    )

    if mint in held:
        entry_status = "in_position"
    else:
        entry_status = "eligible" if qualifies else "standby"

    momentum = (
        price_feed.get_momentum(mint, current_price)
        if current_price
        else None
    )

    return {
        "enabled": True,
        "asset_type": "weth",
        "mint": mint,
        "symbol": "WETH",
        "label": "WETH",
        "price_usd": current_price,
        "momentum_pct": momentum,
        "day_usd_gain": day_usd_gain,
        "day_pct_gain": day_pct_gain,
        "price_change_h24_pct": h24_pct,
        "qualifies": qualifies,
        "entry_status": entry_status,
        "entry_rule_summary": weth_entry_rule_summary(),
        "exit_rule_summary": _memecoin_exit_rule_summary(),
        "liquidity_usd": meta["liquidity_usd"],
        "volume_24h_usd": meta["volume_24h_usd"],
        "dex": meta["dex"],
        "pair_address": meta["pair_address"],
    }


def fetch_weth_trade_candidate(
    price_feed: PriceFeed,
    *,
    status: Optional[Dict] = None,
) -> Optional[MoverCandidate]:
    """Build a WETH candidate when enabled and price is known."""
    if not weth_trading_enabled():
        return None

    if status is None:
        status = probe_weth_trade_status(price_feed)
    if not status.get("enabled"):
        return None

    mint = status["mint"]
    current = status.get("price_usd")
    if current is None or current <= 0:
        return None

    price_feed.update([mint])
    momentum = price_feed.get_momentum(mint, current)
    momentum_pct = momentum if momentum is not None else 0.0
    pair = _best_pair_for_mint(mint)
    meta = _pair_metadata(pair, mint)
    day_usd_gain = status.get("day_usd_gain")
    day_pct_gain = status.get("day_pct_gain")

    logger.info(
        "WETH trade: $%.4f day_usd_gain=%s day_pct=%s status=%s",
        current,
        f"${day_usd_gain:.2f}" if day_usd_gain is not None else "?",
        f"{day_pct_gain * 100:.2f}%" if day_pct_gain is not None else "?",
        status.get("entry_status"),
    )

    return MoverCandidate(
        mint=mint,
        symbol="WETH",
        name=meta["name"] or "Wrapped Ether (WETH)",
        pair_address=meta["pair_address"],
        dex=meta["dex"],
        price_usd=current,
        liquidity_usd=meta["liquidity_usd"],
        volume_24h_usd=meta["volume_24h_usd"],
        momentum_pct=momentum_pct,
        price_change_5m=meta["price_change_5m"],
        price_change_1h=meta["price_change_1h"],
        pool_created_at=meta["pool_created_at"],
        scanned_at=time.time(),
        source="weth_trade",
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def merge_weth_trade_watchlist(
    watchlist: List[MoverCandidate],
    price_feed: PriceFeed,
) -> List[MoverCandidate]:
    """Insert WETH trade candidate at front when enabled (always polled for UI)."""
    if not weth_trading_enabled():
        return watchlist
    mint = Config.WETH_MINT
    filtered = [c for c in watchlist if c.mint != mint]
    candidate = fetch_weth_trade_candidate(price_feed)
    if candidate:
        return [candidate] + filtered
    return filtered
