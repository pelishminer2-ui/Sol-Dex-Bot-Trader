"""Global rate-limited Jupiter HTTP client with price and quote caching."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

import requests

from config import Config

logger = logging.getLogger(__name__)

RATE_LIMIT_POLL_BOOST_SEC = 3
PRICE_CHUNK_SIZE = 50

_client: Optional["JupiterClient"] = None
_warned_rate_limit_keys: set[str] = set()


class JupiterClient:
    """Thread-safe singleton pacing layer for all Jupiter HTTP calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update(Config.jupiter_headers())
        self._last_request_at = 0.0
        self._rate_limit_until = 0.0
        self._price_cache: Dict[str, Tuple[float, float]] = {}
        self._quote_cache: Dict[str, Tuple[float, dict]] = {}
        self._cycle_429_count = 0
        self._total_429_count = 0

    def is_rate_limited(self) -> bool:
        return time.time() < self._rate_limit_until

    def get_poll_interval_boost(self) -> int:
        if self._cycle_429_count >= 2 or self.is_rate_limited():
            return RATE_LIMIT_POLL_BOOST_SEC
        if self._total_429_count >= 3 and time.time() < self._rate_limit_until + 60:
            return RATE_LIMIT_POLL_BOOST_SEC
        return 0

    def get_health(self) -> dict:
        if self.is_rate_limited():
            status = "rate_limited"
        elif self._cycle_429_count > 0:
            status = "recovering"
        else:
            status = "ok"
        limited_until = self._rate_limit_until
        return {
            "status": status,
            "429_count": self._cycle_429_count,
            "total_429": self._total_429_count,
            "rate_limited_until": limited_until if limited_until > time.time() else None,
            "poll_interval_boost_sec": self.get_poll_interval_boost(),
            "api_key_configured": bool(Config.JUPITER_API_KEY),
            "price_cache_entries": len(self._price_cache),
            "quote_cache_entries": len(self._quote_cache),
        }

    def get_prices(self, mints: List[str]) -> Dict[str, float]:
        """Batch-fetch USD prices; serves cached mints within TTL."""
        if not mints:
            return {}

        now = time.time()
        ttl = Config.JUPITER_PRICE_CACHE_TTL_SEC
        result: Dict[str, float] = {}
        need_fetch: List[str] = []

        for mint in mints:
            if not mint:
                continue
            cached = self._price_cache.get(mint)
            if cached and now - cached[0] < ttl:
                result[mint] = cached[1]
            elif mint not in need_fetch:
                need_fetch.append(mint)

        for i in range(0, len(need_fetch), PRICE_CHUNK_SIZE):
            chunk = need_fetch[i : i + PRICE_CHUNK_SIZE]
            fetched = self._fetch_prices_chunk(chunk)
            result.update(fetched)

        return result

    def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int,
        *,
        use_cache: bool = True,
        timeout: int = 15,
        max_retries: int = 3,
    ) -> Optional[dict]:
        cache_key = f"quote:{input_mint}:{output_mint}:{amount}:{slippage_bps}"
        if use_cache:
            cached = self._quote_cache.get(cache_key)
            if cached:
                cached_at, data = cached
                if time.time() - cached_at < Config.JUPITER_QUOTE_CACHE_TTL_SEC:
                    return data

        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
            "restrictIntermediateTokens": "true",
            "instructionVersion": "V2",
        }
        data = self._request(
            "GET",
            Config.JUPITER_QUOTE_API,
            cache_key=cache_key if use_cache else None,
            params=params,
            timeout=timeout,
            max_retries=max_retries,
        )
        if data and use_cache and "error" not in data:
            self._quote_cache[cache_key] = (time.time(), data)
        return data

    def post_swap(self, payload: dict, *, timeout: int = 20, max_retries: int = 3) -> Optional[dict]:
        return self._request(
            "POST",
            Config.JUPITER_SWAP_API,
            cache_key=None,
            json=payload,
            timeout=timeout,
            max_retries=max_retries,
        )

    def invalidate_quote_cache(self) -> None:
        self._quote_cache.clear()

    def _fetch_prices_chunk(self, mints: List[str]) -> Dict[str, float]:
        if not mints:
            return {}

        cache_key = f"price:{','.join(mints)}"
        data = self._request(
            "GET",
            Config.JUPITER_PRICE_API,
            cache_key=cache_key,
            params={"ids": ",".join(mints)},
            timeout=10,
            max_retries=3,
        )
        if not isinstance(data, dict):
            return {}

        price_map = (
            data
            if any(isinstance(v, dict) for v in data.values())
            else data.get("data", {})
        )
        if not isinstance(price_map, dict):
            price_map = {}

        now = time.time()
        prices: Dict[str, float] = {}
        for mint in mints:
            entry = price_map.get(mint) or data.get(mint)
            price: Optional[float] = None
            if isinstance(entry, dict):
                raw = entry.get("usdPrice") or entry.get("price")
                if raw is not None:
                    price = float(raw)
            elif isinstance(entry, (int, float)):
                price = float(entry)
            if price and price > 0:
                prices[mint] = price
                self._price_cache[mint] = (now, price)
        return prices

    def _request(
        self,
        method: str,
        url: str,
        *,
        cache_key: Optional[str],
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        timeout: int = 15,
        max_retries: int = 3,
    ) -> Optional[object]:
        log_key = cache_key or url

        for attempt in range(max_retries):
            with self._lock:
                self._throttle_locked()
                try:
                    response = self._session.request(
                        method,
                        url,
                        params=params,
                        json=json,
                        timeout=timeout,
                    )
                    self._last_request_at = time.time()

                    if response.status_code == 429:
                        self._cycle_429_count += 1
                        self._total_429_count += 1
                        retry_after = self._parse_retry_after(response)
                        wait = max(
                            retry_after,
                            Config.JUPITER_REQUEST_DELAY_SEC * (2**attempt),
                            3.0 * (attempt + 1),
                        )
                        self._rate_limit_until = time.time() + wait
                        self._log_rate_limited(log_key, wait)
                        time.sleep(wait)
                        continue

                    response.raise_for_status()
                    return response.json()
                except requests.RequestException as exc:
                    if attempt < max_retries - 1 and "429" in str(exc):
                        self._cycle_429_count += 1
                        self._total_429_count += 1
                        wait = max(
                            Config.JUPITER_REQUEST_DELAY_SEC * (2 ** (attempt + 1)),
                            3.0 * (attempt + 1),
                        )
                        self._rate_limit_until = time.time() + wait
                        self._log_rate_limited(log_key, wait)
                        time.sleep(wait)
                        continue
                    logger.debug("Jupiter request failed for %s: %s", log_key, exc)
                    return None
        return None

    def _throttle_locked(self) -> None:
        delay = Config.JUPITER_REQUEST_DELAY_SEC
        now = time.time()
        if now < self._rate_limit_until:
            time.sleep(self._rate_limit_until - now)
        elapsed = time.time() - self._last_request_at
        if elapsed < delay:
            time.sleep(delay - elapsed)

    @staticmethod
    def _parse_retry_after(response: requests.Response) -> float:
        header = response.headers.get("Retry-After")
        if not header:
            return 0.0
        try:
            return max(0.0, float(header))
        except ValueError:
            return 0.0

    def _log_rate_limited(self, key: str, wait: float) -> None:
        if key not in _warned_rate_limit_keys:
            logger.warning(
                "Jupiter rate limited for %s — retry in %.1fs",
                key,
                wait,
            )
            _warned_rate_limit_keys.add(key)
        else:
            logger.debug(
                "Jupiter rate limited for %s — retry in %.1fs",
                key,
                wait,
            )


def get_jupiter_client() -> JupiterClient:
    global _client
    if _client is None:
        _client = JupiterClient()
    return _client


def reset_jupiter_client_for_tests() -> None:
    global _client
    _client = None
    _warned_rate_limit_keys.clear()
