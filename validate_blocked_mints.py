"""Validate mint-primary + optional ticker permanent blocklist."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from blocked_mints import (
    block_mint_permanently,
    block_symbol_permanently,
    filter_permanently_blocked_candidates,
    is_mint_permanently_blocked,
    is_permanently_blocked,
    is_symbol_permanently_blocked,
    permanent_block_skip_reason,
    reload_blocked_mints,
    reset_logged_skips,
    unblock_mint_permanently,
    unblock_symbol_permanently,
)
from config import Config
from scanner import MoverCandidate, merge_candidates
from strategy import MomentumStrategy, SignalType

CXMT_RUG_MINT = "6xdewCYC2UFtacydfQMow6B7QGp9Xk3VAzeTcSgMGNV1"
CXMT_OTHER_MINT = "CwGgvHzXmkNyoMCJ7KPjhjjn44EdknuveR2m6ff3G2vH"
CXMT_NEW_MINT = "CrrkHDEFsKrQb8ucyAYKu2iqPDuLD9n8xZpXumpnzUH4"
OTHER_SYMBOL_MINT = "So11111111111111111111111111111111111111112"


def _candidate(mint: str, symbol: str = "CXMT", momentum: float = 0.5) -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="pumpfun",
        price_usd=1.0,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=momentum,
        price_change_5m=momentum,
        price_change_1h=momentum,
        source="pumpfun",
    )


def test_disk_seed_blocks_rug_mints_only():
    reset_logged_skips()
    reload_blocked_mints()
    assert is_mint_permanently_blocked(CXMT_RUG_MINT)
    assert is_mint_permanently_blocked(CXMT_NEW_MINT)
    assert is_mint_permanently_blocked(
        "G9hBjHa9Gmom6R2QYuFYrzqpA4bCd1m8doNmhwCtrHGq"
    )
    # Mint-primary: CXMT ticker must NOT be permanently blocked.
    assert not is_symbol_permanently_blocked("CXMT")
    assert not is_symbol_permanently_blocked("cxmt")
    # Same ticker, different unlisted mint — allowed.
    assert not is_permanently_blocked(CXMT_OTHER_MINT, "CXMT")
    assert permanent_block_skip_reason(CXMT_OTHER_MINT, "CXMT") is None
    reason = permanent_block_skip_reason(CXMT_RUG_MINT, "CXMT")
    assert reason and "permanently blocked mint" in reason
    # Unrelated symbol on an unblocked mint stays open.
    assert permanent_block_skip_reason(OTHER_SYMBOL_MINT, "WBTC") is None
    print("PASS: disk_seed_blocks_rug_mints_only")


def test_strategy_entry_and_dip_reentry_mint_primary():
    reset_logged_skips()
    reload_blocked_mints()
    strategy = MomentumStrategy()
    rug = _candidate(CXMT_RUG_MINT)
    other = _candidate(CXMT_OTHER_MINT)
    unrelated = _candidate(OTHER_SYMBOL_MINT, "NOTCXMT")

    assert strategy.evaluate_entry(rug, 1.0, 0.5) == SignalType.NONE
    assert "permanently blocked" in (strategy.entry_skip_reason(rug, 0.5) or "")
    assert strategy.evaluate_dip_reentry(rug, 0.8, True, momentum=0.5) == SignalType.NONE
    assert "permanently blocked" in (
        strategy.dip_reentry_skip_reason(rug, True, momentum=0.5) or ""
    )

    # Same ticker, different mint — NOT blocked (mint-primary).
    status = strategy.mint_block_status(CXMT_OTHER_MINT, symbol="CXMT")
    assert "permanent_ticker_block" not in status["blocks"]
    assert "permanent_block" not in status["blocks"]

    # Non-CXMT symbol on a free mint is not ticker-blocked.
    status2 = strategy.mint_block_status(OTHER_SYMBOL_MINT, symbol="NOTCXMT")
    assert "permanent_ticker_block" not in status2["blocks"]
    assert "permanent_block" not in status2["blocks"]
    _ = unrelated  # constructed for clarity
    _ = other
    print("PASS: strategy_entry_and_dip_reentry_mint_primary")


def test_scanner_merge_drops_blocked_mints_only():
    reset_logged_skips()
    reload_blocked_mints()
    kept = merge_candidates(
        [
            _candidate(CXMT_RUG_MINT),
            _candidate(CXMT_OTHER_MINT),
            _candidate(OTHER_SYMBOL_MINT, "NOTCXMT"),
        ]
    )
    mints = {c.mint for c in kept}
    assert CXMT_RUG_MINT not in mints
    # Unlisted CXMT mint kept — ticker reuse is allowed.
    assert CXMT_OTHER_MINT in mints
    assert OTHER_SYMBOL_MINT in mints
    print("PASS: scanner_merge_drops_blocked_mints_only")


def test_block_api_mint_and_symbol():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "blocked_mints.json"
        with patch.object(Config, "BLOCKED_MINTS_PATH", str(path)):
            reload_blocked_mints()
            block_mint_permanently(
                CXMT_RUG_MINT, symbol="CXMT", reason="rug_pull", note="test"
            )
            assert is_mint_permanently_blocked(CXMT_RUG_MINT)
            assert not is_mint_permanently_blocked(CXMT_OTHER_MINT)
            assert not is_symbol_permanently_blocked("CXMT")
            # Mint-only: other CXMT mint still allowed until ticker blocked.
            kept = filter_permanently_blocked_candidates(
                [_candidate(CXMT_OTHER_MINT, "CXMT"), _candidate(CXMT_RUG_MINT, "CXMT")]
            )
            assert [c.mint for c in kept] == [CXMT_OTHER_MINT]

            block_symbol_permanently("cxmt", reason="rug_pull_ticker", note="test ticker")
            assert is_symbol_permanently_blocked("CXMT")
            kept2 = filter_permanently_blocked_candidates(
                [_candidate(CXMT_OTHER_MINT, "CXMT"), _candidate(OTHER_SYMBOL_MINT, "OK")]
            )
            assert [c.mint for c in kept2] == [OTHER_SYMBOL_MINT]

            cleared_sym = unblock_symbol_permanently("CXMT")
            assert cleared_sym["cleared"] is True
            cleared = unblock_mint_permanently(CXMT_RUG_MINT)
            assert cleared["cleared"] is True
            assert not is_mint_permanently_blocked(CXMT_RUG_MINT)
    # Restore process state from repo disk file.
    reload_blocked_mints()
    print("PASS: block_api_mint_and_symbol")


if __name__ == "__main__":
    test_disk_seed_blocks_rug_mints_only()
    test_strategy_entry_and_dip_reentry_mint_primary()
    test_scanner_merge_drops_blocked_mints_only()
    test_block_api_mint_and_symbol()
    print("All blocked_mints checks passed.")
