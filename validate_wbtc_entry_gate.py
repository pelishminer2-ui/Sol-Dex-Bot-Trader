"""Validate WBTC entry gates: positive day, $300 gain, +3.25% instant feasibility."""

from unittest.mock import patch

from config import (
    DEFAULT_INSTANT_EXIT_3PCT,
    DEFAULT_WATCHLIST_MINT,
    DEFAULT_WBTC_MIN_DAILY_GAIN_USD,
    DEFAULT_WBTC_REQUIRE_POSITIVE_DAY,
    Config,
    wbtc_min_expected_gain_pct,
)
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType
from wbtc_entry_gate import (
    wbtc_day_gate_passes,
    wbtc_day_gate_skip_reason,
    wbtc_entry_qualifies,
    wbtc_entry_rule_summary,
    wbtc_entry_skip_reason,
    wbtc_instant_gain_feasible_from_quotes,
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


def test_config_defaults():
    assert DEFAULT_WBTC_MIN_DAILY_GAIN_USD == 300.0
    assert DEFAULT_WBTC_REQUIRE_POSITIVE_DAY is True
    assert Config.WBTC_MIN_DAILY_GAIN_USD == 300.0
    assert Config.WBTC_REQUIRE_POSITIVE_DAY is True
    assert wbtc_min_expected_gain_pct() == DEFAULT_INSTANT_EXIT_3PCT
    assert Config.to_dict()["wbtc_min_daily_gain_usd"] == 300.0
    assert Config.to_dict()["wbtc_require_positive_day"] is True
    print("PASS: WBTC entry config defaults")


def test_day_gate_requires_positive_day_and_usd_gain():
    assert wbtc_day_gate_passes(day_usd_gain=350.0, day_pct_gain=0.001) is True
    assert wbtc_day_gate_passes(day_usd_gain=350.0, day_pct_gain=0.0) is False
    assert wbtc_day_gate_passes(day_usd_gain=299.0, day_pct_gain=0.002) is False
    assert wbtc_day_gate_passes(day_usd_gain=None, day_pct_gain=0.01) is False
    print("PASS: day gate requires positive 24h and $300")


def test_day_gate_skip_reasons():
    assert "not positive" in wbtc_day_gate_skip_reason(
        day_usd_gain=350.0, day_pct_gain=0.0
    )
    assert "$299" in wbtc_day_gate_skip_reason(
        day_usd_gain=299.0, day_pct_gain=0.01
    )
    assert wbtc_day_gate_skip_reason(day_usd_gain=350.0, day_pct_gain=0.01) is None
    print("PASS: day gate skip reasons")


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
    print("PASS: strategy blocks WBTC below $300 day gain")


def test_strategy_allows_wbtc_when_day_gate_passes():
    strategy = MomentumStrategy()
    eligible = _wbtc_candidate(day_usd_gain=350.0, day_pct_gain=0.001)
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WBTC_MINT]
    ):
        assert (
            strategy.evaluate_entry(
                eligible, 63636.0, momentum=0.0, usd_gain=350.0
            )
            == SignalType.BUY
        )
    print("PASS: strategy allows WBTC when day gate passes")


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
    assert "$300" in summary
    assert "3.25%" in summary
    print("PASS: entry rule summary")


def test_wbtc_entry_qualifies_helper():
    assert wbtc_entry_qualifies(_wbtc_candidate()) is True
    assert wbtc_entry_qualifies(_wbtc_candidate(day_usd_gain=10.0)) is False
    print("PASS: wbtc_entry_qualifies helper")


def main():
    test_config_defaults()
    test_day_gate_requires_positive_day_and_usd_gain()
    test_day_gate_skip_reasons()
    test_positive_day_can_be_disabled()
    test_strategy_blocks_wbtc_below_threshold()
    test_strategy_allows_wbtc_when_day_gate_passes()
    test_entry_skip_reason_blocks_session_only_gain()
    test_instant_gain_feasible_blocks_high_round_trip()
    test_instant_gain_feasible_allows_low_impact()
    test_custom_expected_gain_pct()
    test_entry_rule_summary()
    test_wbtc_entry_qualifies_helper()
    print("\nAll WBTC entry gate validations passed.")


if __name__ == "__main__":
    main()
