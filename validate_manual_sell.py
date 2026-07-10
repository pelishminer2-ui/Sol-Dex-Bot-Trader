"""Smoke-check manual sell API route + firewall allowlist."""

import json

from app import app
from security_firewall import ALLOWED_ROUTES, _body_has_forbidden_fields, _route_allowed


def test_route_allowlisted():
    assert ("POST", "/api/positions/sell") in ALLOWED_ROUTES
    assert _route_allowed("POST", "/api/positions/sell")
    print("PASS: POST /api/positions/sell allowlisted")


def test_mint_body_allowed_on_sell_route():
    with app.test_request_context(
        "/api/positions/sell",
        method="POST",
        json={"mint": "So11111111111111111111111111111111111111112", "reason": "sell_manual"},
    ):
        blocked = _body_has_forbidden_fields()
        assert blocked is None, f"mint should be allowed on sell route, got: {blocked}"
    print("PASS: mint field allowed on /api/positions/sell")


def test_mint_still_blocked_on_bot_start():
    with app.test_request_context(
        "/api/bot/start",
        method="POST",
        json={"mint": "So11111111111111111111111111111111111111112", "paper_trade": True},
    ):
        blocked = _body_has_forbidden_fields()
        assert blocked is not None
    print("PASS: mint still blocked on /api/bot/start")


def test_endpoint_reachable_localhost():
    with app.test_client() as client:
        r = client.post(
            "/api/positions/sell",
            data=json.dumps({}),
            content_type="application/json",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert r.status_code == 400, f"expected 400 missing mint/symbol, got {r.status_code}"
        data = r.get_json() or {}
        assert data.get("ok") is False
        assert "mint" in (data.get("error") or "").lower() or "symbol" in (data.get("error") or "").lower()

        r2 = client.post(
            "/api/positions/sell",
            data=json.dumps({"mint": "FakeMint1111111111111111111111111111111111111"}),
            content_type="application/json",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        # Not firewall-blocked; may 400 if no open position
        assert r2.status_code != 403, f"firewall blocked sell: {r2.get_json()}"
        data2 = r2.get_json() or {}
        assert data2.get("ok") is False
        assert data2.get("error") in ("no_open_position_found", "sell_failed") or data2.get("error")
    print("PASS: /api/positions/sell reachable (not firewall-blocked)")


def test_ui_has_sell_button():
    from pathlib import Path

    html = Path("static/index.html").read_text(encoding="utf-8")
    assert "/api/positions/sell" in html
    assert "btn-sell-position" in html
    assert 'Sell "' in html or "Sell " in html
    assert "Sell " in html and "now?" in html
    print("PASS: Open Trades UI has Sell button + confirm")


if __name__ == "__main__":
    test_route_allowlisted()
    test_mint_body_allowed_on_sell_route()
    test_mint_still_blocked_on_bot_start()
    test_endpoint_reachable_localhost()
    test_ui_has_sell_button()
    print("\nAll manual-sell checks passed.")
