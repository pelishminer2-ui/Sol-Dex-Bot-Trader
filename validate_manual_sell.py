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
    assert "manualSellPosition" in html
    # Instant sell: no confirm dialog — fires immediately on click.
    assert "confirm(" not in html.split("async function manualSellPosition", 1)[1].split(
        "async function manualDcaPosition", 1
    )[0]
    assert "Instant sell" in html or "no confirm" in html.lower()
    assert "formatSellError" in html
    assert "already_flat" in html
    assert "dataset.selling" in html
    print("PASS: Open Trades UI has Instant Sell (no confirm)")


def test_zero_balance_reconcile_clears_without_error():
    """Flat wallet + open book must clear/persist ok — not return silent fail."""
    import time
    from unittest.mock import MagicMock, patch

    from bot import TradingBot
    from strategy import Position

    bot = TradingBot(dry_run=True)
    bot.risk = MagicMock()
    pos = Position(
        mint="FlatWalletMint1111111111111111111111111111",
        symbol="FLAT",
        entry_price=1.0,
        entry_time=time.time(),
        size_sol=0.1,
        token_amount_raw=1_000_000,
        initial_token_amount_raw=1_000_000,
        remaining_token_amount_raw=1_000_000,
        tp_levels=[],
        tp_portions=[],
        target_net_profit_sol=0.002,
        fee_budget_sol=0.0,
        trough_pnl_pct=-0.02,
        profile={},
    )
    bot.strategy.positions = [pos]
    with patch.object(bot, "_persist_open_positions_safe") as persist:
        journal = bot._reconcile_zero_balance_force_sell(
            pos, current_price=0.98, cause="already_sold_or_zero_balance"
        )
        persist.assert_called()
    assert journal.get("already_flat") is True
    assert journal.get("cleared") is True
    assert journal.get("writeoff") is False
    assert bot.strategy.get_open_positions() == []
    assert bot._last_force_sell_error is None
    print("PASS: zero-balance Instant Sell clears books ok")


if __name__ == "__main__":
    test_route_allowlisted()
    test_mint_body_allowed_on_sell_route()
    test_mint_still_blocked_on_bot_start()
    test_endpoint_reachable_localhost()
    test_ui_has_sell_button()
    test_zero_balance_reconcile_clears_without_error()
    print("\nAll manual-sell checks passed.")
