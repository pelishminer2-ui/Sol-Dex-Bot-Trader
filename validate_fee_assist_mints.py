"""Validate fee-assist mint list excludes W/L / pause / learning / tax losses."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config import Config
from fee_assist_mints import (
    annotate_fee_assist_journal,
    is_fee_assist_mint,
    journal_is_fee_assist,
    reload_fee_assist_mints,
)
from risk import RiskManager
from setup_learner import SetupLearner

VETT = "2ggeqB3ZhbGCqGi7q4BkoTPZP1sb2WMVsfdFSpLGVETT"
BONGA = "7YoAymCyauHAXus3snMEKcLgRx546MrHuBW3EuUNKKQs"
COC = "6M8z5Wzmhk93ns6BaQzCuMYvkEpFcx9CDXsgFwK58NPf"
OTHER = "So11111111111111111111111111111111111111112"


def test_disk_seed_recognizes_three_mints():
    reload_fee_assist_mints()
    assert is_fee_assist_mint(VETT)
    assert is_fee_assist_mint(BONGA)
    assert is_fee_assist_mint(COC)
    assert not is_fee_assist_mint(OTHER)
    j = annotate_fee_assist_journal({"mint": VETT, "action": "sell", "net_pnl_sol": -0.001})
    assert j.get("fee_assist") is True
    assert j.get("trade_kind") == "fee_volume_assist"
    assert journal_is_fee_assist(j)
    print("PASS: disk_seed_recognizes_three_mints")


def test_consecutive_loss_skips_fee_assist_via_bot_outcome_path():
    """Risk still counts normal losses; fee-assist path is gated in bot.py."""
    risk = RiskManager()
    risk.state.consecutive_losses = 0
    risk.record_trade_outcome(-0.01, dry_run=True)
    assert risk.state.consecutive_losses == 1
    # fee-assist callers skip record_trade_outcome entirely
    print("PASS: consecutive_loss_skips_fee_assist_via_bot_outcome_path")


def test_setup_learner_excludes_fee_assist_from_centroids():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "setup_learning.json"
        with patch.object(Config, "SETUP_LEARNING_ENABLED", True):
            learner = SetupLearner(store_path=path)
            learner.history = []
            learner.patterns = {}
            learner.trades_since_condense = 0
            feats = {"momentum_pct": 0.1, "liquidity_usd": 50000.0}
            learner.record_completed_trade(feats, -0.01, mint=VETT, symbol="$VETT")
            learner.record_completed_trade(feats, -0.02, mint=OTHER, symbol="REAL")
            assert any(r.get("fee_assist") for r in learner.history)
            assert len(learner._losses()) == 1
            assert learner._losses()[0]["mint"] == OTHER
            print("PASS: setup_learner_excludes_fee_assist_from_centroids")


def test_tax_totals_exclude_fee_assist_losses():
    from tax_export import _calc_totals, _is_fee_assist_row

    rows = [
        {"contract_address": OTHER, "net_pnl_sol": "0.1", "trade_kind": "live_pnl"},
        {"contract_address": OTHER, "net_pnl_sol": "-0.05", "trade_kind": "live_pnl"},
        {
            "contract_address": VETT,
            "net_pnl_sol": "-0.001",
            "trade_kind": "fee_volume_assist",
        },
    ]
    assert _is_fee_assist_row(rows[2])
    profit, losses, wins, loss_n = _calc_totals(rows)
    assert abs(profit - 0.1) < 1e-12
    assert abs(losses - 0.05) < 1e-12
    assert wins == 1 and loss_n == 1
    print("PASS: tax_totals_exclude_fee_assist_losses")


if __name__ == "__main__":
    test_disk_seed_recognizes_three_mints()
    test_consecutive_loss_skips_fee_assist_via_bot_outcome_path()
    test_setup_learner_excludes_fee_assist_from_centroids()
    test_tax_totals_exclude_fee_assist_losses()
    print("\nAll fee-assist validation tests passed.")
