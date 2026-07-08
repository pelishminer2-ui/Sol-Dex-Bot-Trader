"""Validation tests for scanner API key config and request headers."""
import os
import sys
from unittest.mock import patch

from config import Config


def test_scanner_api_key_status_shape():
    status = Config.scanner_api_key_status()
    assert set(status.keys()) == {"dexscreener", "pumpfun", "birdeye", "gmgn", "jupiter"}
    for value in status.values():
        assert value in ("configured", "public", "skipped")
    print("PASS: scanner_api_key_status_shape")


def test_scanner_api_key_status_without_keys():
    with patch.object(Config, "DEXSCREENER_API_KEY", ""), patch.object(
        Config, "PUMPFUN_API_KEY", ""
    ), patch.object(Config, "BIRDEYE_API_KEY", ""), patch.object(
        Config, "GMGN_API_KEY", ""
    ), patch.object(
        Config, "GMGN_API_KEY", ""
    ), patch.object(
        Config, "JUPITER_API_KEY", ""
    ):
        status = Config.scanner_api_key_status()
    assert status == {
        "dexscreener": "public",
        "pumpfun": "public",
        "birdeye": "skipped",
        "gmgn": "public",
        "jupiter": "public",
    }
    print("PASS: scanner_api_key_status_without_keys")


def test_scanner_api_key_status_with_keys():
    with patch.object(Config, "DEXSCREENER_API_KEY", "dex"), patch.object(
        Config, "PUMPFUN_API_KEY", "pump"
    ), patch.object(Config, "BIRDEYE_API_KEY", "bird"), patch.object(
        Config, "GMGN_API_KEY", "gmgn"
    ), patch.object(
        Config, "GMGN_API_KEY", "gmgn"
    ), patch.object(
        Config, "JUPITER_API_KEY", "jup"
    ):
        status = Config.scanner_api_key_status()
    assert status == {
        "dexscreener": "configured",
        "pumpfun": "configured",
        "birdeye": "configured",
        "gmgn": "configured",
        "jupiter": "configured",
    }
    print("PASS: scanner_api_key_status_with_keys")


def test_dexscreener_headers_without_key():
    with patch.object(Config, "DEXSCREENER_API_KEY", ""):
        headers = Config.dexscreener_headers()
    assert headers["Accept"] == "application/json"
    assert "X-API-KEY" not in headers
    print("PASS: dexscreener_headers_without_key")


def test_dexscreener_headers_with_key():
    with patch.object(Config, "DEXSCREENER_API_KEY", "test-dex-key"):
        headers = Config.dexscreener_headers()
    assert headers["X-API-KEY"] == "test-dex-key"
    print("PASS: dexscreener_headers_with_key")


def test_pumpfun_headers_without_key():
    with patch.object(Config, "PUMPFUN_API_KEY", ""):
        headers = Config.pumpfun_headers()
    assert headers["Origin"] == "https://pump.fun"
    assert "Authorization" not in headers
    print("PASS: pumpfun_headers_without_key")


def test_pumpfun_headers_with_key():
    with patch.object(Config, "PUMPFUN_API_KEY", "jwt-token"):
        headers = Config.pumpfun_headers()
    assert headers["Authorization"] == "Bearer jwt-token"
    print("PASS: pumpfun_headers_with_key")


def test_birdeye_headers_without_key():
    with patch.object(Config, "BIRDEYE_API_KEY", ""):
        headers = Config.birdeye_headers()
    assert headers == {}
    print("PASS: birdeye_headers_without_key")


def test_birdeye_headers_with_key():
    with patch.object(Config, "BIRDEYE_API_KEY", "bird-key"):
        headers = Config.birdeye_headers()
    assert headers["X-API-KEY"] == "bird-key"
    assert headers["x-chain"] == "solana"
    print("PASS: birdeye_headers_with_key")


def test_to_dict_includes_scanner_api_keys():
    data = Config.to_dict()
    assert "scanner_api_keys" in data
    assert isinstance(data["scanner_api_keys"], dict)
    print("PASS: to_dict_includes_scanner_api_keys")


def test_log_missing_scanner_key_once():
    import config as config_module

    config_module._logged_missing_scanner_keys.clear()
    Config.log_missing_scanner_key_once("test-service", "first message")
    Config.log_missing_scanner_key_once("test-service", "second message")
    assert "test-service" in config_module._logged_missing_scanner_keys
    config_module._logged_missing_scanner_keys.discard("test-service")
    print("PASS: log_missing_scanner_key_once")


def test_gmgn_headers_without_key():
    with patch.object(Config, "GMGN_API_KEY", ""):
        headers = Config.gmgn_headers()
    assert headers["Referer"] == "https://gmgn.ai/?chain=sol"
    assert "Authorization" not in headers
    print("PASS: gmgn_headers_without_key")


def test_gmgn_headers_with_key():
    with patch.object(Config, "GMGN_API_KEY", "gmgn-key"):
        headers = Config.gmgn_headers()
    assert headers["Authorization"] == "Bearer gmgn-key"
    print("PASS: gmgn_headers_with_key")


def main():
    test_scanner_api_key_status_shape()
    test_scanner_api_key_status_without_keys()
    test_scanner_api_key_status_with_keys()
    test_dexscreener_headers_without_key()
    test_dexscreener_headers_with_key()
    test_pumpfun_headers_without_key()
    test_pumpfun_headers_with_key()
    test_birdeye_headers_without_key()
    test_birdeye_headers_with_key()
    test_gmgn_headers_without_key()
    test_gmgn_headers_with_key()
    test_to_dict_includes_scanner_api_keys()
    test_log_missing_scanner_key_once()
    print("VALIDATION_OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"VALIDATION_FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"VALIDATION_FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
