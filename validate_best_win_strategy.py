"""Validate Best Win strategy preset, apply path, and entry/exit simulation."""

from unittest.mock import patch

from config import (
    BEST_WIN_STRATEGY_PRESET,
    Config,
    DEFAULT_WATCHLIST_MINT,
    apply_best_win_strategy,
    capture_config_snapshot,
    effective_stop_loss_pct,
)
from scanner import MoverCandidate
from strategy import MomentumStrategy, SignalType


def _candidate(mint: str = "mint123456789", symbol: str = "TEST") -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="",
        dex="test",
        price_usd=1.0,
        liquidity_usd=50000.0,
        volume_24h_usd=100000.0,
        momentum_pct=0.05,
        price_change_5m=0.05,
        price_change_1h=0.05,
        source="test",
    )


def test_best_win_strategy_preset():
    assert BEST_WIN_STRATEGY_PRESET["trade_size_sol"] == 0.10
    assert BEST_WIN_STRATEGY_PRESET["entry_momentum_pct"] == 0.0075
    assert BEST_WIN_STRATEGY_PRESET["stop_loss_pct"] == 0.015
    assert BEST_WIN_STRATEGY_PRESET["min_volume_24h_usd"] == 75000.0
    assert BEST_WIN_STRATEGY_PRESET["non_watchlist_min_volume_24h_usd"] == 75000.0
    assert BEST_WIN_STRATEGY_PRESET["min_momentum_pct"] == 0.020
    assert BEST_WIN_STRATEGY_PRESET["min_net_win_sol"] == 0.003
    assert BEST_WIN_STRATEGY_PRESET["loss_reentry_cooldown_minutes"] == 120
    assert BEST_WIN_STRATEGY_PRESET["loss_reentry_repeat_cooldown_minutes"] == 240
    assert BEST_WIN_STRATEGY_PRESET["reentry_dip_pct"] == 0.10
    assert BEST_WIN_STRATEGY_PRESET["max_potential_mode"] is False
    assert BEST_WIN_STRATEGY_PRESET["block_stock_related_tokens"] is True
    cfg = Config.to_dict()
    assert cfg["best_win_strategy_preset"] == BEST_WIN_STRATEGY_PRESET
    print("PASS: BEST_WIN_STRATEGY_PRESET values")


def test_apply_best_win_strategy_api():
    from app import app
    from config import BOOKMARK_RUNTIME_KEYS

    snap_before = capture_config_snapshot()
    client = app.test_client()
    resp = client.post(
        "/api/config/apply-best-win-strategy",
        json={"save_bookmark": True},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["preset"] == "best_win_strategy"
    assert data["config_bookmark"]["exists"] is True
    assert Config.TRADE_SIZE_SOL == 0.10
    assert Config.ENTRY_MOMENTUM_PCT == 0.0075
    assert Config.STOP_LOSS_PCT == 0.015
    assert Config.MIN_VOLUME_24H_USD == 75000.0
    assert Config.NON_WATCHLIST_MIN_VOLUME_24H_USD == 75000.0
    assert Config.MIN_NET_WIN_SOL == 0.003
    assert Config.MIN_MOMENTUM_PCT == 0.020
    assert Config.LOSS_REENTRY_COOLDOWN_MINUTES == 120
    assert Config.LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES == 240
    assert Config.MAX_POTENTIAL_MODE is False
    runtime = {
        BOOKMARK_RUNTIME_KEYS[k]: snap_before[k]
        for k in BOOKMARK_RUNTIME_KEYS
        if k in snap_before
    }
    Config.update_runtime(**runtime)
    print("PASS: POST /api/config/apply-best-win-strategy")


def test_entry_exit_simulation():
    """Simulate Best Win entry → hold below 3.25% → instant +5% full exit."""
    strategy = MomentumStrategy()
    candidate = _candidate()

    with patch.object(Config, "ENTRY_MOMENTUM_PCT", 0.0075), patch.object(
        Config, "MIN_NET_WIN_SOL", 0.002
    ), patch.object(Config, "INSTANT_PROFIT_EXIT_ENABLED", True), patch.object(
        Config, "INSTANT_EXIT_3PCT", 0.0325
    ), patch.object(Config, "INSTANT_PROFIT_EXIT_PCT", 0.05), patch.object(
        Config, "TAKE_PROFIT_LEVELS", []
    ):
        assert strategy.evaluate_entry(candidate, 1.0, 0.007) == SignalType.NONE
        assert strategy.evaluate_entry(candidate, 1.0, 0.008) == SignalType.BUY

        pos = strategy.open_position(
            candidate, 1.0, 0.10, 0.008, token_amount_raw=1_000_000
        )

        assert strategy.evaluate_exit(pos, 1.03) is None

        signal = strategy.evaluate_exit(pos, 1.05)
        assert signal is not None
        assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT

    print("PASS: Best Win entry/exit simulation (momentum in, +3% hold, +5% out)")


def test_loss_cooldown_blocks_repeat_loser():
    strategy = MomentumStrategy()
    mint = "lossmint987654321"
    with patch.object(Config, "LOSS_REENTRY_COOLDOWN_MINUTES", 120), patch.object(
        Config, "LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES", 240
    ):
        strategy.record_loss_reentry_cooldown(mint)
        assert strategy.is_on_loss_reentry_cooldown(mint)
        result = strategy.evaluate_entry(_candidate(mint=mint), 1.0, 0.06)
    assert result == SignalType.NONE
    print("PASS: loss cooldown blocks repeat-loser re-entry")


def test_wbtc_stop_loss_wider_than_memecoin():
    assert effective_stop_loss_pct(DEFAULT_WATCHLIST_MINT) == Config.WBTC_STOP_LOSS_PCT
    assert effective_stop_loss_pct("SomeMemecoinMint111111111111111111111") == Config.STOP_LOSS_PCT
    assert Config.WBTC_STOP_LOSS_PCT > Config.STOP_LOSS_PCT
    print("PASS: WBTC uses wider stop-loss than memecoins")


if __name__ == "__main__":
    test_best_win_strategy_preset()
    test_apply_best_win_strategy_api()
    test_entry_exit_simulation()
    test_loss_cooldown_blocks_repeat_loser()
    test_wbtc_stop_loss_wider_than_memecoin()
    print("\nAll Best Win strategy validation tests passed.")
