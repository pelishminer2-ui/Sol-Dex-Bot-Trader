"""Validate quote-based stop loss triggers when mark price is stale."""
from strategy import MomentumStrategy, Position, SignalType
from config import Config


def test_stop_on_quote_when_mark_ok():
    strategy = MomentumStrategy()
    pos = Position(
        mint="testmint",
        symbol="TEST",
        entry_price=1.0,
        entry_time=0,
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    signal = strategy._evaluate_stop_loss(
        pos, mark_pnl=0.0, executable_pnl_pct=-0.02
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL


def test_stop_on_mark_when_quote_ok():
    strategy = MomentumStrategy()
    pos = Position(
        mint="testmint",
        symbol="TEST",
        entry_price=1.0,
        entry_time=0,
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    signal = strategy._evaluate_stop_loss(
        pos, mark_pnl=-0.02, executable_pnl_pct=0.0
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL


def test_no_stop_when_both_above_threshold():
    strategy = MomentumStrategy()
    pos = Position(
        mint="testmint",
        symbol="TEST",
        entry_price=1.0,
        entry_time=0,
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    signal = strategy._evaluate_stop_loss(
        pos, mark_pnl=-0.005, executable_pnl_pct=-0.005
    )
    assert signal is None


def test_emergency_stop():
    strategy = MomentumStrategy()
    pos = Position(
        mint="testmint",
        symbol="TEST",
        entry_price=1.0,
        entry_time=0,
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    signal = strategy._evaluate_stop_loss(
        pos, mark_pnl=-0.04, executable_pnl_pct=-0.01
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL


def test_catastrophic_stop():
    strategy = MomentumStrategy()
    pos = Position(
        mint="testmint",
        symbol="TEST",
        entry_price=1.0,
        entry_time=0,
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    signal = strategy._evaluate_stop_loss(
        pos, mark_pnl=-0.01, executable_pnl_pct=-0.02, trough_pnl=-0.06
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL


def test_trough_triggers_stop_when_mark_stale():
    strategy = MomentumStrategy()
    pos = Position(
        mint="testmint",
        symbol="TEST",
        entry_price=1.0,
        entry_time=0,
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
        trough_pnl_pct=-0.02,
    )
    signal = strategy._evaluate_stop_loss(
        pos, mark_pnl=0.0, executable_pnl_pct=0.0, trough_pnl=pos.trough_pnl_pct
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL


def main():
    assert Config.STOP_LOSS_QUOTE_CHECK is True
    assert Config.EMERGENCY_STOP_LOSS_PCT == 0.03
    assert Config.CATASTROPHIC_STOP_LOSS_PCT == 0.05
    test_stop_on_quote_when_mark_ok()
    test_stop_on_mark_when_quote_ok()
    test_no_stop_when_both_above_threshold()
    test_emergency_stop()
    test_catastrophic_stop()
    test_trough_triggers_stop_when_mark_stale()
    print("validate_stop_loss_quote: OK")


if __name__ == "__main__":
    main()
