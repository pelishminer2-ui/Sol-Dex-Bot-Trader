"""
Validate JitoSOL/WETH proxy companion trades (entry-side only).

Proves:
  (a) With JitoSOL or WETH proxy open and COMPANION_TRADE_ENABLED, a second
      companion entry is allowed.
  (b) Without a proxy anchor in play, the normal position cap still applies.
  (c) The companion must still pass all entry filters (win-lean / spike-trap).
  (d) Each leg gets the full independent exit stack (stop loss unchanged).
"""

from unittest.mock import patch

from config import (
    Config,
    JITOSOL_MINT,
    WETH_MINT,
    can_open_more_positions,
    companion_slot_open,
    companion_trade_enabled,
    is_companion_anchor_mint,
    is_proxy_companion_anchor_mint,
    max_allowed_open_positions,
    max_positions_with_companion,
    proxy_companion_slot_open,
    wbtc_companion_slot_open,
    DEFAULT_WATCHLIST_MINT,
)
from entry_filters import entry_winrate_skip_reason
from risk import RiskManager
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType


MINT_A = "MintA1111111111111111111111111111111111111"
MINT_B = "MintB2222222222222222222222222222222222222"
WBTC_MINT = DEFAULT_WATCHLIST_MINT


def _candidate(
    mint: str,
    symbol: str,
    *,
    momentum_pct: float = 40.0,
    price_change_5m: float = 5.0,
    price_change_1h: float = 8.0,
    liquidity_usd: float = 50000.0,
    source: str = "pumpfun",
) -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="raydium",
        price_usd=1.0,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=100000.0,
        momentum_pct=momentum_pct,
        price_change_5m=price_change_5m,
        price_change_1h=price_change_1h,
        price_change_6h=price_change_1h,
        price_change_24h=price_change_1h,
        source=source,
    )


def _companion_enabled_ctx():
    return patch.multiple(
        Config,
        COMPANION_TRADE_ENABLED=True,
        COMPANION_TRADE_MAX=1,
        ENABLE_SOL_TRADING=True,
        SOL_TRADE_MINT=JITOSOL_MINT,
        ENABLE_WETH_TRADING=True,
        WETH_MINT=WETH_MINT,
    )


def test_companion_config_defaults():
    assert Config.COMPANION_TRADE_ENABLED is True
    assert Config.COMPANION_TRADE_MAX == 1
    assert max_positions_with_companion() == 2
    print("PASS: companion trade config defaults")


def test_wbtc_companion_slot_still_works():
    assert wbtc_companion_slot_open([WBTC_MINT]) is True
    assert companion_slot_open([WBTC_MINT]) is True
    assert is_companion_anchor_mint(WBTC_MINT) is True
    assert wbtc_companion_slot_open([WBTC_MINT, MINT_A]) is False
    assert max_allowed_open_positions([WBTC_MINT]) == 2
    print("PASS: WBTC companion slot unchanged")


def test_wbtc_open_allows_companion():
    with _companion_enabled_ctx():
        strategy = MomentumStrategy()
        strategy.open_position(
            _candidate(WBTC_MINT, "WBTC"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert companion_slot_open([WBTC_MINT]) is True
        assert strategy.can_open_more() is True
        assert strategy.can_open_more(MINT_A) is True
        assert max_allowed_open_positions([WBTC_MINT]) == 2
        risk = RiskManager()
        ok, _ = risk.can_open_position(
            1,
            1.0,
            dry_run=True,
            open_mints=[WBTC_MINT],
            candidate_mint=MINT_A,
        )
        assert ok
        strategy.open_position(
            _candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert strategy.open_position_count() == 2
        assert strategy.can_open_more() is False
    print("PASS: WBTC open allows one companion entry")


def test_wbtc_one_strike_does_not_block_companion_memecoin():
    """WBTC one-strike blocks WBTC re-entry only, not a companion memecoin leg."""
    with _companion_enabled_ctx(), patch.object(
        Config, "LOSS_ONE_STRIKE_PER_SESSION", True
    ):
        strategy = MomentumStrategy()
        strategy.record_loss_reentry_cooldown(WBTC_MINT)
        assert strategy.is_on_loss_reentry_cooldown(WBTC_MINT) is True
        strategy.open_position(
            _candidate(WBTC_MINT, "WBTC"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        good = _candidate(MINT_B, "GOOD", momentum_pct=40.0, source="pumpfun")
        with patch("entry_filters.entry_winrate_skip_reason", return_value=None):
            signal = strategy.evaluate_entry(
                good, 1.0, 0.05, sol_trend_snapshot={"sol_trend_ok": True}
            )
        assert signal == SignalType.BUY
        wbtc_signal = strategy.evaluate_entry(
            _candidate(WBTC_MINT, "WBTC2"), 1.0, 0.05, sol_trend_snapshot={}
        )
        assert wbtc_signal == SignalType.NONE
        fresh = MomentumStrategy()
        fresh.record_loss_reentry_cooldown(WBTC_MINT)
        blocked = fresh.evaluate_entry(
            _candidate(WBTC_MINT, "WBTC"), 1.0, 0.05, sol_trend_snapshot={}
        )
        assert blocked == SignalType.NONE
        wbtc_skip = fresh.entry_skip_reason(
            _candidate(WBTC_MINT, "WBTC"), 0.05, sol_trend_snapshot={}
        )
        assert wbtc_skip and "one-strike" in wbtc_skip
    print("PASS: WBTC one-strike does not block companion memecoin")


def test_proxy_anchor_recognition():
    with _companion_enabled_ctx():
        assert is_proxy_companion_anchor_mint(JITOSOL_MINT) is True
        assert is_proxy_companion_anchor_mint(WETH_MINT) is True
        assert is_proxy_companion_anchor_mint(MINT_A) is False
    with patch.object(Config, "COMPANION_TRADE_ENABLED", False):
        assert is_proxy_companion_anchor_mint(JITOSOL_MINT) is False
    print("PASS: proxy anchor mint recognition")


def test_jitosol_open_allows_companion():
    with _companion_enabled_ctx():
        strategy = MomentumStrategy()
        strategy.open_position(
            _candidate(JITOSOL_MINT, "JitoSOL"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert proxy_companion_slot_open([JITOSOL_MINT]) is True
        assert strategy.can_open_more() is True
        assert strategy.can_open_more(MINT_A) is True
        assert max_allowed_open_positions([JITOSOL_MINT]) == 2
        strategy.open_position(
            _candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert strategy.open_position_count() == 2
        assert strategy.can_open_more() is False
    print("PASS: JitoSOL open allows one companion entry")


def test_weth_open_allows_companion():
    with _companion_enabled_ctx():
        strategy = MomentumStrategy()
        strategy.open_position(
            _candidate(WETH_MINT, "WETH"), 3500.0, 0.05, 0.003, token_amount_raw=100
        )
        assert proxy_companion_slot_open([WETH_MINT]) is True
        assert strategy.can_open_more(MINT_B) is True
        risk = RiskManager()
        ok, _ = risk.can_open_position(
            1,
            1.0,
            dry_run=True,
            open_mints=[WETH_MINT],
            candidate_mint=MINT_B,
        )
        assert ok
    print("PASS: WETH open allows one companion entry")


def test_no_proxy_normal_cap_applies():
    with _companion_enabled_ctx():
        strategy = MomentumStrategy()
        strategy.open_position(
            _candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert proxy_companion_slot_open([MINT_A]) is False
        assert strategy.can_open_more() is False
        assert strategy.can_open_more(MINT_B) is False
        assert max_allowed_open_positions([MINT_A]) == 1
        risk = RiskManager()
        blocked, reason = risk.can_open_position(
            1,
            1.0,
            dry_run=True,
            open_mints=[MINT_A],
            candidate_mint=MINT_B,
        )
        assert not blocked
        assert "max open positions" in reason
        assert can_open_more_positions([MINT_A]) is False
    print("PASS: without proxy anchor, normal single-position cap applies")


def test_companion_disabled_restores_single_cap():
    with patch.multiple(
        Config,
        COMPANION_TRADE_ENABLED=False,
        ENABLE_SOL_TRADING=True,
        SOL_TRADE_MINT=JITOSOL_MINT,
    ):
        assert companion_trade_enabled() is False
        strategy = MomentumStrategy()
        strategy.open_position(
            _candidate(JITOSOL_MINT, "JitoSOL"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert strategy.can_open_more() is False
        assert max_allowed_open_positions([JITOSOL_MINT]) == 1
        assert companion_slot_open([JITOSOL_MINT]) is False
        strategy_wbtc = MomentumStrategy()
        strategy_wbtc.open_position(
            _candidate(WBTC_MINT, "WBTC"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert max_allowed_open_positions([WBTC_MINT]) == 2
        assert wbtc_companion_slot_open([WBTC_MINT]) is False
    print("PASS: COMPANION_TRADE_ENABLED=false restores single-position cap")


def test_proxy_plus_companion_blocks_third():
    with _companion_enabled_ctx():
        strategy = MomentumStrategy()
        strategy.open_position(
            _candidate(JITOSOL_MINT, "JitoSOL"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        strategy.open_position(
            _candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert proxy_companion_slot_open([JITOSOL_MINT, MINT_A]) is False
        assert strategy.can_open_more() is False
        risk = RiskManager()
        blocked, reason = risk.can_open_position(
            2,
            1.0,
            dry_run=True,
            open_mints=[JITOSOL_MINT, MINT_A],
            candidate_mint=MINT_B,
        )
        assert not blocked
        assert "max open positions" in reason
    print("PASS: proxy + companion blocks third entry")


def test_companion_still_passes_entry_filters():
    """Companion memecoin must clear win-rate / spike-trap gates."""
    dump = _candidate(
        MINT_A,
        "DUMP",
        momentum_pct=500.0,
        price_change_5m=0.0,
        price_change_1h=0.0,
        liquidity_usd=1000.0,
        source="dexscreener",
    )
    assert entry_winrate_skip_reason(dump, None) is not None

    with _companion_enabled_ctx(), patch.object(
        Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", True
    ):
        strategy = MomentumStrategy()
        strategy.open_position(
            _candidate(WETH_MINT, "WETH"), 3500.0, 0.05, 0.003, token_amount_raw=100
        )
        signal = strategy.evaluate_entry(dump, 1.0, 0.05, sol_trend_snapshot={})
        assert signal == SignalType.NONE
        skip = strategy.entry_skip_reason(dump, 0.05, sol_trend_snapshot={})
        assert skip and (
            "win-rate" in skip.lower()
            or "spike" in skip.lower()
            or "instant" in skip.lower()
            or "momentum" in skip.lower()
            or "dump" in skip.lower()
            or "liquidity" in skip.lower()
        )

        good = _candidate(MINT_B, "GOOD", momentum_pct=40.0, source="pumpfun")
        with patch("entry_filters.entry_winrate_skip_reason", return_value=None):
            signal_ok = strategy.evaluate_entry(
                good, 1.0, 0.05, sol_trend_snapshot={"sol_trend_ok": True}
            )
        assert signal_ok == SignalType.BUY
    print("PASS: companion still subject to entry filters")


def test_independent_exit_stack_per_leg():
    """Each position gets its own stop-loss evaluation (memecoin 1.5%, proxy rules)."""
    with _companion_enabled_ctx():
        strategy = MomentumStrategy()
        proxy_pos = strategy.open_position(
            _candidate(WETH_MINT, "WETH"), 3500.0, 0.05, 0.003, token_amount_raw=100
        )
        mem_pos = strategy.open_position(
            _candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=100
        )

        proxy_sl_price = 3500.0 * (1.0 - Config.STOP_LOSS_PCT)
        mem_sl_price = 1.0 * (1.0 - Config.STOP_LOSS_PCT)

        proxy_exit = strategy.evaluate_exit(proxy_pos, proxy_sl_price - 1.0)
        mem_exit = strategy.evaluate_exit(mem_pos, mem_sl_price - 0.01)

        assert proxy_exit is not None
        assert proxy_exit.signal_type == SignalType.SELL_SL
        assert mem_exit is not None
        assert mem_exit.signal_type == SignalType.SELL_SL

        assert proxy_pos.mint != mem_pos.mint
        assert len(strategy.positions) == 2
    print("PASS: independent exit stack per leg (stop loss unchanged)")


def test_proxy_as_second_entry_when_memecoin_held():
    with _companion_enabled_ctx():
        strategy = MomentumStrategy()
        strategy.open_position(
            _candidate(MINT_A, "AAA"), 1.0, 0.05, 0.003, token_amount_raw=100
        )
        assert strategy.can_open_more(JITOSOL_MINT) is True
        assert max_allowed_open_positions([MINT_A], JITOSOL_MINT) == 2
    print("PASS: proxy may enter as second leg when companion enabled")


def main():
    tests = [
        test_companion_config_defaults,
        test_wbtc_companion_slot_still_works,
        test_wbtc_open_allows_companion,
        test_wbtc_one_strike_does_not_block_companion_memecoin,
        test_proxy_anchor_recognition,
        test_jitosol_open_allows_companion,
        test_weth_open_allows_companion,
        test_no_proxy_normal_cap_applies,
        test_companion_disabled_restores_single_cap,
        test_proxy_plus_companion_blocks_third,
        test_companion_still_passes_entry_filters,
        test_independent_exit_stack_per_leg,
        test_proxy_as_second_entry_when_memecoin_held,
    ]
    for test in tests:
        test()
    print(f"\nAll {len(tests)} companion trade validation tests passed.")


if __name__ == "__main__":
    main()
