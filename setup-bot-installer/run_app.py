"""Frozen entrypoint: Flask dashboard without a visible console (windowed mode).

Runs as a background GUI-subsystem process with:
  - File logging under the install dir (logs/soldexbot.log)
  - System tray icon (Open Dashboard / Quit)
  - Auto-open browser to the local dashboard

Stop via tray Quit, Start Menu "Stop Sol Dex Bot Trader", or:
  taskkill /IM SolDexBotTrader.exe /F
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _writable_root() -> Path:
    """Install / cwd directory for .env, data, logs (not the PyInstaller extract dir)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _setup_file_logging(work: Path) -> Path:
    """Send stdout/stderr + logging to install-dir logs (no console required)."""
    log_dir = work / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "soldexbot.log"

    log_fp = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115

    class _FileStream:
        def write(self, data: str) -> int:
            try:
                log_fp.write(data)
                log_fp.flush()
            except OSError:
                pass
            return len(data) if isinstance(data, str) else 0

        def flush(self) -> None:
            try:
                log_fp.flush()
            except OSError:
                pass

        def isatty(self) -> bool:
            return False

    sys.stdout = _FileStream()  # type: ignore[assignment]
    sys.stderr = _FileStream()  # type: ignore[assignment]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
    logging.getLogger(__name__).info("Logging to %s", log_path)
    return log_path


def _tray_icon_image():
    """Simple branded icon generated at runtime (no external .ico required)."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 3, size - 3], fill=(15, 39, 68, 255))
    draw.ellipse([14, 14, size - 15, size - 15], fill=(26, 107, 138, 255))
    draw.rectangle([28, 22, 36, 42], fill=(232, 238, 245, 255))
    draw.rectangle([22, 28, 42, 36], fill=(232, 238, 245, 255))
    return img


def main() -> None:
    bundle = _app_root()
    work = _writable_root()
    os.chdir(work)

    log_path = _setup_file_logging(work)
    logger = logging.getLogger("run_app")

    # Prefer bundled modules; keep writable root first for .env / data files.
    sys.path.insert(0, str(bundle))
    if str(work) not in sys.path:
        sys.path.insert(0, str(work))

    os.environ.setdefault("FLASK_HOST", "127.0.0.1")
    os.environ.setdefault("GUI_PORT", "5000")
    os.environ.setdefault("SOLANA_AUTO_OPEN_BROWSER", "1")
    # Ensure packaged launches open the browser (not suppressed by launcher flags).
    os.environ.pop("SOLANA_LAUNCHED_BY", None)

    # Seed .env from example on first run if missing.
    env_path = work / ".env"
    example = work / ".env.example"
    if not env_path.exists() and example.exists():
        try:
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("Created %s from .env.example — edit before live trading.", env_path)
        except OSError as exc:
            logger.warning("Could not create .env: %s", exc)

    # Import after path/cwd + logging setup so config.PROJECT_ROOT resolves correctly
    # and app.basicConfig is a no-op (handlers already configured).
    import app as flask_app  # noqa: WPS433

    try:
        import config as cfg

        if getattr(sys, "frozen", False):
            cfg.PROJECT_ROOT = work
    except Exception:
        logger.exception("Could not adjust PROJECT_ROOT for frozen install")

    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("GUI_PORT", "5000"))
    dashboard_url = f"http://127.0.0.1:{port}"

    # If already running, just open the browser and exit (no second tray/server).
    try:
        if flask_app._bot_status_reachable(host, port):
            logger.info("Dashboard already running at %s — opening browser and exiting.", dashboard_url)
            try:
                webbrowser.open(dashboard_url)
            except OSError as exc:
                logger.warning("Could not open browser: %s", exc)
            return
    except Exception:
        logger.debug("Reachability check failed; continuing startup", exc_info=True)

    logger.info(
        "Sol Dex Bot Trader starting — Dashboard %s | Install dir %s | Log %s",
        dashboard_url,
        work,
        log_path,
    )

    from werkzeug.serving import make_server

    server = make_server(host, port, flask_app.app, threaded=True)
    server_thread = threading.Thread(
        target=server.serve_forever,
        name="flask-server",
        daemon=True,
    )
    server_thread.start()

    # Wait briefly for bind, then open browser.
    for _ in range(50):
        if flask_app._bot_status_reachable(host, port):
            break
        time.sleep(0.1)
    flask_app._schedule_browser_open()

    shutdown_once = threading.Event()

    def _shutdown() -> None:
        if shutdown_once.is_set():
            return
        shutdown_once.set()
        logger.info("Shutting down Sol Dex Bot Trader...")
        try:
            server.shutdown()
        except Exception:
            logger.exception("Error during Flask shutdown")

    def _run_tray() -> None:
        try:
            import pystray
            from pystray import MenuItem as Item
        except ImportError:
            logger.warning(
                "pystray not available — running without tray. "
                "Stop with: taskkill /IM SolDexBotTrader.exe /F"
            )
            try:
                while server_thread.is_alive():
                    time.sleep(1.0)
            except KeyboardInterrupt:
                _shutdown()
            return

        def on_open(_icon, _item) -> None:
            try:
                webbrowser.open(dashboard_url)
            except OSError as exc:
                logger.warning("Could not open browser: %s", exc)

        def on_open_logs(_icon, _item) -> None:
            try:
                os.startfile(str(log_path.parent))  # type: ignore[attr-defined]
            except OSError as exc:
                logger.warning("Could not open logs folder: %s", exc)

        def on_quit(icon, _item) -> None:
            _shutdown()
            icon.stop()

        menu = pystray.Menu(
            Item("Open Dashboard", on_open, default=True),
            Item("Open Logs Folder", on_open_logs),
            Item("Quit", on_quit),
        )
        icon = pystray.Icon(
            "SolDexBotTrader",
            _tray_icon_image(),
            "Sol Dex Bot Trader",
            menu,
        )
        logger.info("Tray icon ready — use Quit to stop the bot")
        icon.run()
        _shutdown()

    try:
        _run_tray()
    except Exception:
        logger.exception("Tray/main loop failed")
        traceback.print_exc()
        _shutdown()
    finally:
        # Ensure process exits even if daemon thread lingers.
        time.sleep(0.3)
        if server_thread.is_alive():
            logger.warning("Server thread still alive — forcing exit")
            os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Last-resort: try to write crash to install logs even if setup failed early.
        try:
            work = _writable_root()
            crash = work / "logs" / "soldexbot-crash.log"
            crash.parent.mkdir(parents=True, exist_ok=True)
            crash.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        raise
