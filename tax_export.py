"""CSV tax log for live (non-paper) sell events with automated monthly/yearly summaries."""

import csv
import io
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import Config

logger = logging.getLogger(__name__)

_lock = threading.Lock()

SUMMARY_MARKER = "SUMMARY"
CSV_ENCODING = "utf-8-sig"

CSV_COLUMNS = [
    "timestamp",
    "wallet_address",
    "contract_address",
    "token_symbol",
    "action",
    "sol_in",
    "sol_out",
    "pnl_sol",
    "estimated_fees_sol",
    "gross_pnl_sol",
    "net_pnl_sol",
    "pnl_usd",
    "pnl_pct",
    "tx_signature",
    "entry_price_usd",
    "exit_price_usd",
    "Profit",
    "Losses",
]

MONTHLY_COLUMNS = [
    "year",
    "month",
    "wallet_address",
    "total_profit_sol",
    "total_losses_sol",
    "net_pnl_sol",
    "trade_count",
]

YEARLY_COLUMNS = [
    "year",
    "wallet_address",
    "total_profit_sol",
    "total_losses_sol",
    "net_pnl_sol",
    "trade_count",
]


def get_tax_csv_path() -> Path:
    return Path(Config.TAX_CSV_PATH)


def get_tax_monthly_csv_path() -> Path:
    return Path(Config.TAX_MONTHLY_CSV_PATH)


def get_tax_yearly_csv_path() -> Path:
    return Path(Config.TAX_YEARLY_CSV_PATH)


def _format_timestamp(ts: Optional[float]) -> str:
    if ts is None:
        ts = datetime.now(tz=timezone.utc).timestamp()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text or text == SUMMARY_MARKER:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_summary_row(row: dict[str, str]) -> bool:
    return row.get("timestamp") == SUMMARY_MARKER


def _parse_pnl(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_sol(value: float) -> str:
    return f"{value:.9f}".rstrip("0").rstrip(".") if value else "0"


def _calc_totals(trade_rows: list[dict[str, Any]]) -> tuple[float, float]:
    total_profit = 0.0
    total_losses = 0.0
    for row in trade_rows:
        pnl = _parse_pnl(row.get("net_pnl_sol"))
        if pnl is None:
            pnl = _parse_pnl(row.get("pnl_sol"))
        if pnl is None:
            continue
        if pnl > 0:
            total_profit += pnl
        elif pnl < 0:
            total_losses += abs(pnl)
    return total_profit, total_losses


def _summary_row(total_profit: float, total_losses: float) -> dict[str, Any]:
    return {
        "timestamp": SUMMARY_MARKER,
        "wallet_address": "",
        "contract_address": "",
        "token_symbol": "",
        "action": "",
        "sol_in": "",
        "sol_out": "",
        "pnl_sol": "",
        "pnl_usd": "",
        "pnl_pct": "",
        "tx_signature": "",
        "entry_price_usd": "",
        "exit_price_usd": "",
        "Profit": _format_sol(total_profit),
        "Losses": _format_sol(total_losses),
    }


def _read_all_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding=CSV_ENCODING, newline="") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except OSError as exc:
        logger.error("Failed to read tax CSV: %s", exc)
        return []


def _trade_rows_only(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if not _is_summary_row(row)]


def _write_csv(path: Path, trade_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    total_profit, total_losses = _calc_totals(trade_rows)
    with path.open("w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in trade_rows:
            out = {col: row.get(col, "") for col in CSV_COLUMNS}
            writer.writerow(out)
        writer.writerow(_summary_row(total_profit, total_losses))


def _aggregate_rows(trade_rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    monthly: dict[tuple[int, int, str], dict[str, Any]] = {}
    yearly: dict[tuple[int, str], dict[str, Any]] = {}

    for row in trade_rows:
        dt = _parse_timestamp(row.get("timestamp"))
        if dt is None:
            continue
        wallet = row.get("wallet_address", "") or ""
        pnl = _parse_pnl(row.get("net_pnl_sol"))
        if pnl is None:
            pnl = _parse_pnl(row.get("pnl_sol"))
        profit = pnl if pnl is not None and pnl > 0 else 0.0
        loss = abs(pnl) if pnl is not None and pnl < 0 else 0.0

        m_key = (dt.year, dt.month, wallet)
        if m_key not in monthly:
            monthly[m_key] = {
                "year": str(dt.year),
                "month": str(dt.month),
                "wallet_address": wallet,
                "total_profit_sol": 0.0,
                "total_losses_sol": 0.0,
                "net_pnl_sol": 0.0,
                "trade_count": 0,
            }
        monthly[m_key]["total_profit_sol"] += profit
        monthly[m_key]["total_losses_sol"] += loss
        monthly[m_key]["net_pnl_sol"] += pnl or 0.0
        monthly[m_key]["trade_count"] += 1

        y_key = (dt.year, wallet)
        if y_key not in yearly:
            yearly[y_key] = {
                "year": str(dt.year),
                "wallet_address": wallet,
                "total_profit_sol": 0.0,
                "total_losses_sol": 0.0,
                "net_pnl_sol": 0.0,
                "trade_count": 0,
            }
        yearly[y_key]["total_profit_sol"] += profit
        yearly[y_key]["total_losses_sol"] += loss
        yearly[y_key]["net_pnl_sol"] += pnl or 0.0
        yearly[y_key]["trade_count"] += 1

    monthly_rows = sorted(
        monthly.values(),
        key=lambda r: (int(r["year"]), int(r["month"]), r["wallet_address"]),
    )
    yearly_rows = sorted(
        yearly.values(),
        key=lambda r: (int(r["year"]), r["wallet_address"]),
    )

    for rows in (monthly_rows, yearly_rows):
        for row in rows:
            row["total_profit_sol"] = _format_sol(row["total_profit_sol"])
            row["total_losses_sol"] = _format_sol(row["total_losses_sol"])
            row["net_pnl_sol"] = _format_sol(row["net_pnl_sol"])
            row["trade_count"] = str(row["trade_count"])

    return monthly_rows, yearly_rows


def _write_summary_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


def rebuild_summary_files() -> None:
    """Recalculate monthly and yearly summary CSVs from all trade rows."""
    path = get_tax_csv_path()
    trade_rows = _trade_rows_only(_read_all_rows(path))
    monthly_rows, yearly_rows = _aggregate_rows(trade_rows)
    _write_summary_csv(get_tax_monthly_csv_path(), MONTHLY_COLUMNS, monthly_rows)
    _write_summary_csv(get_tax_yearly_csv_path(), YEARLY_COLUMNS, yearly_rows)


def _journal_to_row(journal_event: dict, wallet_address: str) -> dict[str, Any]:
    action = journal_event.get("action", "")
    sol_in = journal_event.get("sol_in")
    if sol_in is None:
        sol_in = journal_event.get("sol_in_basis", 0.0)
    return {
        "timestamp": _format_timestamp(journal_event.get("timestamp")),
        "wallet_address": wallet_address,
        "contract_address": journal_event.get("mint", ""),
        "token_symbol": journal_event.get("symbol", ""),
        "action": action,
        "sol_in": sol_in,
        "sol_out": journal_event.get("sol_out", 0.0),
        "pnl_sol": journal_event.get("net_pnl_sol", journal_event.get("pnl_sol", "")),
        "estimated_fees_sol": journal_event.get("estimated_fees_sol", ""),
        "gross_pnl_sol": journal_event.get("gross_pnl_sol", ""),
        "net_pnl_sol": journal_event.get("net_pnl_sol", journal_event.get("pnl_sol", "")),
        "pnl_usd": journal_event.get("pnl_usd", ""),
        "pnl_pct": journal_event.get("pnl_pct", ""),
        "tx_signature": journal_event.get("signature", ""),
        "entry_price_usd": journal_event.get("entry_price", ""),
        "exit_price_usd": journal_event.get("exit_price", ""),
        "Profit": "",
        "Losses": "",
    }


def append_tax_row(trade_data: dict, wallet_address: str = "") -> bool:
    """Append a tax CSV row for a live sell. Returns False if skipped."""
    if trade_data.get("paper_trade") or trade_data.get("dry_run"):
        return False

    action = trade_data.get("action", "")
    if action not in ("sell", "sell_partial"):
        return False

    wallet = wallet_address or trade_data.get("wallet_address", "")
    if not wallet:
        logger.warning("Tax CSV skip: missing wallet_address")
        return False

    row = _journal_to_row(trade_data, wallet)
    path = get_tax_csv_path()

    try:
        with _lock:
            existing = _trade_rows_only(_read_all_rows(path))
            existing.append(row)
            _write_csv(path, existing)
            rebuild_summary_files()
        return True
    except OSError as exc:
        logger.error("Failed to write tax CSV: %s", exc)
        return False


def read_tax_rows(limit: Optional[int] = None) -> list[dict[str, str]]:
    path = get_tax_csv_path()
    if not path.exists():
        return []

    with _lock:
        rows = _trade_rows_only(_read_all_rows(path))

    if limit is not None and limit > 0:
        return rows[-limit:]
    return rows


def read_monthly_summary_rows(limit: Optional[int] = None) -> list[dict[str, str]]:
    path = get_tax_monthly_csv_path()
    if not path.exists():
        return []
    with _lock:
        rows = _read_all_rows(path)
    if limit is not None and limit > 0:
        return rows[-limit:]
    return rows


def read_yearly_summary_rows() -> list[dict[str, str]]:
    path = get_tax_yearly_csv_path()
    if not path.exists():
        return []
    with _lock:
        return _read_all_rows(path)


def count_tax_rows() -> int:
    path = get_tax_csv_path()
    if not path.exists():
        return 0
    with _lock:
        return len(_trade_rows_only(_read_all_rows(path)))


def get_tax_totals() -> dict[str, float]:
    """Return aggregate profit and loss totals from trade rows."""
    path = get_tax_csv_path()
    with _lock:
        trade_rows = _trade_rows_only(_read_all_rows(path))
    total_profit, total_losses = _calc_totals(trade_rows)
    return {"total_profit_sol": total_profit, "total_losses_sol": total_losses}


def _period_totals(trade_rows: list[dict[str, str]], year: int, month: Optional[int] = None) -> dict[str, float]:
    profit = 0.0
    losses = 0.0
    count = 0
    for row in trade_rows:
        dt = _parse_timestamp(row.get("timestamp"))
        if dt is None:
            continue
        if dt.year != year:
            continue
        if month is not None and dt.month != month:
            continue
        pnl = _parse_pnl(row.get("net_pnl_sol"))
        if pnl is None:
            pnl = _parse_pnl(row.get("pnl_sol"))
        if pnl is None:
            continue
        count += 1
        if pnl > 0:
            profit += pnl
        elif pnl < 0:
            losses += abs(pnl)
    return {
        "total_profit_sol": profit,
        "total_losses_sol": losses,
        "net_pnl_sol": profit - losses,
        "trade_count": count,
    }


def get_tax_summary() -> dict[str, Any]:
    """Return monthly/yearly summaries and current period breakdown."""
    now = datetime.now(tz=timezone.utc)
    with _lock:
        trade_rows = _trade_rows_only(_read_all_rows(get_tax_csv_path()))
        monthly_rows = _read_all_rows(get_tax_monthly_csv_path())
        yearly_rows = _read_all_rows(get_tax_yearly_csv_path())

    current_month = _period_totals(trade_rows, now.year, now.month)
    current_year = _period_totals(trade_rows, now.year)

    return {
        "current_month": {
            "year": now.year,
            "month": now.month,
            **current_month,
        },
        "current_year": {
            "year": now.year,
            **current_year,
        },
        "monthly": monthly_rows[-12:],
        "yearly": yearly_rows,
        "paths": {
            "trades": str(get_tax_csv_path()),
            "monthly": str(get_tax_monthly_csv_path()),
            "yearly": str(get_tax_yearly_csv_path()),
        },
    }


def get_export_path(report: str = "trades") -> Path:
    report = (report or "trades").lower()
    if report == "monthly":
        return get_tax_monthly_csv_path()
    if report == "yearly":
        return get_tax_yearly_csv_path()
    return get_tax_csv_path()


def export_to_xlsx(report: str = "trades") -> bytes:
    """Build an Excel workbook for the requested report."""
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is required for xlsx export") from exc

    report = (report or "trades").lower()
    wb = Workbook()
    ws = wb.active

    if report == "monthly":
        ws.title = "Monthly Summary"
        columns = MONTHLY_COLUMNS
        rows = read_monthly_summary_rows()
    elif report == "yearly":
        ws.title = "Yearly Summary"
        columns = YEARLY_COLUMNS
        rows = read_yearly_summary_rows()
    else:
        ws.title = "Tax Trades"
        columns = CSV_COLUMNS
        with _lock:
            all_rows = _read_all_rows(get_tax_csv_path())
        rows = all_rows

    ws.append(columns)
    for row in rows:
        ws.append([row.get(col, "") for col in columns])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_download_name(report: str, fmt: str) -> str:
    report = (report or "trades").lower()
    base = {
        "monthly": get_tax_monthly_csv_path().stem,
        "yearly": get_tax_yearly_csv_path().stem,
        "trades": get_tax_csv_path().stem,
    }.get(report, get_tax_csv_path().stem)
    if fmt == "xlsx":
        return f"{base}.xlsx"
    return f"{base}.csv"
