"""SOL macro trend gate — block memecoin entries when SOL is dumping."""

from __future__ import annotations

import logging
import time
from typing import Optional

from config import Config, SOL_MINT

logger = logging.getLogger(__name__)

USDC_SYMBOLS = frozenset({"USDC", "USDT"})

_snapshot: dict = {}
_snapshot_at: float = 0.0
_session_baseline_usd: Optional[float] = None


def reset_session_baseline() -> None:
    """Clear session SOL price baseline (call on bot start)."""
    global _session_baseline_usd
    _session_baseline_usd = None


def reset_sol_trend_state_for_tests() -> None:
    """Reset cached snapshot and session baseline (validation scripts)."""
    global _snapshot, _snapshot_at, _session_baseline_usd
    _snapshot = {}
    _snapshot_at = 0.0
    _session_baseline_usd = None


def _pick_sol_stable_pair(pairs: list) -> Optional[dict]:
    """Best-liquidity SOL/USDC (or USDT) pair on Solana."""
    best: Optional[dict] = None
    best_liq = 0.0
    for pair in pairs:
        if pair.get("chainId") != "solana":
            continue
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        base_sym = (base.get("symbol") or "").upper()
        quote_sym = (quote.get("symbol") or "").upper()
        base_addr = base.get("address") or ""
        quote_addr = quote.get("address") or ""
        has_sol = base_addr == SOL_MINT or quote_addr == SOL_MINT
        has_stable = base_sym in USDC_SYMBOLS or quote_sym in USDC_SYMBOLS
        if not has_sol or not has_stable:
            continue
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        if liq > best_liq:
            best_liq = liq
            best = pair
    return best


def _parse_dex_pct(raw) -> Optional[float]:
    """DexScreener priceChange fields are already in percent (e.g. -0.5 = -0.5%)."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def sol_trend_passes(
    h1_pct: Optional[float],
    h4_pct: Optional[float],
) -> bool:
    """True when SOL macro trend allows memecoin entries."""
    if not Config.SOL_TREND_FILTER_ENABLED:
        return True
    if h1_pct is None and h4_pct is None:
        return True
    if h1_pct is not None and h1_pct < Config.SOL_MIN_CHANGE_1H_PCT:
        return False
    if h4_pct is not None and h4_pct < Config.SOL_MIN_CHANGE_4H_PCT:
        return False
    return True


def fetch_sol_trend_from_dexscreener() -> dict:
    """Fetch SOL 1h/4h change from DexScreener SOL/USDC pair."""
    from dexscreener_client import get_dexscreener_client

    client = get_dexscreener_client()
    pairs = client.get_token_pairs(SOL_MINT)
    pair = _pick_sol_stable_pair(pairs)
    if not pair:
        data = client.get("/latest/dex/search?q=SOL/USDC")
        if isinstance(data, dict):
            pair = _pick_sol_stable_pair(data.get("pairs") or [])

    if not pair:
        return {
            "data_available": False,
            "source": "dexscreener",
            "error": "no_sol_usdc_pair",
            "sol_trend_ok": True,
        }

    price_change = pair.get("priceChange") or {}
    price_usd = float(pair.get("priceUsd") or 0)
    h1_pct = _parse_dex_pct(price_change.get("h1"))
    h4_pct = _parse_dex_pct(price_change.get("h4"))
    h6_pct = _parse_dex_pct(price_change.get("h6"))
    h24_pct = _parse_dex_pct(price_change.get("h24"))

    global _session_baseline_usd
    if _session_baseline_usd is None and price_usd > 0:
        _session_baseline_usd = price_usd

    session_pct: Optional[float] = None
    if _session_baseline_usd and price_usd > 0:
        session_pct = (
            (price_usd - _session_baseline_usd) / _session_baseline_usd
        ) * 100.0

    ok = sol_trend_passes(h1_pct, h4_pct)
    return {
        "data_available": True,
        "source": "dexscreener",
        "sol_price_usd": price_usd,
        "sol_trend_1h_pct": h1_pct,
        "sol_trend_4h_pct": h4_pct,
        "sol_trend_6h_pct": h6_pct,
        "sol_trend_24h_pct": h24_pct,
        "sol_trend_session_pct": session_pct,
        "sol_trend_ok": ok,
        "sol_trend_filter_enabled": Config.SOL_TREND_FILTER_ENABLED,
        "sol_min_change_1h_pct": Config.SOL_MIN_CHANGE_1H_PCT,
        "sol_min_change_4h_pct": Config.SOL_MIN_CHANGE_4H_PCT,
        "pair_address": pair.get("pairAddress"),
        "dex": pair.get("dexId"),
        "updated_at": time.time(),
    }


def get_sol_trend_snapshot(*, force_refresh: bool = False) -> dict:
    """Cached SOL trend snapshot for status API and entry gates."""
    global _snapshot, _snapshot_at
    now = time.time()
    ttl = Config.SOL_TREND_CACHE_TTL_SEC
    if not force_refresh and _snapshot and now - _snapshot_at < ttl:
        return dict(_snapshot)

    fresh = fetch_sol_trend_from_dexscreener()
    if fresh.get("data_available"):
        _snapshot = fresh
        _snapshot_at = now
        return dict(fresh)

    if _snapshot:
        stale = dict(_snapshot)
        stale["stale"] = True
        stale["sol_trend_ok"] = sol_trend_passes(
            stale.get("sol_trend_1h_pct"),
            stale.get("sol_trend_4h_pct"),
        )
        return stale

    _snapshot = fresh
    _snapshot_at = now
    return dict(fresh)


def memecoin_entry_allowed_by_sol_trend(
    snapshot: Optional[dict] = None,
    *,
    candidate=None,
    sell_preview_impact_pct: Optional[float] = None,
) -> tuple[bool, Optional[str]]:
    """
    Return (allowed, skip_reason) for non-watchlist memecoin entries.
    WBTC / pinned watchlist mints are exempt — call only for memecoins.

    Pop-quality override: when the 1h macro gate would block but a ``candidate`` is
    supplied that passes the quality bar (``entry_filters.sol_trend_quality_override
    _passes`` — Pump.fun route, liquid, fresh, exit-able, leans runner), the entry
    is allowed. The 4h sustained-downtrend block is a HARD block and can never be
    bypassed by the override. This only loosens entry selection; it never touches
    stop-loss, profit exits, the 15-minute hold, forced exits, or learning.
    """
    if not Config.SOL_TREND_FILTER_ENABLED:
        return True, None

    snap = snapshot or get_sol_trend_snapshot()
    if not snap.get("data_available"):
        return True, None

    h1 = snap.get("sol_trend_1h_pct")
    h4 = snap.get("sol_trend_4h_pct")
    if h1 is None and h4 is None:
        return True, None

    # 4h sustained downtrend is a hard block — the quality override cannot bypass it.
    if h4 is not None and h4 < Config.SOL_MIN_CHANGE_4H_PCT:
        return (
            False,
            f"SOL macro gate: 4h {h4:+.2f}% < {Config.SOL_MIN_CHANGE_4H_PCT:+.2f}%",
        )

    if h1 is not None and h1 < Config.SOL_MIN_CHANGE_1H_PCT:
        if candidate is not None:
            from entry_filters import sol_trend_quality_override_passes

            if sol_trend_quality_override_passes(candidate, sell_preview_impact_pct):
                logger.info(
                    "SOL macro gate 1h %+.2f%% < %+.2f%% overridden by quality pop: %s",
                    h1,
                    Config.SOL_MIN_CHANGE_1H_PCT,
                    getattr(candidate, "symbol", "?"),
                )
                return True, None
        return (
            False,
            f"SOL macro gate: 1h {h1:+.2f}% < {Config.SOL_MIN_CHANGE_1H_PCT:+.2f}%",
        )
    return True, None
