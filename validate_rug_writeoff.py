"""Validate rug / 100% loss auto-writeoff bookkeeping + alert queue."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from blocked_mints import (
    is_mint_permanently_blocked,
    reload_blocked_mints,
    unblock_mint_permanently,
)
from bot import TradingBot, RUG_INCINERATOR_BURN_TEXT
from config import Config
from strategy import Position


def _fake_position(
    *,
    mint: str = "RugWriteoffTestMint111111111111111111111111",
    symbol: str = "RUGT",
    size_sol: float = 0.1,
    entry_price: float = 1.0,
) -> Position:
    return Position(
        mint=mint,
        symbol=symbol,
        entry_price=entry_price,
        entry_time=time.time() - 60,
        size_sol=size_sol,
        token_amount_raw=1_000_000,
        initial_token_amount_raw=1_000_000,
        remaining_token_amount_raw=1_000_000,
        token_decimals=6,
        tp_levels_hit=[],
        tp_levels=[],
        tp_portions=[],
        target_net_profit_sol=0.002,
        fee_budget_sol=0.0,
        estimated_fees_sol=0.0,
        fees_allocated_sol=0.0,
        realized_net_pnl_sol=0.0,
        momentum_at_entry=0.05,
        l1_protection_armed=False,
        peak_pnl_pct=0.0,
        trough_pnl_pct=-1.0,
        profile={},
        buy_count=1,
    )


def test_is_rug_loss_threshold():
    bot = TradingBot(dry_run=True)
    pos = _fake_position()
    pos.trough_pnl_pct = 0.0
    assert bot._is_rug_loss(pos, 0.0, allow_trough_alone=False) is True
    assert bot._is_rug_loss(pos, 0.001, allow_trough_alone=False) is True  # -99.9%
    assert bot._is_rug_loss(pos, 0.01, allow_trough_alone=False) is False  # -99%
    pos.trough_pnl_pct = -1.0
    # trough alone should NOT force writeoff when mark is fine
    assert bot._is_rug_loss(pos, 0.5, allow_trough_alone=False) is False
    assert bot._is_rug_loss(pos, 0.5, allow_trough_alone=True) is True
    pos.trough_pnl_pct = -0.02
    assert bot._is_rug_loss(pos, 0.5, allow_trough_alone=True) is False
    print("ok: rug threshold detection")


def test_writeoff_books_auto_books_and_alerts():
    mint = "RugWriteoffTestMint111111111111111111111111"
    try:
        unblock_mint_permanently(mint)
    except Exception:
        pass
    reload_blocked_mints()

    bot = TradingBot(dry_run=True)
    bot.risk = MagicMock()
    bot.risk.journal_write = MagicMock()
    bot.setup_learner = MagicMock()
    bot.setup_learner.learning_active = False
    Config.SETUP_LEARNING_ENABLED = True

    # Avoid disk/tax side effects where possible.
    import bot as bot_mod

    bot_mod.append_tax_row = MagicMock()
    bot_mod.pnl_tracker = MagicMock()
    bot_mod.pnl_tracker.record_from_journal = MagicMock()
    bot_mod.paper_session_manager = MagicMock()
    bot_mod.paper_session_manager.record_sell = MagicMock()
    bot_mod.record_session_exit = MagicMock()

    pos = _fake_position(mint=mint, size_sol=0.123)
    bot.strategy.positions = [pos]

    journal = bot.writeoff_position_books(pos, reason="sell_rug_writeoff")

    assert journal["reason"] == "sell_rug_writeoff"
    assert journal["pnl_pct"] == -1.0
    assert abs(float(journal["net_pnl_sol"]) + 0.123) < 1e-9
    assert journal.get("sol_out") == 0.0
    assert journal.get("writeoff") is True
    assert journal.get("rug_pull") is True
    assert bot.strategy.get_open_positions() == []
    assert is_mint_permanently_blocked(mint)

    alerts = bot.get_pending_user_alerts()
    assert len(alerts) == 1
    assert alerts[0]["type"] == "rug_writeoff"
    assert alerts[0]["message"] == RUG_INCINERATOR_BURN_TEXT
    assert "sol-incinerator.com" in (alerts[0].get("url") or "")
    assert bot.ack_user_alert(alerts[0]["id"]) is True
    assert bot.get_pending_user_alerts() == []

    # setup learning recorded with writeoff reason
    assert bot.setup_learner.record_completed_trade.called
    kwargs = bot.setup_learner.record_completed_trade.call_args
    # positional: features, net_pnl_sol; kwargs include exit_reason
    assert kwargs.kwargs.get("exit_reason") == "sell_rug_writeoff" or (
        len(kwargs.args) >= 2 and kwargs.kwargs.get("exit_reason") == "sell_rug_writeoff"
    )

    unblock_mint_permanently(mint)
    print("ok: auto book writeoff + alert + mint block")


def test_rug_alerts_coalesce_into_one():
    """Multiple rugs append into a single pending alert — never stack popups."""
    mint_a = "RugCoalesceMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    mint_b = "RugCoalesceMintBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
    for m in (mint_a, mint_b):
        try:
            unblock_mint_permanently(m)
        except Exception:
            pass
    reload_blocked_mints()

    bot = TradingBot(dry_run=True)
    bot.risk = MagicMock()
    bot.risk.journal_write = MagicMock()
    bot.setup_learner = MagicMock()
    bot.setup_learner.learning_active = False

    import bot as bot_mod

    bot_mod.append_tax_row = MagicMock()
    bot_mod.pnl_tracker = MagicMock()
    bot_mod.pnl_tracker.record_from_journal = MagicMock()
    bot_mod.paper_session_manager = MagicMock()
    bot_mod.paper_session_manager.record_sell = MagicMock()
    bot_mod.record_session_exit = MagicMock()

    pos_a = _fake_position(mint=mint_a, symbol="RUG1", size_sol=0.1)
    pos_b = _fake_position(mint=mint_b, symbol="RUG2", size_sol=0.2)
    bot.strategy.positions = [pos_a, pos_b]

    bot.writeoff_position_books(pos_a, reason="sell_rug_writeoff")
    alerts1 = bot.get_pending_user_alerts()
    assert len(alerts1) == 1
    assert alerts1[0]["type"] == "rug_writeoff"
    assert alerts1[0]["count"] == 1
    assert len(alerts1[0]["rugs"]) == 1
    alert_id = alerts1[0]["id"]

    bot.writeoff_position_books(pos_b, reason="sell_rug_writeoff")
    alerts2 = bot.get_pending_user_alerts()
    assert len(alerts2) == 1, "must stay a single alert, not stack popups"
    assert alerts2[0]["id"] == alert_id
    assert alerts2[0]["count"] == 2
    assert len(alerts2[0]["rugs"]) == 2
    symbols = {r["symbol"] for r in alerts2[0]["rugs"]}
    assert symbols == {"RUG1", "RUG2"}
    assert alerts2[0]["message"] == RUG_INCINERATOR_BURN_TEXT
    assert "2 rug pulls" in (alerts2[0].get("title") or "")
    assert "RUG1" in (alerts2[0].get("detail") or "")
    assert "RUG2" in (alerts2[0].get("detail") or "")
    # Incinerator copy appears once in message (not duplicated per rug in message field)
    assert alerts2[0]["message"].count("Sol-Incinerator") == 1

    for m in (mint_a, mint_b):
        unblock_mint_permanently(m)
    print("ok: rug alerts coalesce into one")


def test_incinerator_text_exact():
    assert RUG_INCINERATOR_BURN_TEXT == "Use Sol-Incinerator pro to burn."
    print("ok: incinerator copy")


if __name__ == "__main__":
    test_incinerator_text_exact()
    test_is_rug_loss_threshold()
    test_writeoff_books_auto_books_and_alerts()
    test_rug_alerts_coalesce_into_one()
    print("ALL validate_rug_writeoff checks passed")
