"""Pinned mint watchlist — always polled; per-mint entry gates and exit rules."""

import logging
import time
from typing import Dict, List, Optional, Set

from config import Config, WatchlistMintRule
from dexscreener_client import get_dexscreener_client
from price_feed import PriceFeed
from scanner import MoverCandidate

logger = logging.getLogger(__name__)


def _best_pair_for_mint(mint: str) -> Optional[dict]:
    """Return the highest-liquidity DexScreener pair for a mint."""
    pairs = get_dexscreener_client().get_token_pairs(mint)
    if not pairs:
        return None

    best: Optional[dict] = None
    best_liq = -1.0
    for pair in pairs:
        if pair.get("chainId") != "solana":
            continue
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        if liq > best_liq:
            best_liq = liq
            best = pair
    return best


def _pair_metadata(pair: Optional[dict], mint: str) -> dict:
    if not pair:
        return {
            "symbol": mint[:8],
            "name": mint[:8],
            "pair_address": "",
            "dex": "unknown",
            "liquidity_usd": 0.0,
            "volume_24h_usd": 0.0,
            "price_change_5m": 0.0,
            "price_change_1h": 0.0,
            "price_change_h24_pct": None,
            "pool_created_at": None,
        }

    base = pair.get("baseToken") or {}
    price_change = pair.get("priceChange") or {}
    h24_raw = price_change.get("h24")
    return {
        "symbol": base.get("symbol") or mint[:8],
        "name": base.get("name") or base.get("symbol") or mint[:8],
        "pair_address": pair.get("pairAddress") or "",
        "dex": pair.get("dexId") or "unknown",
        "liquidity_usd": float((pair.get("liquidity") or {}).get("usd") or 0),
        "volume_24h_usd": float((pair.get("volume") or {}).get("h24") or 0),
        "price_change_5m": float(price_change.get("m5") or 0) / 100.0,
        "price_change_1h": float(price_change.get("h1") or 0) / 100.0,
        "price_change_h24_pct": float(h24_raw) if h24_raw is not None else None,
        "pool_created_at": pair.get("pairCreatedAt"),
    }


def day_usd_gain_from_h24(current_usd: float, h24_pct: float) -> Optional[float]:
    """
    Absolute USD gain since ~24h ago using DexScreener priceChange.h24 (percent).

    Example: price $63,636 with +0.118% 24h change → day open ≈ $63,561 → gain ≈ $75.
    """
    if current_usd <= 0:
        return None
    h24_frac = h24_pct / 100.0
    if h24_frac <= -1.0:
        return None
    day_open = current_usd / (1.0 + h24_frac)
    return current_usd - day_open


def day_pct_gain_from_h24(h24_pct: Optional[float]) -> Optional[float]:
    """24h percent change as a fraction (DexScreener h24=5.0 → 0.05)."""
    if h24_pct is None:
        return None
    return float(h24_pct) / 100.0


def get_watchlist_rule(mint: str) -> Optional[WatchlistMintRule]:
    return Config.get_watchlist_rule(mint)


def compute_watchlist_gains(
    current_usd: float,
    session_baseline_usd: Optional[float],
    h24_pct: Optional[float],
    rule: Optional[WatchlistMintRule] = None,
) -> Dict:
    """
    Compute gains for a pinned-mint entry gate.

  Entry qualifies on rule-specific threshold:
    - min_day_usd_gain: 24h/day USD gain from DexScreener h24
    - min_day_pct_gain: 24h percent change (h24) as a fraction
    """
    session_gain: Optional[float] = None
    day_gain: Optional[float] = None
    day_open: Optional[float] = None
    day_pct_gain = day_pct_gain_from_h24(h24_pct)

    if session_baseline_usd is not None and session_baseline_usd > 0:
        session_gain = current_usd - session_baseline_usd

    if h24_pct is not None:
        day_gain = day_usd_gain_from_h24(current_usd, h24_pct)
        if day_gain is not None:
            day_open = current_usd - day_gain

    eps = 1e-6
    qualifies = False
    if rule and rule.min_day_pct_gain is not None:
        qualifies = day_pct_gain is not None and day_pct_gain >= rule.min_day_pct_gain - eps
    else:
        threshold = (
            rule.min_day_usd_gain
            if rule and rule.min_day_usd_gain is not None
            else Config.WATCHLIST_MIN_USD_GAIN
        )
        qualifies = day_gain is not None and day_gain >= threshold - eps

    usd_gain: Optional[float] = day_gain
    gain_source = "dexscreener_24h" if day_gain is not None else "none"
    baseline_usd: Optional[float] = day_open
    if usd_gain is None and session_gain is not None:
        usd_gain = session_gain
        gain_source = "session_baseline"
        baseline_usd = session_baseline_usd

    return {
        "session_usd_gain": session_gain,
        "session_baseline_usd": session_baseline_usd,
        "day_usd_gain": day_gain,
        "day_pct_gain": day_pct_gain,
        "day_baseline_usd": day_open,
        "usd_gain": usd_gain,
        "baseline_usd": baseline_usd,
        "gain_source": gain_source,
        "qualifies": qualifies,
    }


def is_pinned_watchlist_mint(mint: str) -> bool:
    return Config.watchlist_mint_enabled() and mint in Config.watchlist_mints()


def watchlist_entry_qualifies(
    rule: Optional[WatchlistMintRule],
    *,
    day_usd_gain: Optional[float] = None,
    day_pct_gain: Optional[float] = None,
    usd_gain: Optional[float] = None,
    session_usd_gain: Optional[float] = None,
) -> bool:
    """True when a pinned mint meets its configured entry gate."""
    if rule is None:
        return False
    eps = 1e-6
    if rule.min_day_pct_gain is not None:
        return day_pct_gain is not None and day_pct_gain >= rule.min_day_pct_gain - eps
    threshold = rule.min_day_usd_gain if rule.min_day_usd_gain is not None else Config.WATCHLIST_MIN_USD_GAIN
    if day_usd_gain is not None:
        return day_usd_gain >= threshold - eps
    if usd_gain is not None and usd_gain >= threshold - eps:
        return True
    return False


def compute_entry_watchlist_gains(
    price_feed: PriceFeed, mint: str, current_usd: float
) -> Dict:
    """Fresh session + 24h gains for live entry gating."""
    session_baseline = price_feed.get_session_open(mint)
    pair = _best_pair_for_mint(mint)
    meta = _pair_metadata(pair, mint)
    rule = get_watchlist_rule(mint)
    return compute_watchlist_gains(
        current_usd,
        session_baseline,
        meta.get("price_change_h24_pct"),
        rule=rule,
    )


def compute_usd_gain_from_baseline(
    price_feed: PriceFeed, mint: str, current_usd: float
) -> Optional[float]:
    """Best available USD gain (session or DexScreener 24h) for entry checks."""
    if not is_pinned_watchlist_mint(mint):
        return None
    return compute_entry_watchlist_gains(price_feed, mint, current_usd).get("usd_gain")


def watchlist_usd_gain_qualifies(
    usd_gain: Optional[float],
    *,
    session_usd_gain: Optional[float] = None,
    day_usd_gain: Optional[float] = None,
) -> bool:
    """True when 24h/day USD gain meets the WBTC-style pinned-mint entry threshold."""
    rule = WatchlistMintRule(
        mint="",
        min_day_usd_gain=Config.WATCHLIST_MIN_USD_GAIN,
        use_standard_exits=True,
    )
    return watchlist_entry_qualifies(
        rule,
        day_usd_gain=day_usd_gain,
        usd_gain=usd_gain,
        session_usd_gain=session_usd_gain,
    )


def watchlist_candidate_qualifies(candidate: MoverCandidate) -> bool:
    if candidate.source != "watchlist_mint":
        return False
    rule = get_watchlist_rule(candidate.mint)
    return watchlist_entry_qualifies(
        rule,
        day_usd_gain=getattr(candidate, "day_usd_gain", None),
        day_pct_gain=getattr(candidate, "day_pct_gain", None),
        usd_gain=candidate.usd_gain_baseline,
        session_usd_gain=getattr(candidate, "session_usd_gain", None),
    )


def _status_entry_label(
    qualifies: bool, mint: str, held_mints: Optional[Set[str]]
) -> str:
    if held_mints and mint in held_mints:
        return "in_position"
    return "eligible" if qualifies else "standby"


def _rule_fields_for_status(rule: WatchlistMintRule) -> Dict:
    return {
        "label": rule.label,
        "min_day_usd_gain": rule.min_day_usd_gain,
        "min_day_pct_gain": rule.min_day_pct_gain,
        "sell_at_pct": rule.sell_at_pct,
        "one_buy_one_sell": rule.one_buy_one_sell,
        "override_ladder": rule.override_ladder,
        "use_standard_exits": rule.use_standard_exits,
        "entry_rule_summary": _entry_rule_summary(rule),
        "exit_rule_summary": _exit_rule_summary(rule),
    }


def _entry_rule_summary(rule: WatchlistMintRule) -> str:
    if rule.min_day_pct_gain is not None:
        return f"buy when 24h >= {rule.min_day_pct_gain * 100:.0f}%"
    if rule.min_day_usd_gain is not None:
        return f"buy when 24h day gain >= ${rule.min_day_usd_gain:.0f}"
    return "buy gate configured"


def _exit_rule_summary(rule: WatchlistMintRule) -> str:
    if rule.override_ladder and rule.sell_at_pct is not None:
        suffix = " (one buy, one sell)" if rule.one_buy_one_sell else ""
        return f"sell 100% at +{rule.sell_at_pct * 100:.0f}%{suffix}"
    return "standard ladder exits"


def probe_watchlist_mint_status(
    price_feed: PriceFeed,
    mint: str,
    rule: WatchlistMintRule,
    *,
    held_mints: Optional[Set[str]] = None,
) -> Dict:
    """Return status for one pinned mint (GUI/logging)."""
    prices = price_feed.update([mint])
    current = prices.get(mint)
    pair = _best_pair_for_mint(mint)
    meta = _pair_metadata(pair, mint)
    session_baseline = price_feed.get_session_open(mint) if current else None

    gain_info: Dict = {
        "session_usd_gain": None,
        "session_baseline_usd": session_baseline,
        "day_usd_gain": None,
        "day_pct_gain": None,
        "day_baseline_usd": None,
        "usd_gain": None,
        "baseline_usd": None,
        "gain_source": "none",
        "qualifies": False,
    }
    momentum_pct: Optional[float] = None

    if current and current > 0:
        gain_info = compute_watchlist_gains(
            current,
            session_baseline,
            meta.get("price_change_h24_pct"),
            rule=rule,
        )
        baseline = gain_info.get("baseline_usd")
        if baseline and baseline > 0 and gain_info.get("usd_gain") is not None:
            momentum_pct = gain_info["usd_gain"] / baseline
        elif gain_info.get("day_pct_gain") is not None:
            momentum_pct = gain_info["day_pct_gain"]

    entry_status = _status_entry_label(gain_info["qualifies"], mint, held_mints)

    return {
        "enabled": True,
        "mint": mint,
        "symbol": meta["symbol"],
        "price_usd": current,
        "baseline_usd": gain_info.get("baseline_usd"),
        "session_baseline_usd": gain_info.get("session_baseline_usd"),
        "day_baseline_usd": gain_info.get("day_baseline_usd"),
        "usd_gain": gain_info.get("usd_gain"),
        "session_usd_gain": gain_info.get("session_usd_gain"),
        "day_usd_gain": gain_info.get("day_usd_gain"),
        "day_pct_gain": gain_info.get("day_pct_gain"),
        "price_change_h24_pct": meta.get("price_change_h24_pct"),
        "gain_source": gain_info.get("gain_source"),
        "momentum_pct": momentum_pct,
        "watchlist_min_usd_gain": rule.min_day_usd_gain,
        "watchlist_min_day_pct_gain": rule.min_day_pct_gain,
        "qualifies": gain_info["qualifies"],
        "entry_status": entry_status,
        **_rule_fields_for_status(rule),
    }


def probe_all_watchlist_statuses(
    price_feed: PriceFeed,
    *,
    held_mints: Optional[Set[str]] = None,
) -> List[Dict]:
    """Probe every configured pinned mint."""
    if not Config.watchlist_mint_enabled():
        return []
    held = held_mints or set()
    mints = Config.watchlist_mints()
    if mints:
        price_feed.update(mints)
    return [
        probe_watchlist_mint_status(price_feed, rule.mint, rule, held_mints=held)
        for rule in Config.watchlist_rules()
        if rule.mint
    ]


def fetch_watchlist_mint_candidate(
    price_feed: PriceFeed,
    mint: Optional[str] = None,
    *,
    status: Optional[Dict] = None,
) -> Optional[MoverCandidate]:
    """Build one pinned-mint candidate when enabled and price is known."""
    if not Config.watchlist_mint_enabled():
        return None

    if status is None:
        rule = get_watchlist_rule(mint) if mint else None
        if rule is None and mint:
            return None
        if rule is None:
            rules = Config.watchlist_rules()
            if not rules:
                return None
            rule = rules[0]
            mint = rule.mint
        status = probe_watchlist_mint_status(price_feed, mint, rule)

    if not status or not status.get("enabled"):
        return None

    mint = status["mint"]
    current = status["price_usd"]
    if current is None or current <= 0:
        return None

    usd_gain = status.get("day_usd_gain")
    if usd_gain is None:
        usd_gain = status["usd_gain"]
    momentum_pct = price_feed.get_momentum(mint, current)
    if momentum_pct is None:
        momentum_pct = status.get("momentum_pct") or 0.0

    pair = _best_pair_for_mint(mint)
    meta = _pair_metadata(pair, mint)

    logger.info(
        "Watchlist mint %s: price=$%.6f gain=%s pct=%s source=%s status=%s",
        meta["symbol"],
        current,
        f"${usd_gain:.2f}" if usd_gain is not None else "?",
        (
            f"{status.get('day_pct_gain') * 100:.2f}%"
            if status.get("day_pct_gain") is not None
            else "?"
        ),
        status.get("gain_source"),
        status.get("entry_status"),
    )

    return MoverCandidate(
        mint=mint,
        symbol=meta["symbol"],
        name=meta["name"],
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
        source="watchlist_mint",
        usd_gain_baseline=usd_gain,
        session_usd_gain=status.get("session_usd_gain"),
        day_usd_gain=status.get("day_usd_gain"),
        day_pct_gain=status.get("day_pct_gain"),
    )


def fetch_all_watchlist_candidates(price_feed: PriceFeed) -> List[MoverCandidate]:
    """Build candidates for all pinned mints (always polled for UI)."""
    candidates: List[MoverCandidate] = []
    for status in probe_all_watchlist_statuses(price_feed):
        candidate = fetch_watchlist_mint_candidate(price_feed, status=status)
        if candidate:
            candidates.append(candidate)
    return candidates
