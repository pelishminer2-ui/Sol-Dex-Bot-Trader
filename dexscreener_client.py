"""Global rate-limited DexScreener HTTP client with token-pair caching."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

import requests

from config import Config

logger = logging.getLogger(__name__)

TOKEN_PAIRS_PREFIX = "/token-pairs/v1/solana/"
RATE_LIMIT_SCAN_BOOST_SEC = 5
REDUCED_MAX_SEEDS_WHEN_LIMITED = 15

_client: Optional["DexScreenerClient"] = None
_warned_rate_limit_paths: set[str] = set()


class DexScreenerClient:
    """Thread-safe singleton pacing layer for all DexScreener HTTP calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._last_request_at = 0.0
        self._rate_limit_until = 0.0
        self._pair_cache: Dict[str, Tuple[float, object]] = {}
        self._response_cache: Dict[str, Tuple[float, object]] = {}
        self._deep_scan_offset = 0
        self._cycle_rate_limited = False
        self._cycle_429_count = 0
        self._total_429_count = 0

    def begin_scan_cycle(self) -> None:
        self._cycle_rate_limited = False
        self._cycle_429_count = 0

    def is_rate_limited(self) -> bool:
        return self._cycle_rate_limited or time.time() < self._rate_limit_until

    def effective_max_seeds(self) -> int:
        base = Config.DEXSCREENER_MAX_SEED_MINTS
        if self.is_rate_limited():
            return min(base, REDUCED_MAX_SEEDS_WHEN_LIMITED)
        return base

    def get_scan_interval_boost(self) -> int:
        if self._cycle_429_count >= 2 or time.time() < self._rate_limit_until:
            return RATE_LIMIT_SCAN_BOOST_SEC
        if self._total_429_count >= 3 and self._last_success_within(60):
            return RATE_LIMIT_SCAN_BOOST_SEC
        return 0

    def _last_success_within(self, seconds: float) -> bool:
        # Approximate: if we were rate-limited recently, keep a longer scan interval.
        return self._total_429_count > 0 and time.time() < self._rate_limit_until + seconds

    def get_health(self) -> dict:
        boost = self.get_scan_interval_boost()
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
            "scan_interval_boost_sec": boost,
            "effective_max_seeds": self.effective_max_seeds(),
            "deep_scan_per_cycle": Config.DEXSCREENER_DEEP_SCAN_PER_CYCLE,
            "cache_entries": len(self._pair_cache) + len(self._response_cache),
        }

    def get_seed_batch(
        self, seed_mints: List[str], *, per_cycle: Optional[int] = None
    ) -> List[str]:
        """Return a rotating subset of seed mints for staggered deep scans."""
        unique: List[str] = []
        seen: set[str] = set()
        for mint in seed_mints:
            if mint and mint not in seen:
                seen.add(mint)
                unique.append(mint)

        pool = unique[: self.effective_max_seeds()]
        if not pool:
            return []

        if per_cycle is None:
            per_cycle = Config.DEXSCREENER_DEEP_SCAN_PER_CYCLE
        if self.is_rate_limited():
            per_cycle = max(5, per_cycle // 2)
        per_cycle = min(per_cycle, len(pool))

        start = self._deep_scan_offset % len(pool)
        selected = [pool[(start + i) % len(pool)] for i in range(per_cycle)]
        self._deep_scan_offset = (self._deep_scan_offset + per_cycle) % len(pool)
        return selected

    def get_token_pairs(self, mint: str, timeout: int = 15) -> List[dict]:
        data = self.get(f"{TOKEN_PAIRS_PREFIX}{mint}", timeout=timeout)
        if isinstance(data, list):
            return data
        return []

    def get(self, path: str, timeout: int = 15, max_retries: int = 3) -> Optional[object]:
        ttl = Config.DEXSCREENER_PAIR_CACHE_TTL_SEC
        if path.startswith(TOKEN_PAIRS_PREFIX):
            mint = path[len(TOKEN_PAIRS_PREFIX) :]
            cached = self._pair_cache.get(mint)
            if cached:
                cached_at, data = cached
                if time.time() - cached_at < ttl:
                    return data
        else:
            cached = self._response_cache.get(path)
            if cached:
                cached_at, data = cached
                if time.time() - cached_at < ttl:
                    return data

        for attempt in range(max_retries):
            with self._lock:
                self._throttle_locked()
                try:
                    response = self._session.get(
                        f"{Config.DEXSCREENER_BASE}{path}",
                        headers=Config.dexscreener_headers(),
                        timeout=timeout,
                    )
                    self._last_request_at = time.time()

                    if response.status_code == 429:
                        self._cycle_rate_limited = True
                        self._cycle_429_count += 1
                        self._total_429_count += 1
                        retry_after = self._parse_retry_after(response)
                        wait = max(
                            retry_after,
                            Config.DEXSCREENER_REQUEST_DELAY_SEC * (2**attempt),
                            3.0 * (attempt + 1),
                        )
                        self._rate_limit_until = time.time() + wait
                        self._log_rate_limited(path, wait)
                        time.sleep(wait)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    if path.startswith(TOKEN_PAIRS_PREFIX):
                        mint = path[len(TOKEN_PAIRS_PREFIX) :]
                        self._pair_cache[mint] = (time.time(), data)
                    else:
                        self._response_cache[path] = (time.time(), data)

                    return data
                except requests.RequestException as exc:
                    if attempt < max_retries - 1 and "429" in str(exc):
                        self._cycle_rate_limited = True
                        self._cycle_429_count += 1
                        self._total_429_count += 1
                        wait = max(
                            Config.DEXSCREENER_REQUEST_DELAY_SEC * (2 ** (attempt + 1)),
                            3.0 * (attempt + 1),
                        )
                        self._rate_limit_until = time.time() + wait
                        self._log_rate_limited(path, wait)
                        time.sleep(wait)
                        continue
                    logger.debug("DexScreener request failed for %s: %s", path, exc)
                    return None
        return None

    def _throttle_locked(self) -> None:
        delay = Config.DEXSCREENER_REQUEST_DELAY_SEC
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

    def _log_rate_limited(self, path: str, wait: float) -> None:
        if path not in _warned_rate_limit_paths:
            logger.warning(
                "DexScreener rate limited for %s — retry in %.1fs",
                path,
                wait,
            )
            _warned_rate_limit_paths.add(path)
        else:
            logger.debug(
                "DexScreener rate limited for %s — retry in %.1fs",
                path,
                wait,
            )


def get_dexscreener_client() -> DexScreenerClient:
    global _client
    if _client is None:
        _client = DexScreenerClient()
    return _client


def reset_client_state_for_tests() -> None:
    global _client
    _client = None
    _warned_rate_limit_paths.clear()


def reset_dexscreener_client_for_tests() -> None:
    """Alias for validation scripts."""
    reset_client_state_for_tests()
