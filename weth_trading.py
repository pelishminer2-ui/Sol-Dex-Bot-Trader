"""WETH on Solana — memecoin-standard trading for pinned WETH mint.

Uses regime-based entry momentum (0.50–0.75%), 1.5% SL, instant +3.25% / +5% full exit,
standard slippage gates; exempt from SOL dump filter (like WSOL).
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Set

from config import Config, WETH_MINT, is_weth_trade_mint, weth_trading_enabled
from price_feed import PriceFeed
from scanner import MoverCandidate
from watchlist_scanner import _best_pair_for_mint, _pair_metadata

logger = logging.getLogger(__name__)


def _memecoin_entry_rule_summary() -> str:
    pct = Config.effective_entry_momentum_pct() * 100
    return f"buy when momentum >= +{pct:.2f}% (memecoin standard)"


def _memecoin_exit_rule_summary() -> str:
    pct3 = Config.INSTANT_EXIT_3PCT * 100
    pct5 = Config.INSTANT_PROFIT_EXIT_PCT * 100
    sl = Config.STOP_LOSS_PCT * 100
    return f"instant +{pct3:.2f}% / +{pct5:.0f}% full exit / SL -{sl:.1f}%"


def weth_entry_qualifies(price_feed: PriceFeed, current_price: float) -> bool:
    """WETH entry gate — same momentum threshold as memecoins."""
    if not weth_trading_enabled() or not is_weth_trade_mint(WETH_MINT):
        return False
    momentum = price_feed.get_momentum(WETH_MINT, current_price)
    if momentum is None:
        return False
    return momentum >= Config.effective_entry_momentum_pct()


def weth_entry_skip_reason(
    price_feed: PriceFeed, current_price: float
) -> Optional[str]:
    if not weth_trading_enabled():
        return "WETH trading disabled"
    if weth_entry_qualifies(price_feed, current_price):
        return None
    momentum = price_feed.get_momentum(WETH_MINT, current_price)
    if momentum is None:
        return "WETH: no momentum data"
    min_pct = Config.effective_entry_momentum_pct()
    return (
        f"WETH: momentum {momentum * 100:.2f}% < +{min_pct * 100:.2f}%"
    )


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
    momentum = (
        price_feed.get_momentum(mint, current_price)
        if current_price
        else None
    )
    qualifies = (
        weth_entry_qualifies(price_feed, current_price)
        if current_price
        else False
    )

    if mint in held:
        entry_status = "in_position"
    else:
        entry_status = "eligible" if qualifies else "standby"

    pair = _best_pair_for_mint(mint)
    meta = _pair_metadata(pair, mint)

    return {
        "enabled": True,
        "asset_type": "weth",
        "mint": mint,
        "symbol": "WETH",
        "label": "WETH",
        "price_usd": current_price,
        "momentum_pct": momentum,
        "entry_momentum_pct": Config.effective_entry_momentum_pct(),
        "qualifies": qualifies,
        "entry_status": entry_status,
        "entry_rule_summary": _memecoin_entry_rule_summary(),
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

    logger.info(
        "WETH trade: $%.4f momentum=%.4f%% status=%s",
        current,
        momentum_pct * 100 if momentum_pct else 0.0,
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
