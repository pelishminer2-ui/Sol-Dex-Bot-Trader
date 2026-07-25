"""Validate session closed-loop entry auto-tighten and market-pickup loosen."""

import time
from unittest.mock import patch

from config import (
    DEFAULT_SESSION_AUTO_TIGHTEN_MIN_TRADES,
    DEFAULT_SETUP_LEARNING_MIN_WIN_LEAN,
    Config,
)
from entry_filters import effective_setup_learning_min_win_lean
from session_entry_tuning import (
    has_session_tighten_bumps,
    maybe_auto_loosen,
    maybe_auto_tighten,
    record_exit,
    reset_session,
    session_trade_count,
    session_win_rate,
)

# Steady-ish session base floors used to isolate tests from prior Config pollution.
_TEST_BASE_WIN_LEAN = 0.08
_TEST_BASE_MIN_LIQ = 30000.0
_TEST_BASE_SPIKE_LIQ = 30000.0
_TEST_BASE_GMGN_LIQ = 30000.0


def _restore_steady_floors() -> None:
    Config.SETUP_LEARNING_MIN_WIN_LEAN = _TEST_BASE_WIN_LEAN
    Config.MIN_LIQUIDITY_USD = _TEST_BASE_MIN_LIQ
    Config.SPIKE_MIN_LIQUIDITY_USD = _TEST_BASE_SPIKE_LIQ
    Config.GMGN_MIN_LIQUIDITY_USD = _TEST_BASE_GMGN_LIQ


def test_defaults():
    assert DEFAULT_SETUP_LEARNING_MIN_WIN_LEAN == 0.08
    assert DEFAULT_SESSION_AUTO_TIGHTEN_MIN_TRADES == 20
    assert Config.SESSION_AUTO_TIGHTEN_ENABLED is True
    assert Config.SESSION_AUTO_LOOSEN_ENABLED is True
    assert Config.SESSION_AUTO_LOOSEN_HOT_HOLD_SEC == 600.0
    assert Config.SESSION_AUTO_LOOSEN_COOLDOWN_SEC == 300.0
    print("PASS: session auto-tighten/loosen defaults")


def test_no_tighten_before_min_trades():
    _restore_steady_floors()
    reset_session()
    result = maybe_auto_tighten(0.65)
    assert result["action"] == "hold"
    assert session_trade_count() == 0
    print("PASS: no tighten before min trades")


def test_tighten_when_wr_below_target():
    _restore_steady_floors()
    reset_session()
    base_lean = Config.SETUP_LEARNING_MIN_WIN_LEAN
    base_liq = Config.MIN_LIQUIDITY_USD
    with patch.object(Config, "SESSION_AUTO_TIGHTEN_MIN_TRADES", 5):
        for _ in range(4):
            record_exit(-0.001)
        record_exit(0.002)
        assert session_win_rate() == 0.2
        result = maybe_auto_tighten(0.55)
        assert result["action"] == "tightened"
        assert result["tighten_level"] == 1
        assert Config.SETUP_LEARNING_MIN_WIN_LEAN > base_lean
        assert Config.MIN_LIQUIDITY_USD > base_liq
        assert effective_setup_learning_min_win_lean() == Config.SETUP_LEARNING_MIN_WIN_LEAN
        assert has_session_tighten_bumps() is True
    print("PASS: tighten when WR below target")


def test_no_double_tighten_same_trade_count():
    _restore_steady_floors()
    reset_session()
    with patch.object(Config, "SESSION_AUTO_TIGHTEN_MIN_TRADES", 3):
        record_exit(-0.001)
        record_exit(-0.001)
        record_exit(0.002)
        first = maybe_auto_tighten(0.55)
        second = maybe_auto_tighten(0.55)
        assert first["action"] == "tightened"
        assert second["action"] == "already_tightened_at_count"
    print("PASS: no double tighten at same trade count")


def _force_one_tighten() -> None:
    _restore_steady_floors()
    reset_session()
    with patch.object(Config, "SESSION_AUTO_TIGHTEN_MIN_TRADES", 3):
        record_exit(-0.001)
        record_exit(-0.001)
        record_exit(0.002)
        result = maybe_auto_tighten(0.55)
        assert result["action"] == "tightened"
        assert result["win_lean"] == _TEST_BASE_WIN_LEAN + Config.SESSION_AUTO_TIGHTEN_WIN_LEAN_STEP


def test_loosen_holds_until_hot_sustained():
    _force_one_tighten()
    lean_before = Config.SETUP_LEARNING_MIN_WIN_LEAN
    # Not hot yet
    hold = maybe_auto_loosen({"market_regime": "neutral", "hot_since": None})
    assert hold["action"] == "hold_not_hot"
    assert Config.SETUP_LEARNING_MIN_WIN_LEAN == lean_before

    # Hot but not long enough
    now = time.time()
    with patch.object(Config, "SESSION_AUTO_LOOSEN_HOT_HOLD_SEC", 600.0):
        warm = maybe_auto_loosen(
            {"market_regime": "hot", "hot_since": now - 30.0}
        )
        assert warm["action"] == "hold_hot_warming"
        assert Config.SETUP_LEARNING_MIN_WIN_LEAN == lean_before
    print("PASS: loosen holds until hot sustained")


def test_loosen_steps_toward_base_when_hot():
    _force_one_tighten()
    lean_after_tighten = Config.SETUP_LEARNING_MIN_WIN_LEAN
    liq_after_tighten = Config.MIN_LIQUIDITY_USD
    now = time.time()
    with (
        patch.object(Config, "SESSION_AUTO_LOOSEN_HOT_HOLD_SEC", 60.0),
        patch.object(Config, "SESSION_AUTO_LOOSEN_COOLDOWN_SEC", 0.0),
    ):
        result = maybe_auto_loosen(
            {"market_regime": "hot", "hot_since": now - 120.0}
        )
        assert result["action"] == "loosened"
        assert result["tighten_level"] == 0
        assert Config.SETUP_LEARNING_MIN_WIN_LEAN < lean_after_tighten
        assert Config.MIN_LIQUIDITY_USD < liq_after_tighten
        assert Config.SETUP_LEARNING_MIN_WIN_LEAN == _TEST_BASE_WIN_LEAN
        assert Config.MIN_LIQUIDITY_USD == _TEST_BASE_MIN_LIQ
        assert has_session_tighten_bumps() is False
        assert effective_setup_learning_min_win_lean() == Config.SETUP_LEARNING_MIN_WIN_LEAN
    print("PASS: loosen steps toward base when hot")


def test_tighten_paused_while_hot_with_bumps():
    _force_one_tighten()
    paused = maybe_auto_tighten(0.55, market_regime="hot")
    assert paused["action"] == "paused_hot_loosen"
    # Neutral allows further tighten after new trades
    record_exit(-0.001)
    with patch.object(Config, "SESSION_AUTO_TIGHTEN_MIN_TRADES", 3):
        again = maybe_auto_tighten(0.55, market_regime="neutral")
        assert again["action"] == "tightened"
    print("PASS: tighten paused while hot with bumps")


def main():
    test_defaults()
    test_no_tighten_before_min_trades()
    test_tighten_when_wr_below_target()
    test_no_double_tighten_same_trade_count()
    test_loosen_holds_until_hot_sustained()
    test_loosen_steps_toward_base_when_hot()
    test_tighten_paused_while_hot_with_bumps()
    print("\nAll session entry tuning validations passed.")


if __name__ == "__main__":
    main()
