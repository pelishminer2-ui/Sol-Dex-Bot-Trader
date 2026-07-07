"""Scan pump.fun tokens via pump.fun API and DexScreener fallback."""
import logging
import time
from typing import Dict, List, Optional, Tuple

import requests

from config import Config
from dexscreener_client import get_dexscreener_client
from scanner import MoverCandidate, parse_pair
from scanner_momentum import price_changes_from_dexscreener, price_changes_from_external

logger = logging.getLogger(__name__)

PUMPFUN_DEX_IDS = {"pumpfun", "pump.fun"}
PUMPFUN_API_BASE_FALLBACKS = (
    "https://frontend-api.pump.fun",
    "https://frontend-api-v3.pump.fun",
)

_last_pumpfun_scan_status: str = "idle"
_pumpfun_api_warned = False


def get_last_pumpfun_scan_status() -> str:
    """Return the most recent pump.fun scan mode: active, fallback, add_key, or idle."""
    return _last_pumpfun_scan_status


def _set_pumpfun_scan_status(status: str) -> None:
    global _last_pumpfun_scan_status
    _last_pumpfun_scan_status = status


def _pumpfun_api_bases() -> tuple[str, ...]:
    primary = Config.PUMPFUN_API_BASE.rstrip("/")
    bases = [primary]
    for fallback in PUMPFUN_API_BASE_FALLBACKS:
        if fallback not in bases:
            bases.append(fallback)
    return tuple(bases)


def _pumpfun_api_paths() -> tuple[str, ...]:
    limit = Config.PUMPFUN_API_LIMIT
    return (
        f"/coins/latest?limit={limit}&offset=0&sort=created_timestamp&order=DESC&includeNsfw=false",
        "/coins/king-of-the-hill?includeNsfw=false",
        f"/coins/featured?limit={limit}&offset=0&includeNsfw=false",
        f"/coins/for-you?limit={limit}&offset=0&includeNsfw=false",
        f"/coins/graduated?limit={limit}&offset=0&includeNsfw=false",
    )


def _log_pumpfun_api_failure(exc: requests.RequestException) -> None:
    global _pumpfun_api_warned
    if not _pumpfun_api_warned:
        logger.warning("pump.fun API request failed: %s", exc)
        _pumpfun_api_warned = True
    else:
        logger.debug("pump.fun API request failed: %s", exc)


def _age_minutes(created_at_ms: Optional[int]) -> Optional[float]:
    if not created_at_ms:
        return None
    return (time.time() * 1000 - created_at_ms) / (1000 * 60)


def _coin_created_ms(coin: dict) -> Optional[int]:
    ts = coin.get("created_timestamp") or coin.get("createdTimestamp")
    if ts is None:
        return None
    ts = float(ts)
    if ts < 1e12:
        ts *= 1000
    return int(ts)


def _coin_market_cap_usd(coin: dict) -> float:
    for key in ("usd_market_cap", "marketCapUsd", "market_cap", "marketCap"):
        val = coin.get(key)
        if val is not None:
            return float(val)
    return 0.0


def _coin_price_usd(coin: dict) -> float:
    for key in ("usd_market_cap", "price_usd", "priceUsd"):
        val = coin.get(key)
        if key == "usd_market_cap" and val is not None:
            supply = float(coin.get("total_supply") or coin.get("token_supply") or 0)
            if supply > 0:
                return float(val) / supply
        if val is not None and key != "usd_market_cap":
            return float(val)
    sol_res = float(coin.get("virtual_sol_reserves") or coin.get("virtualSolReserves") or 0)
    token_res = float(coin.get("virtual_token_reserves") or coin.get("virtualTokenReserves") or 0)
    if sol_res > 0 and token_res > 0:
        return (sol_res / 1e9) / (token_res / 1e6) * 150
    return 0.0


def parse_pumpfun_pair(pair: dict) -> Optional[MoverCandidate]:
    """Parse a DexScreener pair as a pump.fun candidate with pump-specific filters."""
    if (pair.get("dexId") or "").lower() not in PUMPFUN_DEX_IDS:
        return None

    market_cap = float(pair.get("marketCap") or pair.get("fdv") or 0)
    if market_cap < Config.PUMPFUN_MIN_MARKET_CAP_USD:
        return None

    created_at = pair.get("pairCreatedAt")
    age_min = _age_minutes(created_at)
    max_age = Config.PUMPFUN_MAX_AGE_MINUTES
    if max_age > 0 and age_min is not None and age_min > max_age:
        return None

    min_liq = Config.effective_pumpfun_min_liquidity()
    min_vol = Config.effective_pumpfun_min_volume()
    min_momentum = Config.effective_pumpfun_min_momentum()

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

    if liquidity < min_liq:
        return None
    if volume_24h < min_vol:
        return None
    if momentum < min_momentum:
        return None

    return MoverCandidate(
        mint=mint,
        symbol=base.get("symbol") or "UNKNOWN",
        name=base.get("name") or base.get("symbol") or "UNKNOWN",
        pair_address=pair.get("pairAddress") or "",
        dex=pair.get("dexId") or "pumpfun",
        price_usd=price_usd,
        liquidity_usd=liquidity,
        volume_24h_usd=volume_24h,
        momentum_pct=momentum,
        price_change_5m=changes.change_5m,
        price_change_1h=changes.change_1h,
        price_change_6h=changes.change_6h,
        price_change_24h=changes.change_24h,
        pool_created_at=created_at,
        source="pumpfun",
    )


def parse_pumpfun_coin(coin: dict) -> Optional[MoverCandidate]:
    """Build a candidate from a pump.fun API coin object (enriched later via DexScreener)."""
    mint = coin.get("mint") or coin.get("address")
    if not mint:
        return None

    market_cap = _coin_market_cap_usd(coin)
    if market_cap < Config.PUMPFUN_MIN_MARKET_CAP_USD:
        return None

    created_ms = _coin_created_ms(coin)
    age_min = _age_minutes(created_ms)
    max_age = Config.PUMPFUN_MAX_AGE_MINUTES
    if max_age > 0 and age_min is not None and age_min > max_age:
        return None

    price_usd = _coin_price_usd(coin)
    if price_usd <= 0:
        return None

    symbol = coin.get("symbol") or "UNKNOWN"
    name = coin.get("name") or symbol
    changes = price_changes_from_external(coin)
    momentum = changes.discovery_momentum()

    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=name,
        pair_address=coin.get("bonding_curve") or coin.get("bondingCurve") or "",
        dex="pumpfun",
        price_usd=price_usd,
        liquidity_usd=market_cap * 0.1,
        volume_24h_usd=float(coin.get("volume") or coin.get("volume24h") or 0),
        momentum_pct=momentum,
        price_change_5m=changes.change_5m,
        price_change_1h=changes.change_1h,
        price_change_6h=changes.change_6h,
        price_change_24h=changes.change_24h,
        pool_created_at=created_ms,
        source="pumpfun",
    )


class PumpFunScanner:
    """Fetch pump.fun tokens from pump.fun public APIs and DexScreener."""

    def __init__(self):
        self._dex_client = get_dexscreener_client()
        self.session = requests.Session()
        if not Config.PUMPFUN_API_KEY:
            Config.log_missing_scanner_key_once(
                "pumpfun",
                "PUMPFUN_API_KEY not set — using public pump.fun endpoints "
                "(optional JWT from wallet login may improve rate limits)",
            )

    def _get_dexscreener(self, path: str, timeout: int = 15) -> Optional[object]:
        return self._dex_client.get(path, timeout=timeout)

    def _get_pumpfun_api(self, url: str, timeout: int = 15) -> Tuple[Optional[object], bool]:
        """Fetch pump.fun API JSON. Returns (data, skip_rest_on_base)."""
        try:
            response = self.session.get(
                url,
                headers=Config.pumpfun_headers(),
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json(), False
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            skip_rest = status in (404, 530)
            _log_pumpfun_api_failure(exc)
            return None, skip_rest
        except requests.RequestException as exc:
            _log_pumpfun_api_failure(exc)
            return None, False

    def _is_pumpfun_pair(self, pair: dict) -> bool:
        return (pair.get("dexId") or "").lower() in PUMPFUN_DEX_IDS

    def _fetch_dexscreener_pumpfun_pairs(self) -> List[dict]:
        pairs: List[dict] = []
        for query in ("pumpfun", "pump.fun"):
            data = self._get_dexscreener(f"/latest/dex/search?q={query}")
            if not isinstance(data, dict):
                continue
            for pair in data.get("pairs") or []:
                if self._is_pumpfun_pair(pair):
                    pairs.append(pair)
        return pairs

    def _extend_coins_from_batch(self, coins: List[dict], seen: set[str], batch: list) -> None:
        for coin in batch:
            if not isinstance(coin, dict):
                continue
            mint = coin.get("mint") or coin.get("address")
            if mint and mint not in seen:
                seen.add(mint)
                coins.append(coin)

    def _fetch_pumpfun_api_coins(self) -> List[dict]:
        coins: List[dict] = []
        seen: set[str] = set()
        api_ok = False

        for base in _pumpfun_api_bases():
            skip_rest_on_base = False
            for path in _pumpfun_api_paths():
                if skip_rest_on_base:
                    break
                data, is_530 = self._get_pumpfun_api(f"{base}{path}")
                if is_530:
                    skip_rest_on_base = True
                    continue
                if data is None:
                    continue
                api_ok = True
                if isinstance(data, list):
                    batch = data
                elif isinstance(data, dict):
                    batch = data.get("coins") or data.get("data") or []
                else:
                    continue
                self._extend_coins_from_batch(coins, seen, batch)
            if api_ok:
                break

        if api_ok:
            _set_pumpfun_scan_status("active")
        elif not Config.PUMPFUN_API_KEY:
            _set_pumpfun_scan_status("add_key")
            logger.debug(
                "pump.fun API unavailable — using DexScreener search fallback "
                "(set PUMPFUN_API_KEY JWT for v3 auth)"
            )
        else:
            _set_pumpfun_scan_status("fallback")
            logger.debug(
                "pump.fun API unavailable — using DexScreener search fallback"
            )

        return coins

    def _fetch_pairs_for_mint(self, mint: str) -> List[dict]:
        data = self._dex_client.get_token_pairs(mint)
        pumpfun_pairs = [p for p in data if self._is_pumpfun_pair(p)]
        return pumpfun_pairs or data

    def _store(self, candidates: Dict[str, MoverCandidate], candidate: Optional[MoverCandidate]):
        if not candidate:
            return
        existing = candidates.get(candidate.mint)
        if not existing or candidate.momentum_pct > existing.momentum_pct:
            candidates[candidate.mint] = candidate

    def scan(self, *, fast_mode: bool = False) -> List[MoverCandidate]:
        candidates: Dict[str, MoverCandidate] = {}

        for pair in self._fetch_dexscreener_pumpfun_pairs():
            self._store(candidates, parse_pumpfun_pair(pair))

        if fast_mode and Config.FIRST_SCAN_FAST_MODE:
            _set_pumpfun_scan_status("fallback")
            ranked = sorted(candidates.values(), key=lambda c: c.momentum_pct, reverse=True)
            logger.info(
                "Scanner found %d pump.fun tokens (first-scan fast path)",
                len(ranked),
            )
            return ranked

        seen_mints: set[str] = set(candidates.keys())
        api_coins = self._fetch_pumpfun_api_coins()
        mint_batch = self._dex_client.get_seed_batch(
            [c.get("mint") or c.get("address") for c in api_coins if c.get("mint") or c.get("address")]
        )
        batch_set = set(mint_batch)
        for coin in api_coins:
            mint = coin.get("mint") or coin.get("address")
            if not mint or mint in seen_mints:
                continue
            if mint not in batch_set:
                self._store(candidates, parse_pumpfun_coin(coin))
                continue
            seen_mints.add(mint)

            enriched = False
            for pair in self._fetch_pairs_for_mint(mint):
                candidate = parse_pumpfun_pair(pair)
                if candidate:
                    self._store(candidates, candidate)
                    enriched = True

            if not enriched:
                self._store(candidates, parse_pumpfun_coin(coin))

        ranked = sorted(candidates.values(), key=lambda c: c.momentum_pct, reverse=True)
        logger.info("Scanner found %d pump.fun tokens", len(ranked))
        return ranked
