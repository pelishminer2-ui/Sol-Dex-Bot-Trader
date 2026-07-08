"""Validation: scan_unified invokes all scanners when flags are enabled."""
import sys
from unittest.mock import MagicMock, patch

from config import Config
from scanner import MoverCandidate, scan_unified


def test_scan_unified_calls_all_when_enabled():
    dex_scan = MagicMock(return_value=[])
    pump_scan = MagicMock(return_value=[])
    bird_scan = MagicMock(return_value=[])
    gmgn_scan = MagicMock(return_value=[])

    with patch("scanner.MoverScanner") as dex_cls:
        dex_cls.return_value.scan = dex_scan
        with patch("pumpfun_scanner.PumpFunScanner") as pump_cls:
            pump_cls.return_value.scan = pump_scan
            with patch("birdeye_scanner.BirdeyeScanner") as bird_cls:
                bird_cls.return_value.scan = bird_scan
                with patch("gmgn_scanner.GmgnScanner") as gmgn_cls:
                    gmgn_cls.return_value.scan = gmgn_scan
                    merged, dex_count, pumpfun_count, birdeye_count, gmgn_count = scan_unified(
                        include_pumpfun=True,
                        include_birdeye=True,
                        include_gmgn=True,
                    )

    dex_scan.assert_called_once_with(fast_mode=False)
    pump_scan.assert_called_once_with(fast_mode=False)
    bird_scan.assert_called_once_with(fast_mode=False)
    gmgn_scan.assert_called_once_with(fast_mode=False)
    assert dex_count == 0
    assert pumpfun_count == 0
    assert birdeye_count == 0
    assert gmgn_count == 0
    assert merged == []
    print("PASS: scan_unified_calls_all_when_enabled")


def test_scan_unified_skips_optional_when_disabled():
    dex_scan = MagicMock(return_value=[])
    pump_scan = MagicMock(return_value=[])
    bird_scan = MagicMock(return_value=[])
    gmgn_scan = MagicMock(return_value=[])

    with patch("scanner.MoverScanner") as dex_cls:
        dex_cls.return_value.scan = dex_scan
        with patch("pumpfun_scanner.PumpFunScanner") as pump_cls:
            pump_cls.return_value.scan = pump_scan
            with patch("birdeye_scanner.BirdeyeScanner") as bird_cls:
                bird_cls.return_value.scan = bird_scan
                with patch("gmgn_scanner.GmgnScanner") as gmgn_cls:
                    gmgn_cls.return_value.scan = gmgn_scan
                    scan_unified(
                        include_pumpfun=False,
                        include_birdeye=False,
                        include_gmgn=False,
                    )

    dex_scan.assert_called_once_with(fast_mode=False)
    pump_scan.assert_not_called()
    bird_scan.assert_not_called()
    gmgn_scan.assert_not_called()
    print("PASS: scan_unified_skips_optional_when_disabled")


def test_scan_unified_first_scan_uses_fast_mode():
    dex_scan = MagicMock(return_value=[])
    pump_scan = MagicMock(return_value=[])
    bird_scan = MagicMock(return_value=[])
    gmgn_scan = MagicMock(return_value=[])

    with patch.object(Config, "FIRST_SCAN_FAST_MODE", True):
        with patch("scanner.MoverScanner") as dex_cls:
            dex_cls.return_value.scan = dex_scan
            with patch("pumpfun_scanner.PumpFunScanner") as pump_cls:
                pump_cls.return_value.scan = pump_scan
                with patch("birdeye_scanner.BirdeyeScanner") as bird_cls:
                    bird_cls.return_value.scan = bird_scan
                    with patch("gmgn_scanner.GmgnScanner") as gmgn_cls:
                        gmgn_cls.return_value.scan = gmgn_scan
                        scan_unified(
                            include_pumpfun=True,
                            include_birdeye=True,
                            include_gmgn=True,
                            first_scan=True,
                        )

    dex_scan.assert_called_once_with(fast_mode=True)
    pump_scan.assert_called_once_with(fast_mode=True)
    bird_scan.assert_called_once_with(fast_mode=True)
    gmgn_scan.assert_called_once_with(fast_mode=True)
    print("PASS: scan_unified_first_scan_uses_fast_mode")


def test_scan_unified_partial_callback_after_each_source():
    dex_mover = MoverCandidate(
        mint="DexMint",
        symbol="DEX",
        name="Dex",
        pair_address="p1",
        dex="raydium",
        price_usd=1.0,
        liquidity_usd=20000,
        volume_24h_usd=60000,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.03,
        source="dexscreener",
    )
    pump_mover = MoverCandidate(
        mint="PumpMint",
        symbol="PUMP",
        name="Pump",
        pair_address="p2",
        dex="pumpfun",
        price_usd=0.5,
        liquidity_usd=15000,
        volume_24h_usd=50000,
        momentum_pct=0.04,
        price_change_5m=0.04,
        price_change_1h=0.02,
        source="pumpfun",
    )
    partial_calls: list[tuple[int, int, int, int, int]] = []

    def on_partial(merged, dex_count, pumpfun_count, birdeye_count, gmgn_count):
        partial_calls.append(
            (len(merged), dex_count, pumpfun_count, birdeye_count, gmgn_count)
        )

    with patch("scanner.MoverScanner") as dex_cls:
        dex_cls.return_value.scan = MagicMock(return_value=[dex_mover])
        with patch("pumpfun_scanner.PumpFunScanner") as pump_cls:
            pump_cls.return_value.scan = MagicMock(return_value=[pump_mover])
            with patch("birdeye_scanner.BirdeyeScanner") as bird_cls:
                bird_cls.return_value.scan = MagicMock(return_value=[])
                with patch("gmgn_scanner.GmgnScanner") as gmgn_cls:
                    gmgn_cls.return_value.scan = MagicMock(return_value=[])
                    merged, dex_count, pumpfun_count, birdeye_count, gmgn_count = scan_unified(
                        include_pumpfun=True,
                        include_birdeye=True,
                        include_gmgn=True,
                        first_scan=True,
                        on_partial=on_partial,
                    )

    assert partial_calls == [
        (1, 1, 0, 0, 0),
        (2, 1, 1, 0, 0),
        (2, 1, 1, 0, 0),
        (2, 1, 1, 0, 0),
    ]
    assert dex_count == 1
    assert pumpfun_count == 1
    assert birdeye_count == 0
    assert gmgn_count == 0
    assert len(merged) == 2
    print("PASS: scan_unified_partial_callback_after_each_source")


def test_config_defaults_all_scanners_on():
    assert Config.SCAN_PUMPFUN is True
    assert Config.INCLUDE_PUMPFUN is True
    assert Config.scan_pumpfun_enabled() is True
    assert Config.SCAN_BIRDEYE is True
    assert Config.scan_birdeye_enabled() is True
    assert Config.SCAN_GMGN is True
    assert Config.scan_gmgn_enabled() is True
    print("PASS: config_defaults_all_scanners_on")


def test_config_first_scan_defaults():
    assert Config.FIRST_SCAN_FAST_MODE is True
    assert Config.FIRST_SCAN_DEEP_MINTS == 5
    print("PASS: config_first_scan_defaults")


def test_config_trade_candidate_top_n_default():
    assert Config.TRADE_CANDIDATE_TOP_N == 10
    assert Config.TRADE_CANDIDATE_TOP_N <= Config.WATCHLIST_TOP_N
    print("PASS: config_trade_candidate_top_n_default")


def main():
    test_scan_unified_calls_all_when_enabled()
    test_scan_unified_skips_optional_when_disabled()
    test_scan_unified_first_scan_uses_fast_mode()
    test_scan_unified_partial_callback_after_each_source()
    test_config_defaults_all_scanners_on()
    test_config_first_scan_defaults()
    test_config_trade_candidate_top_n_default()
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
