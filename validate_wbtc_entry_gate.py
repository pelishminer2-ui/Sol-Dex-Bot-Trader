"""Validate WBTC entry gates: positive day, $301 gain, 30min sustain, +3.25% feasibility."""

import json
import time
from pathlib import Path
from unittest.mock import patch

from config import (
    DEFAULT_INSTANT_EXIT_3PCT,
    DEFAULT_WATCHLIST_MINT,
    DEFAULT_WBTC_DAY_GAIN_SUSTAIN_MINUTES,
    DEFAULT_WBTC_MIN_DAILY_GAIN_USD,
    DEFAULT_WBTC_REQUIRE_POSITIVE_DAY,
    Config,
    wbtc_min_expected_gain_pct,
)
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType
from wbtc_entry_gate import (
    _load_sustain_state,
    _reset_sustain_state,
    _sustain_path,
    wbtc_day_gate_passes,
    wbtc_day_gate_skip_reason,
    wbtc_entry_qualifies,
    wbtc_entry_rule_summary,
    wbtc_entry_skip_reason,
    wbtc_instant_gain_feasible_from_quotes,
    wbtc_sustain_gate_passes,
    wbtc_sustain_skip_reason,
)

WBTC_MINT = DEFAULT_WATCHLIST_MINT


def _wbtc_candidate(
    *,
    day_usd_gain: float = 350.0,
    day_pct_gain: float = 0.001,
) -> MoverCandidate:
    return MoverCandidate(
        mint=WBTC_MINT,
        symbol="WBTC",
        name="Wrapped BTC",
        pair_address="pair",
        dex="raydium",
        price_usd=63636.0,
        liquidity_usd=500000.0,
        volume_24h_usd=1000000.0,
        momentum_pct=0.01,
        price_change_5m=0.01,
        price_change_1h=0.01,
        source="watchlist_mint",
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def _clear_sustain_state() -> None:
    path = _sustain_path()
    if path.exists():
        path.unlink()


def test_config_defaults():
    assert DEFAULT_WBTC_MIN_DAILY_GAIN_USD == 301.0
    assert DEFAULT_WBTC_DAY_GAIN_SUSTAIN_MINUTES == 30
    assert DEFAULT_WBTC_REQUIRE_POSITIVE_DAY is True
    assert Config.WBTC_MIN_DAILY_GAIN_USD == 301.0
    assert Config.WBTC_DAY_GAIN_SUSTAIN_MINUTES == 30
    assert Config.WBTC_REQUIRE_POSITIVE_DAY is True
    assert Config.WBTC_STOP_LOSS_ENABLED is False
    assert wbtc_min_expected_gain_pct() == DEFAULT_INSTANT_EXIT_3PCT
    assert Config.to_dict()["wbtc_min_daily_gain_usd"] == 301.0
    assert Config.to_dict()["wbtc_day_gain_sustain_minutes"] == 30
    assert Config.to_dict()["wbtc_stop_loss_enabled"] is False
    print("PASS: WBTC entry config defaults")


def test_day_gate_requires_positive_day_and_usd_gain():
    assert wbtc_day_gate_passes(day_usd_gain=350.0, day_pct_gain=0.001) is True
    assert wbtc_day_gate_passes(day_usd_gain=350.0, day_pct_gain=0.0) is False
    assert wbtc_day_gate_passes(day_usd_gain=300.0, day_pct_gain=0.002) is False
    assert wbtc_day_gate_passes(day_usd_gain=301.0, day_pct_gain=0.002) is True
    assert wbtc_day_gate_passes(day_usd_gain=None, day_pct_gain=0.01) is False
    print("PASS: day gate requires positive 24h and $301")


def test_day_gate_skip_reasons():
    assert "not positive" in wbtc_day_gate_skip_reason(
        day_usd_gain=350.0, day_pct_gain=0.0
    )
    assert "$300" in wbtc_day_gate_skip_reason(
        day_usd_gain=300.0, day_pct_gain=0.01
    )
    assert wbtc_day_gate_skip_reason(day_usd_gain=350.0, day_pct_gain=0.01) is None
    print("PASS: day gate skip reasons")


def test_sustain_gate_requires_30_minutes():
    _clear_sustain_state()
    base = 1_700_000_000.0
    gain = 350.0
    pct = 0.01
    assert wbtc_sustain_gate_passes(
        day_usd_gain=gain, day_pct_gain=pct, now=base
    ) is False
    assert wbtc_sustain_gate_passes(
        day_usd_gain=gain, day_pct_gain=pct, now=base + 29 * 60
    ) is False
    assert wbtc_sustain_gate_passes(
        day_usd_gain=gain, day_pct_gain=pct, now=base + 30 * 60
    ) is True
    _clear_sustain_state()
    print("PASS: sustain gate requires 30 continuous minutes")


def test_sustain_resets_when_day_gate_drops():
    _clear_sustain_state()
    base = 1_700_000_000.0
    wbtc_sustain_gate_passes(day_usd_gain=350.0, day_pct_gain=0.01, now=base)
    wbtc_sustain_gate_passes(
        day_usd_gain=350.0, day_pct_gain=0.01, now=base + 20 * 60
    )
    wbtc_sustain_gate_passes(day_usd_gain=250.0, day_pct_gain=0.01, now=base + 21 * 60)
    state = _load_sustain_state()
    assert state.get("first_met_at") is None
    _clear_sustain_state()
    print("PASS: sustain timer resets when day gate drops")


def test_sustain_skip_reason():
    _clear_sustain_state()
    reason = wbtc_sustain_skip_reason(day_usd_gain=350.0, day_pct_gain=0.01)
    assert reason and "sustain" in reason.lower()
    _clear_sustain_state()
    print("PASS: sustain skip reason")


def test_positive_day_can_be_disabled():
    with patch.object(Config, "WBTC_REQUIRE_POSITIVE_DAY", False):
        assert wbtc_day_gate_passes(day_usd_gain=350.0, day_pct_gain=-0.01) is True
    print("PASS: positive-day requirement optional")


def test_strategy_blocks_wbtc_below_threshold():
    strategy = MomentumStrategy()
    below = _wbtc_candidate(day_usd_gain=250.0, day_pct_gain=0.001)
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WBTC_MINT]
    ):
        assert (
            strategy.evaluate_entry(
                below, 63636.0, momentum=0.0, usd_gain=250.0
            )
            == SignalType.NONE
        )
    print("PASS: strategy blocks WBTC below $301 day gain")


def test_strategy_blocks_wbtc_before_sustain_met():
    _clear_sustain_state()
    strategy = MomentumStrategy()
    eligible = _wbtc_candidate(day_usd_gain=350.0, day_pct_gain=0.001)
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WBTC_MINT]
    ):
        assert (
            strategy.evaluate_entry(
                eligible, 63636.0, momentum=0.0, usd_gain=350.0
            )
            == SignalType.NONE
        )
    _clear_sustain_state()
    print("PASS: strategy blocks WBTC before 30min sustain")


def test_strategy_allows_wbtc_when_sustain_met():
    _clear_sustain_state()
    strategy = MomentumStrategy()
    eligible = _wbtc_candidate(day_usd_gain=350.0, day_pct_gain=0.001)
    base = time.time() - 31 * 60
    wbtc_sustain_gate_passes(
        day_usd_gain=350.0, day_pct_gain=0.001, now=base
    )
    wbtc_sustain_gate_passes(
        day_usd_gain=350.0, day_pct_gain=0.001, now=time.time()
    )
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WBTC_MINT]
    ):
        assert (
            strategy.evaluate_entry(
                eligible, 63636.0, momentum=0.0, usd_gain=350.0
            )
            == SignalType.BUY
        )
    _clear_sustain_state()
    print("PASS: strategy allows WBTC when sustain met")


def test_entry_skip_reason_blocks_session_only_gain():
    candidate = _wbtc_candidate(day_usd_gain=None, day_pct_gain=None)
    candidate.usd_gain_baseline = 350.0
    reason = wbtc_entry_skip_reason(candidate)
    assert reason
    assert "WBTC:" in reason
    print("PASS: session-only gain does not bypass day gate")


def test_instant_gain_feasible_blocks_high_round_trip():
    target = wbtc_min_expected_gain_pct()
    ok, reason = wbtc_instant_gain_feasible_from_quotes(
        0.10,
        0.001,
        buy_impact_pct=target * 100 * 0.6,
        sell_preview_impact_pct=target * 100 * 0.6,
    )
    assert not ok
    assert reason and "round-trip impact" in reason
    print("PASS: instant feasibility blocks high round-trip impact")


def test_instant_gain_feasible_allows_low_impact():
    ok, reason = wbtc_instant_gain_feasible_from_quotes(
        0.10,
        0.001,
        buy_impact_pct=0.1,
        sell_preview_impact_pct=0.1,
    )
    assert ok, reason
    print("PASS: instant feasibility allows low impact")


def test_custom_expected_gain_pct():
    with patch.object(Config, "WBTC_MIN_EXPECTED_GAIN_PCT", 0.04):
        assert wbtc_min_expected_gain_pct() == 0.04
    print("PASS: WBTC_MIN_EXPECTED_GAIN_PCT override")


def test_entry_rule_summary():
    summary = wbtc_entry_rule_summary()
    assert "positive 24h day" in summary
    assert "$301" in summary
    assert "30min" in summary
    assert "3.25%" in summary
    print("PASS: entry rule summary")


def test_wbtc_entry_qualifies_helper():
    _clear_sustain_state()
    base = time.time() - 31 * 60
    wbtc_sustain_gate_passes(day_usd_gain=350.0, day_pct_gain=0.001, now=base)
    wbtc_sustain_gate_passes(
        day_usd_gain=350.0, day_pct_gain=0.001, now=time.time()
    )
    assert wbtc_entry_qualifies(_wbtc_candidate()) is True
    _reset_sustain_state()
    assert wbtc_entry_qualifies(_wbtc_candidate(day_usd_gain=10.0)) is False
    _clear_sustain_state()
    print("PASS: wbtc_entry_qualifies helper")


def main():
    test_config_defaults()
    test_day_gate_requires_positive_day_and_usd_gain()
    test_day_gate_skip_reasons()
    test_sustain_gate_requires_30_minutes()
    test_sustain_resets_when_day_gate_drops()
    test_sustain_skip_reason()
    test_positive_day_can_be_disabled()
    test_strategy_blocks_wbtc_below_threshold()
    test_strategy_blocks_wbtc_before_sustain_met()
    test_strategy_allows_wbtc_when_sustain_met()
    test_entry_skip_reason_blocks_session_only_gain()
    test_instant_gain_feasible_blocks_high_round_trip()
    test_instant_gain_feasible_allows_low_impact()
    test_custom_expected_gain_pct()
    test_entry_rule_summary()
    test_wbtc_entry_qualifies_helper()
    print("\nAll WBTC entry gate validations passed.")


if __name__ == "__main__":
    main()
