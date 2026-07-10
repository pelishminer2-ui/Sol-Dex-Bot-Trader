"""Frozen entrypoint: start Flask GUI and open the default browser."""

from __future__ import annotations

import os
import sys
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


def main() -> None:
    bundle = _app_root()
    work = _writable_root()
    os.chdir(work)

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
            print(f"Created {env_path} from .env.example — edit before live trading.")
        except OSError as exc:
            print(f"Warning: could not create .env: {exc}", file=sys.stderr)

    # Import after path/cwd setup so config.PROJECT_ROOT resolves correctly.
    import app as flask_app  # noqa: WPS433

    # Point PROJECT_ROOT-style paths at the writable install dir when frozen.
    try:
        import config as cfg

        if getattr(sys, "frozen", False):
            cfg.PROJECT_ROOT = work
    except Exception:
        pass

    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("GUI_PORT", "5000"))
    print(
        f"\nSol Dex Bot Trader\n"
        f"  Dashboard: http://127.0.0.1:{port}\n"
        f"  Install dir: {work}\n"
        f"  Localhost only — do not expose this port.\n"
    )
    flask_app._schedule_browser_open()
    flask_app.app.run(
        host=host,
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
