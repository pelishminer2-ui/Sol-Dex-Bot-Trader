"""Validate SOL macro trend filter, pop-quality override, and one-strike loss rule."""

from contextlib import ExitStack
from unittest.mock import patch

from config import (
    DEFAULT_LOSS_ONE_STRIKE_PER_SESSION,
    DEFAULT_SOL_MIN_CHANGE_1H_PCT,
    DEFAULT_SOL_MIN_CHANGE_4H_PCT,
    DEFAULT_SOL_TREND_FILTER_ENABLED,
    DEFAULT_SOL_TREND_QUALITY_OVERRIDE_ENABLED,
    Config,
)
from entry_filters import spike_trap_reason, sol_trend_quality_override_passes
from scanner import MoverCandidate
from sol_trend_filter import (
    memecoin_entry_allowed_by_sol_trend,
    reset_sol_trend_state_for_tests,
    sol_trend_passes,
)
from strategy import MomentumStrategy


def _candidate(symbol: str = "TEST", mint: str = "mint1234567890123456789012345678901234") -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="test",
        price_usd=1.0,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.05,
    )


def _quality_pop(symbol: str = "POP", mint: str = "PopMint111111111111111111111111111111pump") -> MoverCandidate:
    """Proven-shape pop: Pump.fun route, liquid, fresh 5m/1h, not reversing."""
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="pumpswap",
        price_usd=0.001,
        liquidity_usd=60000.0,
        volume_24h_usd=200000.0,
        momentum_pct=30.0,
        price_change_5m=3.0,
        price_change_1h=30.0,
        price_change_6h=30.0,
        price_change_24h=30.0,
        source="pumpfun",
    )


def _junk_pop(symbol: str = "JUNK", mint: str = "JunkMint2222222222222222222222222222dead") -> MoverCandidate:
    """Not a quality pop: non-Pump.fun route + thin book (should stay blocked)."""
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="raydium",
        price_usd=0.001,
        liquidity_usd=2000.0,
        volume_24h_usd=50000.0,
        momentum_pct=30.0,
        price_change_5m=0.01,
        price_change_1h=0.02,
        price_change_6h=-0.2,
        price_change_24h=-0.3,
        source="dexscreener",
    )


def _spike_config_patches():
    return [
        patch.object(Config, "SPIKE_TRAP_FILTER_ENABLED", True),
        patch.object(Config, "MAX_ENTRY_MOMENTUM_PCT", 50000.0),
        patch.object(Config, "MAX_ENTRY_PRICE_CHANGE_5M_PCT", 50000.0),
        patch.object(Config, "HIGH_MOMENTUM_QUALITY_PCT", 300.0),
        patch.object(Config, "SPIKE_MIN_LIQUIDITY_USD", 8000.0),
        patch.object(Config, "SPIKE_FRESH_CONTINUATION_MIN_PCT", 5.0),
        patch.object(Config, "SPIKE_MAX_ROUNDTRIP_IMPACT_PCT", 0.0),
        patch.object(Config, "SOL_TREND_QUALITY_OVERRIDE_ENABLED", True),
    ]


def test_config_defaults():
    assert DEFAULT_SOL_TREND_FILTER_ENABLED is True
    assert DEFAULT_SOL_MIN_CHANGE_1H_PCT == -1.5
    assert DEFAULT_SOL_MIN_CHANGE_4H_PCT == -1.5
    assert DEFAULT_SOL_TREND_QUALITY_OVERRIDE_ENABLED is True
    assert DEFAULT_LOSS_ONE_STRIKE_PER_SESSION is True
    print("PASS: SOL trend config defaults (1h gate -1.5, quality override on)")


def test_gate_at_negative_1_5():
    """Unified 1h gate at -1.5: mild pullbacks trade, -2%+ blocks (no candidate)."""
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", True), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -1.5
    ), patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5):
        assert sol_trend_passes(-1.4, 0.0) is True
        assert sol_trend_passes(-1.6, 0.0) is False
        allowed_mild, _ = memecoin_entry_allowed_by_sol_trend(
            {"data_available": True, "sol_trend_1h_pct": -1.2, "sol_trend_4h_pct": 0.0}
        )
        allowed_deep, reason_deep = memecoin_entry_allowed_by_sol_trend(
            {"data_available": True, "sol_trend_1h_pct": -2.0, "sol_trend_4h_pct": 0.0}
        )
    assert allowed_mild is True
    assert allowed_deep is False and reason_deep and "1h" in reason_deep
    print("PASS: SOL 1h gate at -1.5 (mild passes, -2% blocks)")


def test_quality_override_allows_good_pop_when_cold():
    """A quality pop trades even when SOL 1h is below the gate (cold-ish tape)."""
    snap = {"data_available": True, "sol_trend_1h_pct": -1.8, "sol_trend_4h_pct": 0.0}
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(Config, "SOL_TREND_FILTER_ENABLED", True))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_1H_PCT", -1.5))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5))
        assert sol_trend_quality_override_passes(_quality_pop()) is True
        allowed, reason = memecoin_entry_allowed_by_sol_trend(snap, candidate=_quality_pop())
        # Without a candidate the same tape blocks.
        blocked, _ = memecoin_entry_allowed_by_sol_trend(snap)
    assert allowed is True and reason is None
    assert blocked is False
    print("PASS: quality pop overrides SOL 1h gate when cold")


def test_quality_override_blocks_junk_when_cold():
    """Junk (non-Pump.fun / thin / reversing) stays blocked when SOL is cold."""
    snap = {"data_available": True, "sol_trend_1h_pct": -1.8, "sol_trend_4h_pct": 0.0}
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(Config, "SOL_TREND_FILTER_ENABLED", True))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_1H_PCT", -1.5))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5))
        assert sol_trend_quality_override_passes(_junk_pop()) is False
        allowed, reason = memecoin_entry_allowed_by_sol_trend(snap, candidate=_junk_pop())
    assert allowed is False and reason and "1h" in reason
    print("PASS: junk pop stays blocked when cold")


def test_quality_override_cannot_bypass_4h_hard_block():
    """4h sustained downtrend is a hard block even for a quality pop."""
    snap = {"data_available": True, "sol_trend_1h_pct": -1.8, "sol_trend_4h_pct": -2.0}
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(Config, "SOL_TREND_FILTER_ENABLED", True))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_1H_PCT", -1.5))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5))
        allowed, reason = memecoin_entry_allowed_by_sol_trend(snap, candidate=_quality_pop())
    assert allowed is False and reason and "4h" in reason
    print("PASS: quality override cannot bypass 4h hard block")


def test_quality_override_disabled_blocks():
    """With the override disabled, a quality pop is still blocked when cold."""
    snap = {"data_available": True, "sol_trend_1h_pct": -1.8, "sol_trend_4h_pct": 0.0}
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(Config, "SOL_TREND_QUALITY_OVERRIDE_ENABLED", False))
        stack.enter_context(patch.object(Config, "SOL_TREND_FILTER_ENABLED", True))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_1H_PCT", -1.5))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5))
        assert sol_trend_quality_override_passes(_quality_pop()) is False
        allowed, reason = memecoin_entry_allowed_by_sol_trend(snap, candidate=_quality_pop())
    assert allowed is False and reason and "1h" in reason
    print("PASS: disabled override blocks quality pop")


def test_sol_trend_passes_thresholds():
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", True), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -0.5
    ), patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5):
        assert sol_trend_passes(-0.3, -1.0) is True
        assert sol_trend_passes(-0.6, 0.0) is False
        assert sol_trend_passes(0.0, -2.0) is False
    print("PASS: sol_trend_passes thresholds")


def test_memecoin_gate_blocks_dump():
    snap = {"data_available": True, "sol_trend_1h_pct": -1.2, "sol_trend_4h_pct": 0.5}
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", True), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -0.5
    ), patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5):
        allowed, reason = memecoin_entry_allowed_by_sol_trend(snap)
    assert allowed is False
    assert reason and "1h" in reason
    print("PASS: memecoin gate blocks SOL dump")


def test_memecoin_gate_disabled():
    snap = {"data_available": True, "sol_trend_1h_pct": -5.0, "sol_trend_4h_pct": -5.0}
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", False):
        allowed, reason = memecoin_entry_allowed_by_sol_trend(snap)
    assert allowed is True
    assert reason is None
    print("PASS: disabled filter allows entries")


def test_strategy_blocks_sol_macro_on_memecoin():
    strategy = MomentumStrategy()
    candidate = _candidate()
    snap = {"data_available": True, "sol_trend_1h_pct": -2.0, "sol_trend_4h_pct": 0.0}
    with patch.object(Config, "SOL_TREND_FILTER_ENABLED", True), patch.object(
        Config, "SOL_MIN_CHANGE_1H_PCT", -0.5
    ), patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5):
        signal = strategy.evaluate_entry(
            candidate, 1.0, 0.05, sol_trend_snapshot=snap
        )
        reason = strategy.entry_skip_reason(
            candidate, 0.05, sol_trend_snapshot=snap
        )
    from strategy import SignalType

    assert signal == SignalType.NONE
    assert reason and "SOL macro" in reason
    print("PASS: strategy blocks memecoin when SOL dumping")


def test_hot_regime_keeps_dump_gate_and_exits_intact():
    """Regime must not weaken the instant-dump gate or any exit. Hot mode only
    loosens entry momentum/volume floors — the spike/dump discriminator stays on
    and stop-loss / profit / 15-min hold / forced-exit config are unchanged."""
    from config import ALLOWED_STOP_LOSS_PCT
    from market_regime import (
        REGIME_COLD,
        REGIME_HOT,
        detect_market_regime,
        reset_market_regime_for_tests,
    )

    dump = MoverCandidate(
        mint="DumpMint3333333333333333333333333333dead",
        symbol="DUMP",
        name="DUMP",
        pair_address="pair",
        dex="raydium",
        price_usd=0.001,
        liquidity_usd=2000.0,
        volume_24h_usd=100000.0,
        momentum_pct=8660.0,
        price_change_5m=8660.0,
        price_change_1h=0.1,
        price_change_6h=-0.1,
        price_change_24h=-0.16,
        source="dexscreener",
    )
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(Config, "HOT_MARKET_MODE_ENABLED", True))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_1H_PCT", -1.5))
        stack.enter_context(patch.object(Config, "SOL_MIN_CHANGE_4H_PCT", -1.5))
        reset_market_regime_for_tests()
        # Hot tape (SOL positive) still runs the dump gate: dump stays blocked.
        hot_snap = {"sol_trend_1h_pct": 1.0, "sol_trend_4h_pct": 0.5}
        regime_hot = detect_market_regime(hot_snap, [])
        assert spike_trap_reason(dump) is not None, "dump gate weakened in hot regime"
        # Cold tape classified cold and still blocks the dump.
        cold_snap = {"sol_trend_1h_pct": -2.0, "sol_trend_4h_pct": 0.0}
        regime_cold = detect_market_regime(cold_snap, [])
        assert regime_cold == REGIME_COLD
        assert spike_trap_reason(dump) is not None
        # Exits never change with regime.
        assert Config.STOP_LOSS_PCT in ALLOWED_STOP_LOSS_PCT
        assert Config.MAX_HOLD_MINUTES_NON_WBTC == 15
        assert Config.INSTANT_EXIT_3PCT > 0 and Config.INSTANT_PROFIT_EXIT_PCT > 0
    reset_market_regime_for_tests()
    print(f"PASS: hot/cold regime keeps dump gate + exits intact (hot={regime_hot})")


def test_one_strike_blocks_repeat_loss_mint():
    strategy = MomentumStrategy()
    mint = "mint1234567890123456789012345678901234"
    with patch.object(Config, "LOSS_ONE_STRIKE_PER_SESSION", True), patch.object(
        Config, "LOSS_REENTRY_COOLDOWN_MINUTES", 0
    ):
        strategy.record_loss_reentry_cooldown(mint)
        assert strategy.is_on_loss_reentry_cooldown(mint) is True
        reason = strategy.entry_skip_reason(_candidate(mint=mint), 0.05)
    assert reason and "one-strike" in reason
    print("PASS: one-strike blocks repeat entry after loss")


def main():
    reset_sol_trend_state_for_tests()
    test_config_defaults()
    test_gate_at_negative_1_5()
    test_sol_trend_passes_thresholds()
    test_memecoin_gate_blocks_dump()
    test_memecoin_gate_disabled()
    test_quality_override_allows_good_pop_when_cold()
    test_quality_override_blocks_junk_when_cold()
    test_quality_override_cannot_bypass_4h_hard_block()
    test_quality_override_disabled_blocks()
    test_hot_regime_keeps_dump_gate_and_exits_intact()
    test_strategy_blocks_sol_macro_on_memecoin()
    test_one_strike_blocks_repeat_loss_mint()
    print("\nAll SOL trend filter validations passed.")


if __name__ == "__main__":
    main()
