import atexit
import logging
import os
import random
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from io import BytesIO
from flask_cors import CORS

from bot_manager import bot_manager
from paper_session import paper_session_manager
from pnl_tracker import pnl_tracker
from config import (
    Config,
    PROJECT_ROOT,
    apply_balanced_win_strategy,
    apply_best_win_strategy,
    apply_steady_trade_strategy,
    ensure_config_bookmark,
    get_config_bookmark_info,
    max_allowed_open_positions,
    maybe_apply_best_win_strategy_env,
    maybe_apply_steady_trade_strategy_env,
    save_config_bookmark,
    wbtc_companion_slot_open,
)
from security_firewall import get_firewall_stats, init_firewall
from tax_export import (
    count_tax_rows,
    export_download_name,
    export_to_xlsx,
    get_export_path,
    get_tax_csv_path,
    get_tax_summary,
    get_tax_totals,
    read_tax_rows,
)

BRANDING_ASSETS = [
    {"id": "cats-of-crypto", "name": "CATS OF CRYPTO", "file": "cats-of-crypto.png"},
    {"id": "oval-bore", "name": "OVAL BORE Tech!", "file": "oval-bore.png"},
    {"id": "pelish-crypto", "name": "PELISH CRYPTO & MORE", "file": "pelish-crypto.png"},
]
BRANDING_DIR = Path(__file__).resolve().parent / "static" / "assets"
STATIC_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


def _static_version() -> str:
    try:
        return str(int(STATIC_INDEX.stat().st_mtime))
    except OSError:
        return "0"


STATIC_VERSION = _static_version()

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enrich_with_server_time(data: dict) -> dict:
    now = _utc_now()
    iso = now.isoformat()
    data["server_time"] = iso
    data["server_time_unix"] = now.timestamp()
    data["timestamp"] = iso
    data["last_updated"] = iso
    return data


def _port_in_use(host: str, port: int) -> bool:
    """True if the port cannot be bound (another process owns it)."""
    bind_host = "127.0.0.1" if host in ("0.0.0.0", "::", "localhost") else host
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((bind_host, port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def _bot_status_reachable(host: str, port: int) -> bool:
    """True if our Flask app is already serving /api/bot/status."""
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "localhost") else host
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://{target}:{port}/api/bot/status", timeout=2
        ) as resp:
            return resp.status == 200
    except OSError:
        return False


_SERVER_LOCK_PATH = PROJECT_ROOT / ".flask.server.lock"
_server_lock_fp = None


def _read_server_lock_pid() -> int | None:
    try:
        raw = _SERVER_LOCK_PATH.read_text(encoding="ascii").strip()
        if raw.isdigit():
            return int(raw)
    except OSError:
        pass
    return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _release_server_instance_lock() -> None:
    global _server_lock_fp
    fp = _server_lock_fp
    _server_lock_fp = None
    if fp is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt

            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fp.close()
    except OSError:
        pass
    try:
        _SERVER_LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _wait_for_peer_server(host: str, port: int, attempts: int = 20) -> bool:
    for _ in range(attempts):
        if _bot_status_reachable(host, port):
            return True
        time.sleep(0.5)
    return False


def _acquire_server_instance_lock() -> bool:
    """Return True when this process should bind the Flask port."""
    global _server_lock_fp

    if _bot_status_reachable(HOST, PORT):
        return False

    lock_pid = _read_server_lock_pid()
    if lock_pid and _pid_alive(lock_pid):
        if _wait_for_peer_server(HOST, PORT):
            return False
        return False

    if _SERVER_LOCK_PATH.exists() and not (lock_pid and _pid_alive(lock_pid)):
        try:
            _SERVER_LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        fp = open(_SERVER_LOCK_PATH, "a+b")
    except OSError:
        return True

    try:
        if sys.platform == "win32":
            import msvcrt

            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        if _wait_for_peer_server(HOST, PORT):
            return False
        lock_pid = _read_server_lock_pid()
        if lock_pid and _pid_alive(lock_pid):
            return False
        return False

    fp.seek(0)
    fp.truncate()
    fp.write(str(os.getpid()).encode("ascii"))
    fp.flush()
    _server_lock_fp = fp
    atexit.register(_release_server_instance_lock)
    return True


def _should_auto_open_browser() -> bool:
    if os.getenv("SOLANA_AUTO_OPEN_BROWSER", "1").strip().lower() in ("0", "false", "no"):
        return False
    if os.getenv("SOLANA_LAUNCHED_BY") in ("watchdog", "launcher"):
        return False
    return True


def _schedule_browser_open() -> None:
    if not _should_auto_open_browser():
        return
    url = get_server_info()["url"]

    def _open() -> None:
        try:
            webbrowser.open(url)
        except OSError as exc:
            logging.getLogger(__name__).warning("Could not open browser: %s", exc)

    threading.Timer(1.0, _open).start()


def _get_lan_ip() -> str | None:
    """Best-effort local LAN address for display when bound to all interfaces."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def get_server_info() -> dict:
    """Return bind host/port and a browser-friendly URL for the dashboard."""
    bind_host = HOST
    port = PORT
    if bind_host in ("0.0.0.0", "::"):
        display_host = "127.0.0.1"
    elif bind_host == "localhost":
        display_host = "127.0.0.1"
    else:
        display_host = bind_host
    url = f"http://{display_host}:{port}"
    info = {
        "host": display_host,
        "port": port,
        "url": url,
        "bind_host": bind_host,
    }
    lan_ip = _get_lan_ip()
    if lan_ip and lan_ip != display_host:
        info["lan_host"] = lan_ip
        info["lan_url"] = f"http://{lan_ip}:{port}"
    return info


def enrich_with_server_info(data: dict) -> dict:
    info = get_server_info()
    data["server"] = info
    data["server_host"] = info["host"]
    data["server_port"] = info["port"]
    data["server_url"] = info["url"]
    return data


def pick_branding() -> dict:
    asset = random.choice(BRANDING_ASSETS)
    url = f"/static/assets/{asset['file']}"
    return {
        "id": asset["id"],
        "name": asset["name"],
        "backdrop_url": url,
        "favicon_url": url,
    }

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = Flask(__name__, static_folder="static", static_url_path="/static")

PORT = Config.GUI_PORT
HOST = Config.FLASK_HOST

if HOST in ("0.0.0.0", "::"):
    print(
        "\n" + "!" * 60 + "\n"
        "  SECURITY WARNING: FLASK_HOST is set to bind all interfaces!\n"
        "  Anyone on your network could reach the dashboard and wallet controls.\n"
        "  Set FLASK_HOST=127.0.0.1 (recommended) before running in production.\n"
        + "!" * 60 + "\n",
        file=sys.stderr,
    )

CORS(
    app,
    origins=[
        f"http://127.0.0.1:{PORT}",
        f"http://localhost:{PORT}",
    ],
    supports_credentials=True,
)

init_firewall(app)
ensure_config_bookmark()
maybe_apply_best_win_strategy_env()
maybe_apply_steady_trade_strategy_env()

# Ensure bot is idle on process start — never auto-run trading.
bot_manager.reset_to_idle(force=True)


@app.after_request
def disable_caching(response):
    for key, value in NO_CACHE_HEADERS.items():
        response.headers[key] = value
    response.headers["X-Static-Version"] = _static_version()
    response.headers.pop("ETag", None)
    return response


@app.route("/")
def index():
    resp = send_from_directory("static", "index.html")
    for key, value in NO_CACHE_HEADERS.items():
        resp.headers[key] = value
    resp.headers.pop("ETag", None)
    return resp


@app.route("/favicon.ico")
def favicon():
    asset = random.choice(BRANDING_ASSETS)
    return send_from_directory(BRANDING_DIR, asset["file"], mimetype="image/png")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    resp = send_from_directory("static", "service-worker.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/api/branding")
def branding():
    return jsonify(pick_branding())


@app.route("/api/wallet", methods=["POST"])
def set_wallet():
    data = request.get_json(silent=True) or {}
    private_key = data.get("private_key", "")
    try:
        result = bot_manager.set_wallet(private_key)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/wallet/balance")
def wallet_balance():
    paper_mode = request.args.get("paper_trade")
    if paper_mode is not None:
        paper_mode = str(paper_mode).lower() in ("1", "true", "yes")
    return jsonify(bot_manager.get_wallet_balance(paper_mode=paper_mode))


@app.route("/api/wallet/save-env", methods=["POST"])
def save_wallet_env():
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"ok": False, "error": "Confirmation required"}), 400
    try:
        bot_manager.save_key_to_env()
        return jsonify({"ok": True, "message": "Saved SOLANA_PRIVATE_KEY to .env"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/bot/start", methods=["POST"])
def bot_start():
    data = request.get_json(silent=True) or {}
    if "paper_trade" in data:
        dry_run = bool(data["paper_trade"])
    else:
        dry_run = data.get("dry_run", True)

    config_payload = {}
    for key in (
        "trade_size_sol",
        "entry_momentum_pct",
        "stop_loss_pct",
        "solana_rpc_url",
        "include_pumpfun",
        "scan_pumpfun",
        "scan_birdeye",
        "scan_gmgn",
    ):
        if key in data and data[key] is not None:
            config_payload[key] = data[key]

    try:
        result = bot_manager.start(dry_run=bool(dry_run), config=config_payload or None)
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "error_code": "invalid_config"}), 400
    except RuntimeError as exc:
        msg = str(exc)
        if "already running" in msg.lower():
            code = "already_running"
        elif "wallet" in msg.lower() or "private key" in msg.lower():
            code = "no_wallet"
        elif "fund" in msg.lower() or "sol" in msg.lower():
            code = "insufficient_funds"
        else:
            code = "start_failed"
        return jsonify({"ok": False, "error": msg, "error_code": code}), 400
    except Exception as exc:
        bot_manager.reset_to_idle(force=True)
        return jsonify({"ok": False, "error": str(exc), "error_code": "start_failed"}), 400


@app.route("/api/bot/stop", methods=["POST"])
def bot_stop():
    result = bot_manager.stop()
    return jsonify({"ok": True, **result})


@app.route("/api/bot/force-reset", methods=["POST"])
def bot_force_reset():
    result = bot_manager.force_reset()
    return jsonify({"ok": True, **result})


@app.route("/api/mint/unblock", methods=["POST"])
def mint_unblock():
    data = request.get_json(silent=True) or {}
    mint = (data.get("mint") or data.get("token_mint") or "").strip()
    symbol = (data.get("symbol") or "").strip()
    name = (data.get("name") or "").strip()
    if not mint:
        return jsonify({"ok": False, "error": "mint is required"}), 400
    try:
        result = bot_manager.unblock_mint(mint, symbol=symbol, name=name)
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/actions/pending")
def actions_pending():
    return jsonify(
        {
            "ok": True,
            "pending": bot_manager.get_pending_actions(),
            "status": bot_manager.reentry_retry_status(),
        }
    )


@app.route("/api/actions/decide", methods=["POST"])
def actions_decide():
    data = request.get_json(silent=True) or {}
    mint = (data.get("mint") or data.get("token_mint") or "").strip()
    if not mint:
        return jsonify({"ok": False, "error": "mint is required"}), 400
    allow_raw = data.get("allow")
    if allow_raw is None:
        decision = (data.get("decision") or "").strip().lower()
        if decision == "allow":
            allow_raw = True
        elif decision == "deny":
            allow_raw = False
        else:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "allow (bool) or decision (allow/deny) is required",
                    }
                ),
                400,
            )
    deny_similar = bool(
        data.get("deny_similar_pattern") or data.get("apply_to_similar")
    )
    try:
        result = bot_manager.decide_reentry_action(
            mint,
            allow=bool(allow_raw),
            deny_similar_pattern=deny_similar,
        )
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/actions/dev/preview", methods=["POST", "DELETE"])
def actions_dev_preview():
    """Localhost-only dev helper to seed/clear a fake pending re-entry action."""
    from reentry_retry import reentry_retry_manager

    if request.method == "DELETE":
        cleared = reentry_retry_manager.clear_dev_preview()
        return jsonify({"ok": True, "cleared": cleared})
    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "TESTCOIN").strip() or "TESTCOIN"
    result = reentry_retry_manager.seed_dev_preview(symbol=symbol)
    return jsonify({"ok": True, **result, "pending": bot_manager.get_pending_actions()})


@app.route("/api/bot/status")
def bot_status():
    status = bot_manager.get_status()
    if status.get("last_scan_time"):
        status["last_scan_iso"] = datetime.fromtimestamp(
            status["last_scan_time"], tz=timezone.utc
        ).isoformat()
    if status.get("last_action_time"):
        status["last_action_iso"] = datetime.fromtimestamp(
            status["last_action_time"], tz=timezone.utc
        ).isoformat()
    enrich_with_server_time(status)
    enrich_with_server_info(status)
    status["project_root"] = str(PROJECT_ROOT)
    status["static_version"] = _static_version()
    return jsonify(status)


@app.route("/api/pnl")
def running_pnl():
    return jsonify(pnl_tracker.get_running_pnl())


@app.route("/api/paper/session")
def paper_session():
    return jsonify(paper_session_manager.get_session_stats())


@app.route("/api/paper/balance", methods=["POST"])
def paper_balance_set():
    data = request.get_json(silent=True) or {}
    if "amount" not in data:
        return jsonify({"ok": False, "error": "amount is required"}), 400
    try:
        result = bot_manager.set_paper_balance(float(data["amount"]))
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/paper/balance/reset", methods=["POST"])
def paper_balance_reset():
    try:
        result = bot_manager.reset_paper_balance()
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/live/tradeable-balance", methods=["GET"])
def live_tradeable_balance_get():
    return jsonify(bot_manager.get_live_tradeable_balance())


@app.route("/api/live/tradeable-balance", methods=["POST"])
def live_tradeable_balance_set():
    data = request.get_json(silent=True) or {}
    if "amount" not in data:
        return jsonify({"ok": False, "error": "amount is required"}), 400
    try:
        result = bot_manager.set_live_tradeable_balance(float(data["amount"]))
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/paper/export")
def paper_export():
    csv_data = paper_session_manager.export_session_csv()
    if not csv_data:
        return jsonify({"ok": False, "error": "No paper session trades yet"}), 404
    return send_file(
        BytesIO(csv_data.encode("utf-8-sig")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="paper_session_trades.csv",
    )


@app.route("/api/movers")
def movers():
    limit = request.args.get("limit", Config.TRADE_CANDIDATE_TOP_N, type=int)
    return jsonify(
        {
            "movers": bot_manager.get_movers(limit=limit),
            "top_gainers": bot_manager.get_top_gainers(),
        }
    )


@app.route("/api/positions")
def positions():
    pos_list = bot_manager.get_positions()
    open_mints = [p.get("mint", "") for p in pos_list if p.get("mint")]
    effective_max = max_allowed_open_positions(open_mints)
    return jsonify(
        {
            "positions": pos_list,
            "count": len(pos_list),
            "max": effective_max,
            "max_open_positions": Config.MAX_OPEN_POSITIONS,
            "max_open_positions_wbtc": Config.MAX_OPEN_POSITIONS_WBTC,
            "wbtc_companion_slot_open": wbtc_companion_slot_open(open_mints),
        }
    )


@app.route("/api/trades")
def trades():
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"trades": bot_manager.get_trades(limit=limit)})


@app.route("/api/logs")
def logs():
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"logs": bot_manager.get_logs(limit=limit)})


@app.route("/api/config", methods=["GET"])
def get_config():
    trade_size = request.args.get("trade_size_sol", type=float)
    live_fees = request.args.get("live_fees", "").lower() in ("1", "true", "yes")
    cfg = Config.to_dict()
    cfg["paper_target_balance_sol"] = paper_session_manager.get_target_balance()
    cfg["paper_simulated_balance_sol"] = paper_session_manager.get_simulated_balance()
    from live_tradeable_balance import live_tradeable_balance_manager

    cfg["live_tradeable_balance_sol"] = live_tradeable_balance_manager.get_balance()
    if trade_size is not None and trade_size > 0:
        summary = Config.strategy_summary(trade_size, live_jupiter=live_fees)
        cfg["estimated_fees_sol"] = summary["estimated_fees_sol"]
        cfg["expected_ladder_gross_sol"] = summary["expected_ladder_gross_sol"]
        cfg["expected_ladder_net_sol"] = summary["expected_ladder_net_sol"]
        cfg["gross_profit_target_sol"] = summary["gross_profit_target_sol"]
        cfg["fee_source"] = summary.get("fee_source")
        cfg["fee_breakdown"] = summary.get("fee_breakdown")
        cfg["preview_trade_size_sol"] = trade_size
    enrich_with_server_time(cfg)
    enrich_with_server_info(cfg)
    cfg["project_root"] = str(PROJECT_ROOT)
    cfg["static_version"] = _static_version()
    cfg["config_bookmark"] = get_config_bookmark_info()
    return jsonify(cfg)


@app.route("/api/config/save-bookmark", methods=["POST"])
def save_config_bookmark_route():
    data = request.get_json(silent=True) or {}
    label = data.get("label") or "pre-best-win"
    description = data.get("description") or "Snapshot before Best Win preset — use Revert to bookmark"
    payload_data = save_config_bookmark(label=label, description=description)
    payload = {
        "ok": True,
        "label": payload_data.get("label"),
        "created_at": payload_data.get("created_at"),
        "config_bookmark": get_config_bookmark_info(),
        "config": Config.to_dict(),
    }
    enrich_with_server_info(payload)
    return jsonify(payload)


@app.route("/api/config/apply-best-win-strategy", methods=["POST"])
def apply_best_win_strategy_route():
    data = request.get_json(silent=True) or {}
    save_bookmark = data.get("save_bookmark", True)
    try:
        result = apply_best_win_strategy(save_bookmark=bool(save_bookmark))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    payload = {"ok": True, **result}
    payload["config_bookmark"] = get_config_bookmark_info()
    enrich_with_server_info(payload)
    return jsonify(payload)


@app.route("/api/config/apply-balanced-win-strategy", methods=["POST"])
def apply_balanced_win_strategy_route():
    data = request.get_json(silent=True) or {}
    save_bookmark = data.get("save_bookmark", True)
    try:
        result = apply_balanced_win_strategy(save_bookmark=bool(save_bookmark))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    payload = {"ok": True, **result}
    payload["config_bookmark"] = get_config_bookmark_info()
    enrich_with_server_info(payload)
    return jsonify(payload)


@app.route("/api/config/apply-steady-trade-strategy", methods=["POST"])
def apply_steady_trade_strategy_route():
    data = request.get_json(silent=True) or {}
    save_bookmark = data.get("save_bookmark", True)
    try:
        result = apply_steady_trade_strategy(save_bookmark=bool(save_bookmark))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    payload = {"ok": True, **result}
    payload["config_bookmark"] = get_config_bookmark_info()
    enrich_with_server_info(payload)
    return jsonify(payload)


@app.route("/api/config/restore-bookmark", methods=["POST"])
def restore_config_bookmark_route():
    try:
        result = bot_manager.restore_config_bookmark()
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    payload = {"ok": True, **result}
    if "config" in payload and isinstance(payload["config"], dict):
        payload["config"]["config_bookmark"] = get_config_bookmark_info()
    enrich_with_server_info(payload)
    return jsonify(payload)


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.get_json(silent=True) or {}
    try:
        result = bot_manager.update_config(data)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    payload = {"ok": True, **result, "config": Config.to_dict()}
    enrich_with_server_info(payload)
    return jsonify(payload)


@app.route("/api/tax/export")
def tax_export():
    fmt = request.args.get("format", "csv").lower()
    report = request.args.get("report", "trades").lower()
    if report not in ("trades", "monthly", "yearly"):
        return jsonify({"ok": False, "error": "Invalid report type"}), 400
    if fmt not in ("csv", "xlsx"):
        return jsonify({"ok": False, "error": "Invalid format (use csv or xlsx)"}), 400

    path = get_export_path(report)
    if fmt == "csv":
        if not path.exists():
            return jsonify({"ok": False, "error": "No tax records yet"}), 404
        return send_file(
            path,
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name=export_download_name(report, "csv"),
        )

    try:
        data = export_to_xlsx(report)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 501
    if report == "trades" and count_tax_rows() == 0:
        return jsonify({"ok": False, "error": "No tax records yet"}), 404
    if report in ("monthly", "yearly") and not path.exists():
        return jsonify({"ok": False, "error": "No summary records yet"}), 404

    return send_file(
        BytesIO(data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=export_download_name(report, "xlsx"),
    )


@app.route("/api/tax/summary")
def tax_summary():
    return jsonify(get_tax_summary())


@app.route("/api/tax/preview")
def tax_preview():
    limit = request.args.get("limit", 20, type=int)
    rows = read_tax_rows(limit=limit)
    totals = get_tax_totals()
    summary = get_tax_summary()
    return jsonify(
        {
            "rows": rows,
            "count": count_tax_rows(),
            "path": str(get_tax_csv_path()),
            "total_profit_sol": totals["total_profit_sol"],
            "total_losses_sol": totals["total_losses_sol"],
            "current_month": summary["current_month"],
            "current_year": summary["current_year"],
            "monthly": summary["monthly"],
            "yearly": summary["yearly"],
        }
    )


if __name__ == "__main__":
    if _bot_status_reachable(HOST, PORT):
        print(
            f"\nBot dashboard is already running at http://127.0.0.1:{PORT}\n"
            f"Exiting - use launch.bat to connect or stop the existing process first.\n",
            file=sys.stderr,
        )
        sys.exit(0)
    if not _acquire_server_instance_lock():
        print(
            f"\nAnother bot server process is already starting or running on port {PORT}.\n"
            f"Exiting - use launch.bat to connect.\n",
            file=sys.stderr,
        )
        sys.exit(0)
    if _port_in_use(HOST, PORT):
        print(
            f"\nPort {PORT} is already in use by another program.\n"
            f"Stop that process or set GUI_PORT to a different port, then retry.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    fw = get_firewall_stats()
    print(
        f"\n{'=' * 60}\n"
        f"  Solana Mover Trading Bot — Web GUI\n"
        f"  Project: {PROJECT_ROOT}\n"
        f"  Open: http://127.0.0.1:{PORT}\n"
        f"\n"
        f"  Tip: double-click launch.bat to start server + open browser.\n"
        f"\n"
        f"  SECURITY: Firewall active — localhost only ({HOST}).\n"
        f"  Do NOT expose port {PORT} publicly.\n"
        f"  Rate limit: {fw['rate_limit_per_min']} req/min per IP.\n"
        f"  Your private key stays in server memory only.\n"
        f"{'=' * 60}\n"
    )
    _schedule_browser_open()
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)
