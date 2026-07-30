"""Validate short-horizon structure proxies + setup learning wiring."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from config import Config
from entry_filters import double_top_reason, entry_winrate_skip_reason
from pattern_structure import (
    compute_structure_scores,
    structure_preference_score,
)
from scanner import MoverCandidate
from setup_learner import (
    STORE_VERSION,
    SetupLearner,
    normalize_setup_features,
)


def _candidate(**kwargs) -> MoverCandidate:
    defaults = dict(
        mint="mint1234567890123456789012345678901234",
        symbol="TEST",
        name="TEST",
        pair_address="pair",
        dex="raydium",
        price_usd=0.001,
        liquidity_usd=50000.0,
        volume_24h_usd=250000.0,
        momentum_pct=0.5,
        price_change_5m=0.05,
        price_change_1h=0.4,
        price_change_6h=0.0,
        price_change_24h=0.0,
        source="pumpfun",
    )
    defaults.update(kwargs)
    return MoverCandidate(**defaults)


def test_friend_sass_blue_sky_shape():
    """Gold Instant exemplars: fresh 1h pop, flat 6h/24h, expanding volume."""
    # friend-like (mixed scales: 5m fraction, 1h percent points)
    friend = compute_structure_scores(
        price_change_5m=0.117,
        price_change_1h=81.8,
        price_change_6h=0.0,
        price_change_24h=0.0,
        liquidity_usd=30500.0,
        volume_24h_usd=475000.0,
    )
    sass = compute_structure_scores(
        price_change_5m=0.285,
        price_change_1h=81.08,
        price_change_6h=0.0,
        price_change_24h=0.0,
        liquidity_usd=31000.0,
        volume_24h_usd=1_050_000.0,
    )
    assert friend["blue_sky_score"] >= 0.55
    assert sass["blue_sky_score"] >= 0.55
    assert friend["double_top_score"] < 0.15
    assert sass["double_top_score"] < 0.15
    assert friend["structure_edge"] > 0.4
    assert sass["structure_edge"] > 0.4
    print("PASS: friend_sass_blue_sky_shape")


def test_double_top_scores_high_on_m_top():
    # Extended uptrend then 5m rejection at highs.
    scores = compute_structure_scores(
        price_change_5m=-0.12,
        price_change_1h=0.18,
        price_change_6h=0.55,
        price_change_24h=0.90,
        liquidity_usd=40000.0,
        volume_24h_usd=80000.0,
    )
    assert scores["double_top_score"] >= 0.65
    assert scores["blue_sky_score"] < 0.35
    print("PASS: double_top_scores_high_on_m_top")


def test_flag_and_cup_proxies_fire():
    flag = compute_structure_scores(
        price_change_5m=0.03,
        price_change_1h=0.35,
        price_change_6h=0.40,
        price_change_24h=0.50,
        liquidity_usd=60000.0,
        volume_24h_usd=400000.0,
    )
    cup = compute_structure_scores(
        price_change_5m=0.04,
        price_change_1h=0.12,
        price_change_6h=0.05,
        price_change_24h=0.45,
        liquidity_usd=70000.0,
        volume_24h_usd=500000.0,
    )
    assert flag["flag_continuation_score"] > 0.35
    assert cup["cup_handle_score"] > 0.35
    print("PASS: flag_and_cup_proxies_fire")


def test_normalize_includes_structure_keys():
    vec = normalize_setup_features(
        {
            "momentum_pct": 81.8,
            "liquidity_usd": 30500.0,
            "volume_24h_usd": 475000.0,
            "price_change_5m": 0.117,
            "price_change_1h": 81.8,
            "price_change_6h": 0.0,
            "price_change_24h": 0.0,
            "is_pumpfun_route": True,
            "scanner_source": "pumpfun",
            "hold_time_sec": 180.0,
        }
    )
    for key in (
        "blue_sky_score",
        "flag_continuation_score",
        "cup_handle_score",
        "ascending_triangle_score",
        "volume_expansion_score",
        "double_top_score",
        "structure_edge",
    ):
        assert key in vec
    assert vec["blue_sky_score"] >= 0.55
    assert vec["double_top_score"] < 0.15
    print("PASS: normalize_includes_structure_keys")


def test_double_top_gate_skips_m_top_allows_blue_sky():
    with ExitStack() as stack:
        stack.enter_context(patch.object(Config, "STRUCTURE_DOUBLE_TOP_GATE_ENABLED", True))
        stack.enter_context(patch.object(Config, "STRUCTURE_DOUBLE_TOP_MAX", 0.65))
        stack.enter_context(patch.object(Config, "SPIKE_TRAP_FILTER_ENABLED", False))
        stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", False))
        m_top = _candidate(
            symbol="MTOP",
            momentum_pct=90.0,
            price_change_5m=-0.12,
            price_change_1h=0.18,
            price_change_6h=0.55,
            price_change_24h=0.90,
            source="dexscreener",
        )
        blue = _candidate(
            symbol="friend",
            momentum_pct=81.8,
            price_change_5m=0.117,
            price_change_1h=81.8,
            price_change_6h=0.0,
            price_change_24h=0.0,
            source="pumpfun",
        )
        assert double_top_reason(m_top) is not None
        assert double_top_reason(blue) is None
        assert entry_winrate_skip_reason(m_top, None) is not None
        assert entry_winrate_skip_reason(blue, None) is None
    print("PASS: double_top_gate_skips_m_top_allows_blue_sky")


def test_instant_win_weight_pulls_centroid():
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "setup_learning.json"
        store.write_text(
            json.dumps({"history": [], "bootstrapped": True, "version": STORE_VERSION}),
            encoding="utf-8",
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(Config, "TRADE_JOURNAL_PATH", str(Path(tmp) / "missing.jsonl"))
            )
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENABLED", True))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_MIN_TRADES", 1))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_CONDENSE_EVERY", 3))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_INSTANT_WIN_WEIGHT", 3.0))
            stack.enter_context(patch.object(Config, "STRUCTURE_ENTRY_BOOST_ENABLED", True))
            learner = SetupLearner(store_path=store)
            # One Instant blue-sky win (friend-like) + one dull non-instant win.
            instant = {
                "momentum_pct": 81.8,
                "liquidity_usd": 30500.0,
                "volume_24h_usd": 475000.0,
                "price_change_5m": 0.117,
                "price_change_1h": 81.8,
                "price_change_6h": 0.0,
                "price_change_24h": 0.0,
                "is_pumpfun_route": True,
                "scanner_source": "pumpfun",
                "hold_time_sec": 180.0,
            }
            dull = {
                "momentum_pct": 0.05,
                "liquidity_usd": 80000.0,
                "volume_24h_usd": 100000.0,
                "price_change_5m": 0.01,
                "price_change_1h": 0.02,
                "price_change_6h": 0.03,
                "price_change_24h": 0.04,
                "is_pumpfun_route": False,
                "scanner_source": "dexscreener",
                "hold_time_sec": 600.0,
            }
            learner.record_completed_trade(
                instant, 0.01, pnl_pct=0.05, exit_reason="sell_instant_5pct", mint="f1", symbol="friend"
            )
            learner.record_completed_trade(
                dull, 0.01, pnl_pct=0.02, exit_reason="sell_time_stop", mint="d1", symbol="dull"
            )
            learner.record_completed_trade(
                dull, -0.01, pnl_pct=-0.02, exit_reason="sell_stop_loss", mint="l1", symbol="loss"
            )
            assert learner.has_patterns
            assert learner.patterns["win_centroid"]["blue_sky_score"] > 0.35
            pop = _candidate(
                momentum_pct=81.8,
                price_change_5m=0.12,
                price_change_1h=81.8,
                price_change_6h=0.0,
                price_change_24h=0.0,
                source="pumpfun",
            )
            flat = _candidate(
                momentum_pct=0.05,
                price_change_5m=0.01,
                price_change_1h=0.02,
                price_change_6h=0.03,
                price_change_24h=0.04,
                source="dexscreener",
                mint="flatmint123456789012345678901234567890",
            )
            assert structure_preference_score(pop) > structure_preference_score(flat)
            assert learner.score_candidate(pop) > learner.score_candidate(flat)
    print("PASS: instant_win_weight_pulls_centroid")


def test_store_version_is_v4():
    assert STORE_VERSION >= 4
    print("PASS: store_version_is_v4")


if __name__ == "__main__":
    test_friend_sass_blue_sky_shape()
    test_double_top_scores_high_on_m_top()
    test_flag_and_cup_proxies_fire()
    test_normalize_includes_structure_keys()
    test_double_top_gate_skips_m_top_allows_blue_sky()
    test_instant_win_weight_pulls_centroid()
    test_store_version_is_v4()
    print("All pattern structure validations passed.")
