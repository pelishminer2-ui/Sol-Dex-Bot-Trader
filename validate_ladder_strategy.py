"""Validate laddered take-profit strategy logic."""

import time
from contextlib import contextmanager
from unittest.mock import patch

from config import Config, DEFAULT_TAKE_PROFIT_LEVELS, DEFAULT_TAKE_PROFIT_PORTIONS, DEFAULT_STOP_LOSS_PCT
from fee_estimator import (
    compute_take_profit_levels,
    estimate_round_trip_fees,
    expected_net_profit_sol,
    get_fee_budget,
)
from strategy import MomentumStrategy, Position, SignalType


@contextmanager
def _without_instant_exit():
    """Disable instant exit and min-net-win gate for ladder TP tests."""
    originals = (
        Config.INSTANT_PROFIT_EXIT_ENABLED,
        Config.MIN_NET_WIN_SOL,
    )
    Config.INSTANT_PROFIT_EXIT_ENABLED = False
    Config.MIN_NET_WIN_SOL = 0.0
    try:
        yield
    finally:
        Config.INSTANT_PROFIT_EXIT_ENABLED = originals[0]
        Config.MIN_NET_WIN_SOL = originals[1]


def _make_position(
    entry_price: float = 1.0,
    token_raw: int = 10000,
    size_sol: float = 0.05,
) -> Position:
    tp_levels = compute_take_profit_levels(size_sol)
    return Position(
        mint="TestMint",
        symbol="TEST",
        entry_price=entry_price,
        entry_time=time.time(),
        size_sol=size_sol,
        token_amount_raw=token_raw,
        initial_token_amount_raw=token_raw,
        remaining_token_amount_raw=token_raw,
        tp_levels=tp_levels,
        tp_portions=list(Config.TAKE_PROFIT_PORTIONS),
        target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
        fee_budget_sol=get_fee_budget(size_sol),
    )


def test_default_ladder_config():
    assert Config.TAKE_PROFIT_LEVELS == DEFAULT_TAKE_PROFIT_LEVELS
    assert Config.TAKE_PROFIT_PORTIONS == DEFAULT_TAKE_PROFIT_PORTIONS
    assert DEFAULT_TAKE_PROFIT_LEVELS == [0.03, 0.04]
    assert DEFAULT_TAKE_PROFIT_PORTIONS == [0.5, 0.5]
    assert Config.TARGET_NET_PROFIT_SOL == 0.0155
    print("PASS: default ladder config")


def test_fee_budget_005_sol():
    fees = estimate_round_trip_fees(0.05)
    assert 0.0008 <= fees <= 0.0025, f"expected ~0.001 SOL fees, got {fees}"
    print(f"PASS: fee budget for 0.05 SOL ~ {fees:.4f}")


def test_ladder_levels_for_005_sol():
    levels = compute_take_profit_levels(0.05)
    assert levels == DEFAULT_TAKE_PROFIT_LEVELS
    assert len(levels) == 2
    print(f"PASS: ladder levels for 0.05 SOL trade (L1-L2: {[round(l*100,1) for l in levels]}%)")


def test_fixed_ladder_same_for_all_trade_sizes():
    small = compute_take_profit_levels(0.05)
    large = compute_take_profit_levels(0.07)
    assert small == large == DEFAULT_TAKE_PROFIT_LEVELS
    assert small == [0.03, 0.04]
    print("PASS: fixed ladder identical for 0.05 and 0.07 SOL")


def test_net_profit_after_fees_2_sol():
    trade_size = 2.0
    levels = compute_take_profit_levels(trade_size)
    net = expected_net_profit_sol(trade_size, levels)
    assert net > 0, f"expected positive net profit at 2.0 SOL, got {net:.6f}"
    print(f"PASS: net profit {net:.6f} SOL with fixed ladder at 2.0 SOL")


def test_evaluate_exit_level1():
    strategy = MomentumStrategy()
    pos = _make_position()
    with _without_instant_exit():
        signal = strategy.evaluate_exit(pos, current_price=1.031)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_TP_PARTIAL
    assert signal.tp_level_index == 0
    print("PASS: +3.1% triggers L1 (+3%)")


def test_evaluate_exit_level2_sequential():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.tp_levels_hit = [0]
    with _without_instant_exit():
        signal = strategy.evaluate_exit(pos, current_price=1.041)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_TP_PARTIAL
    assert signal.tp_level_index == 1
    print("PASS: +4.1% with L1 hit triggers L2 (+4%)")


def test_price_jump_triggers_l1_then_l2():
    """At +4.1% with no levels hit, first call returns L1; after L1 filled, L2."""
    strategy = MomentumStrategy()
    pos = _make_position(token_raw=10000)
    price = 1.041

    with _without_instant_exit():
        signal1 = strategy.evaluate_exit(pos, price)
        assert signal1 is not None
        assert signal1.tp_level_index == 0

        amount = strategy.partial_sell_amount_raw(pos, 0)
        assert amount == 5000
        strategy.apply_partial_tp(pos, 0, amount, price)
        assert pos.tp_levels_hit == [0]
        assert pos.remaining_token_amount_raw == 5000

        signal2 = strategy.evaluate_exit(pos, price)
        assert signal2 is not None
        assert signal2.tp_level_index == 1
    print("PASS: price jump at +4.1% triggers L1 then L2 sequentially")


def test_both_levels_partial_amounts():
    strategy = MomentumStrategy()
    pos = _make_position(token_raw=10000)
    price = 1.05
    with _without_instant_exit():
        for i in range(2):
            amount = strategy.partial_sell_amount_raw(pos, i)
            assert amount == 5000, f"level {i + 1} expected 5000, got {amount}"
            strategy.apply_partial_tp(pos, i, amount, price)
    assert len(pos.tp_levels_hit) == 2
    assert pos.remaining_token_amount_raw == 0
    assert not strategy.has_open_position()
    print("PASS: two partial sells drain position")


def test_l1_protection_after_l1():
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    assert pos.l1_protection_armed is True
    signal = strategy.evaluate_exit(pos, current_price=1.0)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_L1_PROTECTION
    print("PASS: L1 protection at entry after L1 partial")


def test_l1_protection_at_floor():
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    # +0.10% exactly — protection floor
    signal = strategy.evaluate_exit(pos, current_price=1.001)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_L1_PROTECTION
    print("PASS: L1 protection at +0.10% floor")


def test_l1_protection_above_floor_holds():
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    # +3.5% — above protection floor (+0.10%), below L2 (+4%)
    assert strategy.evaluate_exit(pos, current_price=1.035) is None
    print("PASS: hold above L1 protection floor")


def test_stop_loss_sells_all_remaining():
    strategy = MomentumStrategy()
    pos = _make_position()
    strategy.apply_partial_tp(pos, 0, 5000, 1.03)
    with patch.object(Config, "STOP_LOSS_PCT", DEFAULT_STOP_LOSS_PCT):
        signal = strategy.evaluate_exit(pos, current_price=0.978)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    assert pos.remaining_token_amount_raw == 5000
    print("PASS: stop loss after partial L1")


def test_stop_loss_priority_over_tp():
    strategy = MomentumStrategy()
    pos = _make_position()
    with patch.object(Config, "STOP_LOSS_PCT", DEFAULT_STOP_LOSS_PCT):
        signal = strategy.evaluate_exit(pos, current_price=0.978)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: SL takes priority")


def test_no_signal_below_first_level():
    strategy = MomentumStrategy()
    pos = _make_position()
    signal = strategy.evaluate_exit(pos, current_price=1.02)
    assert signal is None
    print("PASS: +2% below L1 (+3%) and instant (+5%) — hold")


def test_time_stop_signal():
    strategy = MomentumStrategy()
    pos = _make_position()
    pos.entry_time = 0
    original_ladder_exits = Config.ENABLE_LADDER_TIME_EXITS
    Config.ENABLE_LADDER_TIME_EXITS = False
    try:
        with patch("strategy.time.time", return_value=Config.TIME_STOP_MINUTES * 60 + 1):
            signal = strategy.evaluate_exit(pos, current_price=1.005)
    finally:
        Config.ENABLE_LADDER_TIME_EXITS = original_ladder_exits
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_TIME
    print("PASS: time stop closes remaining position")


def test_config_to_dict_ladder():
    cfg = Config.to_dict()
    assert "take_profit_levels" in cfg
    assert "take_profit_portions" in cfg
    assert "target_net_profit_sol" in cfg
    assert "estimated_fees_sol" in cfg
    assert "expected_ladder_net_sol" in cfg
    assert cfg["take_profit_levels"] == DEFAULT_TAKE_PROFIT_LEVELS
    assert cfg["spread_defaults"]["take_profit_levels"] == DEFAULT_TAKE_PROFIT_LEVELS
    print("PASS: Config.to_dict includes ladder fields")


def test_api_config_returns_fixed_ladder():
    from app import app

    client = app.test_client()
    resp = client.get("/api/config")
    assert resp.status_code == 200
    cfg = resp.get_json()
    assert cfg["take_profit_levels"] == [0.03, 0.04]
    assert cfg["spread_defaults"]["take_profit_levels"] == DEFAULT_TAKE_PROFIT_LEVELS
    print("PASS: GET /api/config returns fixed ladder [0.03, 0.04]")


def test_api_config_trade_size_preview_keeps_ladder():
    from app import app

    client = app.test_client()
    for size, expect_positive_net in ((0.05, False), (0.10, False), (2.0, True)):
        resp = client.get(f"/api/config?trade_size_sol={size}")
        assert resp.status_code == 200
        cfg = resp.get_json()
        assert cfg["take_profit_levels"] == DEFAULT_TAKE_PROFIT_LEVELS
        if expect_positive_net:
            assert cfg["expected_ladder_net_sol"] > 0
    print("PASS: /api/config preview keeps ladder fixed across trade sizes")


def test_legacy_ladder_env_migrated():
    with patch.dict("os.environ", {"TAKE_PROFIT_LEVELS": "0.10,0.25,0.40,0.65"}):
        from importlib import reload
        import config as config_mod

        reload(config_mod)
        assert config_mod.Config.TAKE_PROFIT_LEVELS == DEFAULT_TAKE_PROFIT_LEVELS
        reload(config_mod)
    print("PASS: legacy +10/+25/+40/+65 env values auto-migrate to fixed ladder")


def test_previous_four_level_env_migrated():
    with patch.dict(
        "os.environ",
        {
            "TAKE_PROFIT_LEVELS": "0.015,0.03,0.07,0.10",
            "TAKE_PROFIT_PORTIONS": "0.25,0.25,0.25,0.25",
        },
    ):
        from importlib import reload
        import config as config_mod

        reload(config_mod)
        assert config_mod.Config.TAKE_PROFIT_LEVELS == DEFAULT_TAKE_PROFIT_LEVELS
        assert config_mod.Config.TAKE_PROFIT_PORTIONS == DEFAULT_TAKE_PROFIT_PORTIONS
        reload(config_mod)
    print("PASS: previous 4-level env values auto-migrate to 2-step ladder")


def test_stale_two_level_env_migrated():
    with patch.dict("os.environ", {"TAKE_PROFIT_LEVELS": "0.001,0.04"}):
        from importlib import reload
        import config as config_mod

        reload(config_mod)
        assert config_mod.Config.TAKE_PROFIT_LEVELS == DEFAULT_TAKE_PROFIT_LEVELS
        reload(config_mod)
    print("PASS: stale +0.10%/+4% env values auto-migrate to +3%/+4% ladder")


def test_position_stores_fee_budget():
    strategy = MomentumStrategy()
    from scanner import MoverCandidate

    candidate = MoverCandidate(
        mint="Mint",
        symbol="TOK",
        name="Token",
        pair_address="pair",
        dex="raydium",
        price_usd=1.0,
        liquidity_usd=50000,
        volume_24h_usd=100000,
        momentum_pct=0.02,
        price_change_5m=0.02,
        price_change_1h=0.01,
    )
    pos = strategy.open_position(candidate, 1.0, 0.05, 0.02, token_amount_raw=1000)
    assert pos.fee_budget_sol > 0
    assert pos.target_net_profit_sol == Config.TARGET_NET_PROFIT_SOL
    assert len(pos.tp_levels) == 2
    assert pos.tp_levels == DEFAULT_TAKE_PROFIT_LEVELS
    print("PASS: position stores fee budget and fixed ladder")


def test_env_override_levels():
    with patch.dict("os.environ", {"TAKE_PROFIT_LEVELS": "0.02,0.04"}):
        from importlib import reload
        import config as config_mod

        reload(config_mod)
        assert config_mod.Config.TAKE_PROFIT_LEVELS == [0.02, 0.04]
        reload(config_mod)
    print("PASS: env override TAKE_PROFIT_LEVELS")


def main():
    test_default_ladder_config()
    test_fee_budget_005_sol()
    test_ladder_levels_for_005_sol()
    test_fixed_ladder_same_for_all_trade_sizes()
    test_net_profit_after_fees_2_sol()
    test_evaluate_exit_level1()
    test_evaluate_exit_level2_sequential()
    test_price_jump_triggers_l1_then_l2()
    test_both_levels_partial_amounts()
    test_l1_protection_after_l1()
    test_l1_protection_at_floor()
    test_l1_protection_above_floor_holds()
    test_stop_loss_sells_all_remaining()
    test_stop_loss_priority_over_tp()
    test_no_signal_below_first_level()
    test_time_stop_signal()
    test_config_to_dict_ladder()
    test_api_config_returns_fixed_ladder()
    test_api_config_trade_size_preview_keeps_ladder()
    test_legacy_ladder_env_migrated()
    test_previous_four_level_env_migrated()
    test_stale_two_level_env_migrated()
    test_position_stores_fee_budget()
    test_env_override_levels()
    print("\nAll ladder strategy tests passed.")


if __name__ == "__main__":
    main()
