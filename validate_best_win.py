"""Validate Best Win preset, bookmark API, and config API exposure."""

from config import BEST_WIN_PRESET, Config, capture_config_snapshot


def test_best_win_preset_values():
    assert BEST_WIN_PRESET["trade_size_sol"] == 0.10
    assert BEST_WIN_PRESET["entry_momentum_pct"] == 0.0075
    assert BEST_WIN_PRESET["stop_loss_pct"] == 0.015
    assert BEST_WIN_PRESET["min_liquidity_usd"] == 15000.0
    assert BEST_WIN_PRESET["min_volume_24h_usd"] == 75000.0
    assert BEST_WIN_PRESET["non_watchlist_min_volume_24h_usd"] == 75000.0
    assert BEST_WIN_PRESET["min_momentum_pct"] == 0.020
    assert BEST_WIN_PRESET["min_expected_net_profit_sol"] == 0.002
    assert BEST_WIN_PRESET["min_net_win_sol"] == 0.003
    assert BEST_WIN_PRESET["max_entry_price_impact_pct"] == 1.0
    assert BEST_WIN_PRESET["reentry_min_momentum_pct"] == 0.015
    assert BEST_WIN_PRESET["loss_reentry_cooldown_minutes"] == 120
    assert BEST_WIN_PRESET["loss_reentry_repeat_cooldown_minutes"] == 240
    assert BEST_WIN_PRESET["weaken_exit_min_profit_pct"] == 0.01
    assert BEST_WIN_PRESET["take_profit_levels"] == [0.03, 0.04]
    assert BEST_WIN_PRESET["take_profit_portions"] == [0.5, 0.5]
    print("PASS: BEST_WIN_PRESET values")


def test_config_api_exposes_best_win():
    cfg = Config.to_dict()
    assert "best_win_preset" in cfg
    assert cfg["best_win_preset"] == BEST_WIN_PRESET
    assert cfg["win_focused_preset"] == BEST_WIN_PRESET
    assert cfg["best_win_preset"]["entry_momentum_pct"] == 0.0075
    assert cfg["best_win_preset"]["stop_loss_pct"] == 0.015
    assert cfg["best_win_preset"]["loss_reentry_repeat_cooldown_minutes"] == 240
    print("PASS: /api/config fields for Best Win")


def test_strategy_summary_uses_trade_size():
    from fee_estimator import get_fee_budget

    size = BEST_WIN_PRESET["trade_size_sol"]
    summary = Config.strategy_summary(trade_size_sol=size)
    assert summary["estimated_fees_sol"] == get_fee_budget(size)
    print("PASS: strategy_summary fee budget scales with trade size")


def test_save_bookmark_api():
    from app import app

    client = app.test_client()
    resp = client.post("/api/config/save-bookmark", json={"label": "validate-best-win"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["label"] == "validate-best-win"
    assert data.get("config_bookmark", {}).get("exists") is True
    print("PASS: POST /api/config/save-bookmark")


if __name__ == "__main__":
    test_best_win_preset_values()
    test_config_api_exposes_best_win()
    test_strategy_summary_uses_trade_size()
    test_save_bookmark_api()
    print("\nAll Best Win validation tests passed.")
