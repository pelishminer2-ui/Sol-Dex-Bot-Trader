"""Validate trade journal enrichment helpers."""

from jupiter import SwapQuote
from scanner import MoverCandidate
from strategy import Position
from trade_utils import (
    build_buy_journal,
    build_sell_journal,
    entry_sol_basis,
    format_trade_cli,
    quote_sol_flow,
)


def _buy_quote() -> SwapQuote:
    return SwapQuote(
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="TestMint111",
        in_amount=100_000_000,
        out_amount=5_000_000_000,
        price_impact_pct=0.12,
        raw={},
        output_decimals=6,
    )


def _sell_quote() -> SwapQuote:
    return SwapQuote(
        input_mint="TestMint111",
        output_mint="So11111111111111111111111111111111111111112",
        in_amount=5_000_000_000,
        out_amount=105_000_000,
        price_impact_pct=0.08,
        raw={},
        output_decimals=6,
    )


def test_quote_sol_flow():
    buy = _buy_quote()
    sol_in, sol_out = quote_sol_flow(buy)
    assert abs(sol_in - 0.1) < 1e-9
    assert sol_out == 0.0
    sell = _sell_quote()
    sol_in, sol_out = quote_sol_flow(sell)
    assert sol_in == 0.0
    assert abs(sol_out - 0.105) < 1e-9
    print("PASS: quote_sol_flow")


def test_build_buy_journal_fields():
    candidate = MoverCandidate(
        mint="TestMint111",
        symbol="TEST",
        name="Test Token",
        pair_address="pair",
        dex="raydium",
        price_usd=0.0002,
        liquidity_usd=50000,
        volume_24h_usd=100000,
        momentum_pct=0.02,
        price_change_5m=0.02,
        price_change_1h=0.01,
    )
    quote = _buy_quote()
    event = build_buy_journal(
        candidate=candidate,
        entry_price=0.0002,
        quote=quote,
        trade_size=0.1,
        momentum=0.02,
        signature="paper-sig",
        dry_run=True,
        sol_price_usd=150.0,
        token_decimals=6,
    )
    assert event["action"] == "buy"
    assert event["symbol"] == "TEST"
    assert event["paper_trade"] is True
    assert abs(event["sol_in"] - 0.1) < 1e-9
    assert event["token_amount_raw"] == 5_000_000_000
    assert event["token_amount"] == 5000.0
    assert event["tokens_usd_value"] == 5000.0 * 0.0002
    assert "cli_line" in event
    assert "BUY TEST" in event["cli_line"]
    assert format_trade_cli(event) == event["cli_line"]
    print("PASS: build_buy_journal_fields")


def test_build_sell_journal_fields():
    position = Position(
        mint="TestMint111",
        symbol="TEST",
        entry_price=0.0002,
        entry_time=0,
        size_sol=0.1,
        initial_token_amount_raw=5_000_000_000,
        remaining_token_amount_raw=5_000_000_000,
        token_decimals=6,
    )
    quote = _sell_quote()
    event = build_sell_journal(
        position=position,
        quote=quote,
        token_raw=5_000_000_000,
        current_price=0.00021,
        pnl_pct=0.05,
        reason="sell_take_profit_l1",
        signature="paper-sig",
        dry_run=True,
        sol_price_usd=150.0,
        token_decimals=6,
        action="sell_partial",
        tp_level=1,
    )
    assert event["sol_out"] == 0.105
    assert abs(event["sol_in_basis"] - 0.1) < 1e-9
    assert abs(event["pnl_sol"] - 0.005) < 1e-9
    assert event["token_amount"] == 5000.0
    assert event["paper_trade"] is True
    assert "cli_line" in event
    assert "SELL_PARTIAL L1" in event["cli_line"]
    print("PASS: build_sell_journal_fields")


def test_entry_sol_basis_partial():
    basis = entry_sol_basis(0.1, 2500, 10000)
    assert abs(basis - 0.025) < 1e-9
    print("PASS: entry_sol_basis_partial")


def main():
    test_quote_sol_flow()
    test_build_buy_journal_fields()
    test_build_sell_journal_fields()
    test_entry_sol_basis_partial()
    print("\nAll trade journal validation tests passed.")


if __name__ == "__main__":
    main()
