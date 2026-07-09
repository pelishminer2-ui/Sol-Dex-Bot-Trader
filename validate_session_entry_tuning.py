"""Validate session closed-loop entry auto-tighten."""

from unittest.mock import patch

from config import (
    DEFAULT_SESSION_AUTO_TIGHTEN_MIN_TRADES,
    DEFAULT_SETUP_LEARNING_MIN_WIN_LEAN,
    Config,
)
from entry_filters import effective_setup_learning_min_win_lean
from session_entry_tuning import (
    maybe_auto_tighten,
    record_exit,
    reset_session,
    session_trade_count,
    session_win_rate,
)


def test_defaults():
    assert DEFAULT_SETUP_LEARNING_MIN_WIN_LEAN == 0.08
    assert DEFAULT_SESSION_AUTO_TIGHTEN_MIN_TRADES == 20
    assert Config.SESSION_AUTO_TIGHTEN_ENABLED is True
    print("PASS: session auto-tighten defaults")


def test_no_tighten_before_min_trades():
    reset_session()
    result = maybe_auto_tighten(0.65)
    assert result["action"] == "hold"
    assert session_trade_count() == 0
    print("PASS: no tighten before min trades")


def test_tighten_when_wr_below_target():
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
    print("PASS: tighten when WR below target")


def test_no_double_tighten_same_trade_count():
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


def main():
    test_defaults()
    test_no_tighten_before_min_trades()
    test_tighten_when_wr_below_target()
    test_no_double_tighten_same_trade_count()
    print("\nAll session entry tuning validations passed.")


if __name__ == "__main__":
    main()
