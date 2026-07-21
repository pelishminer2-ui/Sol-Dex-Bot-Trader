"""Validate chain + DEX fee estimation and profit-first fee gates."""

from unittest.mock import patch

from config import Config
from fee_estimator import (
    estimate_chain_fees_sol,
    estimate_round_trip_fees,
    estimate_round_trip_fees_sol,
    expected_net_profit_sol,
    fee_breakdown_from_quotes,
    l1_gross_profit_sol,
    trade_covers_l1_fees,
    weighted_route_dex_bps,
)
from risk import RiskManager, SKIP_REASON_PREFIX


def _sample_buy_quote(trade_size_sol: float, label: str = "Raydium") -> dict:
    lamports = int(trade_size_sol * 1_000_000_000)
    return {
        "inputMint": "So11111111111111111111111111111111111111112",
        "outputMint": "Mint111111111111111111111111111111111111111",
        "inAmount": str(lamports),
        "outAmount": str(lamports * 1000),
        "routePlan": [
            {
                "percent": 100,
                "swapInfo": {
                    "label": label,
                    "inAmount": str(lamports),
                    "outAmount": str(lamports * 1000),
                },
            }
        ],
    }


def _sample_sell_quote(trade_size_sol: float, portion: float = 0.5) -> dict:
    sol_out = int(trade_size_sol * portion * 1.03 * 1_000_000_000)
    token_in = sol_out * 1000
    return {
        "inputMint": "Mint111111111111111111111111111111111111111",
        "outputMint": "So11111111111111111111111111111111111111112",
        "inAmount": str(token_in),
        "outAmount": str(sol_out),
        "routePlan": [
            {
                "percent": 100,
                "swapInfo": {
                    "label": "Orca",
                    "inAmount": str(token_in),
                    "outAmount": str(sol_out),
                },
            }
        ],
    }


def test_chain_fees_three_tx_round_trip():
    with patch.object(Config, "SOL_PRIORITY_FEE_LAMPORTS", 100_000):
        with patch.object(Config, "SOL_TX_FEE_LAMPORTS", 5000):
            fees = estimate_chain_fees_sol()
    expected = 3 * (100_000 + 5000) / 1_000_000_000
    assert abs(fees - expected) < 1e-9
    print(f"PASS: chain fees 3 tx = {fees:.6f} SOL")


def test_weighted_route_dex_bps():
    quote = _sample_buy_quote(0.1, label="Raydium")
    bps = weighted_route_dex_bps(quote)
    assert bps == 25
    pump = _sample_buy_quote(0.1, label="Pump.fun AMM")
    assert weighted_route_dex_bps(pump) == 100
    print("PASS: weighted_route_dex_bps")


def test_round_trip_fees_010_sol_with_quotes():
    size = 0.10
    buy = _sample_buy_quote(size)
    sell = _sample_sell_quote(size)
    with patch.object(Config, "FEE_BUFFER_PCT", 0.10):
        with patch.object(Config, "DEFAULT_SLIPPAGE_BPS", 100):
            fees = estimate_round_trip_fees_sol(size, buy, sell)
            breakdown = fee_breakdown_from_quotes(size, buy, sell)
    assert 0.0005 <= fees <= 0.005, f"0.10 SOL fees out of range: {fees}"
    assert breakdown["route_labels_buy"] == ["Raydium"]
    assert breakdown["route_labels_sell"] == ["Orca"]
    gross = size * (0.5 * 0.03 + 0.5 * 0.04)
    net = expected_net_profit_sol(size, fee_budget_sol=fees)
    assert abs(net - (gross - fees)) < 1e-9
    print(f"PASS: 0.10 SOL round-trip fees ~ {fees:.4f} SOL, ladder net ~ {net:.4f} SOL")


def test_fallback_fees_005_sol_in_range():
    fees = estimate_round_trip_fees(0.05)
    assert 0.0008 <= fees <= 0.0030, f"expected ~0.001 SOL fees, got {fees}"
    print(f"PASS: fallback fee budget for 0.05 SOL ~ {fees:.4f}")


def test_l1_covers_fees_at_010_sol():
    size = 0.10
    buy = _sample_buy_quote(size)
    sell = _sample_sell_quote(size)
    l1_gross = l1_gross_profit_sol(size)
    ok, gross, required = trade_covers_l1_fees(
        size, jupiter_quote_buy=buy, jupiter_quote_sell=sell
    )
    assert gross == l1_gross
    assert ok, f"L1 gross {gross:.4f} should cover L1 leg fees {required:.4f}"
    print(f"PASS: L1 gross {gross:.4f} >= L1 leg fees {required:.4f}")


def test_entry_blocked_when_trade_too_small_for_l1():
    risk = RiskManager()
    with patch.object(Config, "MIN_NET_WIN_SOL", 0.002):
        ok, reason = risk.check_entry_eligibility(0.01, 50000, 0.1)
    assert not ok
    assert SKIP_REASON_PREFIX in reason
    print(f"PASS: tiny trade blocked — {reason}")


def test_entry_passes_010_sol_with_quote_fees():
    risk = RiskManager()
    size = 0.10
    buy = _sample_buy_quote(size)
    sell = _sample_sell_quote(size)
    with patch.object(Config, "MIN_EXPECTED_NET_PROFIT_SOL", 0.001):
        ok, reason = risk.check_entry_eligibility(
            size, 15000, 0.2, jupiter_quote_buy=buy, jupiter_quote_sell=sell
        )
    assert ok, reason
    print("PASS: 0.10 SOL entry allowed with Jupiter fee quotes")


def test_preview_round_trip_empty_ladder_no_indexerror():
    """Steady Trade / Best Win use empty TP ladder — must not IndexError on portions[0]."""
    from fee_estimator import preview_round_trip_with_jupiter

    buy = _sample_buy_quote(0.1)
    sell = _sample_sell_quote(0.1, portion=1.0)

    def _fake_quote(inp, out, amount, slip, **kwargs):
        if inp.startswith("So1111"):
            return buy
        return sell

    with patch.object(Config, "TAKE_PROFIT_LEVELS", []):
        with patch.object(Config, "TAKE_PROFIT_PORTIONS", []):
            with patch("jupiter_client.get_jupiter_client") as mock_get:
                client = mock_get.return_value
                client.get_quote.side_effect = _fake_quote
                preview = preview_round_trip_with_jupiter(
                    0.1, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
                )
    assert preview.get("estimated_fees_sol") is not None
    assert preview.get("fee_source") in ("jupiter", "jupiter_buy_only")
    print("PASS: empty ladder live fee preview (no IndexError)")


def test_config_live_fees_empty_ladder_no_500():
    """GET /api/config?live_fees=true must not 500 when TP portions are empty."""
    from app import app

    client = app.test_client()
    with patch.object(Config, "TAKE_PROFIT_LEVELS", []):
        with patch.object(Config, "TAKE_PROFIT_PORTIONS", []):
            with patch(
                "fee_estimator.preview_round_trip_with_jupiter",
                side_effect=IndexError("portions[0]"),
            ):
                # Even if preview raises, route should fall back (not 500).
                resp = client.get("/api/config?trade_size_sol=0.1&live_fees=true")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data.get("estimated_fees_sol") is not None
    print("PASS: GET /api/config live_fees survives empty ladder / preview errors")


def main():
    test_chain_fees_three_tx_round_trip()
    test_weighted_route_dex_bps()
    test_round_trip_fees_010_sol_with_quotes()
    test_fallback_fees_005_sol_in_range()
    test_l1_covers_fees_at_010_sol()
    test_entry_blocked_when_trade_too_small_for_l1()
    test_entry_passes_010_sol_with_quote_fees()
    test_preview_round_trip_empty_ladder_no_indexerror()
    test_config_live_fees_empty_ladder_no_500()
    print("\nAll fee estimator validation tests passed.")


if __name__ == "__main__":
    main()
