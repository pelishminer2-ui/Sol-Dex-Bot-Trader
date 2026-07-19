"""Validate stable-quote WSOL day-USD (+$5) entry gate and USDC/USDT↔WSOL allowlist."""

from unittest.mock import patch

from config import (
    Config,
    SOL_MINT,
    USDC_MINT,
    USDT_MINT,
    is_stable_quote_wsol_mint,
    is_stable_wsol_exchange_pair,
    stable_quote_mint,
)
from jupiter import JupiterExecutor, SwapQuote
from scanner import MoverCandidate
from sol_trading import (
    sol_day_usd_gain_from_snapshot,
    stable_quote_wsol_entry_qualifies,
    stable_quote_wsol_entry_skip_reason,
)
from strategy import MomentumStrategy, SignalType
from trade_utils import quote_sol_flow
from tx_authorizer import context_from_quote
from watchlist_scanner import day_usd_gain_from_h24


def test_day_usd_gain_absolute_not_percent():
    # Invert day_usd_gain_from_h24: gain = price * f/(1+f) = $5 at $150
    h24_pct = (5.0 / 145.0) * 100.0
    gain = day_usd_gain_from_h24(150.0, h24_pct)
    assert gain is not None
    assert abs(gain - 5.0) < 1e-6
    snap = {"sol_price_usd": 150.0, "sol_trend_24h_pct": h24_pct}
    assert abs(sol_day_usd_gain_from_snapshot(snap) - 5.0) < 1e-6
    print("PASS: day USD gain is absolute dollars from price + h24%")


def test_stable_quote_wsol_gate_requires_plus_five():
    with patch.object(Config, "PAPER_QUOTE_CURRENCY", "usdc"), patch.object(
        Config, "STABLE_QUOTE_TRADE_SOL_WSOL", True
    ), patch.object(Config, "STABLE_QUOTE_SOL_MIN_DAILY_GAIN_USD", 5.0):
        below = {"sol_price_usd": 150.0, "sol_trend_24h_pct": (4.0 / 146.0) * 100.0}
        at = {"sol_price_usd": 150.0, "sol_trend_24h_pct": (5.0 / 145.0) * 100.0}
        assert stable_quote_wsol_entry_qualifies(below) is False
        assert "day USD" in (stable_quote_wsol_entry_skip_reason(below) or "")
        assert stable_quote_wsol_entry_qualifies(at) is True
        assert stable_quote_wsol_entry_skip_reason(at) is None
    print("PASS: WSOL stable-quote gate is +$5 absolute day USD")


def test_wsol_mint_allowlisted_vs_usdc_usdt():
    assert SOL_MINT == "So11111111111111111111111111111111111111112"
    with patch.object(Config, "PAPER_QUOTE_CURRENCY", "usdc"), patch.object(
        Config, "STABLE_QUOTE_TRADE_SOL_WSOL", True
    ):
        assert is_stable_quote_wsol_mint(SOL_MINT) is True
        assert stable_quote_mint() == USDC_MINT
        assert is_stable_wsol_exchange_pair(USDC_MINT, SOL_MINT) is True
        assert is_stable_wsol_exchange_pair(SOL_MINT, USDC_MINT) is True
    with patch.object(Config, "PAPER_QUOTE_CURRENCY", "usdt"), patch.object(
        Config, "STABLE_QUOTE_TRADE_SOL_WSOL", True
    ):
        assert stable_quote_mint() == USDT_MINT
        assert is_stable_wsol_exchange_pair(USDT_MINT, SOL_MINT) is True
        assert is_stable_wsol_exchange_pair(SOL_MINT, USDT_MINT) is True
    with patch.object(Config, "PAPER_QUOTE_CURRENCY", "sol"), patch.object(
        Config, "STABLE_QUOTE_TRADE_SOL_WSOL", True
    ):
        assert is_stable_quote_wsol_mint(SOL_MINT) is False
    print("PASS: So1111...112 allowed for USDC<->WSOL and USDT<->WSOL when option on")


def test_jupiter_buy_sell_use_stable_mints():
    exe = JupiterExecutor("TestPubkey111", dry_run=True)
    fake = {
        "inAmount": "150000000",
        "outAmount": "1000000000",
        "priceImpactPct": 0.1,
        "routePlan": [],
    }
    with patch.object(exe._client, "get_quote", return_value=fake) as gq:
        q = exe.buy_token(SOL_MINT, 1.0, quote_mint=USDC_MINT, sol_price_usd=150.0)
        assert q is not None
        assert q.input_mint == USDC_MINT
        assert q.output_mint == SOL_MINT
        args = gq.call_args[0]
        assert args[0] == USDC_MINT
        assert args[1] == SOL_MINT

    sell_fake = {
        "inAmount": "1000000000",
        "outAmount": "149000000",
        "priceImpactPct": 0.1,
        "routePlan": [],
    }
    with patch.object(exe._client, "get_quote", return_value=sell_fake) as gq:
        q = exe.sell_token(
            SOL_MINT, 1_000_000_000, quote_mint=USDT_MINT, sol_price_usd=150.0
        )
        assert q is not None
        assert q.input_mint == SOL_MINT
        assert q.output_mint == USDT_MINT
        args = gq.call_args[0]
        assert args[0] == SOL_MINT
        assert args[1] == USDT_MINT
    print("PASS: Jupiter buy/sell route USDC/USDT <-> So1111...112")


def test_quote_sol_flow_and_auth_context_for_stable_wsol():
    buy = SwapQuote(
        input_mint=USDC_MINT,
        output_mint=SOL_MINT,
        in_amount=150_000_000,
        out_amount=1_000_000_000,
        price_impact_pct=0.1,
        raw={},
    )
    sol_in, sol_out = quote_sol_flow(buy, sol_price_usd=150.0)
    assert abs(sol_in - 1.0) < 1e-9
    assert abs(sol_out - 1.0) < 1e-9
    ctx = context_from_quote(buy)
    assert ctx.side == "buy"
    assert ctx.mint == SOL_MINT

    sell = SwapQuote(
        input_mint=SOL_MINT,
        output_mint=USDT_MINT,
        in_amount=1_000_000_000,
        out_amount=149_000_000,
        price_impact_pct=0.1,
        raw={},
    )
    sol_in, sol_out = quote_sol_flow(sell, sol_price_usd=150.0)
    assert abs(sol_in - 1.0) < 1e-9
    assert abs(sol_out - (149.0 / 150.0)) < 1e-9
    ctx = context_from_quote(sell)
    assert ctx.side == "sell"
    assert ctx.mint == SOL_MINT
    print("PASS: quote_sol_flow + auth context for stable<->WSOL")


def test_strategy_buys_wsol_on_day_usd_gate():
    strat = MomentumStrategy()
    cand = MoverCandidate(
        mint=SOL_MINT,
        symbol="WSOL",
        name="WSOL",
        pair_address="x",
        dex="raydium",
        price_usd=150.0,
        liquidity_usd=1_000_000,
        volume_24h_usd=5_000_000,
        momentum_pct=0.0,
        price_change_5m=0.0,
        price_change_1h=0.0,
        pool_created_at=None,
        scanned_at=0.0,
        source="stable_quote_sol",
        day_usd_gain=5.5,
    )
    snap = {"sol_price_usd": 150.0, "sol_trend_24h_pct": 4.0}
    with patch.object(Config, "PAPER_QUOTE_CURRENCY", "usdt"), patch.object(
        Config, "STABLE_QUOTE_TRADE_SOL_WSOL", True
    ), patch.object(Config, "STABLE_QUOTE_SOL_MIN_DAILY_GAIN_USD", 5.0), patch.object(
        Config, "ENABLE_SOL_TRADING", False
    ):
        assert (
            strat.evaluate_entry(cand, 150.0, 0.0, sol_trend_snapshot=snap)
            == SignalType.BUY
        )
        cand.day_usd_gain = 4.0
        assert (
            strat.evaluate_entry(cand, 150.0, 0.0, sol_trend_snapshot=snap)
            == SignalType.NONE
        )
    print("PASS: strategy uses day-USD gate for stable-quote WSOL (exits untouched)")


def main():
    test_day_usd_gain_absolute_not_percent()
    test_stable_quote_wsol_gate_requires_plus_five()
    test_wsol_mint_allowlisted_vs_usdc_usdt()
    test_jupiter_buy_sell_use_stable_mints()
    test_quote_sol_flow_and_auth_context_for_stable_wsol()
    test_strategy_buys_wsol_on_day_usd_gate()
    print("\nAll stable-quote WSOL gate tests passed.")


if __name__ == "__main__":
    main()
