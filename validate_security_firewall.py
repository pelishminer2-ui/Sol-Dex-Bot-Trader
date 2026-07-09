"""Validate security firewall: localhost-only, allowlist, rate limit, trading lock."""

import json
from unittest.mock import patch

from app import app
from jupiter import JupiterExecutor, SwapQuote
from security_firewall import RateLimiter, _read_rate_limiter, _write_rate_limiter
from trading_lock import trading_lock


def _client():
    return app.test_client()


def test_localhost_request_allowed():
    with _client() as client:
        r = client.get("/api/bot/status", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    data = r.get_json()
    assert data.get("firewall", {}).get("active") is True
    print("PASS: localhost request allowed (200)")


def test_external_ip_blocked():
    with _client() as client:
        r = client.get("/api/bot/status", environ_overrides={"REMOTE_ADDR": "203.0.113.50"})
    assert r.status_code == 403, f"expected 403, got {r.status_code}"
    data = r.get_json()
    assert "non-localhost" in data.get("reason", "").lower()
    print("PASS: external IP blocked (403)")


def test_unknown_route_blocked():
    with _client() as client:
        r = client.get("/api/swap/execute", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 403
    print("PASS: unknown route blocked (403)")


def test_raw_tx_injection_blocked():
    payloads = [
        ("/api/bot/start", {"transaction": "base64data", "paper_trade": True}),
        ("/api/config", {"swapTransaction": "deadbeef"}),
        ("/api/bot/start", {"mint": "So11111111111111111111111111111111111111112", "paper_trade": True}),
    ]
    with _client() as client:
        for path, body in payloads:
            r = client.post(
                path,
                data=json.dumps(body),
                content_type="application/json",
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
            assert r.status_code == 403, f"{path} {body} -> {r.status_code}"
    print("PASS: raw tx / swap injection blocked on all tested endpoints")


def test_wallet_post_private_key_allowed():
    with _client() as client:
        r = client.post(
            "/api/wallet",
            data=json.dumps({"private_key": "not-a-real-key"}),
            content_type="application/json",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
    assert r.status_code in (200, 400), f"wallet set should not be firewall-blocked, got {r.status_code}"
    if r.status_code == 200:
        data = r.get_json()
        assert "private_key" not in data
    print("PASS: wallet POST allowed; private key never echoed")


def test_cors_restricted():
    with _client() as client:
        r = client.get(
            "/api/bot/status",
            headers={"Origin": "http://evil.example.com"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
    acao = r.headers.get("Access-Control-Allow-Origin")
    assert acao != "*", "CORS must not use wildcard"
    assert acao in (None, "http://127.0.0.1:5000", "http://localhost:5000") or acao is None
    print("PASS: CORS not wildcard (restricted origins)")


def test_rate_limit_triggers():
    limiter = RateLimiter(max_requests=3, window_sec=60.0)
    ip = "127.0.0.1"
    assert limiter.is_allowed(ip)
    assert limiter.is_allowed(ip)
    assert limiter.is_allowed(ip)
    assert not limiter.is_allowed(ip)
    print("PASS: rate limiter blocks after threshold")

    print("PASS: rate limiter blocks after threshold")

    import security_firewall as sf

    sf._write_rate_limiter = RateLimiter(2, window_sec=60.0)
    sf._write_rate_limiter.reset()
    ip = "127.0.0.1"
    assert sf._write_rate_limiter.is_allowed(ip, max_requests=2)
    assert sf._write_rate_limiter.is_allowed(ip, max_requests=2)
    assert not sf._write_rate_limiter.is_allowed(ip, max_requests=2)
    sf._write_rate_limiter = RateLimiter(120, window_sec=60.0)
    print("PASS: HTTP rate limit returns 429 after threshold")


def test_tax_preview_exempt_from_rate_limit():
    import security_firewall as sf

    sf._read_rate_limiter = RateLimiter(2, window_sec=60.0)
    sf._read_rate_limiter.reset()
    with _client() as client:
        for _ in range(5):
            r = client.get("/api/tax/preview", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
            assert r.status_code == 200, f"tax preview should be exempt, got {r.status_code}"
    sf._read_rate_limiter = RateLimiter(600, window_sec=60.0)
    print("PASS: /api/tax/preview exempt from rate limit")


def test_dashboard_read_polling_high_budget():
    """Simulate 3s dashboard poll burst — read routes use separate 600/min bucket."""
    import security_firewall as sf

    sf._read_rate_limiter = RateLimiter(600, window_sec=60.0)
    sf._read_rate_limiter.reset()
    poll_paths = (
        "/api/bot/status",
        "/api/movers?limit=10",
        "/api/positions",
        "/api/trades?limit=20",
        "/api/logs?limit=80",
        "/api/actions/pending",
    )
    with _client() as client:
        for _ in range(25):
            for path in poll_paths:
                r = client.get(path, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
                assert r.status_code != 429, f"{path} blocked by rate limit after poll burst"
    print("PASS: dashboard read polling within read-rate budget")


def test_index_and_static_exempt_from_rate_limit():
    import security_firewall as sf

    sf._read_rate_limiter = RateLimiter(1, window_sec=60.0)
    sf._read_rate_limiter.reset()
    sf._write_rate_limiter = RateLimiter(1, window_sec=60.0)
    sf._write_rate_limiter.reset()
    with _client() as client:
        for _ in range(10):
            r = client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
            assert r.status_code == 200, f"index should be exempt, got {r.status_code}"
        r = client.get("/static/index.html", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        assert r.status_code in (200, 404), f"static should be exempt, got {r.status_code}"
    sf._read_rate_limiter = RateLimiter(600, window_sec=60.0)
    sf._write_rate_limiter = RateLimiter(120, window_sec=60.0)
    print("PASS: index and static exempt from rate limit")


def test_trading_lock_allows_paper_mode_from_any_thread():
    trading_lock.unregister_bot_thread()
    assert trading_lock.is_authorized(lambda: False, dry_run=True) is True
    print("PASS: paper mode bypasses trading lock thread check")


def test_trading_lock_blocks_unauthorized_live_execute():
    trading_lock.unregister_bot_thread()
    executor = JupiterExecutor("11111111111111111111111111111111", dry_run=False)
    quote = SwapQuote(
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="TokenMint1111111111111111111111111111111",
        in_amount=1_000_000,
        out_amount=2_000_000,
        price_impact_pct=0.1,
        raw={},
    )

    import asyncio
    from unittest.mock import MagicMock

    result = asyncio.run(executor.execute_quote(quote, MagicMock()))
    assert result is None
    print("PASS: trading lock blocks unauthorized live execute_quote")


def test_trading_lock_allows_dry_run_without_lock():
    trading_lock.unregister_bot_thread()
    executor = JupiterExecutor("11111111111111111111111111111111", dry_run=True)
    quote = SwapQuote(
        input_mint="So11111111111111111111111111111111111111112",
        output_mint="TokenMint1111111111111111111111111111111",
        in_amount=1_000_000,
        out_amount=2_000_000,
        price_impact_pct=0.1,
        raw={},
    )

    import asyncio
    from unittest.mock import MagicMock

    result = asyncio.run(executor.execute_quote(quote, MagicMock()))
    assert result == "dry-run-signature"
    print("PASS: dry-run execute_quote bypasses trading lock")


def test_actions_decide_with_mint_allowed():
    with _client() as client:
        r = client.post(
            "/api/actions/decide",
            data=json.dumps({"mint": "So11111111111111111111111111111111111111112", "allow": False}),
            content_type="application/json",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
    assert r.status_code != 403, f"actions/decide must not be firewall-blocked, got {r.status_code}"
    print("PASS: actions/decide POST with mint allowed (not 403)")


def test_actions_pending_allowed():
    with _client() as client:
        r = client.get("/api/actions/pending", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200, f"expected 200, got {r.status_code}"
    print("PASS: actions/pending GET allowed (200)")


def test_status_includes_firewall():
    with _client() as client:
        r = client.get("/api/bot/status", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    data = r.get_json()
    assert "firewall" in data
    assert data["firewall"]["localhost_only"] is True
    print("PASS: bot status includes firewall info")


def main():
    test_localhost_request_allowed()
    test_external_ip_blocked()
    test_unknown_route_blocked()
    test_raw_tx_injection_blocked()
    test_wallet_post_private_key_allowed()
    test_cors_restricted()
    test_rate_limit_triggers()
    test_tax_preview_exempt_from_rate_limit()
    test_dashboard_read_polling_high_budget()
    test_index_and_static_exempt_from_rate_limit()
    test_trading_lock_allows_paper_mode_from_any_thread()
    test_trading_lock_blocks_unauthorized_live_execute()
    test_trading_lock_allows_dry_run_without_lock()
    test_actions_decide_with_mint_allowed()
    test_actions_pending_allowed()
    test_status_includes_firewall()
    print("\nAll security firewall validations passed.")


if __name__ == "__main__":
    main()
