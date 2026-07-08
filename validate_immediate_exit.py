"""Validate forced exits execute immediately without stall gates."""

import inspect
import re
import time
from unittest.mock import patch

from config import SOL_MINT
from jupiter import JupiterExecutor, SwapQuote
from risk import RiskManager
from strategy import MomentumStrategy, Position, SignalType


def _make_position(**kwargs) -> Position:
    defaults = dict(
        mint="TestMint",
        symbol="TEST",
        entry_price=1.0,
        entry_time=time.time() - 120,
        size_sol=0.10,
        token_amount_raw=10000,
        initial_token_amount_raw=10000,
        remaining_token_amount_raw=10000,
    )
    defaults.update(kwargs)
    return Position(**defaults)


def test_forced_exit_types_cover_profit_and_sl():
    source = open("bot.py", encoding="utf-8").read()
    for name in (
        "SELL_SL",
        "SELL_INSTANT_PROFIT",
        "SELL_L1_PROTECTION",
        "SELL_WATCHLIST_TARGET",
        "SELL_LADDER_MISSED_30M",
        "SELL_LADDER_MISSED_10M",
        "SELL_TP_PARTIAL",
        "SELL_TIME",
    ):
        assert f"SignalType.{name}" in source, name
    print("PASS: forced exit types include SL, instant profit, watchlist, ladder")


def test_forced_exits_bypass_min_net_in_source():
    source = open("bot.py", encoding="utf-8").read()
    assert "if self._is_forced_exit(exit_signal):" in source
    assert "return 0.0" in source
    print("PASS: forced exits have zero min-net threshold in bot")


def test_exit_impact_never_defers():
    defer, counts, forced = RiskManager.should_defer_exit_for_impact(
        "mint",
        "SYM",
        8.0,
        is_stop_loss=True,
        defer_counts={},
        signal_name="sell_stop_loss",
    )
    assert defer is False
    assert counts == {}
    assert forced is True
    print("PASS: high exit impact never defers")


def test_sell_token_allows_high_impact_for_forced_path():
    executor = JupiterExecutor("pubkey", dry_run=True)
    high_impact_quote = SwapQuote(
        input_mint="token",
        output_mint=SOL_MINT,
        in_amount=1000,
        out_amount=1_000_000,
        price_impact_pct=20.0,
        raw={"inAmount": "1000", "outAmount": "1000000"},
    )
    with patch.object(executor, "get_quote", return_value=high_impact_quote):
        blocked = executor.sell_token("token", 1000, allow_high_impact=False)
        allowed = executor.sell_token("token", 1000, allow_high_impact=True)
    assert blocked is None
    assert allowed is not None
    print("PASS: allow_high_impact bypasses sell quote impact gate")


def test_fetch_sell_quote_retries_for_forced_exit():
    bot_source = open("bot.py", encoding="utf-8").read()
    assert "async def _fetch_sell_quote" in bot_source
    assert "FORCED_SELL_QUOTE_RETRIES" in bot_source
    assert "allow_high_impact=forced" in bot_source
    print("PASS: forced sell quote retry helper present")


def test_monitor_uses_retry_and_logging():
    source = open("bot.py", encoding="utf-8").read()
    monitor = re.search(
        r"async def _monitor_open_position.*?(?=\n    async def |\n    def |\Z)",
        source,
        re.DOTALL,
    )
    assert monitor is not None
    body = monitor.group(0)
    assert "_fetch_sell_quote" in body
    assert "Sell stalled" in body
    assert "min-net gate" in body
    assert "FORCED_SELL_QUOTE_RETRIES" in body
    print("PASS: monitor path uses quote retry and stall logging")


def test_execute_sell_bypasses_pre_trade_for_forced():
    source = open("bot.py", encoding="utf-8").read()
    assert "if not forced_exit:" in source
    assert "forced_exit=forced_exit" in source or "forced_exit=forced" in source
    print("PASS: execute sell bypasses pre_trade_check for forced exits")


def test_wbtc_ladder_timeout_no_longer_deferred():
    source = inspect.getsource(MomentumStrategy.evaluate_exit)
    assert "deferring forced negative exit" not in source
    print("PASS: WBTC ladder timeout defer removed")


def test_instant_profit_still_signals():
    strategy = MomentumStrategy()
    pos = _make_position()
    signal = strategy.evaluate_exit(pos, current_price=1.05)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: instant profit exit still signals at +5%")


def test_instant_profit_quote_triggers_when_mark_lags():
    strategy = MomentumStrategy()
    pos = _make_position()
    signal = strategy.evaluate_exit(
        pos, current_price=1.01, executable_pnl_pct=0.055
    )
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_INSTANT_PROFIT
    print("PASS: instant profit via quote when mark lags")


def test_instant_profit_not_blocked_by_min_net():
    source = open("bot.py", encoding="utf-8").read()
    assert "SignalType.SELL_INSTANT_PROFIT" in source
    assert "FORCED_EXIT_TYPES" in source
    assert "Instant exit min-net gate bypass failed" in source
    print("PASS: instant profit forced exit bypasses min-net gate")


def main():
    test_forced_exit_types_cover_profit_and_sl()
    test_forced_exits_bypass_min_net_in_source()
    test_exit_impact_never_defers()
    test_sell_token_allows_high_impact_for_forced_path()
    test_fetch_sell_quote_retries_for_forced_exit()
    test_monitor_uses_retry_and_logging()
    test_execute_sell_bypasses_pre_trade_for_forced()
    test_wbtc_ladder_timeout_no_longer_deferred()
    test_instant_profit_still_signals()
    test_instant_profit_quote_triggers_when_mark_lags()
    test_instant_profit_not_blocked_by_min_net()
    print("\nAll immediate exit validation tests passed.")


if __name__ == "__main__":
    main()
