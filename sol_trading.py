"""SOL self-trading via WSOL or liquid-staking proxy on Jupiter.

Native SOL is the quote currency for all trades. Default mint is WSOL
(So11111111111111111111111111111111111111112), which wraps 1:1; paper/live
PnL tracks DexScreener SOL/USDC price movement.

WSOL uses memecoin standards: regime entry momentum, 1.5% SL, +5% instant,
ladder 3%/4%, standard slippage gates; exempt from SOL dump filter (like WBTC).

Legacy proxy (mSOL, etc.) retains macro-momentum entry and proxy-specific exits.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Set

from config import (
    Config,
    JITOSOL_MINT,
    SOL_MINT,
    is_jitosol_trade_mint,
    is_wsol_trade_mint,
    sol_trading_enabled,
)
from market_regime import REGIME_COLD, detect_market_regime
from price_feed import PriceFeed
from scanner import MoverCandidate
from watchlist_scanner import (
    _best_pair_for_mint,
    _pair_metadata,
    day_pct_gain_from_h24,
    day_usd_gain_from_h24,
)

logger = logging.getLogger(__name__)


def sol_instant_exit_threshold() -> float:
    return Config.SOL_TRADE_INSTANT_EXIT_PCT


def _wsol_price_usd(sol_snapshot: Optional[dict]) -> Optional[float]:
    """Canonical SOL/USD price from DexScreener SOL/USDC (used for WSOL PnL)."""
    snap = sol_snapshot or {}
    price = snap.get("sol_price_usd")
    if price and float(price) > 0:
        return float(price)
    from sol_trend_filter import get_sol_trend_snapshot

    fresh = get_sol_trend_snapshot()
    p = fresh.get("sol_price_usd")
    return float(p) if p and float(p) > 0 else None


def _memecoin_entry_rule_summary() -> str:
    pct = Config.effective_entry_momentum_pct() * 100
    return f"buy when momentum >= +{pct:.2f}% (memecoin standard)"


def _memecoin_exit_rule_summary() -> str:
    pct3 = Config.INSTANT_EXIT_3PCT * 100
    pct5 = Config.INSTANT_PROFIT_EXIT_PCT * 100
    sl = Config.STOP_LOSS_PCT * 100
    return f"instant +{pct3:.2f}% / +{pct5:.0f}% full exit / SL -{sl:.1f}%"


def _proxy_entry_rule_summary() -> str:
    mint = (Config.SOL_TRADE_MINT or "").strip()
    if mint == JITOSOL_MINT:
        from proxy_entry_gate import jitosol_entry_rule_summary

        return jitosol_entry_rule_summary()
    min_h1 = Config.SOL_TRADE_MIN_MOMENTUM_1H_PCT
    if Config.HOT_MARKET_MODE_ENABLED:
        min_h4 = Config.HOT_MARKET_SOL_MIN_4H_PCT
        return f"buy when SOL 1h >= +{min_h1:.1f}% and 4h >= +{min_h4:.1f}% (hot market)"
    return f"buy when SOL 1h >= +{min_h1:.1f}%"


def _proxy_exit_rule_summary() -> str:
    pct3 = Config.INSTANT_EXIT_3PCT * 100
    pct5 = Config.INSTANT_PROFIT_EXIT_PCT * 100
    hold = Config.MAX_HOLD_MINUTES_NON_WBTC
    parts = [f"instant +{pct3:.2f}% / +{pct5:.0f}% full exit"]
    parts.append(f"{hold}m green profit-taking")
    if Config.SOL_TRADE_EXIT_ON_TREND_COLD:
        cold = Config.SOL_TRADE_EXIT_COLD_1H_PCT
        parts.append(f"exit when SOL 1h < +{cold:.1f}%")
    return " / ".join(parts)


def wsol_entry_qualifies(price_feed: PriceFeed, current_price: float) -> bool:
    """WSOL entry gate — same momentum threshold as memecoins."""
    if not sol_trading_enabled() or not is_wsol_trade_mint(SOL_MINT):
        return False
    momentum = price_feed.get_momentum(SOL_MINT, current_price)
    if momentum is None:
        return False
    return momentum >= Config.effective_entry_momentum_pct()


def wsol_entry_skip_reason(
    price_feed: PriceFeed, current_price: float
) -> Optional[str]:
    if not sol_trading_enabled():
        return "SOL trading disabled"
    if wsol_entry_qualifies(price_feed, current_price):
        return None
    momentum = price_feed.get_momentum(SOL_MINT, current_price)
    if momentum is None:
        return "WSOL: no momentum data"
    min_pct = Config.effective_entry_momentum_pct()
    return (
        f"WSOL: momentum {momentum * 100:.2f}% < +{min_pct * 100:.2f}%"
    )


def _jitosol_day_gains_from_pair(
    proxy_price: Optional[float], h24_pct: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    if proxy_price is None or proxy_price <= 0 or h24_pct is None:
        return None, None
    return (
        day_usd_gain_from_h24(proxy_price, h24_pct),
        day_pct_gain_from_h24(h24_pct),
    )


def sol_entry_qualifies(
    sol_snapshot: Optional[dict],
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> bool:
    """True when the configured SOL proxy meets its entry gate."""
    if not sol_trading_enabled() or is_wsol_trade_mint(SOL_MINT):
        return False
    if is_jitosol_trade_mint(Config.SOL_TRADE_MINT):
        from proxy_entry_gate import jitosol_day_gate_passes

        return jitosol_day_gate_passes(
            day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
        )
    snap = sol_snapshot or {}
    if not snap.get("data_available", True) and not snap.get("sol_trend_1h_pct"):
        return False

    h1 = snap.get("sol_trend_1h_pct")
    h4 = snap.get("sol_trend_4h_pct")
    if h1 is None:
        return False
    if h1 < Config.SOL_TRADE_MIN_MOMENTUM_1H_PCT:
        return False

    if Config.HOT_MARKET_MODE_ENABLED and h4 is not None:
        if h4 < Config.HOT_MARKET_SOL_MIN_4H_PCT:
            return False
    return True


def sol_entry_skip_reason(
    sol_snapshot: Optional[dict],
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
) -> Optional[str]:
    if not sol_trading_enabled():
        return "SOL trading disabled"
    if is_wsol_trade_mint(SOL_MINT):
        return "WSOL uses price-feed momentum gate"
    if is_jitosol_trade_mint(Config.SOL_TRADE_MINT):
        from proxy_entry_gate import jitosol_day_gate_skip_reason

        if sol_entry_qualifies(
            sol_snapshot, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
        ):
            return None
        return jitosol_day_gate_skip_reason(
            day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
        )
    if sol_entry_qualifies(sol_snapshot):
        return None
    snap = sol_snapshot or {}
    h1 = snap.get("sol_trend_1h_pct")
    h4 = snap.get("sol_trend_4h_pct")
    if h1 is None:
        return "SOL trade: no 1h momentum data"
    if h1 < Config.SOL_TRADE_MIN_MOMENTUM_1H_PCT:
        return (
            f"SOL trade: 1h {h1:+.2f}% < +{Config.SOL_TRADE_MIN_MOMENTUM_1H_PCT:.2f}%"
        )
    if Config.HOT_MARKET_MODE_ENABLED and h4 is not None:
        if h4 < Config.HOT_MARKET_SOL_MIN_4H_PCT:
            return (
                f"SOL trade: 4h {h4:+.2f}% < +{Config.HOT_MARKET_SOL_MIN_4H_PCT:.2f}%"
            )
    return "SOL trade: entry gate not met"


def sol_trend_exit_cold(sol_snapshot: Optional[dict]) -> bool:
    """True when SOL macro trend has turned cold enough to exit a SOL proxy hold."""
    if not Config.SOL_TRADE_EXIT_ON_TREND_COLD:
        return False
    snap = sol_snapshot or {}
    h1 = snap.get("sol_trend_1h_pct")
    h4 = snap.get("sol_trend_4h_pct")

    if Config.HOT_MARKET_MODE_ENABLED:
        regime = detect_market_regime(snap, [])
        if regime == REGIME_COLD:
            return True

    if h1 is not None and h1 < Config.SOL_TRADE_EXIT_COLD_1H_PCT:
        return True
    if h4 is not None and h4 < 0:
        return True
    return False


def probe_sol_trade_status(
    price_feed: PriceFeed,
    *,
    sol_snapshot: Optional[dict] = None,
    held_mints: Optional[Set[str]] = None,
) -> Dict:
    """Status for GUI / bot polling."""
    if not sol_trading_enabled():
        return {"enabled": False, "asset_type": "sol_wsol"}

    mint = Config.SOL_TRADE_MINT
    snap = sol_snapshot or {}
    h1 = snap.get("sol_trend_1h_pct")
    h4 = snap.get("sol_trend_4h_pct")
    held = held_mints or set()
    is_wsol = is_wsol_trade_mint(mint)

    if is_wsol:
        sol_price = _wsol_price_usd(snap)
        if sol_price:
            price_feed.update([mint])
            price_feed.set_dex_price(mint, sol_price)
        prices = price_feed.update([mint]) if sol_price else price_feed.update([mint])
        current_price = sol_price or prices.get(mint)
        momentum = (
            price_feed.get_momentum(mint, current_price)
            if current_price
            else None
        )
        qualifies = (
            wsol_entry_qualifies(price_feed, current_price)
            if current_price
            else False
        )
        entry_rule = _memecoin_entry_rule_summary()
        exit_rule = _memecoin_exit_rule_summary()
        symbol = "WSOL"
        label = "WSOL"
        asset_type = "sol_wsol"
        proxy_symbol = None
        proxy_price = None
    else:
        pair = _best_pair_for_mint(mint)
        meta = _pair_metadata(pair, mint)
        prices = price_feed.update([mint])
        proxy_price = prices.get(mint)
        sol_price = snap.get("sol_price_usd")
        current_price = sol_price or proxy_price
        h24_pct = meta.get("price_change_h24_pct")
        day_usd_gain, day_pct_gain = _jitosol_day_gains_from_pair(proxy_price, h24_pct)
        if is_jitosol_trade_mint(mint):
            qualifies = sol_entry_qualifies(
                snap, day_usd_gain=day_usd_gain, day_pct_gain=day_pct_gain
            )
        else:
            qualifies = sol_entry_qualifies(snap)
        entry_rule = _proxy_entry_rule_summary()
        exit_rule = _proxy_exit_rule_summary()
        proxy_symbol = meta["symbol"]
        if mint.strip() == JITOSOL_MINT:
            symbol = "JitoSOL"
            label = "JitoSOL"
        else:
            symbol = "SOL"
            label = "SOL"
        asset_type = "sol_proxy"
        momentum = None

    if mint in held:
        entry_status = "in_position"
    else:
        entry_status = "eligible" if qualifies else "standby"

    status: Dict = {
        "enabled": True,
        "asset_type": asset_type,
        "mint": mint,
        "symbol": symbol,
        "label": label,
        "sol_trade_mint": mint,
        "wsol_mint": SOL_MINT,
        "price_usd": current_price,
        "sol_trend_1h_pct": h1,
        "sol_trend_4h_pct": h4,
        "qualifies": qualifies,
        "entry_status": entry_status,
        "entry_rule_summary": entry_rule,
        "exit_rule_summary": exit_rule,
        "hot_market_mode_enabled": Config.HOT_MARKET_MODE_ENABLED,
    }
    if is_wsol:
        status["momentum_pct"] = momentum
        status["entry_momentum_pct"] = Config.effective_entry_momentum_pct()
    else:
        status["proxy_symbol"] = proxy_symbol
        status["proxy_price_usd"] = proxy_price
        status["price_change_h24_pct"] = meta.get("price_change_h24_pct")
        status["day_usd_gain"] = day_usd_gain
        status["day_pct_gain"] = day_pct_gain
        status["sol_trade_min_momentum_1h_pct"] = Config.SOL_TRADE_MIN_MOMENTUM_1H_PCT
    return status


def fetch_sol_trade_candidate(
    price_feed: PriceFeed,
    *,
    status: Optional[Dict] = None,
    sol_snapshot: Optional[dict] = None,
) -> Optional[MoverCandidate]:
    """Build a SOL-trade candidate when enabled and price is known."""
    if not sol_trading_enabled():
        return None

    if status is None:
        status = probe_sol_trade_status(price_feed, sol_snapshot=sol_snapshot)
    if not status.get("enabled"):
        return None

    mint = status["mint"]
    is_wsol = is_wsol_trade_mint(mint)

    if is_wsol:
        current = status.get("price_usd") or _wsol_price_usd(sol_snapshot)
        if current is None or current <= 0:
            return None
        price_feed.update([mint])
        momentum = price_feed.get_momentum(mint, current)
        momentum_pct = momentum if momentum is not None else 0.0
        pair = _best_pair_for_mint(SOL_MINT)
        meta = _pair_metadata(pair, SOL_MINT)
        logger.info(
            "WSOL trade: SOL/USDC $%.4f momentum=%.4f%% status=%s",
            current,
            momentum_pct * 100 if momentum_pct else 0.0,
            status.get("entry_status"),
        )
        return MoverCandidate(
            mint=mint,
            symbol="WSOL",
            name="WSOL (SOL exposure)",
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
            source="sol_trade",
        )

    current = status.get("proxy_price_usd")
    if current is None or current <= 0:
        return None

    pair = _best_pair_for_mint(mint)
    meta = _pair_metadata(pair, mint)
    momentum_pct = 0.0
    if status.get("sol_trend_1h_pct") is not None:
        momentum_pct = float(status["sol_trend_1h_pct"]) / 100.0

    logger.info(
        "SOL trade proxy %s: SOL 1h=%s 4h=%s status=%s",
        meta["symbol"],
        (
            f"{status.get('sol_trend_1h_pct'):+.2f}%"
            if status.get("sol_trend_1h_pct") is not None
            else "?"
        ),
        (
            f"{status.get('sol_trend_4h_pct'):+.2f}%"
            if status.get("sol_trend_4h_pct") is not None
            else "?"
        ),
        status.get("entry_status"),
    )

    if mint.strip() == JITOSOL_MINT:
        display_symbol = "JitoSOL"
        display_name = "JitoSOL (SOL exposure)"
    else:
        display_symbol = "SOL"
        display_name = f"SOL ({meta['symbol']} proxy)"

    h24_pct = meta.get("price_change_h24_pct")
    day_usd_gain, day_pct_gain = _jitosol_day_gains_from_pair(current, h24_pct)

    return MoverCandidate(
        mint=mint,
        symbol=display_symbol,
        name=display_name,
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
        source="sol_trade",
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def merge_sol_trade_watchlist(
    watchlist: List[MoverCandidate],
    price_feed: PriceFeed,
    *,
    sol_snapshot: Optional[dict] = None,
) -> List[MoverCandidate]:
    """Insert SOL trade candidate at front when enabled (always polled for UI)."""
    if not sol_trading_enabled():
        return watchlist
    mint = Config.SOL_TRADE_MINT
    filtered = [c for c in watchlist if c.mint != mint]
    candidate = fetch_sol_trade_candidate(price_feed, sol_snapshot=sol_snapshot)
    if candidate:
        return [candidate] + filtered
    return filtered
