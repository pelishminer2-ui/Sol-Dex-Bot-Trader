"""Validate stock-backed / tokenized equity token filter."""

from unittest.mock import patch

from config import Config, DEFAULT_WATCHLIST_MINT
from scanner import MoverCandidate, merge_candidates
from stock_token_filter import (
    is_stock_related_token,
    log_skipped_stock_token,
    reset_logged_skips,
)
from strategy import MomentumStrategy, SignalType


WATCHLIST_MINT = DEFAULT_WATCHLIST_MINT
TSLAX_MINT = "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB"
AAPLX_MINT = "XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp"
SPCX_MINT = "FSiC43YFG6cJswvTVJQowNDPXHu6LTJDdNT7HCwipump"


def _candidate(
    mint: str,
    symbol: str,
    name: str = "",
    momentum: float = 0.02,
) -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=name or symbol,
        pair_address="pair",
        dex="raydium",
        price_usd=1.0,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=momentum,
        price_change_5m=momentum,
        price_change_1h=momentum,
    )


def test_blocks_known_xstock_mints():
    reset_logged_skips()
    assert is_stock_related_token(mint=TSLAX_MINT, symbol="TSLAx")
    assert is_stock_related_token(mint=AAPLX_MINT, symbol="AAPLx")
    print("PASS: blocks_known_xstock_mints")


def test_blocks_stock_tickers_and_x_suffix():
    reset_logged_skips()
    assert is_stock_related_token(symbol="TSLA")
    assert is_stock_related_token(symbol="tslax")
    assert is_stock_related_token(symbol="NVDAx")
    assert is_stock_related_token(symbol="SPY")
    assert is_stock_related_token(symbol="QQQ")
    assert is_stock_related_token(symbol="SPCX")
    print("PASS: blocks_stock_tickers_and_x_suffix")


def test_blocks_spcx_mint_and_spacex_name():
    reset_logged_skips()
    assert is_stock_related_token(mint=SPCX_MINT, symbol="SPCX", name="SPCX")
    assert is_stock_related_token(
        mint=SPCX_MINT,
        symbol="SPCX",
        name="SpaceXAI",
    )
    assert is_stock_related_token(
        mint="FakeMint3333333333333333333333333333333",
        symbol="FOO",
        name="SpaceXAI token",
    )
    print("PASS: blocks_spcx_mint_and_spacex_name")


def test_blocks_name_keywords():
    reset_logged_skips()
    assert is_stock_related_token(
        mint="FakeMint1111111111111111111111111111111",
        symbol="FOO",
        name="Apple xStock tokenized equity",
    )
    assert is_stock_related_token(
        mint="FakeMint2222222222222222222222222222222",
        symbol="BAR",
        name="Backed Finance tokenized US stock",
    )
    print("PASS: blocks_name_keywords")


def test_allows_wbtc_watchlist_mint():
    reset_logged_skips()
    assert not is_stock_related_token(
        mint=WATCHLIST_MINT,
        symbol="WBTC",
        name="Wrapped BTC (Wormhole)",
    )
    print("PASS: allows_wbtc_watchlist_mint")


def test_allows_regular_meme_tokens():
    reset_logged_skips()
    assert not is_stock_related_token(
        mint="So11111111111111111111111111111111111111112",
        symbol="BONK",
        name="Bonk",
    )
    assert not is_stock_related_token(
        mint="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
        symbol="POPCAT",
        name="Popcat",
    )
    print("PASS: allows_regular_meme_tokens")


def test_filter_disabled_allows_stock_token():
    reset_logged_skips()
    with patch.object(Config, "BLOCK_STOCK_RELATED_TOKENS", False):
        assert not is_stock_related_token(symbol="TSLAx", mint=TSLAX_MINT)
    print("PASS: filter_disabled_allows_stock_token")


def test_merge_candidates_excludes_stock_tokens():
    reset_logged_skips()
    meme = _candidate("MemeMint111111111111111111111111111111", "BONK", momentum=0.03)
    stock = _candidate(TSLAX_MINT, "TSLAx", momentum=0.05)
    merged = merge_candidates([meme], [stock])
    assert len(merged) == 1
    assert merged[0].mint == meme.mint
    print("PASS: merge_candidates_excludes_stock_tokens")


def test_strategy_evaluate_entry_blocks_stock():
    reset_logged_skips()
    strategy = MomentumStrategy()
    stock = _candidate(TSLAX_MINT, "TSLAx")
    assert strategy.evaluate_entry(stock, 100.0, momentum=0.02) == SignalType.NONE
    print("PASS: strategy_evaluate_entry_blocks_stock")


def test_strategy_evaluate_entry_allows_wbtc_watchlist():
    reset_logged_skips()
    strategy = MomentumStrategy()
    wbtc = _candidate(WATCHLIST_MINT, "WBTC", name="Wrapped BTC (Wormhole)")
    with patch("watchlist_scanner.watchlist_usd_gain_qualifies", return_value=True):
        with patch("strategy.is_pinned_watchlist_mint", return_value=True):
            assert strategy.evaluate_entry(wbtc, 65000.0, momentum=0.0, usd_gain=80.0) == SignalType.BUY
    print("PASS: strategy_evaluate_entry_allows_wbtc_watchlist")


def test_log_once_per_mint():
    reset_logged_skips()
    with patch("stock_token_filter.logger") as mock_logger:
        log_skipped_stock_token(TSLAX_MINT, "TSLAx")
        log_skipped_stock_token(TSLAX_MINT, "TSLAx")
        assert mock_logger.info.call_count == 1
        args = mock_logger.info.call_args[0]
        assert args[0] == "skipped stock-related token: %s"
        assert args[1] == "TSLAx"
    print("PASS: log_once_per_mint")


def test_risk_check_entry_blocks_stock():
    reset_logged_skips()
    from risk import RiskManager

    risk = RiskManager()
    ok, reason = risk.check_entry_eligibility(
        0.05,
        50000.0,
        0.1,
        mint=TSLAX_MINT,
        symbol="TSLAx",
    )
    assert not ok
    assert "skipped stock-related token" in reason
    print("PASS: risk_check_entry_blocks_stock")


if __name__ == "__main__":
    test_blocks_known_xstock_mints()
    test_blocks_stock_tickers_and_x_suffix()
    test_blocks_spcx_mint_and_spacex_name()
    test_blocks_name_keywords()
    test_allows_wbtc_watchlist_mint()
    test_allows_regular_meme_tokens()
    test_filter_disabled_allows_stock_token()
    test_merge_candidates_excludes_stock_tokens()
    test_strategy_evaluate_entry_blocks_stock()
    test_strategy_evaluate_entry_allows_wbtc_watchlist()
    test_log_once_per_mint()
    test_risk_check_entry_blocks_stock()
    print("ALL STOCK FILTER TESTS PASSED")
