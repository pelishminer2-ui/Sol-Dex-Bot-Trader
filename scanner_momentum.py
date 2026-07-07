"""Scanner discovery momentum using DexScreener native priceChange windows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

# DexScreener pair.priceChange keys (percent points, not fractions).
DEXSCREENER_MOMENTUM_KEYS = ("m5", "h1", "h6", "h24")


@dataclass(frozen=True)
class ScannerPriceChanges:
    change_5m: float = 0.0
    change_1h: float = 0.0
    change_6h: float = 0.0
    change_24h: float = 0.0

    def discovery_momentum(self) -> float:
        return max(self.change_5m, self.change_1h, self.change_6h, self.change_24h)

    def as_dict(self) -> Dict[str, float]:
        return {
            "price_change_5m": self.change_5m,
            "price_change_1h": self.change_1h,
            "price_change_6h": self.change_6h,
            "price_change_24h": self.change_24h,
        }


def _pct_to_fraction(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return 0.0


def price_changes_from_dexscreener(price_change: Optional[Mapping[str, Any]]) -> ScannerPriceChanges:
    """Parse DexScreener priceChange {m5, h1, h6, h24} — no estimates."""
    pc = price_change or {}
    return ScannerPriceChanges(
        change_5m=_pct_to_fraction(pc.get("m5")),
        change_1h=_pct_to_fraction(pc.get("h1")),
        change_6h=_pct_to_fraction(pc.get("h6")),
        change_24h=_pct_to_fraction(pc.get("h24")),
    )


def price_changes_from_external(values: Mapping[str, Any]) -> ScannerPriceChanges:
    """
    Map third-party scanner fields onto DexScreener windows when possible.
    Uses only provided values — no fabricated 3m/15m estimates.
    """
    def _get(*keys: str) -> float:
        for key in keys:
            if key in values and values[key] is not None:
                return _pct_to_fraction(values[key])
        return 0.0

    return ScannerPriceChanges(
        change_5m=_get(
            "price_change_percent5m",
            "price_change_5m_percent",
            "priceChange5mPercent",
            "price_change_5m",
            "m5",
        ),
        change_1h=_get(
            "price_change_percent1h",
            "price_change_1h_percent",
            "priceChange1hPercent",
            "price_change_1h",
            "h1",
        ),
        change_6h=_get(
            "price_change_percent6h",
            "price_change_6h_percent",
            "priceChange6hPercent",
            "price_change_6h",
            "h6",
        ),
        change_24h=_get(
            "price_change_percent24h",
            "price_change_24h_percent",
            "priceChange24hPercent",
            "price_change_24h",
            "price_change_h24_pct",
            "h24",
        ),
    )


def discovery_momentum(
    change_5m: float,
    change_1h: float,
    change_6h: float,
    change_24h: float,
) -> float:
    return max(change_5m, change_1h, change_6h, change_24h)
