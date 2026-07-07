import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from config import Config
from dexscreener_client import get_dexscreener_client
from scanner_momentum import (
    discovery_momentum,
    price_changes_from_dexscreener,
)

logger = logging.getLogger(__name__)

DEXSCREENER_SEARCH_QUERIES = ("SOL/USDC", "USDC/SOL", "SOL", "trending")

@dataclass
class MoverCandidate:
    mint: str
    symbol: str
    name: str
    pair_address: str
    dex: str
    price_usd: float
    liquidity_usd: float
    volume_24h_usd: float
    momentum_pct: float
    price_change_5m: float = 0.0
    price_change_1h: float = 0.0
    price_change_6h: float = 0.0
    price_change_24h: float = 0.0
    pool_created_at: Optional[int] = None
    scanned_at: float = field(default_factory=time.time)
    source: str = "dexscreener"
    # Pinned watchlist mint: best USD gain (session or 24h) for display / entry gate.
    usd_gain_baseline: Optional[float] = None
    session_usd_gain: Optional[float] = None
    day_usd_gain: Optional[float] = None
    day_pct_gain: Optional[float] = None

    def scanner_discovery_momentum(self) -> float:
        """Max momentum across DexScreener windows: m5, h1, h6, h24."""
        return discovery_momentum(
            self.price_change_5m,
            self.price_change_1h,
            self.price_change_6h,
            self.price_change_24h,
        )

    def to_profile(self) -> Dict[str, float]:
        return {
            "momentum_pct": self.momentum_pct,
            "liquidity_usd": self.liquidity_usd,
            "volume_24h_usd": self.volume_24h_usd,
            "price_change_5m": self.price_change_5m,
            "price_change_1h": self.price_change_1h,
            "price_change_6h": self.price_change_6h,
            "price_change_24h": self.price_change_24h,
        }


def _pool_age_hours(created_at_ms: Optional[int]) -> Optional[float]:
    if not created_at_ms:
        return None
    return (time.time() * 1000 - created_at_ms) / (1000 * 3600)


def parse_pair(
    pair: dict,
    *,
    min_liquidity_usd: Optional[float] = None,
    source: str = "dexscreener",
) -> Optional[MoverCandidate]:
    if pair.get("chainId") != "solana":
        return None

    base = pair.get("baseToken") or {}
    mint = base.get("address")
    if not mint:
        return None

    liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
    volume_24h = float((pair.get("volume") or {}).get("h24") or 0)
    price_usd = float(pair.get("priceUsd") or 0)
    if price_usd <= 0:
        return None

    changes = price_changes_from_dexscreener(pair.get("priceChange"))
    momentum = changes.discovery_momentum()

    created_at = pair.get("pairCreatedAt")
    age_hours = _pool_age_hours(created_at)
    if age_hours is not None:
        if age_hours < Config.MIN_POOL_AGE_HOURS:
            return None
        if age_hours > Config.MAX_POOL_AGE_DAYS * 24:
            return None

    min_liq = min_liquidity_usd if min_liquidity_usd is not None else Config.effective_min_liquidity_usd()
    if liquidity < min_liq:
        return None
    if volume_24h < Config.effective_min_volume_for_mint(mint):
        return None
    if momentum < Config.effective_min_momentum_pct():
        return None

    return MoverCandidate(
        mint=mint,
        symbol=base.get("symbol") or "UNKNOWN",
        name=base.get("name") or base.get("symbol") or "UNKNOWN",
        pair_address=pair.get("pairAddress") or "",
        dex=pair.get("dexId") or "unknown",
        price_usd=price_usd,
        liquidity_usd=liquidity,
        volume_24h_usd=volume_24h,
        momentum_pct=momentum,
        price_change_5m=changes.change_5m,
        price_change_1h=changes.change_1h,
        price_change_6h=changes.change_6h,
        price_change_24h=changes.change_24h,
        pool_created_at=created_at,
        source=source,
    )


def merge_candidates(*lists: List[MoverCandidate]) -> List[MoverCandidate]:
    """Merge candidate lists, deduplicating by mint and keeping higher momentum."""
    from stock_token_filter import filter_stock_candidates

    gmgn_min_liq = Config.effective_gmgn_min_liquidity()
    merged: Dict[str, MoverCandidate] = {}
    for candidates in lists:
        for candidate in candidates:
            if candidate.source == "gmgn" and candidate.liquidity_usd < gmgn_min_liq:
                continue
            existing = merged.get(candidate.mint)
            if not existing or candidate.momentum_pct > existing.momentum_pct:
                merged[candidate.mint] = candidate
    ranked = sorted(merged.values(), key=lambda c: c.momentum_pct, reverse=True)
    return filter_stock_candidates(ranked)


class MoverScanner:
    """Fetch Solana movers from DexScreener and apply safety filters.

    Liquidity floor defaults to Config.MIN_LIQUIDITY_USD ($15k pool liquidity by default).
    Uses a shared rate-limited client and staggers deep token-pair scans across cycles.
    """

    def __init__(self):
        self._client = get_dexscreener_client()
        if not Config.DEXSCREENER_API_KEY:
            Config.log_missing_scanner_key_once(
                "dexscreener",
                "DEXSCREENER_API_KEY not set — using public DexScreener API "
                "(no official key required; optional for future premium access)",
            )

    def _get(self, path: str, timeout: int = 15) -> Optional[object]:
        return self._client.get(path, timeout=timeout)

    def _fetch_latest_boosted_tokens(self) -> List[str]:
        mints: List[str] = []
        data = self._get("/token-boosts/latest/v1")
        if not isinstance(data, list):
            return mints
        for item in data:
            if item.get("chainId") == "solana":
                addr = item.get("tokenAddress")
                if addr:
                    mints.append(addr)
        return mints

    def _fetch_boosted_tokens(self) -> List[str]:
        mints: List[str] = []
        data = self._get("/token-boosts/top/v1")
        if not isinstance(data, list):
            return mints
        for item in data:
            if item.get("chainId") == "solana":
                addr = item.get("tokenAddress")
                if addr:
                    mints.append(addr)
        return mints

    def _fetch_latest_profiles(self) -> List[str]:
        mints: List[str] = []
        data = self._get("/token-profiles/latest/v1")
        if not isinstance(data, list):
            return mints
        for item in data:
            if item.get("chainId") == "solana":
                addr = item.get("tokenAddress")
                if addr:
                    mints.append(addr)
        return mints

    def _fetch_pairs_for_mint(self, mint: str) -> List[dict]:
        return self._client.get_token_pairs(mint)

    def _fetch_search_pairs(self) -> List[dict]:
        pairs: List[dict] = []
        seen_pair_addrs: set[str] = set()
        for query in DEXSCREENER_SEARCH_QUERIES:
            data = self._get(f"/latest/dex/search?q={query}")
            if not isinstance(data, dict):
                continue
            for pair in data.get("pairs") or []:
                addr = pair.get("pairAddress")
                if addr and addr in seen_pair_addrs:
                    continue
                if addr:
                    seen_pair_addrs.add(addr)
                pairs.append(pair)
        return pairs

    def _collect_seed_mints(self, *, fast_mode: bool = False) -> List[str]:
        seed_mints: List[str] = []
        seen: set[str] = set()
        if fast_mode and Config.FIRST_SCAN_FAST_MODE:
            mint_sources = (
                self._fetch_boosted_tokens() + self._fetch_latest_profiles()
            )
        else:
            mint_sources = (
                self._fetch_boosted_tokens()
                + self._fetch_latest_boosted_tokens()
                + self._fetch_latest_profiles()
            )
        for mint in mint_sources:
            if mint not in seen:
                seen.add(mint)
                seed_mints.append(mint)
        return seed_mints

    def scan(self, *, fast_mode: bool = False) -> List[MoverCandidate]:
        seed_mints = self._collect_seed_mints(fast_mode=fast_mode)
        per_cycle = None
        if fast_mode and Config.FIRST_SCAN_FAST_MODE:
            per_cycle = Config.FIRST_SCAN_DEEP_MINTS
        mint_batch = self._client.get_seed_batch(seed_mints, per_cycle=per_cycle)

        seen_mints: set[str] = set()
        candidates: Dict[str, MoverCandidate] = {}

        for mint in mint_batch:
            if mint in seen_mints:
                continue
            seen_mints.add(mint)
            for pair in self._fetch_pairs_for_mint(mint):
                candidate = parse_pair(pair, source="dexscreener")
                if candidate:
                    existing = candidates.get(candidate.mint)
                    if not existing or candidate.momentum_pct > existing.momentum_pct:
                        candidates[candidate.mint] = candidate

        if not (fast_mode and Config.FIRST_SCAN_FAST_MODE):
            for pair in self._fetch_search_pairs():
                candidate = parse_pair(pair, source="dexscreener")
                if candidate:
                    existing = candidates.get(candidate.mint)
                    if not existing or candidate.momentum_pct > existing.momentum_pct:
                        candidates[candidate.mint] = candidate

        ranked = sorted(candidates.values(), key=lambda c: c.momentum_pct, reverse=True)
        health = self._client.get_health()
        if ranked:
            logger.info("Scanner found %d qualified movers", len(ranked))
        elif health.get("status") == "rate_limited":
            logger.warning(
                "Scanner found 0 movers — DexScreener rate limited (batch %d/%d seeds)",
                len(mint_batch),
                len(seed_mints[: self._client.effective_max_seeds()]),
            )
        else:
            logger.info("Scanner found %d qualified movers", len(ranked))
        return ranked


def scan_unified(
    include_pumpfun: Optional[bool] = None,
    include_birdeye: Optional[bool] = None,
    include_gmgn: Optional[bool] = None,
    *,
    first_scan: bool = False,
    on_partial: Optional[
        Callable[[List[MoverCandidate], int, int, int, int], None]
    ] = None,
) -> tuple[List[MoverCandidate], int, int, int, int]:
    """Scan DexScreener, pump.fun, Birdeye, and GMGN; return merged list + per-source counts."""
    from birdeye_scanner import BirdeyeScanner
    from gmgn_scanner import GmgnScanner
    from pumpfun_scanner import PumpFunScanner

    include_pf = Config.scan_pumpfun_enabled() if include_pumpfun is None else include_pumpfun
    include_be = Config.scan_birdeye_enabled() if include_birdeye is None else include_birdeye
    include_gn = Config.scan_gmgn_enabled() if include_gmgn is None else include_gmgn
    fast_mode = first_scan and Config.FIRST_SCAN_FAST_MODE

    client = get_dexscreener_client()
    client.begin_scan_cycle()

    logger.info(
        "Unified scan cycle: dexscreener=on pumpfun=%s birdeye=%s gmgn=%s fast=%s",
        "on" if include_pf else "off",
        "on" if include_be else "off",
        "on" if include_gn else "off",
        "on" if fast_mode else "off",
    )

    def _emit_partial(
        dex_movers: List[MoverCandidate],
        pumpfun_movers: List[MoverCandidate],
        birdeye_movers: List[MoverCandidate],
        gmgn_movers: List[MoverCandidate],
    ) -> None:
        if on_partial is None:
            return
        merged = merge_candidates(dex_movers, pumpfun_movers, birdeye_movers, gmgn_movers)
        on_partial(
            merged,
            len(dex_movers),
            len(pumpfun_movers),
            len(birdeye_movers),
            len(gmgn_movers),
        )

    dex_movers = MoverScanner().scan(fast_mode=fast_mode)
    dex_count = len(dex_movers)
    _emit_partial(dex_movers, [], [], [])

    pumpfun_movers: List[MoverCandidate] = []
    if include_pf:
        pumpfun_movers = PumpFunScanner().scan(fast_mode=fast_mode)
        logger.info("Pump.fun scan complete: %d movers", len(pumpfun_movers))
        _emit_partial(dex_movers, pumpfun_movers, [], [])
    else:
        logger.debug("Pump.fun scanning disabled (SCAN_PUMPFUN / INCLUDE_PUMPFUN=false)")

    birdeye_movers: List[MoverCandidate] = []
    if include_be:
        birdeye_movers = BirdeyeScanner().scan(fast_mode=fast_mode)
        logger.info("Birdeye scan complete: %d movers", len(birdeye_movers))
        _emit_partial(dex_movers, pumpfun_movers, birdeye_movers, [])
    else:
        logger.debug("Birdeye scanning disabled (SCAN_BIRDEYE=false)")

    gmgn_movers: List[MoverCandidate] = []
    if include_gn:
        gmgn_movers = GmgnScanner().scan(fast_mode=fast_mode)
        logger.info("GMGN scan complete: %d movers", len(gmgn_movers))
        _emit_partial(dex_movers, pumpfun_movers, birdeye_movers, gmgn_movers)
    else:
        logger.debug("GMGN scanning disabled (SCAN_GMGN / GMGN_ENABLED=false)")

    pumpfun_count = len(pumpfun_movers)
    birdeye_count = len(birdeye_movers)
    gmgn_count = len(gmgn_movers)
    merged = merge_candidates(dex_movers, pumpfun_movers, birdeye_movers, gmgn_movers)
    logger.info(
        "Unified scan merged %d movers (dex=%d pumpfun=%d birdeye=%d gmgn=%d)",
        len(merged),
        dex_count,
        pumpfun_count,
        birdeye_count,
        gmgn_count,
    )
    return merged, dex_count, pumpfun_count, birdeye_count, gmgn_count
