"""Validate position limits (1 default, 2 with WBTC) and -10% dip re-entry."""

from unittest.mock import patch

from config import (
    Config,
    DEFAULT_WATCHLIST_MINT,
    can_open_more_positions,
    max_allowed_open_positions,
    wbtc_companion_slot_open,
)
from reentry_tracker import ReentryTracker
from risk import RiskManager
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType


MINT_A = "MintA1111111111111111111111111111111111111"
MINT_B = "MintB2222222222222222222222222222222222222"
WBTC_MINT = DEFAULT_WATCHLIST_MINT


def _candidate(mint: str, symbol: str, price: float = 1.0) -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="test",
        price_usd=price,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=0.02,
        price_change_5m=0.02,
        price_change_1h=0.01,
    )


def test_trade_candidate_top_n_default():
    assert Config.TRADE_CANDIDATE_TOP_N == 10
    print("PASS: TRADE_CANDIDATE_TOP_N defaults to 10")


def test_max_open_positions_default():
    assert Config.MAX_OPEN_POSITIONS == 1
    assert Config.MAX_OPEN_POSITIONS_WBTC == 2
    print("PASS: MAX_OPEN_POSITIONS defaults to 1 (2 with WBTC)")


def test_reentry_dip_pct_default():
    assert Config.REENTRY_DIP_PCT == 0.10
    print("PASS: REENTRY_DIP_PCT defaults to 0.10")


def test_risk_blocks_second_position_without_wbtc():
    risk = RiskManager()
    ok0, _ = risk.can_open_position(0, 1.0, dry_run=True)
    blocked, reason = risk.can_open_position(
        1,
        1.0,
        dry_run=True,
        open_mints=[MINT_A],
    )
    assert ok0
    assert not blocked
    assert "max open positions" in reason
    print("PASS: risk blocks second position without WBTC")


def test_risk_allows_two_when_wbtc_held():
    risk = RiskManager()
    ok_slot, _ = risk.can_open_position(
        1,
        1.0,
        dry_run=True,
        open_mints=[WBTC_MINT],
        candidate_mint=MINT_A,
    )
    blocked_third, reason = risk.can_open_position(
        2,
        1.0,
        dry_run=True,
        open_mints=[WBTC_MINT, MINT_A],
        candidate_mint=MINT_B,
    )
    assert ok_slot
    assert not blocked_third
    assert "max open positions" in reason
    print("PASS: risk allows 2 concurrent positions when WBTC is held")


def test_risk_allows_wbtc_as_second_entry():
    risk = RiskManager()
    ok, _ = risk.can_open_position(
        1,
        1.0,
        dry_run=True,
        open_mints=[MINT_A],
        candidate_mint=WBTC_MINT,
    )
    assert ok
    print("PASS: risk allows WBTC entry when one other position is open")


def test_normal_single_position_limit():
    strategy = MomentumStrategy()
    strategy.open_position(_candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=100)
    assert strategy.can_open_more() is False
    assert strategy.can_open_more(WBTC_MINT) is True
    assert strategy.can_open_more(MINT_B) is False
    print("PASS: normal single-position limit unless WBTC is next")


def test_wbtc_allows_second_position():
    strategy = MomentumStrategy()
    strategy.open_position(
        _candidate(WBTC_MINT, "WBTC"), 1.0, 0.05, 0.003, token_amount_raw=100
    )
    assert strategy.can_open_more() is True
    strategy.open_position(_candidate(MINT_B, "BBB"), 1.0, 0.05, 0.003, token_amount_raw=100)
    assert strategy.open_position_count() == 2
    assert strategy.can_open_more() is False
    print("PASS: WBTC + one other mint can be open together")


def test_same_mint_not_doubled():
    strategy = MomentumStrategy()
    c1 = _candidate(MINT_A, "AAA")
    strategy.open_position(c1, 1.0, 0.05, 0.003, token_amount_raw=1000)
    signal = strategy.evaluate_entry(c1, 1.0, 0.01)
    assert signal == SignalType.NONE
    dip = strategy.evaluate_dip_reentry(c1, 0.8, True)
    assert dip == SignalType.NONE
    print("PASS: same mint cannot be doubled while held")


def test_dip_reentry_at_minus_10_percent():
    tracker = ReentryTracker()
    tracker.record_exit(MINT_A, exit_price=1.0, symbol="AAA")
    assert tracker.is_dip_reentry(MINT_A, 0.90) is True
    assert tracker.is_dip_reentry(MINT_A, 0.901) is False
    assert tracker.dip_threshold_price(MINT_A) == 0.90
    print("PASS: dip re-entry triggers at -10% from exit")


def test_dip_reentry_bypasses_cooldown():
    strategy = MomentumStrategy()
    strategy.record_trade_cooldown(MINT_A)
    assert strategy.is_on_cooldown(MINT_A)

    tracker = ReentryTracker()
    tracker.record_exit(MINT_A, exit_price=1.0, symbol="AAA")

    candidate = tracker.to_candidate(MINT_A, 0.85)
    with patch.object(Config, "REENTRY_MIN_MOMENTUM_PCT", 0.0):
        signal = strategy.evaluate_dip_reentry(
            candidate, 0.85, tracker.is_dip_reentry(MINT_A, 0.85)
        )
    assert signal == SignalType.BUY
    print("PASS: dip re-entry signal fires despite cooldown (bot clears cooldown on entry)")


def test_mover_entry_respects_cooldown():
    strategy = MomentumStrategy()
    strategy.record_trade_cooldown(MINT_A)
    candidate = _candidate(MINT_A, "AAA")
    signal = strategy.evaluate_entry(candidate, 1.0, 0.01)
    assert signal == SignalType.NONE
    print("PASS: mover entry still respects cooldown")


def test_reentry_only_when_not_holding():
    strategy = MomentumStrategy()
    tracker = ReentryTracker()
    tracker.record_exit(MINT_A, 1.0, "AAA")
    candidate = _candidate(MINT_A, "AAA", 0.85)

    with patch.object(Config, "REENTRY_MIN_MOMENTUM_PCT", 0.0):
        assert strategy.evaluate_dip_reentry(candidate, 0.85, True) == SignalType.BUY

        strategy.open_position(candidate, 0.84, 0.05, 0.0, token_amount_raw=500)
        assert strategy.evaluate_dip_reentry(candidate, 0.70, True) == SignalType.NONE
    print("PASS: re-entry only when mint not currently held")


def test_exit_recorded_for_reentry():
    strategy = MomentumStrategy()
    tracker = ReentryTracker()
    pos = strategy.open_position(
        _candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=1000
    )
    strategy.close_position(pos, exit_price=2.0, reason=SignalType.SELL_SL)
    tracker.record_exit(MINT_A, 2.0, "AAA")
    assert not tracker.is_dip_reentry(MINT_A, 1.85)
    assert tracker.is_dip_reentry(MINT_A, 1.79)
    print("PASS: exit price drives re-entry threshold")


def test_wbtc_open_one_slot_free_allows_non_wbtc():
    """WBTC open + 1 slot free → can_open_more True for non-WBTC candidate."""
    strategy = MomentumStrategy()
    strategy.open_position(
        _candidate(WBTC_MINT, "WBTC"), 1.0, 0.05, 0.003, token_amount_raw=100
    )
    assert wbtc_companion_slot_open([WBTC_MINT]) is True
    assert strategy.can_open_more() is True
    assert strategy.can_open_more(MINT_A) is True
    assert strategy.can_open_more(MINT_B) is True
    print("PASS: WBTC open + 1 slot free allows non-WBTC entry")


def test_wbtc_plus_other_blocks_third():
    """WBTC open + 1 other open → cannot open 3rd."""
    strategy = MomentumStrategy()
    strategy.open_position(
        _candidate(WBTC_MINT, "WBTC"), 1.0, 0.05, 0.003, token_amount_raw=100
    )
    strategy.open_position(_candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=100)
    assert strategy.open_position_count() == 2
    assert wbtc_companion_slot_open([WBTC_MINT, MINT_A]) is False
    assert strategy.can_open_more() is False
    assert strategy.can_open_more(MINT_B) is False
    risk = RiskManager()
    blocked, reason = risk.can_open_position(
        2,
        1.0,
        dry_run=True,
        open_mints=[WBTC_MINT, MINT_A],
        candidate_mint=MINT_B,
    )
    assert not blocked
    assert "max open positions" in reason
    print("PASS: WBTC + one other blocks third entry")


def test_max_allowed_open_positions_helper():
    assert max_allowed_open_positions([]) == 1
    assert max_allowed_open_positions([MINT_A]) == 1
    assert max_allowed_open_positions([MINT_A], WBTC_MINT) == 2
    assert max_allowed_open_positions([WBTC_MINT]) == 2
    assert max_allowed_open_positions([WBTC_MINT, MINT_A]) == 2
    assert can_open_more_positions([MINT_A]) is False
    assert can_open_more_positions([WBTC_MINT]) is True
    print("PASS: position limit helpers")


def main():
    tests = [
        test_trade_candidate_top_n_default,
        test_max_open_positions_default,
        test_reentry_dip_pct_default,
        test_risk_blocks_second_position_without_wbtc,
        test_risk_allows_two_when_wbtc_held,
        test_risk_allows_wbtc_as_second_entry,
        test_normal_single_position_limit,
        test_wbtc_allows_second_position,
        test_wbtc_open_one_slot_free_allows_non_wbtc,
        test_wbtc_plus_other_blocks_third,
        test_same_mint_not_doubled,
        test_dip_reentry_at_minus_10_percent,
        test_dip_reentry_bypasses_cooldown,
        test_mover_entry_respects_cooldown,
        test_reentry_only_when_not_holding,
        test_exit_recorded_for_reentry,
        test_max_allowed_open_positions_helper,
    ]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} multi-position validation tests passed.")


if __name__ == "__main__":
    main()
