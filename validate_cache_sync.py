"""Validate cache-control headers and server time fields on API responses."""

from pathlib import Path

from app import PROJECT_ROOT, STATIC_VERSION, app, get_server_info


def _client():
    return app.test_client()


def _assert_no_cache_headers(response):
    cc = response.headers.get("Cache-Control", "")
    assert "no-store" in cc, f"expected no-store in Cache-Control, got {cc!r}"
    assert "no-cache" in cc, f"expected no-cache in Cache-Control, got {cc!r}"
    assert response.headers.get("Pragma") == "no-cache"
    assert response.headers.get("Expires") == "0"


def test_index_no_cache_headers():
    with _client() as client:
        r = client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    _assert_no_cache_headers(r)
    assert "ETag" not in r.headers
    print("PASS: index.html served with no-cache headers")


def test_static_no_cache_headers():
    asset = Path(__file__).resolve().parent / "static" / "assets" / "cats-of-crypto.png"
    if not asset.exists():
        print("SKIP: static asset missing for cache header test")
        return
    with _client() as client:
        r = client.get(
            "/static/assets/cats-of-crypto.png",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
    assert r.status_code == 200
    _assert_no_cache_headers(r)
    print("PASS: /static/* served with no-cache headers")


def test_api_status_server_time_fields():
    with _client() as client:
        r = client.get("/api/bot/status", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    _assert_no_cache_headers(r)
    data = r.get_json()
    for key in ("server_time", "server_time_unix", "timestamp", "last_updated"):
        assert key in data, f"missing {key} in status response"
    assert data["server_time"].endswith("+00:00") or "T" in data["server_time"]
    assert isinstance(data["server_time_unix"], (int, float))
    assert data["timestamp"] == data["server_time"]
    assert data["last_updated"] == data["server_time"]
    assert data["static_version"] == STATIC_VERSION
    assert data["project_root"] == str(PROJECT_ROOT)
    print("PASS: status API includes server time and sync metadata")


def test_api_status_server_info_fields():
    with _client() as client:
        r = client.get("/api/bot/status", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    data = r.get_json()
    for key in ("server", "server_host", "server_port", "server_url"):
        assert key in data, f"missing {key} in status response"
    server = data["server"]
    assert server["url"] == data["server_url"]
    assert server["host"] == data["server_host"]
    assert server["port"] == data["server_port"]
    assert server["url"].startswith("http://")
    assert str(server["port"]) in server["url"]
    expected = get_server_info()
    assert data["server_url"] == expected["url"]
    print("PASS: status API includes server info")


def test_api_status_trade_candidates_field():
    with _client() as client:
        r = client.get("/api/bot/status", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    data = r.get_json()
    assert "trade_candidates" in data, "missing trade_candidates in status response"
    assert isinstance(data["trade_candidates"], list)
    print("PASS: status API includes trade_candidates list")


def test_api_config_server_info_fields():
    with _client() as client:
        r = client.get("/api/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    data = r.get_json()
    assert "server_url" in data
    assert data["server"]["url"] == data["server_url"]
    print("PASS: config API includes server info")


def test_api_config_server_time_fields():
    with _client() as client:
        r = client.get("/api/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    _assert_no_cache_headers(r)
    data = r.get_json()
    assert "server_time" in data
    assert "last_updated" in data
    assert data["project_root"] == str(PROJECT_ROOT)
    print("PASS: config API includes server time and project_root")


if __name__ == "__main__":
    test_index_no_cache_headers()
    test_static_no_cache_headers()
    test_api_status_server_time_fields()
    test_api_status_server_info_fields()
    test_api_status_trade_candidates_field()
    test_api_config_server_time_fields()
    test_api_config_server_info_fields()
    print("\nAll cache/sync validation tests passed.")
