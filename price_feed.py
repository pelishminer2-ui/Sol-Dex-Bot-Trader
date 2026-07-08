import logging
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from config import Config
from dexscreener_client import get_dexscreener_client
from jupiter_client import get_jupiter_client

logger = logging.getLogger(__name__)


class PriceFeed:
    """Track per-mint prices with rolling baselines for momentum detection."""

    def __init__(self):
        self._dex_client = get_dexscreener_client()
        self._jupiter_client = get_jupiter_client()
        self._history: Dict[str, Deque[Tuple[float, float]]] = {}
        self._dex_prices: Dict[str, float] = {}

    def set_dex_price(self, mint: str, price_usd: float):
        if price_usd > 0:
            self._dex_prices[mint] = price_usd

    def _fetch_jupiter_prices(self, mints: List[str]) -> Dict[str, float]:
        return self._jupiter_client.get_prices(mints)

    def _fetch_dexscreener_prices(self, mints: List[str]) -> Dict[str, float]:
        """Fetch live DexScreener prices for held mints (not only watchlist)."""
        prices: Dict[str, float] = {}
        for mint in mints:
            data = self._dex_client.get(f"/latest/dex/tokens/{mint}", timeout=10)
            if not isinstance(data, dict):
                continue
            pairs = data.get("pairs") or []
            best_price = 0.0
            best_liq = -1.0
            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue
                price = float(pair.get("priceUsd") or 0)
                if price <= 0:
                    continue
                liq = float((pair.get("liquidity") or {}).get("usd") or 0)
                if liq > best_liq:
                    best_liq = liq
                    best_price = price
            if best_price > 0:
                prices[mint] = best_price
                self._dex_prices[mint] = best_price
        return prices

    def update(self, mints: List[str]) -> Dict[str, float]:
        now = time.time()
        jupiter_prices = self._fetch_jupiter_prices(mints)
        missing = [m for m in mints if m not in jupiter_prices]
        if missing and not self._jupiter_client.is_rate_limited():
            self._fetch_dexscreener_prices(missing)
        elif missing:
            logger.debug(
                "Skipping DexScreener fallback for %d mint(s) while Jupiter is throttled",
                len(missing),
            )

        result: Dict[str, float] = {}

        for mint in mints:
            price = jupiter_prices.get(mint) or self._dex_prices.get(mint)
            if not price or price <= 0:
                continue
            result[mint] = price
            if mint not in self._history:
                self._history[mint] = deque(maxlen=500)
            self._history[mint].append((now, price))

        # WSOL wraps 1:1 — use DexScreener SOL/USDC for canonical price & PnL.
        from config import SOL_MINT

        if SOL_MINT in mints:
            try:
                from sol_trend_filter import get_sol_trend_snapshot

                snap = get_sol_trend_snapshot()
                sol_usd = snap.get("sol_price_usd")
                if sol_usd and float(sol_usd) > 0:
                    sol_usd = float(sol_usd)
                    result[SOL_MINT] = sol_usd
                    self._dex_prices[SOL_MINT] = sol_usd
                    if SOL_MINT not in self._history:
                        self._history[SOL_MINT] = deque(maxlen=500)
                    self._history[SOL_MINT].append((now, sol_usd))
            except Exception:
                logger.debug("SOL/USDC price fallback unavailable for WSOL", exc_info=True)

        return result

    def get_trough_price_since(self, mint: str, since_ts: float) -> Optional[float]:
        """Lowest price seen in feed history since since_ts (catches between-poll drops)."""
        history = self._history.get(mint)
        if not history:
            return None
        trough = None
        for ts, price in history:
            if ts >= since_ts and price > 0:
                if trough is None or price < trough:
                    trough = price
        return trough

    def update_for_positions(self, mints: List[str]) -> Dict[str, float]:
        """Fetch prices for open positions — always merge DexScreener, use lower price."""
        now = time.time()
        jupiter_prices = self._fetch_jupiter_prices(mints)
        dex_prices = self._fetch_dexscreener_prices(mints)

        result: Dict[str, float] = {}
        for mint in mints:
            j_price = jupiter_prices.get(mint)
            d_price = dex_prices.get(mint)
            if j_price and d_price and j_price > 0 and d_price > 0:
                price = min(j_price, d_price)
            else:
                price = j_price or d_price or self._dex_prices.get(mint)
            if not price or price <= 0:
                continue
            result[mint] = price
            if mint not in self._history:
                self._history[mint] = deque(maxlen=500)
            self._history[mint].append((now, price))

        from config import SOL_MINT

        if SOL_MINT in mints:
            try:
                from sol_trend_filter import get_sol_trend_snapshot

                snap = get_sol_trend_snapshot()
                sol_usd = snap.get("sol_price_usd")
                if sol_usd and float(sol_usd) > 0:
                    sol_usd = float(sol_usd)
                    result[SOL_MINT] = sol_usd
                    self._dex_prices[SOL_MINT] = sol_usd
                    if SOL_MINT not in self._history:
                        self._history[SOL_MINT] = deque(maxlen=500)
                    self._history[SOL_MINT].append((now, sol_usd))
            except Exception:
                logger.debug("SOL/USDC price fallback unavailable for WSOL", exc_info=True)

        return result

    def update_with_retry(self, mints: List[str], retries: int = 2) -> Dict[str, float]:
        """Fetch prices with retries for open-position monitoring."""
        result = self.update_for_positions(mints)
        if self._jupiter_client.is_rate_limited():
            return result
        for attempt in range(retries):
            missing = [m for m in mints if m not in result]
            if not missing:
                break
            time.sleep(0.35 * (attempt + 1))
            retry_prices = self.update_for_positions(missing)
            result.update(retry_prices)
        return result

    def get_baseline(self, mint: str, window_sec: Optional[int] = None) -> Optional[float]:
        window = window_sec or Config.BASELINE_WINDOW_SEC
        history = self._history.get(mint)
        if not history:
            return None

        now = time.time()
        cutoff = now - window
        baseline_points = [price for ts, price in history if ts <= cutoff]
        if baseline_points:
            return baseline_points[-1]

        return history[0][1]

    def get_session_open(self, mint: str) -> Optional[float]:
        """First tracked price for mint since this bot session started polling it."""
        history = self._history.get(mint)
        if not history:
            return None
        return history[0][1]

    def get_momentum(self, mint: str, current_price: float) -> Optional[float]:
        baseline = self.get_baseline(mint)
        if not baseline or baseline <= 0:
            return None
        return (current_price - baseline) / baseline

    def get_latest(self, mint: str) -> Optional[float]:
        history = self._history.get(mint)
        if not history:
            return None
        return history[-1][1]

    def get_peak_price_since(self, mint: str, since_ts: float) -> Optional[float]:
        """Highest price seen in feed history since since_ts (catches between-poll spikes)."""
        history = self._history.get(mint)
        if not history:
            return None
        peak = 0.0
        for ts, price in history:
            if ts >= since_ts and price > peak:
                peak = price
        return peak if peak > 0 else None

    def _points_in_range(
        self, mint: str, start_ts: float, end_ts: float
    ) -> List[Tuple[float, float]]:
        history = self._history.get(mint)
        if not history:
            return []
        return [(ts, price) for ts, price in history if start_ts <= ts <= end_ts]

    def get_window_momentum(
        self, mint: str, end_offset_sec: float = 0, window_sec: float = 30
    ) -> Optional[float]:
        """Price change fraction over a window ending end_offset_sec ago."""
        now = time.time()
        window_end = now - end_offset_sec
        window_start = window_end - window_sec
        points = self._points_in_range(mint, window_start, window_end)
        if len(points) < 2:
            history = self._history.get(mint)
            if not history or len(history) < 2:
                return None
            points = list(history)[-2:]
        start_price = points[0][1]
        end_price = points[-1][1]
        if start_price <= 0:
            return None
        return (end_price - start_price) / start_price

    def get_poll_momentum_series(
        self, mint: str, poll_sec: int, count: int = 4
    ) -> List[float]:
        """Momentum for the last N poll-length intervals (oldest first)."""
        series: List[float] = []
        for i in range(count - 1, -1, -1):
            end_offset = i * poll_sec
            m = self.get_window_momentum(mint, end_offset_sec=end_offset, window_sec=poll_sec)
            if m is not None:
                series.insert(0, m)
        return series

    def momentum_declining_streak(
        self, mint: str, poll_sec: int, min_streak: int = 2
    ) -> bool:
        series = self.get_poll_momentum_series(mint, poll_sec, count=min_streak + 1)
        if len(series) < min_streak + 1:
            return False
        streak = 0
        for i in range(1, len(series)):
            if series[i] < series[i - 1]:
                streak += 1
                if streak >= min_streak:
                    return True
            else:
                streak = 0
        return False

    def get_peak_momentum_since(self, mint: str, since_ts: float) -> Optional[float]:
        history = self._history.get(mint)
        if not history:
            return None
        points = [(ts, p) for ts, p in history if ts >= since_ts]
        if len(points) < 2:
            return None
        peak = 0.0
        for i in range(1, len(points)):
            base = points[i - 1][1]
            if base > 0:
                m = abs((points[i][1] - base) / base)
                peak = max(peak, m)
        return peak if peak > 0 else None
