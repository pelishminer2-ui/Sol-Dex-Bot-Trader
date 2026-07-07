"""Validate DexScreener-native scanner momentum (m5, h1, h6, h24)."""
from scanner_momentum import (
    DEXSCREENER_MOMENTUM_KEYS,
    price_changes_from_dexscreener,
    price_changes_from_external,
)


def test_dexscreener_native_keys():
    assert DEXSCREENER_MOMENTUM_KEYS == ("m5", "h1", "h6", "h24")
    print("PASS: DexScreener native keys m5/h1/h6/h24")


def test_parse_dexscreener_no_estimates():
    changes = price_changes_from_dexscreener(
        {"m5": 2.0, "h1": 5.0, "h6": 8.0, "h24": 12.0}
    )
    assert changes.change_5m == 0.02
    assert changes.change_1h == 0.05
    assert changes.change_6h == 0.08
    assert changes.change_24h == 0.12
    assert changes.discovery_momentum() == 0.12
    print("PASS: parse DexScreener priceChange")


def test_missing_windows_zero_not_estimated():
    changes = price_changes_from_dexscreener({"m5": 3.0, "h1": 1.0})
    assert changes.change_5m == 0.03
    assert changes.change_1h == 0.01
    assert changes.change_6h == 0.0
    assert changes.change_24h == 0.0
    assert changes.discovery_momentum() == 0.03
    print("PASS: missing h6/h24 stay zero")


def test_external_mapping():
    changes = price_changes_from_external(
        {"price_change_percent5m": 4.0, "price_change_percent1h": 2.0}
    )
    assert changes.discovery_momentum() == 0.04
    print("PASS: external GMGN-style mapping")


if __name__ == "__main__":
    test_dexscreener_native_keys()
    test_parse_dexscreener_no_estimates()
    test_missing_windows_zero_not_estimated()
    test_external_mapping()
    print("All validate_scanner_momentum tests passed.")
