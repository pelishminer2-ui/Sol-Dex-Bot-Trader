"""Scan Birdeye Find Gems (1h gainers), new listings, and trending via the Birdeye public API."""
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

from config import Config
from dexscreener_client import get_dexscreener_client
from scanner import MoverCandidate, parse_pair
from scanner_momentum import price_changes_from_external

logger = logging.getLogger(__name__)

BIRDEYE_BASE = Config.BIRDEYE_API_BASE
NEW_LISTING_PATH = Config.BIRDEYE_NEW_LISTING_PATH
TRENDING_PATH = Config.BIRDEYE_TRENDING_PATH
FIND_GEMS_PATH = Config.BIRDEYE_FIND_GEMS_PATH
MEME_LIST_PATH = Config.BIRDEYE_MEME_LIST_PATH
OVERVIEW_PATH = Config.BIRDEYE_OVERVIEW_PATH
NEW_LISTING_API_MAX_LIMIT = 20
FIND_GEMS_API_MAX_LIMIT = 100

# Find Gems ?type=trending — dedicated always-attempt entry path (win-lean exempt).
BIRDEYE_TRENDING_TOP5_SOURCE = "birdeye_trending_top5"

DEXSCREENER_FALLBACK_PATHS = (
    "/token-boosts/top/v1",
    "/token-boosts/latest/v1",
    "/token-profiles/latest/v1",
)
DEXSCREENER_FALLBACK_SEARCH = ("trending", "SOL/USDC")

_last_birdeye_scan_status: str = "idle"
_birdeye_auth_warned = False
_last_birdeye_auth_status: str = "idle"  # ok | 401 | missing_key | idle
_last_trending_top5_status: Dict = {
    "enabled": False,
    "auth": "idle",
    "source": None,
    "symbols": [],
    "mints": [],
    "ranks": [],
    "last_fetch_ts": None,
    "last_attempt_reasons": [],
    "last_error": None,
}


def get_last_birdeye_scan_status() -> str:
    """Return the most recent Birdeye scan mode: active, fallback, failed, or skipped."""
    return _last_birdeye_scan_status


def get_last_birdeye_auth_status() -> str:
    """Return last Birdeye auth probe: ok, 401, missing_key, or idle."""
    return _last_birdeye_auth_status


def get_trending_top5_status() -> dict:
    """Lightweight in-memory status for Find Gems trending top-5 attempts."""
    return dict(_last_trending_top5_status)


def update_trending_top5_attempt_reasons(reasons: List[str]) -> None:
    global _last_trending_top5_status
    _last_trending_top5_status = {
        **_last_trending_top5_status,
        "last_attempt_reasons": list(reasons or [])[:20],
    }


def _set_birdeye_scan_status(status: str) -> None:
    global _last_birdeye_scan_status
    _last_birdeye_scan_status = status


def _set_birdeye_auth_status(status: str) -> None:
    global _last_birdeye_auth_status
    _last_birdeye_auth_status = status


def _set_trending_top5_status(**kwargs) -> None:
    global _last_trending_top5_status
    _last_trending_top5_status = {**_last_trending_top5_status, **kwargs}


def parse_birdeye_pair(pair: dict) -> Optional[MoverCandidate]:
    """Parse a DexScreener pair as a Birdeye candidate with Birdeye-specific filters."""
    candidate = parse_pair(
        pair,
        min_liquidity_usd=Config.effective_birdeye_min_liquidity(),
        source="birdeye",
    )
    if not candidate:
        return None

    min_vol = Config.effective_birdeye_min_volume()
    min_momentum = Config.effective_birdeye_min_momentum()
    if candidate.volume_24h_usd < min_vol:
        return None
    if candidate.momentum_pct < min_momentum:
        return None
    return candidate


def _parse_liquidity_added_at(value: Optional[str]) -> Optional[int]:
    """Parse Birdeye liquidityAddedAt (ISO) to unix seconds for new_listing pagination."""
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        elif "T" in text and "+" not in text:
            text = f"{text}+00:00"
        return int(datetime.fromisoformat(text).timestamp())
    except (TypeError, ValueError):
        return None


def parse_birdeye_token(
    token: dict,
    overview: Optional[dict] = None,
) -> Optional[MoverCandidate]:
    """Build a candidate from Birdeye list data, optionally enriched with token overview."""
    mint = token.get("address")
    if not mint:
        return None

    data = overview or {}
    liquidity = float(token.get("liquidity") or data.get("liquidity") or 0)
    volume_24h = float(
        token.get("volume24hUSD")
        or token.get("volume_24h_usd")
        or data.get("v24hUSD")
        or data.get("volume24h")
        or 0
    )
    price_usd = float(token.get("price") or data.get("price") or 0)

    merged = {**data, **token}
    changes = price_changes_from_external(merged)
    momentum = changes.discovery_momentum()

    min_liq = Config.effective_birdeye_min_liquidity()
    min_vol = Config.effective_birdeye_min_volume()
    min_momentum = Config.effective_birdeye_min_momentum()

    if liquidity < min_liq:
        return None
    if volume_24h < min_vol:
        return None
    if price_usd <= 0:
        return None
    if momentum < min_momentum:
        return None

    symbol = token.get("symbol") or data.get("symbol") or "UNKNOWN"
    name = token.get("name") or data.get("name") or symbol
    pool_created_at = _parse_liquidity_added_at(token.get("liquidityAddedAt"))

    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=name,
        pair_address="",
        dex=token.get("source") or "birdeye",
        price_usd=price_usd,
        liquidity_usd=liquidity,
        volume_24h_usd=volume_24h,
        momentum_pct=momentum,
        price_change_5m=changes.change_5m,
        price_change_1h=changes.change_1h,
        price_change_6h=changes.change_6h,
        price_change_24h=changes.change_24h,
        pool_created_at=pool_created_at,
        source="birdeye",
    )


def parse_birdeye_trending_top5_token(
    token: dict,
    overview: Optional[dict] = None,
    *,
    source: str = BIRDEYE_TRENDING_TOP5_SOURCE,
) -> Optional[MoverCandidate]:
    """Parse a Find Gems trending token for the always-attempt top-5 path.

    Liquidity floor only (Balanced/current Birdeye min). Does NOT drop on
    volume/momentum floors — those would silently prevent a real attempt.
    Win-lean is bypassed later via ``source == birdeye_trending_top5``.
    """
    mint = token.get("address")
    if not mint:
        return None

    data = overview or {}
    liquidity = float(token.get("liquidity") or data.get("liquidity") or 0)
    volume_24h = float(
        token.get("volume24hUSD")
        or token.get("volume_24h_usd")
        or data.get("v24hUSD")
        or data.get("volume24h")
        or 0
    )
    price_usd = float(token.get("price") or data.get("price") or 0)
    min_liq = Config.effective_birdeye_min_liquidity()
    if liquidity < min_liq:
        logger.info(
            "top5 trending skip parse (liquidity): %s liq=$%.0f < $%.0f",
            token.get("symbol") or mint[:8],
            liquidity,
            min_liq,
        )
        return None
    if price_usd <= 0:
        # Allow through with a tiny placeholder; price feed / Jupiter still gate.
        price_usd = float(data.get("price") or 0) or 1e-12

    merged = {**data, **token}
    # Normalize common trending field aliases into price_changes_from_external shape.
    if "price24hChangePercent" in merged and "priceChange24hPercent" not in merged:
        merged["priceChange24hPercent"] = merged["price24hChangePercent"]
    changes = price_changes_from_external(merged)
    momentum = changes.discovery_momentum()
    symbol = token.get("symbol") or data.get("symbol") or "UNKNOWN"
    name = token.get("name") or data.get("name") or symbol

    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=name,
        pair_address="",
        dex=token.get("source") or "birdeye",
        price_usd=price_usd,
        liquidity_usd=liquidity,
        volume_24h_usd=volume_24h,
        momentum_pct=momentum,
        price_change_5m=changes.change_5m,
        price_change_1h=changes.change_1h,
        price_change_6h=changes.change_6h,
        price_change_24h=changes.change_24h,
        pool_created_at=_parse_liquidity_added_at(token.get("liquidityAddedAt")),
        source=source,
    )


class BirdeyeScanner:
    """Fetch Find Gems 1h gainers, newly listed, and trending Solana tokens from Birdeye."""

    def __init__(self):
        self.base_url = BIRDEYE_BASE
        self.session = requests.Session()
        headers = Config.birdeye_headers()
        if headers:
            self.session.headers.update(headers)
        self.dex_base = Config.DEXSCREENER_BASE
        self._dex_client = get_dexscreener_client()
        if not Config.birdeye_api_available():
            _set_birdeye_auth_status("missing_key")
            Config.log_missing_scanner_key_once(
                "birdeye",
                "BIRDEYE_API_KEY not set — Birdeye Find Gems (1h gainers) skipped; using "
                "DexScreener trending fallback. Get a free key at https://birdeye.so — "
                "find-gems page: https://birdeye.so/solana/find-gems",
            )

    def _log_auth_failure(self, status_code: int) -> None:
        global _birdeye_auth_warned
        _set_birdeye_auth_status(str(status_code))
        message = (
            f"Birdeye API auth failed ({status_code}) — set BIRDEYE_API_KEY from "
            "https://birdeye.so (trending top5 attempts blocked until key works)"
        )
        if not _birdeye_auth_warned:
            logger.warning(message)
            _birdeye_auth_warned = True
        else:
            logger.debug(message)

    def _get_birdeye(self, path: str, params: Optional[dict] = None, timeout: int = 15) -> Optional[object]:
        if not Config.birdeye_api_available():
            _set_birdeye_auth_status("missing_key")
            return None
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=timeout,
            )
            if response.status_code in (401, 403):
                self._log_auth_failure(response.status_code)
                return None
            if response.status_code == 429:
                logger.warning("Birdeye API rate limited; skipping birdeye scan this cycle")
                return None
            response.raise_for_status()
            _set_birdeye_auth_status("ok")
            return response.json()
        except requests.RequestException as exc:
            logger.warning("Birdeye request failed for %s: %s", path, exc)
            return None

    def _get_dexscreener(self, path: str, timeout: int = 15) -> Optional[object]:
        return self._dex_client.get(path, timeout=timeout)

    def _extract_tokens(self, data: Optional[object]) -> List[dict]:
        if not isinstance(data, dict) or not data.get("success"):
            return []
        payload = data.get("data") or {}
        if isinstance(payload, list):
            return [t for t in payload if isinstance(t, dict)]
        tokens = payload.get("tokens") or payload.get("items") or []
        return [t for t in tokens if isinstance(t, dict)]

    def _collect_dexscreener_seed_mints(self) -> List[str]:
        mints: List[str] = []
        seen: set[str] = set()

        def _add_from_list(items: Optional[object]) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict) or item.get("chainId") != "solana":
                    continue
                addr = item.get("tokenAddress")
                if addr and addr not in seen:
                    seen.add(addr)
                    mints.append(addr)

        for path in DEXSCREENER_FALLBACK_PATHS:
            _add_from_list(self._get_dexscreener(path))

        for query in DEXSCREENER_FALLBACK_SEARCH:
            data = self._get_dexscreener(f"/latest/dex/search?q={query}")
            if not isinstance(data, dict):
                continue
            for pair in data.get("pairs") or []:
                if not isinstance(pair, dict) or pair.get("chainId") != "solana":
                    continue
                base = pair.get("baseToken") or {}
                addr = base.get("address")
                if addr and addr not in seen:
                    seen.add(addr)
                    mints.append(addr)

        return mints

    def _scan_dexscreener_fallback(self, *, fast_mode: bool = False) -> List[MoverCandidate]:
        """Trending-style candidates from public DexScreener when Birdeye API is unavailable."""
        _set_birdeye_scan_status("fallback")
        limit = Config.BIRDEYE_TRENDING_LIMIT
        seed_mints = self._collect_dexscreener_seed_mints()
        if not seed_mints:
            logger.info("Scanner found 0 birdeye gems (DexScreener fallback, no seeds)")
            return []

        per_cycle = None
        if fast_mode and Config.FIRST_SCAN_FAST_MODE:
            per_cycle = Config.FIRST_SCAN_DEEP_MINTS

        candidates: Dict[str, MoverCandidate] = {}
        mint_batch = self._dex_client.get_seed_batch(seed_mints, per_cycle=per_cycle)
        for mint in mint_batch:
            for pair in self._fetch_pairs_for_mint(mint):
                self._store(candidates, parse_birdeye_pair(pair))

        ranked = sorted(candidates.values(), key=lambda c: c.momentum_pct, reverse=True)[:limit]
        logger.info("Scanner found %d birdeye gems (DexScreener fallback)", len(ranked))
        return ranked

    def _fetch_new_listing_tokens(self, limit: int) -> List[dict]:
        """Primary discovery via GET /defi/v2/tokens/new_listing (max 20 per request)."""
        tokens: List[dict] = []
        seen: set[str] = set()
        time_to: Optional[int] = None

        while len(tokens) < limit:
            batch_limit = min(NEW_LISTING_API_MAX_LIMIT, limit - len(tokens))
            params: dict = {
                "limit": batch_limit,
                "meme_platform_enabled": True,
            }
            if time_to is not None:
                params["time_to"] = time_to

            batch = self._extract_tokens(self._get_birdeye(NEW_LISTING_PATH, params=params))
            if not batch:
                break

            for token in batch:
                mint = token.get("address")
                if mint and mint not in seen:
                    seen.add(mint)
                    tokens.append(token)

            if len(batch) < batch_limit:
                break

            oldest_ts = _parse_liquidity_added_at(batch[-1].get("liquidityAddedAt"))
            if oldest_ts is None or oldest_ts <= 1:
                break
            time_to = oldest_ts - 1

        return tokens

    def _fetch_find_gems_gainers(self, limit: int) -> List[dict]:
        """Primary Find Gems source: meme tokens sorted by % change for the configured timeframe."""
        if not Config.birdeye_find_gems_enabled():
            return []
        batch_limit = min(limit, FIND_GEMS_API_MAX_LIMIT)
        meme_list = self._get_birdeye(
            FIND_GEMS_PATH,
            params={
                "sort_by": Config.birdeye_gainer_sort_by(),
                "sort_type": "desc",
                "offset": 0,
                "limit": batch_limit,
            },
        )
        tokens = self._extract_tokens(meme_list)
        if tokens:
            logger.debug(
                "Birdeye Find Gems (%s gainers): %d tokens from %s",
                Config.BIRDEYE_GAINER_TIMEFRAME,
                len(tokens),
                FIND_GEMS_PATH,
            )
        return tokens

    def _fetch_trending_tokens(self) -> List[dict]:
        limit = Config.BIRDEYE_TRENDING_LIMIT
        tokens: List[dict] = []
        seen: set[str] = set()

        def _add(batch: List[dict]) -> None:
            for token in batch:
                mint = token.get("address")
                if mint and mint not in seen:
                    seen.add(mint)
                    tokens.append(token)

        _add(self._fetch_find_gems_gainers(limit))

        remaining = limit - len(tokens)
        if remaining > 0:
            _add(self._fetch_new_listing_tokens(remaining))

        if len(tokens) < limit // 2:
            trending = self._get_birdeye(
                TRENDING_PATH,
                params={
                    "sort_by": "rank",
                    "sort_type": "asc",
                    "offset": 0,
                    "limit": limit,
                },
            )
            _add(self._extract_tokens(trending))

        if len(tokens) < limit // 2:
            meme_list = self._get_birdeye(
                MEME_LIST_PATH,
                params={
                    "sort_by": "volume_24h_usd",
                    "sort_type": "desc",
                    "offset": 0,
                    "limit": limit,
                },
            )
            _add(self._extract_tokens(meme_list))

        return tokens[:limit]

    def _fetch_token_overview(self, mint: str) -> Optional[dict]:
        if not Config.birdeye_api_available():
            return None
        data = self._get_birdeye(OVERVIEW_PATH, params={"address": mint})
        if not isinstance(data, dict) or not data.get("success"):
            return None
        overview = data.get("data")
        return overview if isinstance(overview, dict) else None

    def _fetch_pairs_for_mint(self, mint: str) -> List[dict]:
        data = self._get_dexscreener(f"/token-pairs/v1/solana/{mint}")
        if isinstance(data, list):
            return data
        return []

    def _store(self, candidates: Dict[str, MoverCandidate], candidate: Optional[MoverCandidate]):
        if not candidate:
            return
        existing = candidates.get(candidate.mint)
        if not existing or candidate.momentum_pct > existing.momentum_pct:
            candidates[candidate.mint] = candidate

    def _fetch_trending_rank_tokens(self, limit: int) -> List[dict]:
        """Find Gems trending board: GET /defi/token_trending sorted by rank ascending."""
        data = self._get_birdeye(
            TRENDING_PATH,
            params={
                "sort_by": "rank",
                "sort_type": "asc",
                "offset": 0,
                "limit": limit,
            },
        )
        tokens = self._extract_tokens(data)
        # Preserve API rank order (do not re-sort by momentum).
        return tokens[:limit]

    def _trending_top5_from_dexscreener(self, limit: int) -> List[MoverCandidate]:
        """Labeled DexScreener fallback used only when Birdeye trending auth fails."""
        seed_mints = self._collect_dexscreener_seed_mints()[: max(limit * 4, limit)]
        if not seed_mints:
            logger.warning(
                "top5 trending: Birdeye unavailable and DexScreener fallback has no seeds"
            )
            return []

        ordered: List[MoverCandidate] = []
        seen: set[str] = set()
        for mint in seed_mints:
            if len(ordered) >= limit:
                break
            for pair in self._fetch_pairs_for_mint(mint):
                raw = parse_pair(
                    pair,
                    min_liquidity_usd=Config.effective_birdeye_min_liquidity(),
                    source=BIRDEYE_TRENDING_TOP5_SOURCE,
                )
                if not raw or raw.mint in seen:
                    continue
                # Retag explicitly for win-lean bypass path.
                raw.source = BIRDEYE_TRENDING_TOP5_SOURCE
                seen.add(raw.mint)
                ordered.append(raw)
                break
        logger.warning(
            "top5 trending (DexScreener fallback — Birdeye auth/unavailable): %s",
            ", ".join(c.symbol for c in ordered) or "(none)",
        )
        return ordered

    def fetch_trending_top5(self) -> List[MoverCandidate]:
        """Top N Find Gems trending tokens (same ranking as /find-gems?type=trending).

        Uses GET /defi/token_trending?sort_by=rank&sort_type=asc. Preserves rank
        order. On Birdeye 401/unavailable, falls back to DexScreener boosts/trending
        with a clear label (same pattern as the main Birdeye scanner fallback).
        """
        limit = int(Config.BIRDEYE_TRENDING_TOP5_LIMIT)
        _set_trending_top5_status(
            enabled=Config.birdeye_trending_top5_enabled(),
            last_fetch_ts=time.time(),
            last_error=None,
        )

        tokens: List[dict] = []
        fetch_source = "birdeye"
        if Config.birdeye_api_available():
            tokens = self._fetch_trending_rank_tokens(limit)
            if not tokens and _last_birdeye_auth_status in ("401", "403"):
                fetch_source = "dexscreener_fallback"
                logger.warning(
                    "top5 trending blocked on Birdeye auth (%s) — using DexScreener "
                    "fallback until BIRDEYE_API_KEY is rotated",
                    _last_birdeye_auth_status,
                )
        else:
            fetch_source = "dexscreener_fallback"
            _set_birdeye_auth_status("missing_key")

        candidates: List[MoverCandidate] = []
        if tokens and fetch_source == "birdeye":
            for idx, token in enumerate(tokens):
                mint = token.get("address")
                if not mint:
                    continue
                cand = parse_birdeye_trending_top5_token(token)
                if cand is None:
                    # Try overview enrichment when list row lacks price/liq detail.
                    overview = self._fetch_token_overview(mint)
                    cand = parse_birdeye_trending_top5_token(token, overview)
                if cand is None:
                    # Last resort: DexScreener pair liquidity for this mint only.
                    for pair in self._fetch_pairs_for_mint(mint):
                        pair_cand = parse_pair(
                            pair,
                            min_liquidity_usd=Config.effective_birdeye_min_liquidity(),
                            source=BIRDEYE_TRENDING_TOP5_SOURCE,
                        )
                        if pair_cand:
                            pair_cand.source = BIRDEYE_TRENDING_TOP5_SOURCE
                            cand = pair_cand
                            break
                if cand is None:
                    logger.info(
                        "top5 trending skip: could not build candidate for rank=%s %s",
                        token.get("rank", idx),
                        token.get("symbol") or mint[:8],
                    )
                    continue
                candidates.append(cand)
        else:
            candidates = self._trending_top5_from_dexscreener(limit)
            fetch_source = "dexscreener_fallback"

        symbols = [c.symbol for c in candidates]
        mints = [c.mint for c in candidates]
        ranks = list(range(1, len(candidates) + 1))
        _set_trending_top5_status(
            enabled=Config.birdeye_trending_top5_enabled(),
            auth=_last_birdeye_auth_status,
            source=fetch_source,
            symbols=symbols,
            mints=mints,
            ranks=ranks,
            last_fetch_ts=time.time(),
            last_error=(
                f"birdeye_auth_{_last_birdeye_auth_status}"
                if fetch_source == "dexscreener_fallback"
                and _last_birdeye_auth_status in ("401", "403", "missing_key")
                else None
            ),
        )
        logger.info(
            "top5 trending fetched (%s, auth=%s): %s",
            fetch_source,
            _last_birdeye_auth_status,
            ", ".join(f"{i+1}.{s}" for i, s in enumerate(symbols)) or "(none)",
        )
        return candidates

    def scan(self, *, fast_mode: bool = False) -> List[MoverCandidate]:
        if not Config.birdeye_api_available():
            return self._scan_dexscreener_fallback(fast_mode=fast_mode)

        if fast_mode and Config.FIRST_SCAN_FAST_MODE:
            _set_birdeye_scan_status("active")
            limit = min(10, Config.BIRDEYE_TRENDING_LIMIT)
            if Config.birdeye_find_gems_enabled():
                tokens = self._fetch_find_gems_gainers(limit)
            else:
                trending = self._get_birdeye(
                    TRENDING_PATH,
                    params={
                        "sort_by": "rank",
                        "sort_type": "asc",
                        "offset": 0,
                        "limit": limit,
                    },
                )
                tokens = self._extract_tokens(trending)[:limit]
            if not tokens:
                return self._scan_dexscreener_fallback(fast_mode=True)

            candidates: Dict[str, MoverCandidate] = {}
            for token in tokens:
                mint = token.get("address")
                if not mint:
                    continue
                self._store(candidates, parse_birdeye_token(token))

            ranked = sorted(candidates.values(), key=lambda c: c.momentum_pct, reverse=True)
            logger.info(
                "Scanner found %d birdeye gems (first-scan quick endpoint)",
                len(ranked),
            )
            return ranked

        _set_birdeye_scan_status("active")
        tokens = self._fetch_trending_tokens()
        if not tokens:
            global _birdeye_auth_warned
            if _birdeye_auth_warned:
                _set_birdeye_scan_status("failed")
            logger.info("Scanner found 0 birdeye gems")
            return []

        candidates: Dict[str, MoverCandidate] = {}
        for token in tokens:
            mint = token.get("address")
            if not mint:
                continue

            enriched = False
            for pair in self._fetch_pairs_for_mint(mint):
                candidate = parse_birdeye_pair(pair)
                if candidate:
                    self._store(candidates, candidate)
                    enriched = True

            if not enriched:
                overview = self._fetch_token_overview(mint)
                self._store(candidates, parse_birdeye_token(token, overview))

        ranked = sorted(candidates.values(), key=lambda c: c.momentum_pct, reverse=True)
        logger.info("Scanner found %d birdeye gems", len(ranked))
        return ranked
