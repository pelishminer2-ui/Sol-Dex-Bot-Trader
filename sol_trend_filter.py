"""SOL macro trend gate — block memecoin entries when SOL is dumping."""

from __future__ import annotations

import logging
import time
from typing import Optional

from config import Config, SOL_MINT

logger = logging.getLogger(__name__)

USDC_SYMBOLS = frozenset({"USDC", "USDT"})

# Rolling SOL/USD samples for 30m change (DexScreener has m5/h1/h6/h24, not m30).
_PRICE_HISTORY_MAX_SEC = 4 * 3600
_PRICE_HISTORY_TOLERANCE_SEC = 5 * 60
_price_history: list[tuple[float, float]] = []  # (unix_ts, price_usd)

_snapshot: dict = {}
_snapshot_at: float = 0.0
_session_baseline_usd: Optional[float] = None


def reset_session_baseline() -> None:
    """Clear session SOL price baseline (call on bot start)."""
    global _session_baseline_usd
    _session_baseline_usd = None


def reset_sol_trend_state_for_tests() -> None:
    """Reset cached snapshot and session baseline (validation scripts)."""
    global _snapshot, _snapshot_at, _session_baseline_usd, _price_history
    _snapshot = {}
    _snapshot_at = 0.0
    _session_baseline_usd = None
    _price_history = []


def _record_sol_price(price_usd: float, *, now: Optional[float] = None) -> None:
    """Append a SOL/USD sample for rolling 30m change."""
    global _price_history
    if price_usd <= 0:
        return
    ts = float(now if now is not None else time.time())
    if _price_history and abs(_price_history[-1][0] - ts) < 1.0:
        _price_history[-1] = (ts, price_usd)
    else:
        _price_history.append((ts, price_usd))
    cutoff = ts - _PRICE_HISTORY_MAX_SEC
    while _price_history and _price_history[0][0] < cutoff:
        _price_history.pop(0)


def _rolling_change_pct(
    price_usd: float,
    lookback_sec: float,
    *,
    now: Optional[float] = None,
) -> Optional[float]:
    """Percent change vs the sample nearest to ``now - lookback_sec``."""
    if price_usd <= 0 or not _price_history:
        return None
    ts_now = float(now if now is not None else time.time())
    target = ts_now - lookback_sec
    older: Optional[tuple[float, float]] = None
    for sample_ts, sample_px in _price_history:
        if sample_ts <= target:
            older = (sample_ts, sample_px)
        else:
            break
    if older is None:
        return None
    # Need a sample reasonably close to the lookback point (not brand-new).
    if older[0] > target + _PRICE_HISTORY_TOLERANCE_SEC:
        return None
    base = float(older[1])
    if base <= 0:
        return None
    return ((price_usd - base) / base) * 100.0


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
    m30_pct: Optional[float] = None,
) -> bool:
    """True when SOL macro trend allows memecoin entries."""
    if not Config.SOL_TREND_FILTER_ENABLED:
        return True
    if h1_pct is None and h4_pct is None and m30_pct is None:
        return True
    min_30m = float(getattr(Config, "SOL_MIN_CHANGE_30M_PCT", -1.5))
    if m30_pct is not None and m30_pct < min_30m:
        return False
    if h1_pct is not None and h1_pct < Config.SOL_MIN_CHANGE_1H_PCT:
        return False
    if h4_pct is not None and h4_pct < Config.SOL_MIN_CHANGE_4H_PCT:
        return False
    return True


def fetch_sol_trend_from_dexscreener() -> dict:
    """Fetch SOL 30m/1h/4h change from DexScreener SOL/USDC pair (+ rolling 30m)."""
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
    now = time.time()
    _record_sol_price(price_usd, now=now)

    # Dex may expose m30 in future; until then use rolling samples.
    m30_pct = _parse_dex_pct(
        price_change.get("m30")
        if price_change.get("m30") is not None
        else price_change.get("30m")
    )
    m30_source = "dexscreener" if m30_pct is not None else None
    if m30_pct is None:
        m30_pct = _rolling_change_pct(price_usd, 30 * 60, now=now)
        if m30_pct is not None:
            m30_source = "rolling"

    h1_pct = _parse_dex_pct(price_change.get("h1"))
    h4_pct = _parse_dex_pct(price_change.get("h4"))
    h6_pct = _parse_dex_pct(price_change.get("h6"))
    h24_pct = _parse_dex_pct(price_change.get("h24"))
    # If Dex has no h4, approximate from h6 (conservative: scale toward 4h window).
    if h4_pct is None and h6_pct is not None:
        h4_pct = h6_pct * (4.0 / 6.0)

    global _session_baseline_usd
    if _session_baseline_usd is None and price_usd > 0:
        _session_baseline_usd = price_usd

    session_pct: Optional[float] = None
    if _session_baseline_usd and price_usd > 0:
        session_pct = (
            (price_usd - _session_baseline_usd) / _session_baseline_usd
        ) * 100.0

    ok = sol_trend_passes(h1_pct, h4_pct, m30_pct)
    return {
        "data_available": True,
        "source": "dexscreener",
        "sol_price_usd": price_usd,
        "sol_trend_30m_pct": m30_pct,
        "sol_trend_30m_source": m30_source,
        "sol_trend_1h_pct": h1_pct,
        "sol_trend_4h_pct": h4_pct,
        "sol_trend_6h_pct": h6_pct,
        "sol_trend_24h_pct": h24_pct,
        "sol_trend_session_pct": session_pct,
        "sol_trend_ok": ok,
        "sol_trend_filter_enabled": Config.SOL_TREND_FILTER_ENABLED,
        "sol_min_change_30m_pct": float(
            getattr(Config, "SOL_MIN_CHANGE_30M_PCT", -1.5)
        ),
        "sol_min_change_1h_pct": Config.SOL_MIN_CHANGE_1H_PCT,
        "sol_min_change_4h_pct": Config.SOL_MIN_CHANGE_4H_PCT,
        "pair_address": pair.get("pairAddress"),
        "dex": pair.get("dexId"),
        "updated_at": now,
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
            stale.get("sol_trend_30m_pct"),
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

    Pop-quality override: when the 30m/1h macro gate would block but a ``candidate``
    is supplied that passes the quality bar (``entry_filters.sol_trend_quality_override
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

    m30 = snap.get("sol_trend_30m_pct")
    h1 = snap.get("sol_trend_1h_pct")
    h4 = snap.get("sol_trend_4h_pct")
    if m30 is None and h1 is None and h4 is None:
        return True, None

    min_30m = float(getattr(Config, "SOL_MIN_CHANGE_30M_PCT", -1.5))

    # 4h sustained downtrend is a hard block — the quality override cannot bypass it.
    if h4 is not None and h4 < Config.SOL_MIN_CHANGE_4H_PCT:
        return (
            False,
            f"SOL macro gate: 4h {h4:+.2f}% < {Config.SOL_MIN_CHANGE_4H_PCT:+.2f}%",
        )

    soft_reasons: list[str] = []
    if m30 is not None and m30 < min_30m:
        soft_reasons.append(f"30m {m30:+.2f}% < {min_30m:+.2f}%")
    if h1 is not None and h1 < Config.SOL_MIN_CHANGE_1H_PCT:
        soft_reasons.append(
            f"1h {h1:+.2f}% < {Config.SOL_MIN_CHANGE_1H_PCT:+.2f}%"
        )

    if soft_reasons:
        if candidate is not None:
            from entry_filters import sol_trend_quality_override_passes

            if sol_trend_quality_override_passes(candidate, sell_preview_impact_pct):
                logger.info(
                    "SOL macro gate %s overridden by quality pop: %s",
                    " / ".join(soft_reasons),
                    getattr(candidate, "symbol", "?"),
                )
                return True, None
        return False, f"SOL macro gate: {' · '.join(soft_reasons)}"
    return True, None
