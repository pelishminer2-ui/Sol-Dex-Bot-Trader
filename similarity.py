import logging
import math
from typing import Dict, List, Optional

from scanner import MoverCandidate
from strategy import TradeProfile

logger = logging.getLogger(__name__)

FEATURE_KEYS = [
    "momentum_pct",
    "liquidity_usd",
    "volume_24h_usd",
    "price_change_5m",
    "price_change_1h",
]


def _candidate_vector(candidate: MoverCandidate) -> Dict[str, float]:
    return {
        "momentum_pct": candidate.momentum_pct,
        "liquidity_usd": math.log10(max(candidate.liquidity_usd, 1)),
        "volume_24h_usd": math.log10(max(candidate.volume_24h_usd, 1)),
        "price_change_5m": candidate.price_change_5m,
        "price_change_1h": candidate.price_change_1h,
    }


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in FEATURE_KEYS)
    norm_a = math.sqrt(sum(a.get(k, 0) ** 2 for k in FEATURE_KEYS))
    norm_b = math.sqrt(sum(b.get(k, 0) ** 2 for k in FEATURE_KEYS))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SimilarityScorer:
    """Rank movers by similarity to the last profitable trade profile."""

    def __init__(self):
        self.reference: Optional[Dict[str, float]] = None

    def set_reference(self, profile: TradeProfile):
        self.reference = profile.to_vector()
        logger.info("Similarity reference set from profitable trade: %s", profile.symbol)

    def score(self, candidate: MoverCandidate) -> float:
        if not self.reference:
            return candidate.momentum_pct
        candidate_vec = _candidate_vector(candidate)
        similarity = _cosine_similarity(self.reference, candidate_vec)
        return similarity * 0.6 + candidate.momentum_pct * 0.4

    def _discovery_score(self, candidate: MoverCandidate) -> float:
        """Prefer movers with both momentum and volume when no profitable reference."""
        mom = max(candidate.momentum_pct, 0.0)
        vol = math.log10(max(candidate.volume_24h_usd, 1))
        return mom * 0.7 + vol * 0.001

    def rank(self, candidates: List[MoverCandidate]) -> List[MoverCandidate]:
        if not candidates:
            return []
        if not self.reference:
            return sorted(candidates, key=self._discovery_score, reverse=True)
        return sorted(candidates, key=self.score, reverse=True)
