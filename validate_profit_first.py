"""Validate profit-first entry filters, L1 protection, and loss pause."""

import time
from unittest.mock import patch

from config import Config
from fee_estimator import compute_take_profit_levels, get_fee_budget
from risk import RiskManager, SKIP_REASON_PREFIX
from scanner import MoverCandidate
from strategy import MomentumStrategy, Position, SignalType


def _fee_quotes(size: float):
    lamports = int(size * 1_000_000_000)
    buy = {
        "inputMint": "So11111111111111111111111111111111111111112",
        "inAmount": str(lamports),
        "routePlan": [{"percent": 100, "swapInfo": {"label": "Raydium", "inAmount": str(lamports)}}],
    }
    sell = {
        "outputMint": "So11111111111111111111111111111111111111112",
        "outAmount": str(int(size * 0.5 * 1.03 * 1_000_000_000)),
        "routePlan": [{"percent": 100, "swapInfo": {"label": "Orca"}}],
    }
    return buy, sell


def _make_position(entry_price: float = 1.0, token_raw: int = 10000) -> Position:
    size_sol = 0.05
    return Position(
        mint="TestMint",
        symbol="TEST",
        entry_price=entry_price,
        entry_time=time.time(),
        size_sol=size_sol,
        token_amount_raw=token_raw,
        initial_token_amount_raw=token_raw,
        remaining_token_amount_raw=token_raw,
        tp_levels=compute_take_profit_levels(size_sol),
        tp_portions=list(Config.TAKE_PROFIT_PORTIONS),
        target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
        fee_budget_sol=get_fee_budget(size_sol),
    )


def test_l1_protection_after_l1():
    strategy = MomentumStrategy()
    pos = _make_position()
    assert not pos.l1_protection_armed

    strategy.apply_partial_tp(pos, 0, 5000, 1.0011)
    assert pos.l1_protection_armed is True
    assert pos.remaining_token_amount_raw == 5000

    # At entry price — L1 protection (not regular -1.5% SL)
    signal = strategy.evaluate_exit(pos, current_price=1.0)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_L1_PROTECTION

    # Above +0.10% floor but below instant (+5%) and L2 (+4%) — hold for next TP level
    pos2 = _make_position()
    strategy.apply_partial_tp(pos2, 0, 5000, 1.0011)
    assert strategy.evaluate_exit(pos2, current_price=1.035) is None
    print("PASS: L1 protection after L1 partial")


def test_entry_skipped_high_impact():
    risk = RiskManager()
    ok, reason = risk.check_entry_eligibility(0.05, 50000, 1.5)
    assert not ok
    assert SKIP_REASON_PREFIX in reason
    assert "price impact" in reason
    print(f"PASS: high impact blocked — {reason}")


def test_entry_skipped_low_liquidity():
    risk = RiskManager()
    with patch.object(Config, "MAX_POTENTIAL_MODE", False):
        ok, reason = risk.check_entry_eligibility(0.10, 14000, 0.2)
    assert not ok
    assert SKIP_REASON_PREFIX in reason
    assert "liquidity" in reason
    print(f"PASS: low pool liquidity blocked at $14k — {reason}")


def test_entry_passes_at_min_pool_liquidity():
    risk = RiskManager()
    buy, sell = _fee_quotes(0.10)
    with patch.object(Config, "MIN_EXPECTED_NET_PROFIT_SOL", 0.001):
        ok, reason = risk.check_entry_eligibility(
            0.10, 15000, 0.2, jupiter_quote_buy=buy, jupiter_quote_sell=sell
        )
    assert ok, reason
    print("PASS: entry allowed at $15k pool liquidity for 0.10 SOL")


def test_entry_blocked_at_default_trade_size_insufficient_edge():
    risk = RiskManager()
    with patch.object(Config, "MIN_EXPECTED_NET_PROFIT_SOL", 0.001):
        ok, reason = risk.check_entry_eligibility(0.05, 15000, 0.2)
    assert not ok
    assert SKIP_REASON_PREFIX in reason
    assert "expected net" in reason
    print(f"PASS: 0.05 SOL blocked when min net edge is 0.001 — {reason}")


def test_entry_passes_at_min_trade_size_for_ladder():
    risk = RiskManager()
    buy, sell = _fee_quotes(0.10)
    with patch.object(Config, "MIN_EXPECTED_NET_PROFIT_SOL", 0.001):
        ok, reason = risk.check_entry_eligibility(
            0.10, 15000, 0.2, jupiter_quote_buy=buy, jupiter_quote_sell=sell
        )
    assert ok, reason
    print("PASS: entry allowed for 0.10 SOL with 2-step ladder net >= min")


def test_parse_pair_pool_liquidity_filter():
    import time

    from scanner import parse_pair

    base = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pair123",
        "priceUsd": "0.001",
        "volume": {"h24": 100000},
        "priceChange": {"m5": 5.0, "h1": 2.0},
        "pairCreatedAt": int(time.time() * 1000) - 2 * 3600 * 1000,
        "baseToken": {
            "address": "Mint111111111111111111111111111111111111111",
            "symbol": "TEST",
            "name": "Test Token",
        },
    }
    blocked = parse_pair({**base, "liquidity": {"usd": 14000}}, min_liquidity_usd=15000)
    assert blocked is None
    allowed = parse_pair({**base, "liquidity": {"usd": 15000}}, min_liquidity_usd=15000)
    assert allowed is not None
    assert allowed.liquidity_usd == 15000
    print("PASS: parse_pair blocks $14k pool liquidity, allows $15k+")


def test_entry_skipped_insufficient_edge():
    risk = RiskManager()
    ok, reason = risk.check_entry_eligibility(0.01, 50000, 0.1)
    assert not ok
    assert SKIP_REASON_PREFIX in reason
    assert "expected net" in reason or "trade size too small" in reason
    print(f"PASS: insufficient edge blocked — {reason}")


def test_consecutive_loss_pause_disabled_by_default():
    risk = RiskManager()
    for _ in range(10):
        risk.record_trade_outcome(-0.01)
    can, _ = risk.can_open_position(0, 1.0, dry_run=True)
    assert can
    assert Config.MAX_CONSECUTIVE_LOSSES == 0
    print("PASS: consecutive loss pause disabled by default (0 = no limit)")


def test_consecutive_loss_pause_when_enabled():
    from unittest.mock import patch

    risk = RiskManager()
    with patch.object(Config, "MAX_CONSECUTIVE_LOSSES", 3), patch.object(
        Config, "CONSECUTIVE_LOSS_PAUSE_MINUTES", 25
    ):
        for _ in range(3):
            risk.record_trade_outcome(-0.01)
        can, reason = risk.can_open_position(0, 1.0, dry_run=True)
        assert not can
        assert "consecutive losses" in reason
        assert "remaining" in reason
        print(f"PASS: consecutive loss pause when enabled — {reason}")

        risk.state.consecutive_loss_pause_until = time.time() - 1
        can, _ = risk.can_open_position(0, 1.0, dry_run=True)
        assert can
        print("PASS: consecutive loss pause auto-expires")

    risk.record_trade_outcome(0.02)
    can, _ = risk.can_open_position(0, 1.0, dry_run=True)
    assert can
    print("PASS: winning trade resets consecutive loss counter")


def test_momentum_entry_threshold():
    strategy = MomentumStrategy()
    candidate = MoverCandidate(
        mint="Mint",
        symbol="TOK",
        name="Token",
        pair_address="pair",
        dex="raydium",
        price_usd=1.0,
        liquidity_usd=50000,
        volume_24h_usd=100000,
        momentum_pct=0.004,
        price_change_5m=0.004,
        price_change_1h=0.003,
    )
    from config import DEFAULT_ENTRY_MOMENTUM_PCT

    assert Config.ENTRY_MOMENTUM_PCT == DEFAULT_ENTRY_MOMENTUM_PCT
    below = DEFAULT_ENTRY_MOMENTUM_PCT - 0.001
    above = DEFAULT_ENTRY_MOMENTUM_PCT + 0.001
    assert strategy.evaluate_entry(candidate, 1.0, below) == SignalType.NONE
    assert strategy.evaluate_entry(candidate, 1.0, above) == SignalType.BUY
    print(f"PASS: entry momentum threshold {DEFAULT_ENTRY_MOMENTUM_PCT * 100:.2f}%")


def test_similarity_reference_requires_profit_in_bot():
    """Bot only calls set_reference when profile.profitable — verify gate in bot.py."""
    import inspect
    import bot

    source = inspect.getsource(bot.TradingBot._monitor_open_position)
    assert "if profile.profitable:" in source
    assert "self.similarity.set_reference(profile)" in source
    print("PASS: bot gates similarity reference on profitable trades only")


def main():
    test_l1_protection_after_l1()
    test_entry_skipped_high_impact()
    test_entry_skipped_low_liquidity()
    test_entry_passes_at_min_pool_liquidity()
    test_entry_blocked_at_default_trade_size_insufficient_edge()
    test_entry_passes_at_min_trade_size_for_ladder()
    test_parse_pair_pool_liquidity_filter()
    test_entry_skipped_insufficient_edge()
    test_consecutive_loss_pause_disabled_by_default()
    test_consecutive_loss_pause_when_enabled()
    test_momentum_entry_threshold()
    test_similarity_reference_requires_profit_in_bot()
    print("\nAll profit-first validation tests passed.")


if __name__ == "__main__":
    main()
