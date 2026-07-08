"""Validate self-adjusting Steady Trade (regime-aware entry gates).

Proves the Steady Trade auto-adjust feature:
  (a) HOT regime yields the LOOSEST entry gates (more trades).
  (b) COLD regime yields the TIGHTEST entry gates; NEUTRAL sits in between.
  (c) Exits are byte-for-byte identical across hot / neutral / cold regimes,
      and regime gate dicts never expose any exit knob.
  (d) The STEADY_TRADE_AUTO_ADJUST master switch, when off, falls back to the
      classic static Steady baseline (no regime differentiation).
  (e) The spike / instant-dump gate stays ACTIVE in the hot regime (entries are
      only tightened/loosened — the dump protection is never weakened).

Entry selection ONLY — never stops, instant profit, 15-min hold, or forced sells.
"""

from unittest.mock import patch

from config import Config
from entry_filters import entry_winrate_skip_reason, spike_trap_reason
from market_regime import (
    REGIME_COLD,
    REGIME_HOT,
    REGIME_NEUTRAL,
    get_regime_gates,
    reset_market_regime_for_tests,
    update_market_regime,
)
from scanner import MoverCandidate


# Explicit, self-contained regime gate values (hot loosest -> cold tightest).
_HOT = {"entry": 0.004, "mom": 0.015, "vol": 45000.0}
_NEUTRAL = {"entry": 0.006, "mom": 0.018, "vol": 60000.0}
_COLD = {"entry": 0.0075, "mom": 0.020, "vol": 75000.0}
_BASELINE = {"entry": 0.004, "mom": 0.015, "vol": 45000.0}  # static Steady


def _candidate(mint: str = "mint1234567890123456789012345678901234") -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol="TEST",
        name="TEST",
        pair_address="pair",
        dex="test",
        price_usd=1.0,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.05,
        price_change_6h=0.02,
        price_change_24h=0.02,
        source="pumpfun",
    )


def _regime_ctx(auto_adjust: bool = True):
    """Config context: Steady's hot-market mode + auto-adjust with fixed gates."""
    return (
        patch.object(Config, "HOT_MARKET_MODE_ENABLED", True),
        patch.object(Config, "STEADY_TRADE_AUTO_ADJUST", auto_adjust),
        patch.object(Config, "SOL_MIN_CHANGE_1H_PCT", -1.5),
        patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5),
        patch.object(Config, "HOT_MARKET_SOL_MIN_1H_PCT", 0.5),
        patch.object(Config, "HOT_MARKET_SOL_MIN_4H_PCT", 0.0),
        patch.object(Config, "HOT_MARKET_MIN_SCANNER_CANDIDATES", 5),
        patch.object(Config, "HOT_MARKET_MIN_GMGN_VOLUME_USD", 0.0),
        patch.object(Config, "HOT_MARKET_ENTRY_MOMENTUM_PCT", _HOT["entry"]),
        patch.object(Config, "HOT_MARKET_MIN_MOMENTUM_PCT", _HOT["mom"]),
        patch.object(Config, "HOT_MARKET_MIN_VOLUME_24H_USD", _HOT["vol"]),
        patch.object(Config, "NEUTRAL_MARKET_ENTRY_MOMENTUM_PCT", _NEUTRAL["entry"]),
        patch.object(Config, "NEUTRAL_MARKET_MIN_MOMENTUM_PCT", _NEUTRAL["mom"]),
        patch.object(Config, "NEUTRAL_MARKET_MIN_VOLUME_24H_USD", _NEUTRAL["vol"]),
        patch.object(Config, "COLD_MARKET_ENTRY_MOMENTUM_PCT", _COLD["entry"]),
        patch.object(Config, "COLD_MARKET_MIN_MOMENTUM_PCT", _COLD["mom"]),
        patch.object(Config, "COLD_MARKET_MIN_VOLUME_24H_USD", _COLD["vol"]),
        # Baseline (used when auto-adjust is off) — deliberately loose.
        patch.object(Config, "ENTRY_MOMENTUM_PCT", _BASELINE["entry"]),
        patch.object(Config, "MIN_MOMENTUM_PCT", _BASELINE["mom"]),
        patch.object(Config, "MIN_VOLUME_24H_USD", _BASELINE["vol"]),
        patch.object(Config, "NON_WATCHLIST_MIN_VOLUME_24H_USD", _BASELINE["vol"]),
    )


class _ctx:
    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


def _hot_snap():
    return {"sol_trend_1h_pct": 1.0, "sol_trend_4h_pct": 0.5}


def _neutral_snap():
    # SOL positive (passes macro gate) but too few scanner candidates -> neutral.
    return {"sol_trend_1h_pct": 1.0, "sol_trend_4h_pct": 0.5}


def _cold_snap():
    return {"sol_trend_1h_pct": -2.0, "sol_trend_4h_pct": -2.0}


def _gates_for(snap, watchlist):
    update_market_regime(snap, watchlist)
    return get_regime_gates()


def test_hot_loosest_cold_tightest():
    reset_market_regime_for_tests()
    hot_wl = [_candidate(f"mint{i:032d}") for i in range(6)]
    thin_wl = [_candidate()]
    with _ctx(_regime_ctx(auto_adjust=True)):
        hot = _gates_for(_hot_snap(), hot_wl)
        neutral = _gates_for(_neutral_snap(), thin_wl)
        cold = _gates_for(_cold_snap(), hot_wl)

    # HOT is the loosest (lowest floors) — enables MORE trades.
    assert hot["entry_momentum_pct"] == _HOT["entry"], hot
    assert hot["entry_momentum_pct"] < neutral["entry_momentum_pct"] < cold["entry_momentum_pct"]
    assert hot["min_momentum_pct"] < neutral["min_momentum_pct"] < cold["min_momentum_pct"]
    assert hot["min_volume_24h_usd"] < neutral["min_volume_24h_usd"] < cold["min_volume_24h_usd"]
    # COLD is the tightest / most selective.
    assert cold["entry_momentum_pct"] == _COLD["entry"], cold
    assert cold["min_volume_24h_usd"] == _COLD["vol"], cold
    reset_market_regime_for_tests()
    print("PASS: hot=loosest, neutral=mid, cold=tightest entry gates")


def test_auto_adjust_off_is_static_baseline():
    reset_market_regime_for_tests()
    hot_wl = [_candidate(f"mint{i:032d}") for i in range(6)]
    with _ctx(_regime_ctx(auto_adjust=False)):
        hot = _gates_for(_hot_snap(), hot_wl)
        cold = _gates_for(_cold_snap(), hot_wl)
    # With the master switch off, every regime collapses to the static baseline.
    for gates in (hot, cold):
        assert gates["entry_momentum_pct"] == _BASELINE["entry"], gates
        assert gates["min_momentum_pct"] == _BASELINE["mom"], gates
        assert gates["min_volume_24h_usd"] == _BASELINE["vol"], gates
    reset_market_regime_for_tests()
    print("PASS: STEADY_TRADE_AUTO_ADJUST=false -> classic static Steady baseline")


def test_effective_methods_follow_regime():
    reset_market_regime_for_tests()
    hot_wl = [_candidate(f"mint{i:032d}") for i in range(6)]
    with _ctx(_regime_ctx(auto_adjust=True)):
        update_market_regime(_cold_snap(), hot_wl)
        gates = get_regime_gates()
        assert Config.effective_entry_momentum_pct() == gates["entry_momentum_pct"]
        assert Config.effective_min_momentum_pct() == gates["min_momentum_pct"]
        assert Config.effective_min_volume_24h_usd() == gates["min_volume_24h_usd"]
        assert Config.effective_entry_momentum_pct() == _COLD["entry"]
    reset_market_regime_for_tests()
    print("PASS: Config.effective_* entry gates follow the live regime")


def test_gate_dict_never_exposes_exits():
    reset_market_regime_for_tests()
    hot_wl = [_candidate(f"mint{i:032d}") for i in range(6)]
    with _ctx(_regime_ctx(auto_adjust=True)):
        for snap in (_hot_snap(), _cold_snap()):
            gates = _gates_for(snap, hot_wl)
            for key in gates:
                low = key.lower()
                assert "stop" not in low, key
                assert "profit" not in low, key
                assert "hold" not in low, key
                assert "forced" not in low and "force" not in low, key
                assert "exit" not in low, key
    reset_market_regime_for_tests()
    print("PASS: regime gate dict exposes entry floors only — never any exit knob")


def test_exits_identical_across_regimes():
    exit_attrs = (
        "STOP_LOSS_PCT",
        "WBTC_STOP_LOSS_PCT",
        "INSTANT_EXIT_3PCT",
        "INSTANT_PROFIT_EXIT_PCT",
        "EMERGENCY_STOP_LOSS_PCT",
        "CATASTROPHIC_STOP_LOSS_PCT",
        "MAX_HOLD_MINUTES_NON_WBTC",
        "MAX_HOLD_ENABLED",
    )
    present = tuple(a for a in exit_attrs if hasattr(Config, a))
    baseline = {a: getattr(Config, a) for a in present}
    hot_wl = [_candidate(f"mint{i:032d}") for i in range(6)]
    reset_market_regime_for_tests()
    with _ctx(_regime_ctx(auto_adjust=True)):
        for snap in (_hot_snap(), _neutral_snap(), _cold_snap()):
            update_market_regime(snap, hot_wl if snap is not _neutral_snap() else [_candidate()])
            for a in present:
                assert getattr(Config, a) == baseline[a], (snap, a)
    reset_market_regime_for_tests()
    print("PASS: stops / instant-profit / 15-min hold identical across all regimes")


def test_hot_regime_keeps_instant_dump_gate_active():
    reset_market_regime_for_tests()
    hot_wl = [_candidate(f"mint{i:032d}") for i in range(6)]
    stale_dump = MoverCandidate(
        mint="mint1234567890123456789012345678901234",
        symbol="DUMP", name="DUMP", pair_address="pair", dex="test",
        price_usd=1.0, liquidity_usd=50000.0, volume_24h_usd=100000.0,
        momentum_pct=400.0, price_change_5m=0.1, price_change_1h=0.1,
        price_change_6h=20.0, price_change_24h=15.0, source="pumpfun",
    )
    with _ctx((
        *_regime_ctx(auto_adjust=True),
        patch.object(Config, "SPIKE_TRAP_FILTER_ENABLED", True),
        patch.object(Config, "SPIKE_MIN_LIQUIDITY_USD", 8000.0),
        patch.object(Config, "SPIKE_FRESH_CONTINUATION_MIN_PCT", 5.0),
        patch.object(Config, "HIGH_MOMENTUM_QUALITY_PCT", 300.0),
        patch.object(Config, "MAX_ENTRY_MOMENTUM_PCT", 50000.0),
        patch.object(Config, "MAX_ENTRY_PRICE_CHANGE_5M_PCT", 50000.0),
    )):
        update_market_regime(_hot_snap(), hot_wl)  # force hot regime
        assert spike_trap_reason(stale_dump) is not None
        assert entry_winrate_skip_reason(stale_dump, None) is not None
    reset_market_regime_for_tests()
    print("PASS: hot regime keeps spike / instant-dump gate active (never weakened)")


def main():
    test_hot_loosest_cold_tightest()
    test_auto_adjust_off_is_static_baseline()
    test_effective_methods_follow_regime()
    test_gate_dict_never_exposes_exits()
    test_exits_identical_across_regimes()
    test_hot_regime_keeps_instant_dump_gate_active()
    print("\nAll Steady Trade auto-adjust validations passed.")


if __name__ == "__main__":
    main()
