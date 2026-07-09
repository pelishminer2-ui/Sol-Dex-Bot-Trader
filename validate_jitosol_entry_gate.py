"""Validate JitoSOL entry gates: positive day, $20 gain, +3.25% instant feasibility."""

from unittest.mock import patch

from config import (
    DEFAULT_INSTANT_EXIT_3PCT,
    DEFAULT_JITOSOL_MIN_DAILY_GAIN_USD,
    DEFAULT_JITOSOL_REQUIRE_POSITIVE_DAY,
    JITOSOL_MINT,
    Config,
    jitosol_min_expected_gain_pct,
)
from proxy_entry_gate import (
    jitosol_day_gate_passes,
    jitosol_day_gate_skip_reason,
    jitosol_entry_qualifies,
    jitosol_entry_rule_summary,
    jitosol_entry_skip_reason,
    jitosol_instant_gain_feasible_from_quotes,
)
from scanner import MoverCandidate
from sol_trading import sol_entry_qualifies, sol_entry_skip_reason
from strategy import MomentumStrategy, SignalType


def _jitosol_candidate(
    *,
    day_usd_gain: float = 25.0,
    day_pct_gain: float = 0.001,
) -> MoverCandidate:
    return MoverCandidate(
        mint=JITOSOL_MINT,
        symbol="JitoSOL",
        name="JitoSOL (SOL exposure)",
        pair_address="pair",
        dex="raydium",
        price_usd=200.0,
        liquidity_usd=500000.0,
        volume_24h_usd=1000000.0,
        momentum_pct=0.01,
        price_change_5m=0.01,
        price_change_1h=0.01,
        source="sol_trade",
        day_usd_gain=day_usd_gain,
        day_pct_gain=day_pct_gain,
    )


def test_config_defaults():
    assert DEFAULT_JITOSOL_MIN_DAILY_GAIN_USD == 20.0
    assert DEFAULT_JITOSOL_REQUIRE_POSITIVE_DAY is True
    assert Config.JITOSOL_MIN_DAILY_GAIN_USD == 20.0
    assert Config.JITOSOL_REQUIRE_POSITIVE_DAY is True
    assert jitosol_min_expected_gain_pct() == DEFAULT_INSTANT_EXIT_3PCT
    assert Config.to_dict()["jitosol_min_daily_gain_usd"] == 20.0
    assert Config.to_dict()["jitosol_require_positive_day"] is True
    print("PASS: JitoSOL entry config defaults")


def test_day_gate_requires_positive_day_and_usd_gain():
    assert jitosol_day_gate_passes(day_usd_gain=25.0, day_pct_gain=0.001) is True
    assert jitosol_day_gate_passes(day_usd_gain=25.0, day_pct_gain=0.0) is False
    assert jitosol_day_gate_passes(day_usd_gain=19.0, day_pct_gain=0.002) is False
    assert jitosol_day_gate_passes(day_usd_gain=None, day_pct_gain=0.01) is False
    print("PASS: day gate requires positive 24h and $20")


def test_day_gate_skip_reasons():
    assert "not positive" in jitosol_day_gate_skip_reason(
        day_usd_gain=25.0, day_pct_gain=0.0
    )
    assert "$19" in jitosol_day_gate_skip_reason(
        day_usd_gain=19.0, day_pct_gain=0.01
    )
    assert jitosol_day_gate_skip_reason(day_usd_gain=25.0, day_pct_gain=0.01) is None
    print("PASS: day gate skip reasons")


def test_sol_entry_qualifies_uses_day_gate_for_jitosol():
    with patch.object(Config, "ENABLE_SOL_TRADING", True), patch.object(
        Config, "SOL_TRADE_MINT", JITOSOL_MINT
    ):
        assert sol_entry_qualifies(None, day_usd_gain=25.0, day_pct_gain=0.01) is True
        assert sol_entry_qualifies(None, day_usd_gain=10.0, day_pct_gain=0.01) is False
        reason = sol_entry_skip_reason(None, day_usd_gain=10.0, day_pct_gain=0.01)
        assert reason and "JitoSOL" in reason
    print("PASS: sol_entry_qualifies uses JitoSOL day gate")


def test_strategy_blocks_jitosol_below_threshold():
    strategy = MomentumStrategy()
    below = _jitosol_candidate(day_usd_gain=10.0, day_pct_gain=0.001)
    with patch.object(Config, "ENABLE_SOL_TRADING", True), patch.object(
        Config, "SOL_TRADE_MINT", JITOSOL_MINT
    ):
        assert (
            strategy.evaluate_entry(
                below, 200.0, momentum=0.0, usd_gain=10.0
            )
            == SignalType.NONE
        )
    print("PASS: strategy blocks JitoSOL below $20 day gain")


def test_strategy_allows_jitosol_when_day_gate_passes():
    strategy = MomentumStrategy()
    eligible = _jitosol_candidate(day_usd_gain=25.0, day_pct_gain=0.001)
    with patch.object(Config, "ENABLE_SOL_TRADING", True), patch.object(
        Config, "SOL_TRADE_MINT", JITOSOL_MINT
    ):
        assert (
            strategy.evaluate_entry(
                eligible, 200.0, momentum=0.0, usd_gain=25.0
            )
            == SignalType.BUY
        )
    print("PASS: strategy allows JitoSOL when day gate passes")


def test_instant_gain_feasible_blocks_high_round_trip():
    target = jitosol_min_expected_gain_pct()
    ok, reason = jitosol_instant_gain_feasible_from_quotes(
        0.10,
        0.001,
        buy_impact_pct=target * 100 * 0.6,
        sell_preview_impact_pct=target * 100 * 0.6,
    )
    assert not ok
    assert reason and "round-trip impact" in reason
    print("PASS: instant feasibility blocks high round-trip impact")


def test_instant_gain_feasible_allows_low_impact():
    ok, reason = jitosol_instant_gain_feasible_from_quotes(
        0.10,
        0.001,
        buy_impact_pct=0.1,
        sell_preview_impact_pct=0.1,
    )
    assert ok, reason
    print("PASS: instant feasibility allows low impact")


def test_entry_rule_summary():
    summary = jitosol_entry_rule_summary()
    assert "positive 24h day" in summary
    assert "$20" in summary
    assert "3.25%" in summary
    print("PASS: entry rule summary")


def test_jitosol_entry_qualifies_helper():
    assert jitosol_entry_qualifies(_jitosol_candidate()) is True
    assert jitosol_entry_qualifies(_jitosol_candidate(day_usd_gain=10.0)) is False
    print("PASS: jitosol_entry_qualifies helper")


def main():
    test_config_defaults()
    test_day_gate_requires_positive_day_and_usd_gain()
    test_day_gate_skip_reasons()
    test_sol_entry_qualifies_uses_day_gate_for_jitosol()
    test_strategy_blocks_jitosol_below_threshold()
    test_strategy_allows_jitosol_when_day_gate_passes()
    test_instant_gain_feasible_blocks_high_round_trip()
    test_instant_gain_feasible_allows_low_impact()
    test_entry_rule_summary()
    test_jitosol_entry_qualifies_helper()
    print("\nAll JitoSOL entry gate validations passed.")


if __name__ == "__main__":
    main()
