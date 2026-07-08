"""Validate tax CSV export for live trades."""

import csv
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from jupiter import SwapQuote
from strategy import Position
from trade_utils import build_sell_journal


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


def _position() -> Position:
    return Position(
        mint="TestMint111",
        symbol="TEST",
        entry_price=0.0002,
        entry_time=0,
        size_sol=0.1,
        initial_token_amount_raw=5_000_000_000,
        remaining_token_amount_raw=5_000_000_000,
        token_decimals=6,
    )


def _with_temp_csv(test_fn):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        csv_path = tmp_path / "tax_trades.csv"
        monthly_path = tmp_path / "tax_summary_monthly.csv"
        yearly_path = tmp_path / "tax_summary_yearly.csv"
        old = {
            "TAX_CSV_PATH": os.environ.get("TAX_CSV_PATH"),
            "TAX_MONTHLY_CSV_PATH": os.environ.get("TAX_MONTHLY_CSV_PATH"),
            "TAX_YEARLY_CSV_PATH": os.environ.get("TAX_YEARLY_CSV_PATH"),
        }
        os.environ["TAX_CSV_PATH"] = str(csv_path)
        os.environ["TAX_MONTHLY_CSV_PATH"] = str(monthly_path)
        os.environ["TAX_YEARLY_CSV_PATH"] = str(yearly_path)
        try:
            from importlib import reload

            import config
            import tax_export

            reload(config)
            reload(tax_export)
            test_fn(csv_path, monthly_path, yearly_path, tax_export)
        finally:
            for key, val in old.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
            from importlib import reload

            import config
            import tax_export

            reload(config)
            reload(tax_export)


def _summary_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("timestamp") == "SUMMARY"]


def _utc_ts(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def _append_live_sell(
    tax_export,
    wallet: str,
    signature: str,
    pnl_sol: float | None = None,
    timestamp: float | None = None,
):
    journal = build_sell_journal(
        position=_position(),
        quote=_sell_quote(),
        token_raw=5_000_000_000,
        current_price=0.00021,
        pnl_pct=0.05,
        reason="sell_take_profit_l1",
        signature=signature,
        dry_run=False,
        sol_price_usd=150.0,
        token_decimals=6,
        action="sell_partial",
        tp_level=1,
    )
    journal["timestamp"] = timestamp if timestamp is not None else time.time()
    if pnl_sol is not None:
        journal["pnl_sol"] = pnl_sol
        journal["net_pnl_sol"] = pnl_sol
    return tax_export.append_tax_row(journal, wallet)


def test_live_sell_appends_csv_row(csv_path, monthly_path, yearly_path, tax_export):
    wallet = "WalletPubkey1111111111111111111111111111111"
    ok = _append_live_sell(tax_export, wallet, "live-tx-sig-abc123")
    assert ok is True
    assert csv_path.exists()
    assert monthly_path.exists()
    assert yearly_path.exists()

    trades = tax_export.read_tax_rows()
    assert len(trades) == 1
    row = trades[0]
    assert row["wallet_address"] == wallet
    assert row["contract_address"] == "TestMint111"
    assert row["token_symbol"] == "TEST"
    assert row["action"] == "sell_partial"
    assert row["tx_signature"] == "live-tx-sig-abc123"
    assert abs(float(row["pnl_sol"]) - 0.005) < 1e-9
    assert row["entry_price_usd"] == "0.0002"
    assert row["exit_price_usd"] == "0.00021"
    assert row["Profit"] == ""
    assert row["Losses"] == ""

    summaries = _summary_rows(csv_path)
    assert len(summaries) == 1
    assert abs(float(summaries[0]["Profit"]) - 0.005) < 1e-9
    assert summaries[0]["Losses"] == "0"
    print("PASS: live_sell_appends_csv_row")


def test_paper_sell_skips_csv(csv_path, monthly_path, yearly_path, tax_export):
    journal = build_sell_journal(
        position=_position(),
        quote=_sell_quote(),
        token_raw=5_000_000_000,
        current_price=0.00021,
        pnl_pct=0.05,
        reason="sell_take_profit_l1",
        signature="paper-sig",
        dry_run=True,
        sol_price_usd=150.0,
        token_decimals=6,
    )
    journal["timestamp"] = time.time()

    ok = tax_export.append_tax_row(journal, "WalletPubkey1111111111111111111111111111111")
    assert ok is False
    assert not csv_path.exists()
    assert not monthly_path.exists()
    assert not yearly_path.exists()
    print("PASS: paper_sell_skips_csv")


def test_export_endpoint_returns_file(csv_path, monthly_path, yearly_path, tax_export):
    from importlib import reload

    import app as app_module

    reload(app_module)
    client = app_module.app.test_client()

    missing = client.get("/api/tax/export")
    assert missing.status_code == 404

    wallet = "WalletPubkey2222222222222222222222222222222"
    journal = build_sell_journal(
        position=_position(),
        quote=_sell_quote(),
        token_raw=5_000_000_000,
        current_price=0.00021,
        pnl_pct=0.05,
        reason="sell_stop_loss",
        signature="live-tx-sig-xyz",
        dry_run=False,
        sol_price_usd=150.0,
        token_decimals=6,
        action="sell",
    )
    journal["timestamp"] = time.time()
    tax_export.append_tax_row(journal, wallet)

    preview = client.get("/api/tax/preview")
    assert preview.status_code == 200
    data = preview.get_json()
    assert data["count"] == 1
    assert len(data["rows"]) == 1
    assert data["rows"][0]["wallet_address"] == wallet
    assert "total_profit_sol" in data
    assert "total_losses_sol" in data
    assert "current_month" in data
    assert "current_year" in data
    assert "monthly" in data
    assert "yearly" in data

    summary = client.get("/api/tax/summary")
    assert summary.status_code == 200
    summary_data = summary.get_json()
    assert "current_month" in summary_data
    assert "yearly" in summary_data

    resp = client.get("/api/tax/export")
    assert resp.status_code == 200
    assert "csv" in resp.mimetype
    assert b"wallet_address" in resp.data
    assert b"Profit" in resp.data
    assert b"Losses" in resp.data
    assert b"SUMMARY" in resp.data
    assert wallet.encode() in resp.data

    monthly_resp = client.get("/api/tax/export?report=monthly")
    assert monthly_resp.status_code == 200
    assert b"total_profit_sol" in monthly_resp.data

    yearly_resp = client.get("/api/tax/export?report=yearly")
    assert yearly_resp.status_code == 200
    assert b"net_pnl_sol" in yearly_resp.data

    xlsx_resp = client.get("/api/tax/export?format=xlsx")
    assert xlsx_resp.status_code == 200
    assert "spreadsheetml" in xlsx_resp.mimetype
    print("PASS: export_endpoint_returns_file")


def test_count_and_preview_limit(csv_path, monthly_path, yearly_path, tax_export):
    wallet = "WalletPubkey3333333333333333333333333333333"
    for i in range(3):
        journal = build_sell_journal(
            position=_position(),
            quote=_sell_quote(),
            token_raw=1_000_000_000,
            current_price=0.00021,
            pnl_pct=0.05,
            reason="sell_take_profit_l1",
            signature=f"sig-{i}",
            dry_run=False,
            sol_price_usd=150.0,
            token_decimals=6,
            action="sell_partial",
        )
        journal["timestamp"] = time.time() + i
        tax_export.append_tax_row(journal, wallet)

    assert tax_export.count_tax_rows() == 3
    assert len(_summary_rows(csv_path)) == 1
    preview = tax_export.read_tax_rows(limit=2)
    assert len(preview) == 2
    assert preview[-1]["tx_signature"] == "sig-2"
    print("PASS: count_and_preview_limit")


def test_summary_footer_totals(csv_path, monthly_path, yearly_path, tax_export):
    wallet = "WalletPubkey4444444444444444444444444444444"
    _append_live_sell(tax_export, wallet, "sig-profit", pnl_sol=0.1)
    _append_live_sell(tax_export, wallet, "sig-loss", pnl_sol=-0.05)

    totals = tax_export.get_tax_totals()
    assert abs(totals["total_profit_sol"] - 0.1) < 1e-9
    assert abs(totals["total_losses_sol"] - 0.05) < 1e-9

    summaries = _summary_rows(csv_path)
    assert len(summaries) == 1
    assert abs(float(summaries[0]["Profit"]) - 0.1) < 1e-9
    assert abs(float(summaries[0]["Losses"]) - 0.05) < 1e-9
    print("PASS: summary_footer_totals")


def test_reappend_no_duplicate_summary(csv_path, monthly_path, yearly_path, tax_export):
    wallet = "WalletPubkey5555555555555555555555555555555"
    for i in range(4):
        _append_live_sell(tax_export, wallet, f"sig-{i}", pnl_sol=0.01 * (i + 1))

    assert len(_summary_rows(csv_path)) == 1
    assert tax_export.count_tax_rows() == 4

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        all_rows = list(csv.DictReader(f))
    assert len(all_rows) == 5  # 4 trades + 1 summary
    print("PASS: reappend_no_duplicate_summary")


def test_monthly_summary_per_month(csv_path, monthly_path, yearly_path, tax_export):
    wallet = "WalletPubkey6666666666666666666666666666666"
    year = datetime.now(tz=timezone.utc).year
    _append_live_sell(tax_export, wallet, "jan-1", pnl_sol=0.1, timestamp=_utc_ts(year, 1, 5))
    _append_live_sell(tax_export, wallet, "jan-2", pnl_sol=-0.03, timestamp=_utc_ts(year, 1, 20))
    _append_live_sell(tax_export, wallet, "feb-1", pnl_sol=0.05, timestamp=_utc_ts(year, 2, 10))

    monthly = tax_export.read_monthly_summary_rows()
    assert len(monthly) == 2

    jan = next(r for r in monthly if r["month"] == "1")
    assert jan["year"] == str(year)
    assert abs(float(jan["total_profit_sol"]) - 0.1) < 1e-9
    assert abs(float(jan["total_losses_sol"]) - 0.03) < 1e-9
    assert abs(float(jan["net_pnl_sol"]) - 0.07) < 1e-9
    assert jan["trade_count"] == "2"

    feb = next(r for r in monthly if r["month"] == "2")
    assert abs(float(feb["total_profit_sol"]) - 0.05) < 1e-9
    assert feb["trade_count"] == "1"
    print("PASS: monthly_summary_per_month")


def test_yearly_summary_aggregates(csv_path, monthly_path, yearly_path, tax_export):
    wallet = "WalletPubkey7777777777777777777777777777777"
    year = datetime.now(tz=timezone.utc).year
    _append_live_sell(tax_export, wallet, "jan", pnl_sol=0.2, timestamp=_utc_ts(year, 1, 1))
    _append_live_sell(tax_export, wallet, "mar", pnl_sol=-0.05, timestamp=_utc_ts(year, 3, 1))
    _append_live_sell(tax_export, wallet, "dec", pnl_sol=0.1, timestamp=_utc_ts(year, 12, 1))

    yearly = tax_export.read_yearly_summary_rows()
    assert len(yearly) == 1
    row = yearly[0]
    assert row["year"] == str(year)
    assert abs(float(row["total_profit_sol"]) - 0.3) < 1e-9
    assert abs(float(row["total_losses_sol"]) - 0.05) < 1e-9
    assert abs(float(row["net_pnl_sol"]) - 0.25) < 1e-9
    assert row["trade_count"] == "3"
    print("PASS: yearly_summary_aggregates")


def test_rebuild_idempotent_no_duplicate_summary_rows(csv_path, monthly_path, yearly_path, tax_export):
    wallet = "WalletPubkey8888888888888888888888888888888"
    _append_live_sell(tax_export, wallet, "a", pnl_sol=0.01)
    _append_live_sell(tax_export, wallet, "b", pnl_sol=-0.02)

    tax_export.rebuild_summary_files()
    tax_export.rebuild_summary_files()

    monthly = tax_export.read_monthly_summary_rows()
    yearly = tax_export.read_yearly_summary_rows()
    assert len(monthly) == 1
    assert len(yearly) == 1
    assert monthly[0]["trade_count"] == "2"
    assert yearly[0]["trade_count"] == "2"
    print("PASS: rebuild_idempotent_no_duplicate_summary_rows")


def test_paper_trades_excluded_from_summaries(csv_path, monthly_path, yearly_path, tax_export):
    wallet = "WalletPubkey9999999999999999999999999999999"
    _append_live_sell(tax_export, wallet, "live-only", pnl_sol=0.04)

    journal = build_sell_journal(
        position=_position(),
        quote=_sell_quote(),
        token_raw=5_000_000_000,
        current_price=0.00021,
        pnl_pct=0.05,
        reason="sell_take_profit_l1",
        signature="paper-skip",
        dry_run=True,
        sol_price_usd=150.0,
        token_decimals=6,
    )
    journal["timestamp"] = time.time()
    assert tax_export.append_tax_row(journal, wallet) is False

    monthly = tax_export.read_monthly_summary_rows()
    yearly = tax_export.read_yearly_summary_rows()
    assert len(monthly) == 1
    assert monthly[0]["trade_count"] == "1"
    assert abs(float(monthly[0]["total_profit_sol"]) - 0.04) < 1e-9
    assert yearly[0]["trade_count"] == "1"
    print("PASS: paper_trades_excluded_from_summaries")


def main():
    _with_temp_csv(test_live_sell_appends_csv_row)
    _with_temp_csv(test_paper_sell_skips_csv)
    _with_temp_csv(test_export_endpoint_returns_file)
    _with_temp_csv(test_count_and_preview_limit)
    _with_temp_csv(test_summary_footer_totals)
    _with_temp_csv(test_reappend_no_duplicate_summary)
    _with_temp_csv(test_monthly_summary_per_month)
    _with_temp_csv(test_yearly_summary_aggregates)
    _with_temp_csv(test_rebuild_idempotent_no_duplicate_summary_rows)
    _with_temp_csv(test_paper_trades_excluded_from_summaries)
    print("\nAll tax export validation tests passed.")


if __name__ == "__main__":
    main()
