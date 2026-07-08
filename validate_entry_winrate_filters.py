"""
Validate entry win-rate filters: absolute ceiling, high-momentum pop-vs-drop
discriminator, win-lean gate, and route/asset sanity. These gates only tighten
ENTRY SELECTION and must never affect exits.

Covered cases (from the task):
  * Absurd momentum blocked by the absolute ceiling.
  * High momentum + DUMP signature (stale spike / reversal / thin book / flat
    round-trip) => blocked.
  * High momentum + GOOD quality (fresh, liquid, Pump.fun, exit-able) => allowed.
  * Moderate momentum unaffected by the spike gate.
  * Win-lean gate falls back to allow when the learner has no data; blocks
    loss-like and allows win-like normal-momentum setups.
  * Quality high-momentum pops bypass the momentum-dominated win-lean gate.
  * Non-memecoin proxy mints (JitoSOL / WETH) cannot enter as momentum picks.
  * Strategy integration: evaluate_entry blocks dump spikes, allows quality pops
    and moderate movers.
  * Journal evidence replay: 9/10 real high-momentum losers carry a dump signature.
  * Not all entries are blocked (activity preserved).
  * Exit / hold config remains untouched.
"""

import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from config import (
    Config,
    JITOSOL_MINT,
    WETH_MINT,
    is_non_memecoin_proxy_mint,
)
from entry_filters import (
    entry_winrate_skip_reason,
    instant_dump_reason,
    is_high_momentum,
    pop_vs_drop_score,
    spike_trap_reason,
    win_lean_reason,
)
from scanner import MoverCandidate
from setup_learner import STORE_VERSION, SetupLearner
from strategy import MomentumStrategy, SignalType


def _candidate(
    *,
    momentum_pct: float = 30.0,
    price_change_5m: float = 3.0,
    price_change_1h: float = 30.0,
    price_change_6h: float = 30.0,
    price_change_24h: float = 30.0,
    liquidity_usd: float = 50000.0,
    volume_24h_usd: float = 100000.0,
    source: str = "pumpfun",
    mint: str = "MoverMint1111111111111111111111111111pump",
    symbol: str = "MOVER",
) -> MoverCandidate:
    return MoverCandidate(
        mint=mint,
        symbol=symbol,
        name=symbol,
        pair_address="pair",
        dex="raydium",
        price_usd=0.001,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd,
        momentum_pct=momentum_pct,
        price_change_5m=price_change_5m,
        price_change_1h=price_change_1h,
        price_change_6h=price_change_6h,
        price_change_24h=price_change_24h,
        source=source,
    )


def _fresh_store(tmp: str) -> Path:
    store = Path(tmp) / "setup_learning.json"
    store.write_text(
        json.dumps({"history": [], "bootstrapped": True, "version": STORE_VERSION}),
        encoding="utf-8",
    )
    return store


def _spike_config_patches():
    return [
        patch.object(Config, "SPIKE_TRAP_FILTER_ENABLED", True),
        patch.object(Config, "MAX_ENTRY_MOMENTUM_PCT", 50000.0),
        patch.object(Config, "MAX_ENTRY_PRICE_CHANGE_5M_PCT", 50000.0),
        patch.object(Config, "HIGH_MOMENTUM_QUALITY_PCT", 300.0),
        patch.object(Config, "SPIKE_MIN_LIQUIDITY_USD", 8000.0),
        patch.object(Config, "SPIKE_FRESH_CONTINUATION_MIN_PCT", 5.0),
        patch.object(Config, "SPIKE_MAX_ROUNDTRIP_IMPACT_PCT", 0.0),
    ]


# --- Absolute ceiling -----------------------------------------------------


def test_absurd_ceiling_blocked():
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        absurd = _candidate(momentum_pct=99999.0, price_change_1h=99999.0, symbol="ABSURD")
        reason = spike_trap_reason(absurd)
        assert reason is not None and "ceiling" in reason.lower(), reason
    print("PASS: absurd_ceiling_blocked")


# --- High-momentum DUMP signatures blocked --------------------------------


def test_stale_spike_blocked():
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        # ANSEM-like: whole move in 6h window, 5m/1h flat -> pop already ran.
        ansem = _candidate(
            momentum_pct=24618.0, price_change_5m=0.02, price_change_1h=-0.07,
            price_change_6h=24618.0, price_change_24h=-0.28, symbol="ANSEM",
        )
        reason = spike_trap_reason(ansem)
        assert reason is not None and "stale" in reason.lower(), reason
    print("PASS: stale_spike_blocked")


def test_reversal_spike_blocked():
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        # Cupsey-like: fresh 5m spike but 6h AND 24h already negative.
        cupsey = _candidate(
            momentum_pct=8660.0, price_change_5m=8660.0, price_change_1h=0.13,
            price_change_6h=-0.10, price_change_24h=-0.16, source="pumpfun",
            symbol="Cupsey",
        )
        reason = spike_trap_reason(cupsey)
        assert reason is not None and "revers" in reason.lower(), reason
    print("PASS: reversal_spike_blocked")


def test_thin_liquidity_blocked():
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        # High momentum with a fresh, non-reversing move but a micro-pool book.
        thin = _candidate(
            momentum_pct=600.0, price_change_5m=40.0, price_change_1h=600.0,
            price_change_6h=600.0, price_change_24h=600.0, liquidity_usd=2500.0,
            symbol="THIN",
        )
        reason = spike_trap_reason(thin)
        assert reason is not None and "liquidity" in reason.lower(), reason
    print("PASS: thin_liquidity_blocked")


def test_flat_book_roundtrip_blocked():
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        # Otherwise-clean high-mom pop, but the sell preview says we can't exit
        # above stop -> flat-book instant dump (stop loss default 1.5%).
        cand = _candidate(
            momentum_pct=600.0, price_change_5m=40.0, price_change_1h=600.0,
            price_change_6h=600.0, price_change_24h=600.0, symbol="FLATBOOK",
        )
        reason = spike_trap_reason(cand, sell_preview_impact_pct=0.05)
        assert reason is not None and "flat-book" in reason.lower(), reason
    print("PASS: flat_book_roundtrip_blocked")


# --- High-momentum QUALITY pops allowed -----------------------------------


def test_high_momentum_quality_allowed():
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        # Fresh, liquid, Pump.fun, multi-window rising, exit-able => a real pop.
        pop = _candidate(
            momentum_pct=600.0, price_change_5m=40.0, price_change_1h=300.0,
            price_change_6h=500.0, price_change_24h=600.0, liquidity_usd=60000.0,
            source="pumpfun", symbol="RUNNER",
        )
        assert is_high_momentum(pop) is True
        assert spike_trap_reason(pop) is None
        assert spike_trap_reason(pop, sell_preview_impact_pct=0.004) is None
    print("PASS: high_momentum_quality_allowed")


def test_moderate_momentum_unaffected():
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        # Below HIGH_MOMENTUM_QUALITY_PCT -> discriminator never runs.
        cand = _candidate(momentum_pct=49.0, price_change_5m=2.9, symbol="CashCow")
        assert is_high_momentum(cand) is False
        assert spike_trap_reason(cand) is None
    print("PASS: moderate_momentum_unaffected")


def test_spike_filter_disabled_is_noop():
    with patch.object(Config, "SPIKE_TRAP_FILTER_ENABLED", False):
        cand = _candidate(momentum_pct=999999.0, price_change_5m=999999.0)
        assert spike_trap_reason(cand) is None
    print("PASS: spike_filter_disabled_is_noop")


# --- pop_vs_drop_score ----------------------------------------------------


def test_pop_vs_drop_score_separates():
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        runner = _candidate(
            momentum_pct=600.0, price_change_5m=40.0, price_change_1h=300.0,
            price_change_6h=500.0, price_change_24h=600.0, liquidity_usd=60000.0,
            source="pumpfun",
        )
        dump = _candidate(
            momentum_pct=8660.0, price_change_5m=8660.0, price_change_1h=0.1,
            price_change_6h=-0.1, price_change_24h=-0.16, liquidity_usd=2000.0,
            source="dexscreener",
        )
        rs = pop_vs_drop_score(runner)
        ds = pop_vs_drop_score(dump)
        assert rs > 0 > ds, (rs, ds)
        # Sell-preview impact should push the score further apart.
        assert pop_vs_drop_score(dump, sell_preview_impact_pct=0.06) < ds
    print("PASS: pop_vs_drop_score_separates")


# --- Win-lean gate --------------------------------------------------------


def _train_learner(store: Path) -> SetupLearner:
    learner = SetupLearner(store_path=store)
    win_feat = {
        "momentum_pct": 30.0,
        "liquidity_usd": 50000.0,
        "volume_24h_usd": 100000.0,
        "price_change_5m": 2.0,
        "price_change_1h": 30.0,
        "is_pumpfun_route": True,
        "scanner_source": "pumpfun",
    }
    loss_feat = {
        "momentum_pct": 2000.0,
        "liquidity_usd": 50000.0,
        "volume_24h_usd": 100000.0,
        "price_change_5m": 2000.0,
        "price_change_1h": 0.0,
        "is_pumpfun_route": False,
        "scanner_source": "dexscreener",
    }
    for i in range(5):
        learner.record_completed_trade(dict(win_feat), 0.01, pnl_pct=0.05, mint=f"w{i}")
    for i in range(5):
        learner.record_completed_trade(dict(loss_feat), -0.01, pnl_pct=-0.02, mint=f"l{i}")
    return learner


def test_win_lean_fallback_when_no_data():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with ExitStack() as stack:
            stack.enter_context(patch.object(Config, "TRADE_JOURNAL_PATH", str(Path(tmp) / "missing.jsonl")))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENABLED", True))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", True))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_MIN_TRADES", 5))
            learner = SetupLearner(store_path=store)
            assert learner.learning_active is False
            assert learner.win_lean_score(_candidate()) is None
            assert win_lean_reason(_candidate(), learner) is None
            assert win_lean_reason(_candidate(), None) is None
    print("PASS: win_lean_fallback_when_no_data")


def test_win_lean_blocks_loss_like_allows_win_like():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with ExitStack() as stack:
            stack.enter_context(patch.object(Config, "TRADE_JOURNAL_PATH", str(Path(tmp) / "missing.jsonl")))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENABLED", True))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", True))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_MIN_TRADES", 2))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_CONDENSE_EVERY", 100))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_MIN_WIN_LEAN", 0.0))
            learner = _train_learner(store)
            assert learner.learning_active is True

            win_like = _candidate(
                momentum_pct=30.0, price_change_1h=30.0, price_change_5m=2.0,
                source="pumpfun", symbol="WINLIKE",
            )
            # Loss-like but kept UNDER the high-momentum threshold so the win-lean
            # gate (not the spike discriminator) is what is exercised here.
            loss_like = _candidate(
                momentum_pct=100.0, price_change_1h=0.0, price_change_5m=100.0,
                price_change_6h=0.0, price_change_24h=0.0,
                source="dexscreener", symbol="LOSSLIKE",
                mint="LossLikeMint2222222222222222222222222222",
            )
            win_score = learner.win_lean_score(win_like)
            loss_score = learner.win_lean_score(loss_like)
            assert win_score is not None and loss_score is not None
            assert win_score > loss_score
            assert win_lean_reason(win_like, learner) is None
            assert win_lean_reason(loss_like, learner) is not None
    print("PASS: win_lean_blocks_loss_like_allows_win_like")


def test_win_lean_gate_disabled_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with ExitStack() as stack:
            stack.enter_context(patch.object(Config, "TRADE_JOURNAL_PATH", str(Path(tmp) / "missing.jsonl")))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENABLED", True))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", False))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_MIN_TRADES", 2))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_CONDENSE_EVERY", 100))
            learner = _train_learner(store)
            loss_like = _candidate(
                momentum_pct=100.0, price_change_1h=0.0, price_change_5m=100.0,
                source="dexscreener", symbol="LOSSLIKE",
                mint="LossLikeMint2222222222222222222222222222",
            )
            assert win_lean_reason(loss_like, learner) is None
    print("PASS: win_lean_gate_disabled_is_noop")


def test_high_mom_quality_bypasses_win_lean():
    """A quality high-momentum pop that would fail win-lean is still allowed by
    the combined gate (spike discriminator takes precedence). We train a learner
    whose LOSS profile looks like a high-momentum pop so win-lean would veto it."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _fresh_store(tmp)
        with ExitStack() as stack:
            for p in _spike_config_patches():
                stack.enter_context(p)
            stack.enter_context(patch.object(Config, "TRADE_JOURNAL_PATH", str(Path(tmp) / "missing.jsonl")))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENABLED", True))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", True))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_MIN_TRADES", 2))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_CONDENSE_EVERY", 100))
            stack.enter_context(patch.object(Config, "SETUP_LEARNING_MIN_WIN_LEAN", 0.0))
            learner = SetupLearner(store_path=store)
            # Wins were low-momentum scalps; losses looked like big pops.
            win_feat = {
                "momentum_pct": 20.0, "liquidity_usd": 50000.0, "volume_24h_usd": 100000.0,
                "price_change_5m": 1.0, "price_change_1h": 20.0, "price_change_6h": 0.0,
                "price_change_24h": 0.0, "is_pumpfun_route": True, "scanner_source": "pumpfun",
            }
            loss_feat = {
                "momentum_pct": 600.0, "liquidity_usd": 60000.0, "volume_24h_usd": 120000.0,
                "price_change_5m": 40.0, "price_change_1h": 300.0, "price_change_6h": 500.0,
                "price_change_24h": 600.0, "is_pumpfun_route": True, "scanner_source": "pumpfun",
            }
            for i in range(5):
                learner.record_completed_trade(dict(win_feat), 0.01, pnl_pct=0.05, mint=f"w{i}")
            for i in range(5):
                learner.record_completed_trade(dict(loss_feat), -0.01, pnl_pct=-0.02, mint=f"l{i}")

            pop = _candidate(
                momentum_pct=600.0, price_change_5m=40.0, price_change_1h=300.0,
                price_change_6h=500.0, price_change_24h=600.0, liquidity_usd=60000.0,
                source="pumpfun", symbol="POP",
            )
            # win-lean alone would reject (this pop looks like the learned losses).
            assert win_lean_reason(pop, learner) is not None
            # combined gate allows it (quality pop bypasses win-lean).
            assert entry_winrate_skip_reason(pop, learner) is None
    print("PASS: high_mom_quality_bypasses_win_lean")


# --- Route / asset sanity -------------------------------------------------


def test_non_memecoin_proxy_detection():
    assert is_non_memecoin_proxy_mint(JITOSOL_MINT) is True
    assert is_non_memecoin_proxy_mint(WETH_MINT) is True
    assert is_non_memecoin_proxy_mint("MoverMint1111111111111111111111111111pump") is False
    assert is_non_memecoin_proxy_mint("") is False
    print("PASS: non_memecoin_proxy_detection")


# --- Strategy integration -------------------------------------------------


def _strategy_entry_patches(stack: ExitStack):
    for p in _spike_config_patches():
        stack.enter_context(p)
    stack.enter_context(patch.object(Config, "SETUP_LEARNING_ENTRY_GATE_ENABLED", False))
    stack.enter_context(patch.object(Config, "SOL_TREND_FILTER_ENABLED", False))
    stack.enter_context(patch.object(Config, "ENABLE_SOL_TRADING", False))
    stack.enter_context(patch.object(Config, "ENABLE_WETH_TRADING", False))


def test_evaluate_entry_blocks_dump_allows_pop_and_moderate():
    with ExitStack() as stack:
        _strategy_entry_patches(stack)
        strat = MomentumStrategy()

        moderate = _candidate(momentum_pct=49.0, price_change_5m=2.9, symbol="CashCow")
        pop = _candidate(
            momentum_pct=600.0, price_change_5m=40.0, price_change_1h=300.0,
            price_change_6h=500.0, price_change_24h=600.0, liquidity_usd=60000.0,
            source="pumpfun", symbol="POP",
            mint="PopMint4444444444444444444444444444444pump",
        )
        dump = _candidate(
            momentum_pct=16104.0, price_change_5m=0.003, price_change_1h=16104.0,
            price_change_6h=-0.12, price_change_24h=-0.27, source="dexscreener",
            symbol="KINS", mint="SpikeMint3333333333333333333333333333pump",
        )

        assert strat.evaluate_entry(moderate, moderate.price_usd, 0.5, sol_trend_snapshot={}) == SignalType.BUY
        assert strat.evaluate_entry(pop, pop.price_usd, 0.5, sol_trend_snapshot={}) == SignalType.BUY
        assert strat.evaluate_entry(dump, dump.price_usd, 0.5, sol_trend_snapshot={}) == SignalType.NONE
        reason = (strat.entry_skip_reason(dump, 0.5, sol_trend_snapshot={}) or "").lower()
        assert "dump" in reason, reason
    print("PASS: evaluate_entry_blocks_dump_allows_pop_and_moderate")


def test_evaluate_entry_blocks_proxy_mint():
    with ExitStack() as stack:
        _strategy_entry_patches(stack)
        strat = MomentumStrategy()
        jito = _candidate(momentum_pct=40.0, price_change_5m=2.0, mint=JITOSOL_MINT, symbol="JitoSOL")
        weth = _candidate(momentum_pct=40.0, price_change_5m=2.0, mint=WETH_MINT, symbol="WETH")
        assert strat.evaluate_entry(jito, jito.price_usd, 0.5, sol_trend_snapshot={}) == SignalType.NONE
        assert strat.evaluate_entry(weth, weth.price_usd, 0.5, sol_trend_snapshot={}) == SignalType.NONE
        reason = strat.entry_skip_reason(jito, 0.5, sol_trend_snapshot={}) or ""
        assert "proxy" in reason.lower()
    print("PASS: evaluate_entry_blocks_proxy_mint")


# --- Journal evidence replay ----------------------------------------------


def test_journal_high_mom_losers_have_dump_signature():
    """Replay the 10 real high-momentum (>=500) losers from setup_learning.json.
    At least 9 must carry an instant-dump signature (liquidity uses the raw USD
    reconstructed from the stored log10 so the stale/reversal signal is exercised).
    """
    # (symbol, momentum, 5m, 1h, 6h, 24h, log10_liquidity, source)
    losers = [
        ("ANSEM", 24618.23, 0.0174, -0.0684, 24618.23, -0.2779, 5.70, "gmgn"),
        ("KINS", 16104.47, 0.003, 16104.47, -0.1159, -0.2743, 4.67, "gmgn"),
        ("Cupsey", 8660.07, 8660.07, 0.1301, -0.0968, -0.1596, 4.79, "pumpfun"),
        ("Jotchua", 4620.07, -0.0273, -0.0842, 4620.07, 3816.99, 6.44, "gmgn"),
        ("ANSEM2", 2586.15, 0.0, 2586.15, -0.1086, 1891.01, 5.88, "gmgn"),
        ("Jotchua2", 2317.28, 2317.28, -0.0702, -0.2632, -0.219, 5.26, "gmgn"),
        ("drooling", 2311.62, 0.0008, 0.0593, 2311.62, -0.3743, 5.29, "pumpfun"),
        ("ANSEM3", 2152.66, 2152.66, -0.0037, -0.1321, -0.285, 5.86, "gmgn"),
        ("manlet", 1030.08, 0.0097, 0.2147, -0.2432, 1030.08, 4.69, "gmgn"),
        ("manlet2", 1029.92, 0.0309, 0.2084, -0.2173, 1029.92, 4.69, "gmgn"),
    ]
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        blocked = []
        allowed = []
        for sym, mom, c5, c1, c6, c24, logliq, src in losers:
            cand = _candidate(
                momentum_pct=mom, price_change_5m=c5, price_change_1h=c1,
                price_change_6h=c6, price_change_24h=c24,
                liquidity_usd=round(10 ** logliq), source=src, symbol=sym,
            )
            if instant_dump_reason(cand) is not None:
                blocked.append(sym)
            else:
                allowed.append(sym)
        assert len(blocked) >= 9, f"only blocked {blocked}, allowed {allowed}"
    print(f"PASS: journal_high_mom_losers_have_dump_signature (blocked {len(blocked)}/10, slipped {allowed})")


def test_activity_preserved_journal_winners():
    """Every learned WIN setup (momentum <= ~90) must still pass the spike gate,
    and a healthy fresh pop must be allowed => not all entries blocked."""
    with ExitStack() as stack:
        for p in _spike_config_patches():
            stack.enter_context(p)
        winning_momenta = [49.0, 45.53, 13.68, 26.28, 19.27, 19.2, 32.97, 8.75,
                           33.88, 11.23, 10.19, 8.58, 20.78, 9.48, 38.76, 11.19,
                           57.67, 85.94, 90.64]
        for mom in winning_momenta:
            cand = _candidate(
                momentum_pct=mom, price_change_5m=min(mom, 20.78), price_change_1h=mom,
                price_change_6h=mom, price_change_24h=mom,
            )
            assert spike_trap_reason(cand) is None, f"winner momentum {mom} wrongly blocked"
        pop = _candidate(
            momentum_pct=600.0, price_change_5m=40.0, price_change_1h=300.0,
            price_change_6h=500.0, price_change_24h=600.0, source="pumpfun",
        )
        assert spike_trap_reason(pop) is None
    print("PASS: activity_preserved_journal_winners")


# --- Exits untouched ------------------------------------------------------


def test_exit_config_untouched():
    from config import ALLOWED_STOP_LOSS_PCT

    assert Config.STOP_LOSS_PCT in ALLOWED_STOP_LOSS_PCT
    assert Config.WBTC_STOP_LOSS_PCT in ALLOWED_STOP_LOSS_PCT
    assert Config.INSTANT_PROFIT_EXIT_PCT > 0
    assert Config.INSTANT_EXIT_3PCT > 0
    assert Config.EMERGENCY_STOP_LOSS_PCT > 0
    assert Config.CATASTROPHIC_STOP_LOSS_PCT > Config.EMERGENCY_STOP_LOSS_PCT
    assert Config.MAX_HOLD_MINUTES_NON_WBTC == 15
    assert Config.STOP_LOSS_NEVER_MISS is True
    print("PASS: exit_config_untouched")


if __name__ == "__main__":
    test_absurd_ceiling_blocked()
    test_stale_spike_blocked()
    test_reversal_spike_blocked()
    test_thin_liquidity_blocked()
    test_flat_book_roundtrip_blocked()
    test_high_momentum_quality_allowed()
    test_moderate_momentum_unaffected()
    test_spike_filter_disabled_is_noop()
    test_pop_vs_drop_score_separates()
    test_win_lean_fallback_when_no_data()
    test_win_lean_blocks_loss_like_allows_win_like()
    test_win_lean_gate_disabled_is_noop()
    test_high_mom_quality_bypasses_win_lean()
    test_non_memecoin_proxy_detection()
    test_evaluate_entry_blocks_dump_allows_pop_and_moderate()
    test_evaluate_entry_blocks_proxy_mint()
    test_journal_high_mom_losers_have_dump_signature()
    test_activity_preserved_journal_winners()
    test_exit_config_untouched()
    print("All entry win-rate filter validations passed.")
