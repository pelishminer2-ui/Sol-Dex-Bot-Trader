"""Validation tests for pinned watchlist mint scanner and entry signals."""
import time
from collections import deque
from unittest.mock import patch

from config import (
    Config,
    DEFAULT_WATCHLIST_MIN_USD_GAIN,
    DEFAULT_WATCHLIST_MINT,
    DEFAULT_WATCHLIST_MINT_B,
    WatchlistMintRule,
)
from price_feed import PriceFeed
from scanner import MoverCandidate
from strategy import MomentumStrategy, Position, SignalType
from watchlist_scanner import (
    compute_usd_gain_from_baseline,
    compute_watchlist_gains,
    day_usd_gain_from_h24,
    fetch_all_watchlist_candidates,
    fetch_watchlist_mint_candidate,
    is_pinned_watchlist_mint,
    probe_all_watchlist_statuses,
    probe_watchlist_mint_status,
    watchlist_entry_qualifies,
    watchlist_usd_gain_qualifies,
)

WATCHLIST_MINT = DEFAULT_WATCHLIST_MINT
PCT_WATCHLIST_MINT = DEFAULT_WATCHLIST_MINT_B

WBTC_RULE = WatchlistMintRule(
    mint=WATCHLIST_MINT,
    label="WBTC",
    min_day_usd_gain=75.0,
    use_standard_exits=True,
)
PCT_RULE = WatchlistMintRule(
    mint=PCT_WATCHLIST_MINT,
    label="6M8z",
    min_day_pct_gain=0.05,
    sell_at_pct=0.20,
    one_buy_one_sell=True,
    override_ladder=True,
    use_standard_exits=False,
)


def _seed_price_feed(price_feed: PriceFeed, mint: str, baseline: float, current: float):
    now = time.time()
    window = Config.BASELINE_WINDOW_SEC
    price_feed._history[mint] = deque(
        [
            (now - window - 5, baseline),
            (now - 1, current),
        ],
        maxlen=500,
    )
    price_feed._dex_prices[mint] = current


def _sample_pair(mint: str = WATCHLIST_MINT, *, h24_pct: float = 0.0) -> dict:
    return {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "pair-watch",
        "priceUsd": "150.00",
        "liquidity": {"usd": 500000},
        "volume": {"h24": 1000000},
        "priceChange": {"m5": 2.0, "h1": 5.0, "h24": h24_pct},
        "pairCreatedAt": int(time.time() * 1000) - 48 * 3600 * 1000,
        "baseToken": {
            "address": mint,
            "symbol": "WLTEST",
            "name": "Watchlist Test",
        },
    }

def test_probe_disabled_when_watchlist_off():
    feed = PriceFeed()
    with patch.object(Config, "WATCHLIST_ENABLED", False):
        status = probe_all_watchlist_statuses(feed)
    assert status == []
    print("PASS: probe_disabled_when_watchlist_off")


def test_default_usd_gain_threshold_is_75():
    assert DEFAULT_WATCHLIST_MIN_USD_GAIN == 75.0
    print("PASS: default_usd_gain_threshold_is_75")


def test_day_gain_example_63636_plus_75():
    """WBTC at $63,636 up $75 for the day qualifies via DexScreener 24h %."""
    price = 63636.0
    day_open = price - 75.0
    h24_pct = ((price / day_open) - 1.0) * 100.0
    day_gain = day_usd_gain_from_h24(price, h24_pct)
    assert day_gain is not None
    assert abs(day_gain - 75.0) < 0.02
    info = compute_watchlist_gains(
        price, session_baseline_usd=None, h24_pct=h24_pct, rule=WBTC_RULE
    )
    assert info["qualifies"] is True
    assert info["gain_source"] == "dexscreener_24h"
    print("PASS: day_gain_example_63636_plus_75")


def test_fetch_always_returns_candidate_when_enabled():
    feed = PriceFeed()
    current = 175.0
    _seed_price_feed(feed, WATCHLIST_MINT, baseline=100.0, current=current)
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_rules", return_value=(WBTC_RULE,)
    ), patch.object(Config, "watchlist_mints", return_value=[WATCHLIST_MINT]), patch.object(
        Config, "WATCHLIST_MIN_USD_GAIN", 75.0
    ), patch(
        "watchlist_scanner._best_pair_for_mint",
        return_value=_sample_pair(h24_pct=75.0),
    ), patch.object(feed, "update", return_value={WATCHLIST_MINT: current}):
        statuses = probe_all_watchlist_statuses(feed)
        candidate = fetch_watchlist_mint_candidate(feed, WATCHLIST_MINT)
    assert len(statuses) == 1
    status = statuses[0]
    assert status["enabled"] is True
    assert status["qualifies"] is True
    assert status["entry_status"] == "eligible"
    assert status["day_usd_gain"] is not None
    assert status["day_usd_gain"] >= 75.0
    assert status["watchlist_min_usd_gain"] == 75.0
    assert candidate is not None
    assert candidate.mint == WATCHLIST_MINT
    assert candidate.source == "watchlist_mint"
    assert candidate.day_usd_gain is not None
    assert candidate.day_usd_gain >= 75.0
    print("PASS: fetch_always_returns_candidate_when_enabled")


def test_fetch_returns_candidate_below_usd_gain_threshold():
    feed = PriceFeed()
    _seed_price_feed(feed, WATCHLIST_MINT, baseline=100.0, current=150.0)
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_rules", return_value=(WBTC_RULE,)
    ), patch.object(Config, "watchlist_mints", return_value=[WATCHLIST_MINT]), patch.object(
        Config, "WATCHLIST_MIN_USD_GAIN", 75.0
    ), patch(
        "watchlist_scanner._best_pair_for_mint", return_value=_sample_pair()
    ), patch.object(feed, "update", return_value={WATCHLIST_MINT: 150.0}):
        statuses = probe_all_watchlist_statuses(feed)
        candidate = fetch_watchlist_mint_candidate(feed, WATCHLIST_MINT)
    status = statuses[0]
    assert status["qualifies"] is False
    assert status["entry_status"] == "standby"
    assert status["day_usd_gain"] == 0.0
    assert candidate is not None
    assert candidate.day_usd_gain == 0.0
    print("PASS: fetch_returns_candidate_below_usd_gain_threshold")


def test_day_gain_qualifies_when_session_below_threshold():
    price = 63636.0
    h24_pct = 0.118
    info = compute_watchlist_gains(
        price, session_baseline_usd=63600.0, h24_pct=h24_pct, rule=WBTC_RULE
    )
    assert info["session_usd_gain"] == 36.0
    assert info["day_usd_gain"] is not None
    assert info["day_usd_gain"] >= 75.0
    assert info["qualifies"] is True
    print("PASS: day_gain_qualifies_when_session_below_threshold")


def test_usd_gain_qualifies_requires_day_gain():
    with patch.object(Config, "WATCHLIST_MIN_USD_GAIN", 75.0):
        assert watchlist_usd_gain_qualifies(75.0) is True
        assert watchlist_usd_gain_qualifies(74.99) is False
        assert watchlist_usd_gain_qualifies(-10.0) is False
        assert watchlist_usd_gain_qualifies(None) is False
        assert watchlist_usd_gain_qualifies(None, session_usd_gain=50.0, day_usd_gain=80.0) is True
        assert watchlist_usd_gain_qualifies(None, session_usd_gain=80.0, day_usd_gain=50.0) is False
        assert watchlist_usd_gain_qualifies(None, session_usd_gain=80.0, day_usd_gain=None) is False
    print("PASS: usd_gain_qualifies_requires_day_gain")


def test_pct_gain_entry_qualifies_at_5_percent():
    info = compute_watchlist_gains(1.0, None, h24_pct=5.0, rule=PCT_RULE)
    assert info["day_pct_gain"] == 0.05
    assert info["qualifies"] is True
    assert watchlist_entry_qualifies(PCT_RULE, day_pct_gain=0.05) is True
    assert watchlist_entry_qualifies(PCT_RULE, day_pct_gain=0.049) is False
    print("PASS: pct_gain_entry_qualifies_at_5_percent")


def test_strategy_buy_on_pct_watchlist_gate():
    strategy = MomentumStrategy()
    eligible = MoverCandidate(
        mint=PCT_WATCHLIST_MINT,
        symbol="PCT",
        name="Pct Test",
        pair_address="pair",
        dex="raydium",
        price_usd=1.05,
        liquidity_usd=500000,
        volume_24h_usd=1000000,
        momentum_pct=0.01,
        price_change_5m=0.02,
        price_change_1h=0.05,
        source="watchlist_mint",
        day_pct_gain=0.06,
    )
    below = MoverCandidate(
        mint=PCT_WATCHLIST_MINT,
        symbol="PCT",
        name="Pct Test",
        pair_address="pair",
        dex="raydium",
        price_usd=1.02,
        liquidity_usd=500000,
        volume_24h_usd=1000000,
        momentum_pct=0.01,
        price_change_5m=0.02,
        price_change_1h=0.03,
        source="watchlist_mint",
        day_pct_gain=0.04,
    )
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[PCT_WATCHLIST_MINT]
    ), patch.object(Config, "get_watchlist_rule", return_value=PCT_RULE):
        assert strategy.evaluate_entry(eligible, 1.05, momentum=0.01) == SignalType.BUY
        assert strategy.evaluate_entry(below, 1.02, momentum=0.01) == SignalType.NONE
    print("PASS: strategy_buy_on_pct_watchlist_gate")


def test_strategy_pct_watchlist_holds_below_20_sells_at_20():
    strategy = MomentumStrategy()
    pos = Position(
        mint=PCT_WATCHLIST_MINT,
        symbol="PCT",
        entry_price=1.0,
        entry_time=time.time(),
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    with patch.object(Config, "get_watchlist_rule", return_value=PCT_RULE):
        assert strategy.evaluate_exit(pos, current_price=1.10) is None
        assert strategy.evaluate_exit(pos, current_price=1.19) is None
        signal = strategy.evaluate_exit(pos, current_price=1.20)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_WATCHLIST_TARGET
    print("PASS: strategy_pct_watchlist_holds_below_20_sells_at_20")


def test_strategy_pct_watchlist_stop_loss_still_applies():
    strategy = MomentumStrategy()
    pos = Position(
        mint=PCT_WATCHLIST_MINT,
        symbol="PCT",
        entry_price=1.0,
        entry_time=time.time(),
        size_sol=0.1,
        token_amount_raw=1000,
        initial_token_amount_raw=1000,
        remaining_token_amount_raw=1000,
    )
    with patch.object(Config, "get_watchlist_rule", return_value=PCT_RULE):
        signal = strategy.evaluate_exit(pos, current_price=0.98)
    assert signal is not None
    assert signal.signal_type == SignalType.SELL_SL
    print("PASS: strategy_pct_watchlist_stop_loss_still_applies")


def test_probe_in_position_status():
    feed = PriceFeed()
    _seed_price_feed(feed, PCT_WATCHLIST_MINT, baseline=1.0, current=1.05)
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_rules", return_value=(PCT_RULE,)
    ), patch(
        "watchlist_scanner._best_pair_for_mint",
        return_value=_sample_pair(PCT_WATCHLIST_MINT, h24_pct=6.0),
    ), patch.object(feed, "update", return_value={PCT_WATCHLIST_MINT: 1.05}):
        status = probe_watchlist_mint_status(
            feed, PCT_WATCHLIST_MINT, PCT_RULE, held_mints={PCT_WATCHLIST_MINT}
        )
    assert status["entry_status"] == "in_position"
    print("PASS: probe_in_position_status")


def test_fetch_all_watchlist_candidates_both_mints():
    feed = PriceFeed()
    _seed_price_feed(feed, WATCHLIST_MINT, baseline=100.0, current=175.0)
    _seed_price_feed(feed, PCT_WATCHLIST_MINT, baseline=1.0, current=1.06)

    def _pair_for_mint(mint):
        if mint == WATCHLIST_MINT:
            return _sample_pair(WATCHLIST_MINT, h24_pct=75.0)
        return _sample_pair(PCT_WATCHLIST_MINT, h24_pct=6.0)

    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_rules", return_value=(WBTC_RULE, PCT_RULE)
    ), patch(
        "watchlist_scanner._best_pair_for_mint", side_effect=_pair_for_mint
    ), patch.object(
        feed,
        "update",
        return_value={WATCHLIST_MINT: 175.0, PCT_WATCHLIST_MINT: 1.06},
    ):
        candidates = fetch_all_watchlist_candidates(feed)
    assert len(candidates) == 2
    mints = {c.mint for c in candidates}
    assert WATCHLIST_MINT in mints
    assert PCT_WATCHLIST_MINT in mints
    print("PASS: fetch_all_watchlist_candidates_both_mints")


def test_strategy_buy_on_watchlist_usd_gain():
    strategy = MomentumStrategy()
    eligible = MoverCandidate(
        mint=WATCHLIST_MINT,
        symbol="WLTEST",
        name="Watchlist Test",
        pair_address="pair",
        dex="raydium",
        price_usd=175.0,
        liquidity_usd=500000,
        volume_24h_usd=1000000,
        momentum_pct=0.75,
        price_change_5m=0.02,
        price_change_1h=0.05,
        source="watchlist_mint",
        usd_gain_baseline=75.0,
        day_usd_gain=75.0,
    )
    below = MoverCandidate(
        mint=WATCHLIST_MINT,
        symbol="WLTEST",
        name="Watchlist Test",
        pair_address="pair",
        dex="raydium",
        price_usd=150.0,
        liquidity_usd=500000,
        volume_24h_usd=1000000,
        momentum_pct=0.5,
        price_change_5m=0.02,
        price_change_1h=0.05,
        source="watchlist_mint",
        usd_gain_baseline=50.0,
        day_usd_gain=50.0,
    )
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WATCHLIST_MINT]
    ), patch.object(Config, "get_watchlist_rule", return_value=WBTC_RULE):
        assert strategy.evaluate_entry(eligible, 175.0, momentum=0.75) == SignalType.BUY
        assert strategy.evaluate_entry(below, 150.0, momentum=0.5) == SignalType.NONE
    print("PASS: strategy_buy_on_watchlist_usd_gain")


def test_strategy_blocks_momentum_bypass_for_pinned_mint():
    strategy = MomentumStrategy()
    mover_disguise = MoverCandidate(
        mint=WATCHLIST_MINT,
        symbol="WLTEST",
        name="Watchlist Test",
        pair_address="pair",
        dex="raydium",
        price_usd=150.0,
        liquidity_usd=500000,
        volume_24h_usd=1000000,
        momentum_pct=0.5,
        price_change_5m=0.02,
        price_change_1h=0.05,
        source="dexscreener",
        usd_gain_baseline=50.0,
    )
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WATCHLIST_MINT]
    ), patch.object(Config, "get_watchlist_rule", return_value=WBTC_RULE):
        assert (
            strategy.evaluate_entry(mover_disguise, 150.0, momentum=0.5)
            == SignalType.NONE
        )
    print("PASS: strategy_blocks_momentum_bypass_for_pinned_mint")


def test_strategy_blocks_dip_reentry_for_pinned_mint():
    strategy = MomentumStrategy()
    candidate = MoverCandidate(
        mint=WATCHLIST_MINT,
        symbol="WLTEST",
        name="Watchlist Test",
        pair_address="pair",
        dex="reentry",
        price_usd=90.0,
        liquidity_usd=500000,
        volume_24h_usd=1000000,
        momentum_pct=0.0,
        price_change_5m=0.0,
        price_change_1h=0.0,
        source="reentry",
    )
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WATCHLIST_MINT]
    ):
        assert strategy.evaluate_dip_reentry(candidate, 90.0, True) == SignalType.NONE
    print("PASS: strategy_blocks_dip_reentry_for_pinned_mint")


def test_fresh_usd_gain_at_entry_overrides_stale_candidate():
    feed = PriceFeed()
    _seed_price_feed(feed, WATCHLIST_MINT, baseline=100.0, current=174.0)
    with patch.object(Config, "WATCHLIST_MIN_USD_GAIN", 75.0), patch(
        "watchlist_scanner._best_pair_for_mint", return_value=_sample_pair(h24_pct=75.0)
    ), patch.object(Config, "get_watchlist_rule", return_value=WBTC_RULE):
        gain = compute_usd_gain_from_baseline(feed, WATCHLIST_MINT, 174.0)
    assert gain is not None
    assert gain < 75.0
    strategy = MomentumStrategy()
    stale = MoverCandidate(
        mint=WATCHLIST_MINT,
        symbol="WLTEST",
        name="Watchlist Test",
        pair_address="pair",
        dex="raydium",
        price_usd=175.0,
        liquidity_usd=500000,
        volume_24h_usd=1000000,
        momentum_pct=0.75,
        price_change_5m=0.02,
        price_change_1h=0.05,
        source="watchlist_mint",
        usd_gain_baseline=75.0,
        day_usd_gain=75.0,
    )
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WATCHLIST_MINT]
    ), patch.object(Config, "get_watchlist_rule", return_value=WBTC_RULE):
        assert (
            strategy.evaluate_entry(stale, 174.0, momentum=0.74, usd_gain=74.0)
            == SignalType.NONE
        )
        assert (
            strategy.evaluate_entry(stale, 175.0, momentum=0.75, usd_gain=75.0)
            == SignalType.BUY
        )
    print("PASS: fresh_usd_gain_at_entry_overrides_stale_candidate")


def test_is_pinned_watchlist_mint():
    with patch.object(Config, "WATCHLIST_ENABLED", True), patch.object(
        Config, "watchlist_mints", return_value=[WATCHLIST_MINT, PCT_WATCHLIST_MINT]
    ):
        assert is_pinned_watchlist_mint(WATCHLIST_MINT) is True
        assert is_pinned_watchlist_mint(PCT_WATCHLIST_MINT) is True
        assert is_pinned_watchlist_mint("other") is False
    print("PASS: is_pinned_watchlist_mint")


def main():
    test_probe_disabled_when_watchlist_off()
    test_default_usd_gain_threshold_is_75()
    test_day_gain_example_63636_plus_75()
    test_fetch_always_returns_candidate_when_enabled()
    test_fetch_returns_candidate_below_usd_gain_threshold()
    test_day_gain_qualifies_when_session_below_threshold()
    test_usd_gain_qualifies_requires_day_gain()
    test_pct_gain_entry_qualifies_at_5_percent()
    test_strategy_buy_on_pct_watchlist_gate()
    test_strategy_pct_watchlist_holds_below_20_sells_at_20()
    test_strategy_pct_watchlist_stop_loss_still_applies()
    test_probe_in_position_status()
    test_fetch_all_watchlist_candidates_both_mints()
    test_strategy_buy_on_watchlist_usd_gain()
    test_strategy_blocks_momentum_bypass_for_pinned_mint()
    test_strategy_blocks_dip_reentry_for_pinned_mint()
    test_fresh_usd_gain_at_entry_overrides_stale_candidate()
    test_is_pinned_watchlist_mint()
    print("All watchlist scanner validation tests passed.")


if __name__ == "__main__":
    main()
