"""Scan GMGN.ai Solana trending tokens via the public quotation API."""
import logging
import time
from typing import Dict, List, Optional

import requests

from config import Config
from dexscreener_client import get_dexscreener_client
from scanner import MoverCandidate, parse_pair
from scanner_momentum import price_changes_from_external

logger = logging.getLogger(__name__)

GMGN_SOURCE_TAG = "gmgn"
GMGN_RANK_PATH = "/defi/quotation/v1/rank/sol/swaps/{timeframe}"
GMGN_PUMP_RANK_PATH = "/defi/quotation/v1/rank/sol/pump/{timeframe}"

_last_gmgn_scan_status: str = "idle"
_gmgn_api_warned = False
_last_gmgn_request_at: float = 0.0


def get_last_gmgn_scan_status() -> str:
    """Return the most recent GMGN scan mode: active, failed, or idle."""
    return _last_gmgn_scan_status


def _set_gmgn_scan_status(status: str) -> None:
    global _last_gmgn_scan_status
    _last_gmgn_scan_status = status


def _gmgn_pace() -> None:
    global _last_gmgn_request_at
    delay = Config.GMGN_REQUEST_DELAY_SEC
    if delay <= 0:
        return
    elapsed = time.time() - _last_gmgn_request_at
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_gmgn_request_at = time.time()


def parse_gmgn_pair(pair: dict) -> Optional[MoverCandidate]:
    """Parse a DexScreener pair as a GMGN candidate with GMGN-specific filters."""
    candidate = parse_pair(
        pair,
        min_liquidity_usd=Config.effective_gmgn_min_liquidity(),
        source=GMGN_SOURCE_TAG,
    )
    if not candidate:
        return None

    min_vol = Config.effective_min_volume_for_mint(candidate.mint)
    min_momentum = Config.effective_gmgn_min_momentum()
    if candidate.volume_24h_usd < min_vol:
        return None
    if candidate.momentum_pct < min_momentum:
        return None
    return candidate


def parse_gmgn_token(token: dict) -> Optional[MoverCandidate]:
    """Build a candidate from a GMGN rank token object."""
    mint = token.get("address")
    if not mint:
        return None

    liquidity = float(token.get("liquidity") or 0)
    volume = float(token.get("volume") or 0)
    price_usd = float(token.get("price") or 0)

    changes = price_changes_from_external(token)
    momentum = changes.discovery_momentum()

    min_liq = Config.effective_gmgn_min_liquidity()
    min_vol = Config.effective_min_volume_for_mint(mint)
    min_momentum = Config.effective_gmgn_min_momentum()

    if liquidity < min_liq:
        return None
    if volume < min_vol:
        return None
    if price_usd <= 0:
        return None
    if momentum < min_momentum:
        return None

    open_ts = token.get("open_timestamp") or token.get("creation_timestamp")
    pool_created_at = None
    if open_ts is not None:
        try:
            ts = int(open_ts)
            pool_created_at = ts * 1000 if ts < 1_000_000_000_000 else ts
        except (TypeError, ValueError):
            pool_created_at = None

    symbol = token.get("symbol") or "UNKNOWN"
    name = token.get("name") or symbol
    launchpad = token.get("launchpad") or token.get("pool_type_str") or "gmgn"

    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=name,
        pair_address="",
        dex=str(launchpad),
        price_usd=price_usd,
        liquidity_usd=liquidity,
        volume_24h_usd=volume,
        momentum_pct=momentum,
        price_change_5m=changes.change_5m,
        price_change_1h=changes.change_1h,
        price_change_6h=changes.change_6h,
        price_change_24h=changes.change_24h,
        pool_created_at=pool_created_at,
        source=GMGN_SOURCE_TAG,
    )


class GmgnScanner:
    """Fetch trending Solana tokens from GMGN.ai (https://gmgn.ai/?chain=sol)."""

    def __init__(self):
        self.base_url = Config.GMGN_API_BASE.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(Config.gmgn_headers())
        self._dex_client = get_dexscreener_client()
        if not Config.GMGN_API_KEY:
            Config.log_missing_scanner_key_once(
                "gmgn",
                "GMGN_API_KEY not set — using public GMGN quotation API "
                "(optional key from https://gmgn.ai/ai for Agent API access)",
            )

    def _log_api_failure(self, message: str) -> None:
        global _gmgn_api_warned
        if not _gmgn_api_warned:
            logger.warning(message)
            _gmgn_api_warned = True
        else:
            logger.debug(message)

    def _extract_rank_tokens(self, data: Optional[object]) -> List[dict]:
        if not isinstance(data, dict):
            return []
        if data.get("code") not in (0, None) and not data.get("success"):
            return []
        payload = data.get("data") or {}
        if not isinstance(payload, dict):
            return []
        tokens = payload.get("rank") or []
        return [t for t in tokens if isinstance(t, dict)]

    def _fetch_rank_tokens(
        self,
        timeframe: str,
        orderby: str,
        *,
        limit: int,
        rank_path: str = GMGN_RANK_PATH,
    ) -> List[dict]:
        path = rank_path.format(timeframe=timeframe)
        query_pairs = [
            ("orderby", orderby),
            ("direction", "desc"),
            ("limit", str(limit)),
        ]
        for filt in Config.gmgn_safety_filters():
            query_pairs.append(("filters[]", filt))

        _gmgn_pace()
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=query_pairs,
                timeout=15,
            )
            if response.status_code == 403:
                self._log_api_failure("GMGN API blocked (403); skipping rank fetch")
                return []
            if response.status_code == 429:
                logger.warning("GMGN API rate limited")
                return []
            response.raise_for_status()
            return self._extract_rank_tokens(response.json())
        except requests.RequestException as exc:
            self._log_api_failure(f"GMGN rank fetch failed ({orderby}/{timeframe}): {exc}")
            return []

    def _collect_seed_tokens(self, *, fast_mode: bool = False) -> List[dict]:
        limit = Config.GMGN_TRENDING_LIMIT
        if fast_mode and Config.FIRST_SCAN_FAST_MODE:
            limit = min(10, limit)

        timeframe = Config.GMGN_TIMEFRAME
        tokens: List[dict] = []
        seen: set[str] = set()

        def _add(batch: List[dict]) -> None:
            for token in batch:
                mint = token.get("address")
                if mint and mint not in seen:
                    seen.add(mint)
                    tokens.append(token)

        _add(self._fetch_rank_tokens(timeframe, "volume", limit=limit))
        if not fast_mode and len(tokens) < limit:
            remaining = limit - len(tokens)
            _add(self._fetch_rank_tokens(timeframe, "swaps", limit=remaining))
        if not fast_mode and len(tokens) < limit:
            remaining = limit - len(tokens)
            _add(self._fetch_rank_tokens(timeframe, "price_change", limit=remaining))
        if not fast_mode and len(tokens) < limit // 2:
            remaining = limit - len(tokens)
            _add(self._fetch_rank_tokens(timeframe, "smartmoney", limit=remaining))
        if not fast_mode and len(tokens) < limit // 2:
            remaining = limit - len(tokens)
            _add(
                self._fetch_rank_tokens(
                    timeframe,
                    "volume",
                    limit=remaining,
                    rank_path=GMGN_PUMP_RANK_PATH,
                )
            )

        return tokens[:limit]

    def _fetch_pairs_for_mint(self, mint: str) -> List[dict]:
        return self._dex_client.get_token_pairs(mint)

    def _store(self, candidates: Dict[str, MoverCandidate], candidate: Optional[MoverCandidate]) -> None:
        if not candidate:
            return
        existing = candidates.get(candidate.mint)
        if not existing or candidate.momentum_pct > existing.momentum_pct:
            candidates[candidate.mint] = candidate

    def scan(self, *, fast_mode: bool = False) -> List[MoverCandidate]:
        if not Config.scan_gmgn_enabled():
            _set_gmgn_scan_status("idle")
            return []

        tokens = self._collect_seed_tokens(fast_mode=fast_mode)
        if not tokens:
            _set_gmgn_scan_status("failed")
            logger.info("Scanner found 0 gmgn movers")
            return []

        _set_gmgn_scan_status("active")
        candidates: Dict[str, MoverCandidate] = {}
        mint_batch = self._dex_client.get_seed_batch(
            [t.get("address") for t in tokens if t.get("address")],
            per_cycle=Config.FIRST_SCAN_DEEP_MINTS if fast_mode and Config.FIRST_SCAN_FAST_MODE else None,
        )
        batch_set = set(mint_batch)

        for token in tokens:
            mint = token.get("address")
            if not mint:
                continue

            if mint in batch_set:
                enriched = False
                for pair in self._fetch_pairs_for_mint(mint):
                    candidate = parse_gmgn_pair(pair)
                    if candidate:
                        self._store(candidates, candidate)
                        enriched = True
                if not enriched:
                    self._store(candidates, parse_gmgn_token(token))
            else:
                self._store(candidates, parse_gmgn_token(token))

        ranked = sorted(candidates.values(), key=lambda c: c.momentum_pct, reverse=True)
        logger.info("Scanner found %d gmgn movers", len(ranked))
        return ranked
