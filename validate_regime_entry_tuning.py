"""Validate regime-aware ENTRY tuning.

Covers, without ever changing exits/learning:
  * SOL 1h gate loosened to -1.5 (4h stays -1.5 as a hard block).
  * Pop-quality override lets a proven-shape pop bypass the 1h gate only.
  * Junk (thin / stale / reversing / wrong route) never bypasses the gate.
  * Deep dumps (4h below floor) stay blocked even for quality pops.
  * Hot regime keeps the instant-dump / spike-trap gate ACTIVE.
  * Stops and profit targets are identical in cold / neutral / hot regimes.
"""

from unittest.mock import patch

from config import Config
from entry_filters import (
    entry_winrate_skip_reason,
    sol_trend_quality_override_passes,
    spike_trap_reason,
)
from scanner import MoverCandidate
from sol_trend_filter import (
    memecoin_entry_allowed_by_sol_trend,
    reset_sol_trend_state_for_tests,
)


def _candidate(
    *,
    symbol="POP",
    source="pumpfun",
    liquidity_usd=50000.0,
    momentum_pct=400.0,
    change_5m=30.0,
    change_1h=30.0,
    change_6h=20.0,
    change_24h=15.0,
):
    return MoverCandidate(
        mint="mint1234567890123456789012345678901234",
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="test",
        price_usd=1.0,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=100000.0,
        momentum_pct=momentum_pct,
        price_change_5m=change_5m,
        price_change_1h=change_1h,
        price_change_6h=change_6h,
        price_change_24h=change_24h,
        source=source,
    )


def _snap(h1, h4):
    return {"data_available": True, "sol_trend_1h_pct": h1, "sol_trend_4h_pct": h4}


def _entry_ctx():
    """Config context matching the new bottom line for entry gates."""
    return (
        patch.object(Config, "SOL_TREND_FILTER_ENABLED", True),
        patch.object(Config, "SOL_MIN_CHANGE_1H_PCT", -1.5),
        patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5),
        patch.object(Config, "SOL_TREND_QUALITY_OVERRIDE_ENABLED", True),
        patch.object(Config, "SPIKE_TRAP_FILTER_ENABLED", True),
        patch.object(Config, "SPIKE_MIN_LIQUIDITY_USD", 8000.0),
        patch.object(Config, "SPIKE_FRESH_CONTINUATION_MIN_PCT", 5.0),
        patch.object(Config, "HIGH_MOMENTUM_QUALITY_PCT", 300.0),
        patch.object(Config, "MAX_ENTRY_MOMENTUM_PCT", 50000.0),
        patch.object(Config, "MAX_ENTRY_PRICE_CHANGE_5M_PCT", 50000.0),
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


def test_config_is_new_bottom_line():
    assert Config.SOL_MIN_CHANGE_1H_PCT == -1.5, Config.SOL_MIN_CHANGE_1H_PCT
    assert Config.SOL_MIN_CHANGE_4H_PCT == -1.5, Config.SOL_MIN_CHANGE_4H_PCT
    assert Config.SOL_TREND_QUALITY_OVERRIDE_ENABLED is True
    print("PASS: config reflects new bottom line (1h=-1.5, override on, 4h=-1.5)")


def test_mild_dip_allowed_directly():
    with _ctx(_entry_ctx()):
        allowed, reason = memecoin_entry_allowed_by_sol_trend(
            _snap(-1.2, -1.0), candidate=_candidate()
        )
    assert allowed is True, reason
    print("PASS: cold SOL 1h -1.2% mild dip passes gate directly")


def test_quality_pop_overrides_1h_block():
    # 1h -1.7% is below the -1.5 gate; a quality pop bypasses via override.
    with _ctx(_entry_ctx()):
        allowed, reason = memecoin_entry_allowed_by_sol_trend(
            _snap(-1.7, -1.0), candidate=_candidate()
        )
    assert allowed is True, reason
    print("PASS: quality pop overrides SOL 1h block (1h -1.7% < -1.5)")


def test_junk_never_overrides():
    cases = {
        "thin liquidity": _candidate(liquidity_usd=2000.0),
        "stale spike": _candidate(change_5m=0.1, change_1h=0.1),
        "reversing": _candidate(change_6h=-10.0, change_24h=-20.0),
        "wrong route": _candidate(source="dexscreener"),
    }
    with _ctx(_entry_ctx()):
        for label, cand in cases.items():
            assert sol_trend_quality_override_passes(cand) is False, label
            allowed, reason = memecoin_entry_allowed_by_sol_trend(
                _snap(-1.7, -1.0), candidate=cand
            )
            assert allowed is False, f"{label} should stay blocked"
            assert reason and "1h" in reason
    print("PASS: junk (thin/stale/reversing/wrong-route) never overrides 1h gate")


def test_deep_dump_4h_hard_block_not_bypassed():
    # 4h -2.0% is below the -1.5 hard block; even a quality pop stays blocked.
    with _ctx(_entry_ctx()):
        allowed, reason = memecoin_entry_allowed_by_sol_trend(
            _snap(-1.7, -2.0), candidate=_candidate()
        )
    assert allowed is False, "4h hard block must not be bypassed"
    assert reason and "4h" in reason, reason
    print("PASS: 4h sustained-downtrend hard block cannot be bypassed by override")


def test_hot_regime_keeps_instant_dump_gate_active():
    # A high-momentum dump-signature candidate must still be blocked while
    # Hot Market Mode is enabled (pop/drop protection stays on in hot regime).
    stale_dump = _candidate(symbol="DUMP", change_5m=0.1, change_1h=0.1)
    reversing_dump = _candidate(symbol="REV", change_6h=-10.0, change_24h=-20.0)
    with _ctx((*_entry_ctx(), patch.object(Config, "HOT_MARKET_MODE_ENABLED", True))):
        assert spike_trap_reason(stale_dump) is not None
        assert spike_trap_reason(reversing_dump) is not None
        assert entry_winrate_skip_reason(stale_dump, None) is not None
    print("PASS: hot regime keeps instant-dump / spike-trap gate active")


def test_exits_identical_across_regimes():
    import market_regime as mr
    from scanner import MoverCandidate  # noqa: F401

    exit_attrs = (
        "STOP_LOSS_PCT",
        "WBTC_STOP_LOSS_PCT",
        "INSTANT_EXIT_3PCT",
        "INSTANT_PROFIT_EXIT_PCT",
        "EMERGENCY_STOP_LOSS_PCT",
        "CATASTROPHIC_STOP_LOSS_PCT",
    )
    baseline = {a: getattr(Config, a) for a in exit_attrs}

    with patch.object(Config, "HOT_MARKET_MODE_ENABLED", True):
        for regime, snap in (
            (mr.REGIME_COLD, _snap(-2.0, -2.0)),
            (mr.REGIME_NEUTRAL, _snap(0.0, 0.0)),
            (mr.REGIME_HOT, _snap(1.0, 1.0)),
        ):
            mr.update_market_regime(snap, [])
            gates = mr.get_regime_gates()
            # Regime gates only expose entry momentum/volume floors — never exits.
            for key in gates:
                assert "stop" not in key.lower(), key
                assert "profit" not in key.lower(), key
                assert "hold" not in key.lower(), key
            for a in exit_attrs:
                assert getattr(Config, a) == baseline[a], (regime, a)

    mr.reset_market_regime_for_tests()
    print("PASS: stops / profit targets identical across cold / neutral / hot")


def main():
    reset_sol_trend_state_for_tests()
    test_config_is_new_bottom_line()
    test_mild_dip_allowed_directly()
    test_quality_pop_overrides_1h_block()
    test_junk_never_overrides()
    test_deep_dump_4h_hard_block_not_bypassed()
    test_hot_regime_keeps_instant_dump_gate_active()
    test_exits_identical_across_regimes()
    print("\nAll regime entry-tuning validations passed.")


if __name__ == "__main__":
    main()
