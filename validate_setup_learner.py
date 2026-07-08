"""Validate setup learning: record, score, persistence, win/loss bias, centroids."""

import json
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from config import Config
from scanner import MoverCandidate
from setup_learner import (
    STORE_VERSION,
    SetupLearner,
    _cosine_similarity,
    normalize_setup_features,
)


def _candidate(
    *,
    momentum_pct: float = 0.02,
    liquidity_usd: float = 50000.0,
    volume_24h_usd: float = 100000.0,
    price_change_5m: float = 0.02,
    price_change_1h: float = 0.01,
    source: str = "dexscreener",
    mint: str = "mint1234567890123456789012345678901234",
) -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol="TEST",
        name="TEST",
        pair_address="pair",
        dex="raydium",
        price_usd=0.001,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd,
        momentum_pct=momentum_pct,
        price_change_5m=price_change_5m,
        price_change_1h=price_change_1h,
        source=source,
    )


def _win_features(**overrides) -> dict:
    base = {
        "momentum_pct": 0.04,
        "liquidity_usd": 80000.0,
        "volume_24h_usd": 200000.0,
        "price_change_5m": 0.03,
        "price_change_1h": 0.02,
        "price_change_6h": 0.01,
        "price_change_24h": 0.05,
        "entry_price_impact_pct": 0.5,
        "is_pumpfun_route": False,
        "hold_time_sec": 120.0,
        "scanner_source": "gmgn",
    }
    base.update(overrides)
    return base


def _loss_features(**overrides) -> dict:
    return _win_features(
        momentum_pct=0.008,
        liquidity_usd=20000.0,
        volume_24h_usd=40000.0,
        price_change_5m=0.005,
        price_change_1h=0.003,
        entry_price_impact_pct=1.2,
        is_pumpfun_route=True,
        scanner_source="pumpfun",
        **overrides,
    )


def _fresh_store(tmp: str, name: str = "setup_learning.json") -> Path:
    store = Path(tmp) / name
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        json.dumps({"history": [], "bootstrapped": True, "version": STORE_VERSION}),
        encoding="utf-8",
    )
    return store


def _config_patches(**overrides):
    defaults = {
        "SETUP_LEARNING_ENABLED": True,
        "SETUP_LEARNING_MIN_TRADES": 1,
        "SETUP_LEARNING_MAX_HISTORY": 100,
        "SETUP_LEARNING_RAW_HISTORY": 50,
        "SETUP_LEARNING_CONDENSE_EVERY": 10,
        "SETUP_LEARNING_MAX_AGE_DAYS": 10,
        "SETUP_LEARNING_CENTROID_WEIGHT": 0.7,
        "SETUP_LEARNING_WIN_WEIGHT": 0.6,
        "SETUP_LEARNING_LOSS_WEIGHT": 0.4,
    }
    defaults.update(overrides)
    return [patch.object(Config, key, value) for key, value in defaults.items()]


def _run_with_config(tmp: str, store: Path, *, journal: str | None = None, **overrides):
    stack = ExitStack()
    journal_path = journal or str(Path(tmp) / "missing.jsonl")
    stack.enter_context(patch.object(Config, "TRADE_JOURNAL_PATH", journal_path))
    for patcher in _config_patches(**overrides):
        stack.enter_context(patcher)
    return stack


def _record_trade(learner: SetupLearner, win: bool, idx: int = 0) -> None:
    features = _win_features() if win else _loss_features()
    pnl = 0.01 if win else -0.01
    learner.record_completed_trade(
        features,
        pnl,
        pnl_pct=pnl,
        mint=f"mint{idx:04d}",
    )


def test_record_and_stats():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(tmp, store):
            learner = SetupLearner(store_path=store)
            learner.record_completed_trade(
                _win_features(), 0.01, pnl_pct=0.05, exit_reason="sell_instant_5pct"
            )
            stats = learner.get_stats()
            assert stats["win_count"] == 1
            assert stats["loss_count"] == 0
            assert stats["trades_learned"] == 1
            assert stats["learning_active"] is True
            assert stats["has_patterns"] is False
            assert stats["raw_history_count"] == 1
    print("PASS: record_and_stats")


def test_persistence_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(
            tmp,
            store,
            SETUP_LEARNING_MAX_HISTORY=10,
            SETUP_LEARNING_CONDENSE_EVERY=100,
        ):
            learner = SetupLearner(store_path=store)
            learner.record_completed_trade(_win_features(), 0.02, pnl_pct=0.04)
            learner.record_completed_trade(_loss_features(), -0.01, pnl_pct=-0.02)

            reloaded = SetupLearner(store_path=store)
            assert len(reloaded.history) == 2
            assert reloaded._wins()[0]["win"] is True
            assert reloaded._losses()[0]["win"] is False
            payload = json.loads(store.read_text(encoding="utf-8"))
            assert payload["version"] == STORE_VERSION
            assert store.parent.exists()
    print("PASS: persistence_round_trip")


def test_learning_inactive_below_min_trades():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(
            tmp,
            store,
            SETUP_LEARNING_MIN_TRADES=5,
            SETUP_LEARNING_CONDENSE_EVERY=100,
        ):
            learner = SetupLearner(store_path=store)
            for i in range(3):
                learner.record_completed_trade(
                    _win_features(), 0.01, pnl_pct=0.02, mint=f"mint{i}"
                )
            assert learner.learning_active is False
            high = _candidate(momentum_pct=0.05)
            low = _candidate(momentum_pct=0.01, mint="lowmint123456789012345678901234567")
            ranked = learner.rank([low, high])
            assert ranked[0].mint == high.mint
    print("PASS: learning_inactive_below_min_trades")


def test_win_loss_bias_scoring():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(
            tmp,
            store,
            SETUP_LEARNING_MIN_TRADES=3,
            SETUP_LEARNING_CONDENSE_EVERY=100,
        ):
            learner = SetupLearner(store_path=store)
            for _ in range(4):
                learner.record_completed_trade(_win_features(), 0.01, pnl_pct=0.03)
            for _ in range(4):
                learner.record_completed_trade(_loss_features(), -0.01, pnl_pct=-0.02)

            win_like = _candidate(momentum_pct=0.04, source="gmgn")
            loss_like = _candidate(
                momentum_pct=0.008,
                liquidity_usd=20000.0,
                volume_24h_usd=40000.0,
                price_change_5m=0.005,
                source="pumpfun",
                mint="losslike123456789012345678901234567890",
            )
            assert learner.score_candidate(win_like) > learner.score_candidate(loss_like)
            ranked = learner.rank([loss_like, win_like])
            assert ranked[0].mint == win_like.mint
    print("PASS: win_loss_bias_scoring")


def test_normalize_setup_features():
    vec = normalize_setup_features(
        {
            "momentum_pct": 0.02,
            "liquidity_usd": 1000.0,
            "volume_24h_usd": 5000.0,
            "is_pumpfun_route": True,
            "scanner_source": "pumpfun",
            "hold_time_sec": 7200.0,
        }
    )
    assert vec["is_pumpfun_route"] == 1.0
    assert vec["scanner_source"] == 0.2
    assert vec["hold_time_sec"] == 2.0
    print("PASS: normalize_setup_features")


def test_bootstrap_from_journal():
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "trades.jsonl"
        buy = {
            "action": "buy",
            "mint": "MintA",
            "symbol": "A",
            "momentum": 0.03,
            "price_impact_pct": 0.4,
            "timestamp": 1000.0,
        }
        sell = {
            "action": "sell",
            "mint": "MintA",
            "symbol": "A",
            "reason": "sell_take_profit",
            "net_pnl_sol": 0.005,
            "pnl_pct": 0.02,
            "timestamp": 1100.0,
        }
        journal.write_text(
            json.dumps(buy) + "\n" + json.dumps(sell) + "\n",
            encoding="utf-8",
        )
        store = Path(tmp) / "setup_learning.json"
        with _run_with_config(
            tmp,
            store,
            journal=str(journal),
            SETUP_LEARNING_CONDENSE_EVERY=100,
        ):
            learner = SetupLearner(store_path=store)
            assert len(learner.history) == 1
            assert learner.history[0]["win"] is True
            assert learner.history[0]["features"]["momentum_pct"] == 0.03
    print("PASS: bootstrap_from_journal")


def test_condense_every_n_trades():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(tmp, store, SETUP_LEARNING_CONDENSE_EVERY=5):
            learner = SetupLearner(store_path=store)
            for i in range(4):
                _record_trade(learner, win=True, idx=i)
            assert learner.has_patterns is False
            assert learner.trades_since_condense == 4

            _record_trade(learner, win=False, idx=4)
            assert learner.has_patterns is True
            assert learner.trades_since_condense == 0
            stats = learner.get_stats()
            assert stats["win_centroid_trades"] == 4
            assert stats["loss_centroid_trades"] == 1
            assert stats["last_condensed_at"] is not None
    print("PASS: condense_every_n_trades")


def test_history_trimmed_after_condense():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(
            tmp,
            store,
            SETUP_LEARNING_RAW_HISTORY=50,
            SETUP_LEARNING_CONDENSE_EVERY=60,
        ):
            learner = SetupLearner(store_path=store)
            for i in range(55):
                _record_trade(learner, win=(i % 2 == 0), idx=i)
            assert len(learner.history) <= 50
            assert learner.has_patterns is True
            stats = learner.get_stats()
            assert stats["raw_history_count"] == len(learner.history)
            assert stats["raw_history_count"] <= 50
    print("PASS: history_trimmed_after_condense")


def test_centroids_persist_after_trim():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(
            tmp,
            store,
            SETUP_LEARNING_RAW_HISTORY=10,
            SETUP_LEARNING_CONDENSE_EVERY=15,
        ):
            learner = SetupLearner(store_path=store)
            for i in range(15):
                _record_trade(learner, win=True, idx=i)
            assert len(learner.history) == 10
            win_centroid = dict(learner.patterns["win_centroid"])
            win_count = learner.patterns["win_count"]

            reloaded = SetupLearner(store_path=store)
            assert reloaded.has_patterns is True
            assert reloaded.patterns["win_count"] == win_count
            assert reloaded.patterns["win_centroid"] == win_centroid
            assert len(reloaded.history) == 10
    print("PASS: centroids_persist_after_trim")


def test_scoring_uses_centroids_when_available():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(
            tmp,
            store,
            SETUP_LEARNING_MIN_TRADES=3,
            SETUP_LEARNING_CONDENSE_EVERY=5,
            SETUP_LEARNING_CENTROID_WEIGHT=1.0,
        ):
            learner = SetupLearner(store_path=store)
            for i in range(5):
                _record_trade(learner, win=True, idx=i)
            assert learner.has_patterns is True

            win_like = _candidate(momentum_pct=0.04, source="gmgn")
            loss_like = _candidate(
                momentum_pct=0.008,
                liquidity_usd=20000.0,
                volume_24h_usd=40000.0,
                price_change_5m=0.005,
                source="pumpfun",
                mint="losslike123456789012345678901234567890",
            )
            candidate_vec = normalize_setup_features(
                {
                    "momentum_pct": win_like.momentum_pct,
                    "liquidity_usd": win_like.liquidity_usd,
                    "volume_24h_usd": win_like.volume_24h_usd,
                    "price_change_5m": win_like.price_change_5m,
                    "price_change_1h": win_like.price_change_1h,
                    "is_pumpfun_route": False,
                    "scanner_source": win_like.source,
                }
            )
            centroid_sim = _cosine_similarity(
                candidate_vec, learner.patterns["win_centroid"]
            )
            assert centroid_sim > 0.9
            assert learner.score_candidate(win_like) > learner.score_candidate(loss_like)
    print("PASS: scoring_uses_centroids_when_available")


def test_migration_from_v1_store():
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "setup_learning.json"
        history = []
        for i in range(12):
            features = normalize_setup_features(_win_features() if i % 2 == 0 else _loss_features())
            history.append(
                {
                    "recorded_at": time.time(),
                    "mint": f"mint{i}",
                    "features": features,
                    "net_pnl_sol": 0.01 if i % 2 == 0 else -0.01,
                    "win": i % 2 == 0,
                }
            )
        store.write_text(
            json.dumps(
                {
                    "version": 1,
                    "bootstrapped": True,
                    "history": history,
                }
            ),
            encoding="utf-8",
        )
        with _run_with_config(tmp, store, SETUP_LEARNING_RAW_HISTORY=50):
            learner = SetupLearner(store_path=store)
            assert learner.has_patterns is True
            assert len(learner.history) <= 50
            assert learner.patterns["win_count"] == 6
            assert learner.patterns["loss_count"] == 6
            payload = json.loads(store.read_text(encoding="utf-8"))
            assert payload["version"] == STORE_VERSION
            assert payload["patterns"] is not None
    print("PASS: migration_from_v1_store")


def test_reset_patterns():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with _run_with_config(tmp, store, SETUP_LEARNING_CONDENSE_EVERY=3):
            learner = SetupLearner(store_path=store)
            for i in range(3):
                _record_trade(learner, win=True, idx=i)
            assert learner.has_patterns is True
            history_len = len(learner.history)
            learner.reset_patterns()
            assert learner.has_patterns is False
            assert len(learner.history) == history_len
    print("PASS: reset_patterns")


if __name__ == "__main__":
    test_record_and_stats()
    test_persistence_round_trip()
    test_learning_inactive_below_min_trades()
    test_win_loss_bias_scoring()
    test_normalize_setup_features()
    test_bootstrap_from_journal()
    test_condense_every_n_trades()
    test_history_trimmed_after_condense()
    test_centroids_persist_after_trim()
    test_scoring_uses_centroids_when_available()
    test_migration_from_v1_store()
    test_reset_patterns()
    print("All setup learner validations passed.")
