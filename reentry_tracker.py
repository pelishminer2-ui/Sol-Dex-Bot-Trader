"""Track per-mint exit prices for dip re-entry (REENTRY_DIP_PCT from last exit)."""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import Config
from scanner import MoverCandidate


@dataclass
class ExitRecord:
    mint: str
    symbol: str
    last_exit_price: float
    last_exit_time: float


class ReentryTracker:
    def __init__(self):
        self._exits: Dict[str, ExitRecord] = {}

    def record_exit(self, mint: str, exit_price: float, symbol: str = ""):
        if exit_price <= 0:
            return
        self._exits[mint] = ExitRecord(
            mint=mint,
            symbol=symbol or mint[:8],
            last_exit_price=exit_price,
            last_exit_time=time.time(),
        )

    def get_record(self, mint: str) -> Optional[ExitRecord]:
        return self._exits.get(mint)

    def get_tracked_mints(self) -> List[str]:
        return list(self._exits.keys())

    def dip_threshold_price(self, mint: str) -> Optional[float]:
        record = self._exits.get(mint)
        if not record or record.last_exit_price <= 0:
            return None
        return record.last_exit_price * (1.0 - Config.REENTRY_DIP_PCT)

    def is_dip_reentry(self, mint: str, current_price: float) -> bool:
        threshold = self.dip_threshold_price(mint)
        if threshold is None or current_price <= 0:
            return False
        return current_price <= threshold

    def to_candidate(self, mint: str, current_price: float = 0.0) -> Optional[MoverCandidate]:
        record = self._exits.get(mint)
        if not record:
            return None
        return MoverCandidate(
            mint=mint,
            symbol=record.symbol,
            name=record.symbol,
            pair_address="",
            dex="reentry",
            price_usd=current_price or record.last_exit_price,
            liquidity_usd=0.0,
            volume_24h_usd=0.0,
            momentum_pct=0.0,
            price_change_5m=0.0,
            price_change_1h=0.0,
            source="reentry",
        )
