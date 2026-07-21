"""Validate GUI HTML/JS and core API endpoints used by dashboard buttons.

Brave browser notes:
- Brave is Chromium-based; same JS/CSS as Chrome/Edge.
- Brave Shields may block service workers or strict fetch — dashboard uses same-origin
  fetch with credentials=same-origin and degrades gracefully if SW registration fails.
- If buttons appear dead in Brave: disable Shields for 127.0.0.1, hard-refresh Ctrl+Shift+R.

Firefox notes:
- Firefox caches aggressively; stale index.html can leave bindClick handlers unbound.
- Hard-refresh Ctrl+Shift+R after server updates. Private browsing blocks localStorage (OK).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from app import STATIC_VERSION, app

PROJECT_ROOT = Path(__file__).resolve().parent
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"

REQUIRED_BUTTON_IDS = [
    "btnStart",
    "btnStop",
    "btnApplyConfig",
    "btnBestWin",
    "btnSteadyTrade",
    "btnBalancedWin",
    "btnRevertBookmark",
    "btnResetSpreads",
    "btnForceReset",
]

BRAVE_FOOTER_MARKERS = [
    "Brave users",
    "brave://settings/shields",
    "127.0.0.1",
]

FIREFOX_FOOTER_MARKERS = [
    "Firefox users",
    "Ctrl+Shift+R",
]

FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)


def _client():
    return app.test_client()


def test_index_html_has_required_buttons():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for btn_id in REQUIRED_BUTTON_IDS:
        assert f'id="{btn_id}"' in html, f"missing button id={btn_id}"
    print("PASS: required button elements present in index.html")


def test_index_javascript_parses():
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r"<script>([\s\S]*)</script>", html)
    assert match, "inline script block missing"
    js = match.group(1)
    tmp = PROJECT_ROOT / "_validate_gui_extract.js"
    tmp.write_text(js, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["node", "--check", str(tmp)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        if tmp.exists():
            tmp.unlink()
    assert proc.returncode == 0, f"JavaScript syntax error:\n{proc.stderr}"
    print("PASS: index.html JavaScript parses (node --check)")


def test_update_bookmark_hint_in_scope():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "function updateBookmarkHint(bookmark)" in html
    assert "window.updateBookmarkHint = updateBookmarkHint" in html
    fill_idx = html.find("function fillConfig(")
    hint_idx = html.find("function updateBookmarkHint(bookmark)")
    bind_idx = html.find("function bindUiHandlers(")
    assert hint_idx >= 0 and fill_idx >= 0, "updateBookmarkHint or fillConfig missing"
    assert hint_idx < fill_idx, "updateBookmarkHint must be defined before fillConfig (scope for Start Bot refresh)"
    assert hint_idx < bind_idx, "updateBookmarkHint must be defined before bindUiHandlers (preset button scope)"
    # Preset/config handlers must use window.updateBookmarkHint (Firefox-safe global)
    for fn in ("applySteadyTradePreset", "applyBalancedWinPreset", "applyBestWinPreset", "btnRevertBookmark"):
        chunk_start = html.find(fn)
        assert chunk_start >= 0, f"{fn} missing"
        chunk = html[chunk_start:chunk_start + 2500]
        if "updateBookmarkHint" in chunk:
            assert "window.updateBookmarkHint" in chunk, f"{fn} must call window.updateBookmarkHint"
    print("PASS: updateBookmarkHint defined before fillConfig and bindUiHandlers")


def test_rpc_and_wallet_persist_ui():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "let rpcTouched = false" in html
    assert "!respectTouched || !rpcTouched" in html
    assert 'id="walletKeyStatus"' in html
    assert "Key set for session" in html
    assert "omit so Start/Apply cannot wipe a saved RPC" in html
    assert "function isPublicRpcUrl(" in html
    assert "function displayRpcFromConfig(" in html
    assert 'id="rpcHeliusNote"' in html
    assert "Helius" in html
    assert "font-weight:700" in html
    # Never refill the field from public mainnet
    assert "Never inject public mainnet" in html
    assert "clearCredentialFieldsUI" in html
    assert "suppressCredFieldFill" in html
    print("PASS: RPC/wallet persist UI guards present")


def test_bind_helpers_and_no_inline_onclick():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "function bindClick(" in html
    assert "function bindChange(" in html
    assert "function isFirefox(" in html
    assert 'type="button"' in html and 'id="btnStart"' in html
    assert "credentials: \"same-origin\"" in html
    assert "registerServiceWorkerSafe" in html
    assert 'onclick="' not in html.lower().replace("addeventlistener", "")
    # Every bindClick must pass a string button id (regression: bindClick(async () => ...) broke Firefox)
    bad_bind = re.findall(r"bindClick\(\s*(?:async\s*)?\(\)", html)
    assert not bad_bind, "bindClick calls missing button id string"
    for btn_id in REQUIRED_BUTTON_IDS:
        assert f'bindClick("{btn_id}"' in html, f"bindClick missing for {btn_id}"
    print("PASS: defensive bind helpers and same-origin fetch present")


def test_brave_compat_footer():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for marker in BRAVE_FOOTER_MARKERS:
        assert marker in html, f"Brave guidance missing: {marker}"
    print("PASS: Brave compatibility footer guidance present")


def test_firefox_compat_footer():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for marker in FIREFOX_FOOTER_MARKERS:
        assert marker in html, f"Firefox guidance missing: {marker}"
    print("PASS: Firefox compatibility footer guidance present")


def test_api_bot_status():
    with _client() as client:
        r = client.get("/api/bot/status", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    data = r.get_json()
    assert "status" in data
    assert data.get("static_version") == STATIC_VERSION
    print("PASS: GET /api/bot/status")


def test_api_config_get_post():
    with _client() as client:
        r = client.get("/api/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    cfg = r.get_json()
    skip = {
        "server_time",
        "server_time_unix",
        "last_updated",
        "timestamp",
        "static_version",
        "project_root",
        "server_url",
        "server",
    }
    payload = {k: v for k, v in cfg.items() if k not in skip}
    with _client() as client:
        r2 = client.post(
            "/api/config",
            json=payload,
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
    assert r2.status_code == 200, r2.get_data(as_text=True)
    print("PASS: POST /api/config (Apply Config)")


def test_api_bot_start_stop_paper():
    from unittest.mock import patch

    with patch("bot_manager.RiskManager.can_start_trading", return_value=(True, "")):
        with patch("bot_manager.has_open_positions", return_value=False):
            with _client() as client:
                client.post("/api/bot/force-reset", json={}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
                r = client.post(
                    "/api/bot/start",
                    json={"paper_trade": True},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
                )
    assert r.status_code == 200, r.get_data(as_text=True)
    with _client() as client:
        rs = client.post("/api/bot/stop", json={}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert rs.status_code == 200
    print("PASS: POST /api/bot/start + stop (paper)")


def test_api_bot_start_firefox_ua_cors():
    """Simulate Firefox same-origin POST with Origin header."""
    from unittest.mock import patch

    with patch("bot_manager.RiskManager.can_start_trading", return_value=(True, "")):
        with patch("bot_manager.has_open_positions", return_value=False):
            with _client() as client:
                client.post("/api/bot/force-reset", json={}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
                origin = "http://127.0.0.1:5000"
                r = client.post(
                    "/api/bot/start",
                    json={"paper_trade": True},
                    headers={"Origin": origin, "User-Agent": FIREFOX_UA},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
                )
    assert r.status_code == 200, r.get_data(as_text=True)
    acao = r.headers.get("Access-Control-Allow-Origin")
    assert acao == origin, f"CORS origin mismatch for Firefox: {acao}"
    with _client() as client:
        client.post("/api/bot/stop", json={}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    print("PASS: Firefox UA + CORS POST /api/bot/start")


def main():
    tests = [
        test_index_html_has_required_buttons,
        test_index_javascript_parses,
        test_update_bookmark_hint_in_scope,
        test_rpc_and_wallet_persist_ui,
        test_bind_helpers_and_no_inline_onclick,
        test_brave_compat_footer,
        test_firefox_compat_footer,
        test_api_bot_status,
        test_api_config_get_post,
        test_api_bot_start_stop_paper,
        test_api_bot_start_firefox_ua_cors,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"FAIL: {fn.__name__}: {exc}", file=sys.stderr)
    if failed:
        print(f"\n{failed} test(s) failed.", file=sys.stderr)
        sys.exit(1)
    print(f"\nAll {len(tests)} GUI validation checks passed.")
    print("Firefox: hard-refresh Ctrl+Shift+R if Start Bot is unresponsive.")
    print("Brave: if UI is unresponsive, disable Shields for 127.0.0.1 and Ctrl+Shift+R.")


if __name__ == "__main__":
    main()
