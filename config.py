import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

_logger = logging.getLogger(__name__)
_logged_missing_scanner_keys: set[str] = set()


def _resolve_project_root() -> Path:
    """Repo root in dev; install directory next to the frozen exe when packaged."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PROJECT_ROOT = _resolve_project_root()

load_dotenv(PROJECT_ROOT / ".env")


def resolve_data_path(value: str) -> Path:
    """Resolve a config path relative to PROJECT_ROOT unless already absolute."""
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path

SOL_MINT = "So11111111111111111111111111111111111111112"
JITOSOL_MINT = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
WETH_MINT = "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs"

# Standard strategy defaults (used for env fallbacks and GUI "Reset to defaults")
DEFAULT_ENTRY_MOMENTUM_PCT = 0.0075
ALLOWED_ENTRY_MOMENTUM_PCT = (0.0025, 0.004, 0.005, 0.0075)
DEFAULT_TARGET_NET_PROFIT_SOL = 0.0155
# Win-focused preset: 0.10 SOL + +3%/+4% ladder must clear fees with meaningful net.
DEFAULT_MIN_EXPECTED_NET_PROFIT_SOL = 0.003
DEFAULT_MIN_NET_WIN_SOL = 0.003
DEFAULT_LOSS_REENTRY_COOLDOWN_MINUTES = 120
DEFAULT_LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES = 360
DEFAULT_REENTRY_MIN_MOMENTUM_PCT = 0.015
# Smart re-chase retry after loss-strike / loss-cooldown (entry-only).
DEFAULT_REENTRY_RETRY_ENABLED = True
DEFAULT_REENTRY_RETRY_WINDOW_MINUTES = 60
DEFAULT_REENTRY_RETRY_BLOCK_HOURS = 2
DEFAULT_REENTRY_RETRY_MAX_ATTEMPTS = 1
DEFAULT_MAX_LOSS_PER_TRADE_SOL = 0.012
DEFAULT_MIN_LIQUIDITY_USD = 30000
DEFAULT_MIN_MOMENTUM_PCT = 0.015
# Slippage / price-impact gates (percent points, e.g. 1.0 = 1%).
# Entry default 1.0%; optional relax to 1.5% via MAX_ENTRY_PRICE_IMPACT_PCT in .env.
DEFAULT_MAX_ENTRY_PRICE_IMPACT_PCT = 1.0
DEFAULT_MAX_EXIT_PRICE_IMPACT_PCT = 1.5
DEFAULT_MAX_ROUND_TRIP_IMPACT_PCT = 2.0
# Hard ceiling for Jupiter quote validation / forced exits (never swap above this).
DEFAULT_MAX_ABSOLUTE_PRICE_IMPACT_PCT = 15.0
# Block entry when sell-preview routes through Pump.fun Amm above this impact.
DEFAULT_PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT = 0.75
DEFAULT_EXIT_IMPACT_FORCE_RETRIES = 5
# Max-potential mode relaxes discovery/entry floors (never weakens stop-loss or exits).
MAX_POTENTIAL_MIN_LIQUIDITY_USD = 10000
MAX_POTENTIAL_MIN_VOLUME_24H_USD = 35000
MAX_POTENTIAL_MIN_MOMENTUM_PCT = 0.008
MAX_POTENTIAL_MAX_ENTRY_PRICE_IMPACT_PCT = 1.5
DEFAULT_DEXSCREENER_MAX_SEED_MINTS = 30
MAX_POTENTIAL_DEXSCREENER_MAX_SEED_MINTS = 30
DEFAULT_DEXSCREENER_REQUEST_DELAY_SEC = 1.1
DEFAULT_DEXSCREENER_PAIR_CACHE_TTL_SEC = 60
DEFAULT_JUPITER_REQUEST_DELAY_SEC = 0.75
DEFAULT_JUPITER_PRICE_CACHE_TTL_SEC = 20
DEFAULT_JUPITER_QUOTE_CACHE_TTL_SEC = 15
DEFAULT_SOL_TX_FEE_LAMPORTS = 5000
DEFAULT_SOL_PRIORITY_FEE_LAMPORTS = 100_000
DEFAULT_FEE_BUFFER_PCT = 0.10
DEFAULT_FALLBACK_DEX_FEE_BPS = 25
DEFAULT_FEE_WALLET = "8TdLLnveaK5iFD6dmVU7qfw8V14cM7CyCcHiZfgcRQMi"
DEFAULT_LIVE_START_FEE_SOL = 0.025
DEFAULT_LIVE_START_FEE_RELAY_BUFFER_SOL = 0.001
DEFAULT_FEE_ENABLED = True
DEFAULT_DEXSCREENER_DEEP_SCAN_PER_CYCLE = 15
DEFAULT_FIRST_SCAN_DEEP_MINTS = 5
DEFAULT_FIRST_SCAN_FAST_MODE = True
DEFAULT_WATCHLIST_TOP_N = 40
MAX_POTENTIAL_WATCHLIST_TOP_N = 50
DEFAULT_TRADE_CANDIDATE_TOP_N = 10
DEFAULT_BIRDEYE_TRENDING_LIMIT = 30
MAX_POTENTIAL_BIRDEYE_TRENDING_LIMIT = 40
DEFAULT_GMGN_TRENDING_LIMIT = 30
MAX_POTENTIAL_GMGN_TRENDING_LIMIT = 40
DEFAULT_GMGN_REQUEST_DELAY_SEC = 1.0
DEFAULT_GMGN_TIMEFRAME = "1h"
DEFAULT_PUMPFUN_API_LIMIT = 50
MAX_POTENTIAL_PUMPFUN_API_LIMIT = 100
DEFAULT_MAX_CONSECUTIVE_LOSSES = 3
DEFAULT_CONSECUTIVE_LOSS_PAUSE_MINUTES = 25
DEFAULT_CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY = True
DEFAULT_MAX_REALISTIC_TP_PCT = 1.0
DEFAULT_TAKE_PROFIT_LEVELS: list[float] = []
DEFAULT_TAKE_PROFIT_PORTIONS: list[float] = []
# Previous 2-level partial ladder; auto-migrated on load to instant-only exits.
STALE_LADDER_TAKE_PROFIT_LEVELS = [0.03, 0.04]
STALE_LADDER_TAKE_PROFIT_PORTIONS = [0.5, 0.5]
# Wrong L1 (+0.10%/+4%) from mistaken TP/protection merge; auto-migrated on load.
STALE_TAKE_PROFIT_LEVELS = [0.001, 0.04]
# Previous 4-level ladder; auto-migrated on load so stale .env / runtime values cannot persist.
PREVIOUS_TAKE_PROFIT_LEVELS = [0.015, 0.03, 0.07, 0.10]
PREVIOUS_TAKE_PROFIT_PORTIONS = [0.25, 0.25, 0.25, 0.25]
# Older fee-scaled ladder; auto-migrated on load.
LEGACY_TAKE_PROFIT_LEVELS = [0.10, 0.25, 0.40, 0.65]
DEFAULT_LADDER_EARLY_EXIT_LEVELS = [2]
PREVIOUS_LADDER_EARLY_EXIT_LEVELS = [2, 3]
DEFAULT_MOMENTUM_SLOWDOWN_PCT = 0.5
DEFAULT_WEAKEN_EXIT_MIN_PROFIT_PCT = 0.01
DEFAULT_WEAKEN_EXIT_ENABLED = True
DEFAULT_INSTANT_EXIT_3PCT = 0.0325
DEFAULT_INSTANT_PROFIT_EXIT_PCT = 0.05
DEFAULT_INSTANT_PROFIT_EXIT_ENABLED = True
DEFAULT_STOP_LOSS_PCT = 0.015
DEFAULT_WBTC_STOP_LOSS_PCT = 0.02
DEFAULT_STOP_LOSS_QUOTE_CHECK = True
DEFAULT_EMERGENCY_STOP_LOSS_PCT = 0.03
DEFAULT_CATASTROPHIC_STOP_LOSS_PCT = 0.05
DEFAULT_LOSS_FRESH_QUOTE_PCT = 0.01
DEFAULT_STOP_LOSS_NEVER_MISS = True
DEFAULT_MAX_FULL_EXIT_SELL_PREVIEW_IMPACT_PCT = 4.0
DEFAULT_WBTC_PROFIT_ONLY_EXITS = True
DEFAULT_WBTC_MIN_DAILY_GAIN_USD = 301.0
DEFAULT_WBTC_REQUIRE_POSITIVE_DAY = True
DEFAULT_WBTC_DAY_GAIN_SUSTAIN_MINUTES = 30
DEFAULT_WBTC_STOP_LOSS_ENABLED = False
DEFAULT_WBTC_ENTRY_SUSTAIN_PATH = "data/wbtc_entry_sustain.json"
DEFAULT_JITOSOL_MIN_DAILY_GAIN_USD = 50.0
DEFAULT_JITOSOL_REQUIRE_POSITIVE_DAY = True
DEFAULT_WETH_MIN_DAILY_GAIN_USD = 150.0
DEFAULT_WETH_REQUIRE_POSITIVE_DAY = True
DEFAULT_GMGN_MIN_LIQUIDITY_USD = 30000
DEFAULT_MIN_VOLUME_24H_USD = 75000
DEFAULT_NON_WATCHLIST_MIN_VOLUME_24H_USD = 75000
DEFAULT_L1_PROTECTION_PCT = 0.001
DEFAULT_ENABLE_L1_PROTECTION = False
DEFAULT_LADDER_MISSED_POSITIVE_EXIT_MINUTES = 10
DEFAULT_LADDER_MISSED_NEGATIVE_DCA_MINUTES = 30
DEFAULT_MAX_BUYS_PER_MINT = 3
DEFAULT_ENABLE_LADDER_TIME_EXITS = True
DEFAULT_MAX_HOLD_MINUTES_NON_WBTC = 15
DEFAULT_MAX_HOLD_ENABLED = True
ALLOWED_STOP_LOSS_PCT = (0.015, 0.02, 0.03, 0.05)
DEFAULT_TRADE_SIZE_SOL = 0.10
ALLOWED_TRADE_SIZE_SOL = (0.05, 0.07, 0.10, 0.20, 0.30, 0.50, 1.0)
DEFAULT_MAX_WALLET_TRADE_PCT = 0.75
DEFAULT_MIN_FUND_SOL = 0.75
DEFAULT_MIN_FUND_WAIVER_HOURS = 1.0
DEFAULT_MIN_FUND_WAIVER_AFTER_SESSION_TRADE = True
# Paper start / default simulated wallet — product gate is 2.00 SOL (not 3.00).
DEFAULT_PAPER_SIMULATED_BALANCE_SOL = 2.00
DEFAULT_MIN_PAPER_FUND_SOL = 2.00
MIN_PAPER_FUND_SOL = DEFAULT_MIN_PAPER_FUND_SOL
MIN_PAPER_SIMULATED_BALANCE_SOL = 0.75
MAX_PAPER_SIMULATED_BALANCE_SOL = 5.0
MIN_PAPER_BALANCE_SOL = MIN_PAPER_SIMULATED_BALANCE_SOL
MAX_PAPER_BALANCE_SOL = MAX_PAPER_SIMULATED_BALANCE_SOL
DEFAULT_LIVE_TRADEABLE_BALANCE_SOL = 0.75
MIN_LIVE_TRADEABLE_BALANCE_SOL = 0.75
MAX_LIVE_TRADEABLE_BALANCE_SOL = 5.0
# Pinned mints: always polled when enabled; per-mint entry/exit rules in DEFAULT_WATCHLIST_RULES.
DEFAULT_WATCHLIST_MINT = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"
DEFAULT_MAX_OPEN_POSITIONS = 1
DEFAULT_MAX_OPEN_POSITIONS_WBTC = 2
# When JitoSOL or WETH proxy is held, allow COMPANION_TRADE_MAX extra concurrent
# positions (typically one memecoin momentum pick alongside the stable proxy leg).
DEFAULT_COMPANION_TRADE_ENABLED = True
DEFAULT_COMPANION_TRADE_MAX = 1
DEFAULT_REENTRY_DIP_PCT = 0.10
DEFAULT_SOL_TREND_FILTER_ENABLED = True
# DexScreener percent points (e.g. -1.5 = allow down to -1.5% on 1h). Loosened so
# memecoins can still trade in mild SOL pullbacks; -2%+ 1h dumps still block. The
# 4h floor stays tighter so sustained downtrends keep blocking.
DEFAULT_SOL_MIN_CHANGE_1H_PCT = -1.5
DEFAULT_SOL_MIN_CHANGE_4H_PCT = -1.5
DEFAULT_SOL_TREND_CACHE_TTL_SEC = 60
# Pop-quality override: when the SOL 1h macro gate would block a memecoin, allow a
# proven-shape "quality pop" through anyway (acceptable route + liquid + fresh +
# exit-able / no instant-dump signature). The 4h sustained-downtrend block still
# cannot be bypassed. Tightens nothing; only lets good pops trade in cold-ish tape.
DEFAULT_SOL_TREND_QUALITY_OVERRIDE_ENABLED = True
DEFAULT_LOSS_ONE_STRIKE_PER_SESSION = True
# SOL self-trading via JitoSOL (default) or WSOL / other liquid-staking proxy on Jupiter.
DEFAULT_ENABLE_SOL_TRADING = False
DEFAULT_SOL_TRADE_MINT = JITOSOL_MINT  # JitoSOL — liquid-staking proxy on Jupiter
DEFAULT_SOL_TRADE_MIN_MOMENTUM_1H_PCT = 0.5  # DexScreener percent points (+0.5%)
DEFAULT_SOL_TRADE_INSTANT_EXIT_PCT = 0.03
DEFAULT_SOL_TRADE_EXIT_ON_TREND_COLD = True
DEFAULT_SOL_TRADE_EXIT_COLD_1H_PCT = 0.0
# WETH on Solana — memecoin-standard entry/exit (momentum, 1.5% SL, +5% instant, ladder).
DEFAULT_ENABLE_WETH_TRADING = True
# Hot-market adaptive gates (Steady Trade strategy).
DEFAULT_HOT_MARKET_MODE_ENABLED = False
DEFAULT_HOT_MARKET_SOL_MIN_1H_PCT = 0.5
DEFAULT_HOT_MARKET_SOL_MIN_4H_PCT = 0.0
DEFAULT_HOT_MARKET_MIN_SCANNER_CANDIDATES = 5
DEFAULT_HOT_MARKET_MIN_GMGN_VOLUME_USD = 0.0
# Steady Trade self-adjust: when enabled, the Steady preset re-derives its own
# ENTRY-SELECTION gates from the live market regime every scan (hot = looser to
# take more trades; neutral/cold = tighter for fewer, higher-quality trades).
# This ONLY loosens/tightens entry momentum + volume floors — it never touches
# any exit (stop loss, instant profit, 15-min hold, forced sell) or the
# spike/instant-dump gate. Master switch layered on Steady's hot-market mode.
DEFAULT_STEADY_TRADE_AUTO_ADJUST = True
# HOT regime = loosest entry gates (take more of the momentum pops).
DEFAULT_HOT_MARKET_ENTRY_MOMENTUM_PCT = 0.004
DEFAULT_HOT_MARKET_MIN_MOMENTUM_PCT = 0.015
DEFAULT_HOT_MARKET_MIN_VOLUME_24H_USD = 45000.0
# NEUTRAL regime = moderately tight (between hot-loose and cold-tight).
DEFAULT_NEUTRAL_MARKET_ENTRY_MOMENTUM_PCT = 0.006
DEFAULT_NEUTRAL_MARKET_MIN_MOMENTUM_PCT = 0.025
DEFAULT_NEUTRAL_MARKET_MIN_VOLUME_24H_USD = 65000.0
# COLD regime = tightest / most selective (Best-Win-ish quality bar).
DEFAULT_COLD_MARKET_ENTRY_MOMENTUM_PCT = 0.0075
DEFAULT_COLD_MARKET_MIN_MOMENTUM_PCT = 0.020
DEFAULT_COLD_MARKET_MIN_VOLUME_24H_USD = 75000.0
DEFAULT_HOT_MARKET_TARGET_WIN_RATE = 0.65
DEFAULT_NEUTRAL_MARKET_TARGET_WIN_RATE = 0.55
DEFAULT_COLD_MARKET_TARGET_WIN_RATE = 0.55
# Session closed-loop entry tightening when WR trails regime target (entry only).
DEFAULT_SESSION_AUTO_TIGHTEN_ENABLED = True
DEFAULT_SESSION_AUTO_TIGHTEN_MIN_TRADES = 20
DEFAULT_SESSION_AUTO_TIGHTEN_WIN_LEAN_STEP = 0.02
DEFAULT_SESSION_AUTO_TIGHTEN_WIN_LEAN_CAP = 0.15
DEFAULT_SESSION_AUTO_TIGHTEN_LIQUIDITY_STEP_USD = 2500.0
DEFAULT_SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD = 40000.0
DEFAULT_SETUP_LEARNING_ENABLED = True
DEFAULT_SETUP_LEARNING_MIN_TRADES = 5
DEFAULT_SETUP_LEARNING_MAX_HISTORY = 100
DEFAULT_SETUP_LEARNING_RAW_HISTORY = 50
DEFAULT_SETUP_LEARNING_CONDENSE_EVERY = 10
DEFAULT_SETUP_LEARNING_MAX_AGE_DAYS = 10
DEFAULT_SETUP_LEARNING_CENTROID_WEIGHT = 0.7
DEFAULT_SETUP_LEARNING_WIN_WEIGHT = 0.6
DEFAULT_SETUP_LEARNING_LOSS_WEIGHT = 0.4
# Entry win-rate filters (learned-pattern driven; do NOT weaken exits).
# Spike-trap gate: momentum trading WANTS high-momentum "quick pops" — so we no
# longer blanket-block on momentum alone. Instead we only hard-cap truly absurd
# bonding-curve artifacts, and for genuinely high momentum we run a "pop vs drop"
# discriminator (see entry_filters.pop_vs_drop_score / instant_dump_reason) that
# blocks ONLY the flat-book / stale-spike / already-reversed signatures and lets
# quality pops through. Values are on the stored feature scale (raw DexScreener/
# pump.fun percent), same units as MoverCandidate.momentum_pct / price_change_*.
#
# Journal evidence (data/setup_learning.json, 50 round-trips): momentum_pct 0-50
# is the profitable zone (48% win, net +0.052 SOL); every entry >=500 (n=10) lost
# but with TINY net losses (stop-outs, not catastrophes). 9/10 of those losers
# carried the whole move in ONE stale window (max(5m,1h) ~ 0) OR had 6h AND 24h
# both negative (already reversing), and 8/10 were non-Pump.fun. That is the
# instant-dump signature we now target instead of momentum magnitude alone.
DEFAULT_SPIKE_TRAP_FILTER_ENABLED = True
# Absolute ceiling: block only clearly-absurd values (data artifacts). Raised far
# above real winners so it almost never triggers; the discriminator does the work.
DEFAULT_MAX_ENTRY_MOMENTUM_PCT = 50000.0
DEFAULT_MAX_ENTRY_PRICE_CHANGE_5M_PCT = 50000.0
# Momentum at/above this (stored scale) is treated as a "high-momentum pop" and
# routed through the pop-vs-drop discriminator instead of being allowed freely.
# Set comfortably above the learned winner range (~90) so normal movers are
# unaffected and only real spikes get scrutinized.
DEFAULT_HIGH_MOMENTUM_QUALITY_PCT = 300.0
# High-momentum flat-book floor: a high-momentum candidate with less than this
# much pool liquidity (USD) is treated as an instant-dump trap.
DEFAULT_SPIKE_MIN_LIQUIDITY_USD = 30000.0
# High-momentum freshness: require at least this much recent movement (max of 5m
# and 1h, stored percent) so we only chase pops that are happening NOW, not stale
# 6h/24h spikes that already ran and reversed.
DEFAULT_SPIKE_FRESH_CONTINUATION_MIN_PCT = 5.0
# Optional sell-preview round-trip impact ceiling: when a sell preview is supplied
# and the round-trip price impact meets/exceeds this fraction, the book is too thin
# to exit above stop -> instant-dump trap. 0 => fall back to STOP_LOSS_PCT.
DEFAULT_SPIKE_MAX_ROUNDTRIP_IMPACT_PCT = 0.0
# Win-similarity entry gate: require a candidate to lean toward the learned win
# centroid over the loss centroid before entering (not just ranking). Falls back
# to "allow" when the learner has insufficient data.
DEFAULT_SETUP_LEARNING_ENTRY_GATE_ENABLED = True
DEFAULT_SETUP_LEARNING_MIN_WIN_LEAN = 0.08


def is_wbtc_watchlist_mint(mint: str) -> bool:
    """True for the pinned WBTC watchlist mint (Config.WATCHLIST_MINT)."""
    if not mint:
        return False
    wbtc = (Config.WATCHLIST_MINT or DEFAULT_WATCHLIST_MINT).strip()
    return mint.strip() == wbtc


def sol_trading_enabled() -> bool:
    """True when SOL self-trading is enabled with a configured mint (WSOL or proxy)."""
    if not Config.ENABLE_SOL_TRADING:
        return False
    mint = (Config.SOL_TRADE_MINT or "").strip()
    return bool(mint)


def is_sol_trade_mint(mint: str) -> bool:
    """True for the configured SOL trade mint (WSOL or liquid-staking proxy)."""
    if not mint or not sol_trading_enabled():
        return False
    return mint.strip() == (Config.SOL_TRADE_MINT or "").strip()


def is_wsol_trade_mint(mint: str) -> bool:
    """True when trading WSOL (1:1 SOL wrap) at memecoin standards."""
    if not mint or not sol_trading_enabled():
        return False
    trade_mint = (Config.SOL_TRADE_MINT or "").strip()
    return trade_mint == SOL_MINT and mint.strip() == SOL_MINT


def is_sol_proxy_trade_mint(mint: str) -> bool:
    """True for legacy liquid-staking proxy (mSOL, jitoSOL, etc.) — not WSOL."""
    return is_sol_trade_mint(mint) and not is_wsol_trade_mint(mint)


def is_jitosol_trade_mint(mint: str) -> bool:
    """True when JitoSOL is the configured SOL trade mint and matches."""
    if not mint or not sol_trading_enabled():
        return False
    trade_mint = (Config.SOL_TRADE_MINT or "").strip()
    return trade_mint == JITOSOL_MINT and mint.strip() == JITOSOL_MINT


def weth_trading_enabled() -> bool:
    """True when WETH trading is enabled with the configured mint."""
    if not Config.ENABLE_WETH_TRADING:
        return False
    mint = (Config.WETH_MINT or "").strip()
    return bool(mint)


def is_weth_trade_mint(mint: str) -> bool:
    """True for the configured WETH mint at memecoin standards."""
    if not mint or not weth_trading_enabled():
        return False
    return mint.strip() == (Config.WETH_MINT or WETH_MINT).strip()


def is_memecoin_standard_special_mint(mint: str) -> bool:
    """WSOL/WETH — momentum entry, standard exits; exempt from SOL dump filter."""
    return is_wsol_trade_mint(mint) or is_weth_trade_mint(mint)


def is_non_memecoin_proxy_mint(mint: str) -> bool:
    """
    True for SOL / JitoSOL / WETH proxy mints (and any configured SOL/WETH trade
    mint). These must only ever be entered through their dedicated enabled paths
    (SOL trade / WETH trade branches), never as a random momentum pick. Prevents
    wrong-asset entries slipping through the memecoin scanner.
    """
    if not mint:
        return False
    m = mint.strip()
    proxies = {SOL_MINT, JITOSOL_MINT, WETH_MINT}
    trade_mint = (Config.SOL_TRADE_MINT or "").strip()
    if trade_mint:
        proxies.add(trade_mint)
    weth_mint = (Config.WETH_MINT or "").strip()
    if weth_mint:
        proxies.add(weth_mint)
    return m in proxies


# Voluntary WBTC exits that require quote-verified net > 0 after fees.
WBTC_VOLUNTARY_EXIT_SIGNALS = frozenset({
    "sell_take_profit_partial",
    "sell_trend_weaken_2pct",
    "sell_ladder_missed_10m_positive",
    "sell_slowdown",
    "sell_time_stop",
    "sell_wbtc_hold_profit",
    "sell_instant_5pct",
})


def wbtc_profit_gate_applies(mint: str, signal_type_value: str) -> bool:
    """True when a WBTC discretionary exit must clear MIN_NET_WIN_SOL after fees."""
    if not Config.WBTC_PROFIT_ONLY_EXITS:
        return False
    if not is_wbtc_watchlist_mint(mint):
        return False
    return signal_type_value in WBTC_VOLUNTARY_EXIT_SIGNALS


def instant_profit_exempt_from_min_net_win(mint: str) -> bool:
    """Instant +5% uses the same quote min-net gate as ladder/weaken exits."""
    return False


def wbtc_min_net_win_threshold() -> float:
    """Effective min net SOL for WBTC hold-until-profit exits (fee-positive)."""
    if not Config.WBTC_PROFIT_ONLY_EXITS:
        if Config.MIN_NET_WIN_SOL > 0:
            return Config.MIN_NET_WIN_SOL
        return DEFAULT_MIN_NET_WIN_SOL
    return 0.0


def wbtc_hold_until_profit_mode(mint: str) -> bool:
    """True when WBTC uses hold-until fee-positive exit (no SL / no time force)."""
    return is_wbtc_watchlist_mint(mint) and Config.WBTC_PROFIT_ONLY_EXITS


def stop_loss_applies_for_mint(mint: str) -> bool:
    """False for WBTC when WBTC_STOP_LOSS_ENABLED=false; always True for memecoins."""
    if is_wbtc_watchlist_mint(mint):
        return Config.WBTC_STOP_LOSS_ENABLED
    return True


def wbtc_min_expected_gain_pct() -> float:
    """WBTC entry instant-target floor; defaults to INSTANT_EXIT_3PCT (0.0325)."""
    override = getattr(Config, "WBTC_MIN_EXPECTED_GAIN_PCT", None)
    if override is not None:
        return float(override)
    return Config.INSTANT_EXIT_3PCT


def jitosol_min_expected_gain_pct() -> float:
    """JitoSOL entry instant-target floor; defaults to INSTANT_EXIT_3PCT (0.0325)."""
    override = getattr(Config, "JITOSOL_MIN_EXPECTED_GAIN_PCT", None)
    if override is not None:
        return float(override)
    return Config.INSTANT_EXIT_3PCT


def weth_min_expected_gain_pct() -> float:
    """WETH entry instant-target floor; defaults to INSTANT_EXIT_3PCT (0.0325)."""
    override = getattr(Config, "WETH_MIN_EXPECTED_GAIN_PCT", None)
    if override is not None:
        return float(override)
    return Config.INSTANT_EXIT_3PCT


def is_proxy_mainstream_mint(mint: str) -> bool:
    """
    True for WBTC, enabled JitoSOL, and enabled WETH — wrapped mainstream assets
    with dollar-based entry gates and 15-min green profit-taking time exits.
    WSOL is excluded (momentum entry, memecoin-style exits).
    """
    if not mint:
        return False
    if is_wbtc_watchlist_mint(mint):
        return True
    if is_sol_proxy_trade_mint(mint):
        return True
    if is_weth_trade_mint(mint):
        return True
    return False


def companion_trade_enabled() -> bool:
    """True when WBTC / JitoSOL / WETH companion slots are allowed."""
    return bool(Config.COMPANION_TRADE_ENABLED)


def is_companion_anchor_mint(mint: str) -> bool:
    """
    True for WBTC or enabled JitoSOL/WETH proxy mints — the stable leg that
    unlocks COMPANION_TRADE_MAX extra concurrent memecoin slot(s) when held.
    """
    if not mint or not companion_trade_enabled():
        return False
    return (
        is_wbtc_watchlist_mint(mint)
        or is_sol_proxy_trade_mint(mint)
        or is_weth_trade_mint(mint)
    )


def is_proxy_companion_anchor_mint(mint: str) -> bool:
    """
    True for enabled JitoSOL (liquid-staking proxy) or WETH trade mints — the
    stable leg that unlocks a companion momentum slot when held or entered second.
    WSOL is excluded; only the configured proxy anchors qualify.
    """
    if not mint or not companion_trade_enabled():
        return False
    return is_sol_proxy_trade_mint(mint) or is_weth_trade_mint(mint)


def max_positions_with_companion() -> int:
    """Base cap plus configured companion slots (default 1 + 1 = 2)."""
    return Config.MAX_OPEN_POSITIONS + max(0, Config.COMPANION_TRADE_MAX)


def companion_slot_open(open_mints: list[str]) -> bool:
    """True when a sole companion anchor is open and a memecoin slot is free."""
    return (
        companion_trade_enabled()
        and len(open_mints) == 1
        and is_companion_anchor_mint(open_mints[0])
    )


def wbtc_companion_slot_open(open_mints: list[str]) -> bool:
    """True when WBTC is the sole open position and a companion slot is available."""
    return (
        companion_trade_enabled()
        and len(open_mints) == 1
        and is_wbtc_watchlist_mint(open_mints[0])
    )


def proxy_companion_slot_open(open_mints: list[str]) -> bool:
    """True when a sole JitoSOL/WETH proxy is open and a companion slot is free."""
    return (
        companion_trade_enabled()
        and len(open_mints) == 1
        and is_proxy_companion_anchor_mint(open_mints[0])
    )


def max_allowed_open_positions(
    open_mints: list[str],
    candidate_mint: Optional[str] = None,
) -> int:
    """
    Max concurrent positions: 1 normally; +COMPANION_TRADE_MAX when a companion
    anchor (WBTC / JitoSOL / WETH) is held or is the next entry while companion
    trading is enabled. Legacy MAX_OPEN_POSITIONS_WBTC still applies when companion
    trading is disabled.
    """
    limits = [Config.MAX_OPEN_POSITIONS]
    if companion_trade_enabled():
        if any(is_companion_anchor_mint(m) for m in open_mints):
            limits.append(max_positions_with_companion())
        if (
            candidate_mint
            and is_companion_anchor_mint(candidate_mint)
            and open_mints
        ):
            limits.append(max_positions_with_companion())
    else:
        if any(is_wbtc_watchlist_mint(m) for m in open_mints):
            limits.append(Config.MAX_OPEN_POSITIONS_WBTC)
        if candidate_mint and is_wbtc_watchlist_mint(candidate_mint) and open_mints:
            limits.append(Config.MAX_OPEN_POSITIONS_WBTC)
    return max(limits)


def can_open_more_positions(
    open_mints: list[str],
    candidate_mint: Optional[str] = None,
) -> bool:
    return len(open_mints) < max_allowed_open_positions(open_mints, candidate_mint)
DEFAULT_WATCHLIST_MINT_B = "6M8z5Wzmhk93ns6BaQzCuMYvkEpFcx9CDXsgFwK58NPf"
DEFAULT_WATCHLIST_MIN_USD_GAIN = 75.0
DEFAULT_WATCHLIST_MIN_DAY_PCT_GAIN_B = 0.05
DEFAULT_WATCHLIST_SELL_AT_PCT_B = 0.20
DEFAULT_WATCHLIST_ENABLED = True
DEFAULT_BLOCK_STOCK_RELATED_TOKENS = True

# Pre-win-focused-preset snapshot (revertible bookmark values).
PRE_WIN_PRESET_BOOKMARK = {
    "trade_size_sol": 0.05,
    "entry_momentum_pct": 0.0025,
    "stop_loss_pct": 0.015,
    "min_liquidity_usd": 12000.0,
    "min_momentum_pct": 0.01,
    "min_expected_net_profit_sol": 0.001,
    "take_profit_levels": list(PREVIOUS_TAKE_PROFIT_LEVELS),
    "take_profit_portions": list(PREVIOUS_TAKE_PROFIT_PORTIONS),
}

# Snapshot before fee-aware economics (win-focused at 0.10 SOL, 3% SL, 0.001 min edge).
PRE_FEE_AWARE_BOOKMARK = {
    "trade_size_sol": 0.10,
    "entry_momentum_pct": 0.005,
    "stop_loss_pct": 0.03,
    "min_liquidity_usd": 15000.0,
    "min_momentum_pct": 0.015,
    "min_expected_net_profit_sol": 0.001,
    "min_net_win_sol": 0.0,
    "loss_reentry_cooldown_minutes": 0,
    "weaken_exit_min_profit_pct": 0.02,
    "take_profit_levels": list(DEFAULT_TAKE_PROFIT_LEVELS),
    "take_profit_portions": list(DEFAULT_TAKE_PROFIT_PORTIONS),
}

# Best Win — canonical fee-viable preset (paper + live share strategy.py / risk.py).
BEST_WIN_PRESET = {
    "trade_size_sol": 0.10,
    "entry_momentum_pct": 0.0075,
    "stop_loss_pct": 0.015,
    "min_liquidity_usd": 15000.0,
    "min_momentum_pct": 0.020,
    "min_volume_24h_usd": 75000.0,
    "non_watchlist_min_volume_24h_usd": 75000.0,
    "min_expected_net_profit_sol": 0.002,
    "min_net_win_sol": 0.003,
    "max_entry_price_impact_pct": 1.0,
    "loss_reentry_cooldown_minutes": 120,
    "loss_reentry_repeat_cooldown_minutes": 240,
    "reentry_min_momentum_pct": 0.015,
    "weaken_exit_min_profit_pct": 0.01,
    "instant_exit_3pct": DEFAULT_INSTANT_EXIT_3PCT,
    "instant_profit_exit_pct": DEFAULT_INSTANT_PROFIT_EXIT_PCT,
    "instant_profit_exit_enabled": DEFAULT_INSTANT_PROFIT_EXIT_ENABLED,
    "take_profit_levels": list(DEFAULT_TAKE_PROFIT_LEVELS),
    "take_profit_portions": list(DEFAULT_TAKE_PROFIT_PORTIONS),
    "enable_l1_protection": DEFAULT_ENABLE_L1_PROTECTION,
}

# Backward-compatible alias for GUI / API consumers.
WIN_FOCUSED_PRESET = BEST_WIN_PRESET

TIGHT_LOSSES_PRESET = {
    **BEST_WIN_PRESET,
    "min_net_win_sol": 0.003,
    "loss_reentry_cooldown_minutes": 120,
    "loss_reentry_repeat_cooldown_minutes": 240,
}

# Best Win Strategy: profit-first preset tuned for 0.10 SOL ladder economics,
# strict scanner filters, fee-aware exits, and loss cooldown on repeat losers.
BEST_WIN_STRATEGY_PRESET = {
    **WIN_FOCUSED_PRESET,
    "reentry_dip_pct": 0.10,
    "max_potential_mode": False,
    "block_stock_related_tokens": True,
    "watchlist_min_usd_gain": DEFAULT_WATCHLIST_MIN_USD_GAIN,
    "gmgn_min_liquidity_usd": DEFAULT_GMGN_MIN_LIQUIDITY_USD,
}

# Balanced Win — more scanner/entry opportunities while keeping fee-aware exits,
# loss cooldowns, dip momentum gate, and stock filter.
BALANCED_WIN_PRESET = {
    "trade_size_sol": 0.10,
    "entry_momentum_pct": 0.005,
    "stop_loss_pct": 0.015,
    "min_liquidity_usd": 15000.0,
    "min_momentum_pct": 0.015,
    "min_volume_24h_usd": 50000.0,
    "non_watchlist_min_volume_24h_usd": 50000.0,
    "min_expected_net_profit_sol": 0.002,
    "min_net_win_sol": 0.002,
    "max_entry_price_impact_pct": 1.0,
    "loss_reentry_cooldown_minutes": 90,
    "loss_reentry_repeat_cooldown_minutes": 180,
    "reentry_min_momentum_pct": 0.005,
    "weaken_exit_min_profit_pct": 0.01,
    "instant_exit_3pct": DEFAULT_INSTANT_EXIT_3PCT,
    "instant_profit_exit_pct": DEFAULT_INSTANT_PROFIT_EXIT_PCT,
    "instant_profit_exit_enabled": DEFAULT_INSTANT_PROFIT_EXIT_ENABLED,
    "take_profit_levels": list(DEFAULT_TAKE_PROFIT_LEVELS),
    "take_profit_portions": list(DEFAULT_TAKE_PROFIT_PORTIONS),
    "enable_l1_protection": DEFAULT_ENABLE_L1_PROTECTION,
}

BALANCED_WIN_STRATEGY_PRESET = {
    **BALANCED_WIN_PRESET,
    "reentry_dip_pct": 0.10,
    "max_potential_mode": False,
    "block_stock_related_tokens": True,
    "watchlist_min_usd_gain": DEFAULT_WATCHLIST_MIN_USD_GAIN,
    "gmgn_min_liquidity_usd": 15000.0,
}

# Steady Trade — balanced scanner/entry looseness with Best Win loss protections.
# Target ~3–6 trades/hr in active markets (~15–30 per 6hr), not churn.
STEADY_TRADE_PRESET = {
    "trade_size_sol": 0.10,
    "entry_momentum_pct": 0.004,
    "stop_loss_pct": 0.015,
    "min_liquidity_usd": 30000.0,
    "min_momentum_pct": 0.015,
    "min_volume_24h_usd": 45000.0,
    "non_watchlist_min_volume_24h_usd": 45000.0,
    "min_expected_net_profit_sol": 0.0004,
    "min_net_win_sol": 0.002,
    "max_entry_price_impact_pct": 1.0,
    "loss_reentry_cooldown_minutes": 120,
    "loss_reentry_repeat_cooldown_minutes": 240,
    "reentry_min_momentum_pct": 0.015,
    "weaken_exit_min_profit_pct": 0.01,
    "instant_exit_3pct": DEFAULT_INSTANT_EXIT_3PCT,
    "instant_profit_exit_pct": DEFAULT_INSTANT_PROFIT_EXIT_PCT,
    "instant_profit_exit_enabled": DEFAULT_INSTANT_PROFIT_EXIT_ENABLED,
    "take_profit_levels": list(DEFAULT_TAKE_PROFIT_LEVELS),
    "take_profit_portions": list(DEFAULT_TAKE_PROFIT_PORTIONS),
    "enable_l1_protection": DEFAULT_ENABLE_L1_PROTECTION,
}

STEADY_TRADE_STRATEGY_PRESET = {
    **STEADY_TRADE_PRESET,
    "reentry_dip_pct": 0.10,
    "max_potential_mode": False,
    "block_stock_related_tokens": True,
    "watchlist_min_usd_gain": DEFAULT_WATCHLIST_MIN_USD_GAIN,
    "gmgn_min_liquidity_usd": 30000.0,
    "hot_market_mode_enabled": True,
    # Steady Trade tolerates mild SOL pullbacks; cold regime still blocks deep dumps via 4h gate.
    # Regime-aware entry tuning: 1h gate loosened -1.0 -> -1.5 (pop-quality override bypasses
    # the 1h gate only, never the 4h hard block). Revert ref: data/regime_tuning_revert.json.
    "sol_min_change_1h_pct": -1.5,
}

CONFIG_BOOKMARK_PATH = resolve_data_path("presets/win_focused_bookmark.json")
BEST_WIN_BOOKMARK_PATH = resolve_data_path("presets/best_win_bookmark.json")

BOOKMARK_ENV_KEYS = {
    "trade_size_sol": "TRADE_SIZE_SOL",
    "entry_momentum_pct": "ENTRY_MOMENTUM_PCT",
    "stop_loss_pct": "STOP_LOSS_PCT",
    "min_liquidity_usd": "MIN_LIQUIDITY_USD",
    "min_volume_24h_usd": "MIN_VOLUME_24H_USD",
    "min_momentum_pct": "MIN_MOMENTUM_PCT",
    "min_expected_net_profit_sol": "MIN_EXPECTED_NET_PROFIT_SOL",
    "min_net_win_sol": "MIN_NET_WIN_SOL",
    "loss_reentry_cooldown_minutes": "LOSS_REENTRY_COOLDOWN_MINUTES",
    "loss_reentry_repeat_cooldown_minutes": "LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES",
    "max_entry_price_impact_pct": "MAX_ENTRY_PRICE_IMPACT_PCT",
    "reentry_min_momentum_pct": "REENTRY_MIN_MOMENTUM_PCT",
    "non_watchlist_min_volume_24h_usd": "NON_WATCHLIST_MIN_VOLUME_24H_USD",
    "weaken_exit_min_profit_pct": "WEAKEN_EXIT_MIN_PROFIT_PCT",
    "take_profit_levels": "TAKE_PROFIT_LEVELS",
    "take_profit_portions": "TAKE_PROFIT_PORTIONS",
}

BOOKMARK_RUNTIME_KEYS = {
    "trade_size_sol": "TRADE_SIZE_SOL",
    "entry_momentum_pct": "ENTRY_MOMENTUM_PCT",
    "stop_loss_pct": "STOP_LOSS_PCT",
    "min_liquidity_usd": "MIN_LIQUIDITY_USD",
    "min_volume_24h_usd": "MIN_VOLUME_24H_USD",
    "min_momentum_pct": "MIN_MOMENTUM_PCT",
    "min_expected_net_profit_sol": "MIN_EXPECTED_NET_PROFIT_SOL",
    "min_net_win_sol": "MIN_NET_WIN_SOL",
    "loss_reentry_cooldown_minutes": "LOSS_REENTRY_COOLDOWN_MINUTES",
    "loss_reentry_repeat_cooldown_minutes": "LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES",
    "max_entry_price_impact_pct": "MAX_ENTRY_PRICE_IMPACT_PCT",
    "reentry_min_momentum_pct": "REENTRY_MIN_MOMENTUM_PCT",
    "non_watchlist_min_volume_24h_usd": "NON_WATCHLIST_MIN_VOLUME_24H_USD",
    "weaken_exit_min_profit_pct": "WEAKEN_EXIT_MIN_PROFIT_PCT",
    "take_profit_levels": "TAKE_PROFIT_LEVELS",
    "take_profit_portions": "TAKE_PROFIT_PORTIONS",
}

BEST_WIN_ENV_KEYS = {
    **BOOKMARK_ENV_KEYS,
    "reentry_dip_pct": "REENTRY_DIP_PCT",
    "max_potential_mode": "MAX_POTENTIAL_MODE",
    "watchlist_min_usd_gain": "WATCHLIST_MIN_USD_GAIN",
    "gmgn_min_liquidity_usd": "GMGN_MIN_LIQUIDITY_USD",
}

BEST_WIN_RUNTIME_KEYS = {
    **BOOKMARK_RUNTIME_KEYS,
    "reentry_dip_pct": "REENTRY_DIP_PCT",
    "max_potential_mode": "MAX_POTENTIAL_MODE",
    "block_stock_related_tokens": "BLOCK_STOCK_RELATED_TOKENS",
    "watchlist_min_usd_gain": "WATCHLIST_MIN_USD_GAIN",
    "gmgn_min_liquidity_usd": "GMGN_MIN_LIQUIDITY_USD",
}

STEADY_TRADE_ENV_KEYS = {
    **BEST_WIN_ENV_KEYS,
    "hot_market_mode_enabled": "HOT_MARKET_MODE_ENABLED",
    "sol_min_change_1h_pct": "SOL_MIN_CHANGE_1H_PCT",
}

STEADY_TRADE_RUNTIME_KEYS = {
    **BEST_WIN_RUNTIME_KEYS,
    "hot_market_mode_enabled": "HOT_MARKET_MODE_ENABLED",
    "sol_min_change_1h_pct": "SOL_MIN_CHANGE_1H_PCT",
}


def _format_bookmark_env_value(env_key: str, value: Any) -> str:
    if env_key in ("TAKE_PROFIT_LEVELS", "TAKE_PROFIT_PORTIONS") and isinstance(value, list):
        return ",".join(str(v) for v in value)
    if env_key in (
        "MAX_POTENTIAL_MODE",
        "BLOCK_STOCK_RELATED_TOKENS",
        "HOT_MARKET_MODE_ENABLED",
    ):
        return "true" if value else "false"
    return str(value)


def _write_env_keys(values: dict[str, Any], key_map: dict[str, str]) -> None:
    """Persist config keys to .env (create or update lines)."""
    path = PROJECT_ROOT / ".env"
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    pending = {
        env_key: _format_bookmark_env_value(env_key, values[api_key])
        for api_key, env_key in key_map.items()
        if api_key in values
    }
    new_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        matched = False
        for env_key, formatted in pending.items():
            if line.startswith(f"{env_key}="):
                new_lines.append(f"{env_key}={formatted}")
                seen.add(env_key)
                matched = True
                break
        if not matched:
            new_lines.append(line)
    for env_key, formatted in pending.items():
        if env_key not in seen:
            new_lines.append(f"{env_key}={formatted}")
    try:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except OSError as exc:
        _logger.warning("Could not save env values to .env: %s", exc)


def _write_bookmark_env(values: dict[str, Any]) -> None:
    """Persist bookmark trading keys to .env (create or update lines)."""
    _write_env_keys(values, BOOKMARK_ENV_KEYS)


def ensure_config_bookmark(
    label: str = "pre-win-preset",
    values: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create bookmark file if missing (explicit pre-win values by default)."""
    path = CONFIG_BOOKMARK_PATH
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    snapshot = dict(values if values is not None else PRE_WIN_PRESET_BOOKMARK)
    payload = {
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": "Snapshot before Best Win preset — use restore to revert",
        "values": snapshot,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _read_bookmark_file(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def get_active_bookmark_path() -> Path:
    """Prefer best-win bookmark when present, else legacy win-focused bookmark."""
    if BEST_WIN_BOOKMARK_PATH.exists():
        return BEST_WIN_BOOKMARK_PATH
    return CONFIG_BOOKMARK_PATH


def get_config_bookmark_info() -> dict[str, Any]:
    """Return bookmark metadata and whether a file exists."""
    path = get_active_bookmark_path()
    data = _read_bookmark_file(path)
    if not data:
        return {
            "exists": False,
            "path": str(path),
            "label": None,
            "created_at": None,
        }
    return {
        "exists": True,
        "path": str(path),
        "label": data.get("label"),
        "created_at": data.get("created_at"),
        "description": data.get("description"),
        "values": data.get("values", {}),
        "bookmark_kind": "best_win" if path == BEST_WIN_BOOKMARK_PATH else "win_focused",
    }



@dataclass(frozen=True)
class WatchlistMintRule:
    """Per-mint pinned watchlist entry and exit policy."""

    mint: str
    label: str = ""
    min_day_usd_gain: Optional[float] = None
    min_day_pct_gain: Optional[float] = None
    sell_at_pct: Optional[float] = None
    one_buy_one_sell: bool = False
    override_ladder: bool = False
    use_standard_exits: bool = True

    def to_dict(self) -> dict:
        return {
            "mint": self.mint,
            "label": self.label,
            "min_day_usd_gain": self.min_day_usd_gain,
            "min_day_pct_gain": self.min_day_pct_gain,
            "sell_at_pct": self.sell_at_pct,
            "one_buy_one_sell": self.one_buy_one_sell,
            "override_ladder": self.override_ladder,
            "use_standard_exits": self.use_standard_exits,
        }


DEFAULT_WATCHLIST_RULES: tuple[WatchlistMintRule, ...] = (
    WatchlistMintRule(
        mint=DEFAULT_WATCHLIST_MINT,
        label="WBTC",
        min_day_usd_gain=DEFAULT_WATCHLIST_MIN_USD_GAIN,
        use_standard_exits=True,
    ),
    WatchlistMintRule(
        mint=DEFAULT_WATCHLIST_MINT_B,
        label="6M8z",
        min_day_pct_gain=DEFAULT_WATCHLIST_MIN_DAY_PCT_GAIN_B,
        sell_at_pct=DEFAULT_WATCHLIST_SELL_AT_PCT_B,
        one_buy_one_sell=True,
        override_ladder=True,
        use_standard_exits=False,
    ),
)


def _parse_mint_list(env_val: str) -> frozenset[str]:
    if not env_val or not env_val.strip():
        return frozenset()
    return frozenset(m.strip() for m in env_val.split(",") if m.strip())


def normalize_entry_momentum_pct(value: float) -> float:
    """Map entry momentum to an allowed fraction (0.25%, 0.50%, or 0.75%)."""
    for allowed in ALLOWED_ENTRY_MOMENTUM_PCT:
        if abs(float(value) - allowed) < 1e-9:
            return allowed
    raise ValueError(
        "entry_momentum_pct must be 0.0025 (0.25%), 0.004 (0.40%), 0.005 (0.50%), or 0.0075 (0.75%)"
    )


def effective_stop_loss_pct(mint: str) -> float:
    """1.5% stop for memecoins; 2% for pinned WBTC watchlist only."""
    if is_wbtc_watchlist_mint(mint):
        return Config.WBTC_STOP_LOSS_PCT
    return Config.STOP_LOSS_PCT


def normalize_stop_loss_pct(value: float) -> float:
    """Map a stop-loss fraction to an allowed value (handles float drift)."""
    for allowed in ALLOWED_STOP_LOSS_PCT:
        if abs(float(value) - allowed) < 1e-9:
            return allowed
    raise ValueError(
        "stop_loss_pct must be 0.015 (1.5%), 0.02 (2.0%), 0.03 (3.0%), or 0.05 (5.0%)"
    )


def normalize_trade_size(value: float) -> float:
    """Map a trade size to an allowed SOL amount (handles float drift)."""
    for allowed in ALLOWED_TRADE_SIZE_SOL:
        if abs(float(value) - allowed) < 1e-9:
            return allowed
    raise ValueError(
        f"trade_size_sol must be one of: {', '.join(str(v) for v in ALLOWED_TRADE_SIZE_SOL)} SOL"
    )


def normalize_paper_balance_sol(value: float) -> float:
    """Validate user-set paper trading balance (SOL)."""
    amount = float(value)
    if amount < MIN_PAPER_SIMULATED_BALANCE_SOL:
        raise ValueError(
            f"paper balance must be at least {MIN_PAPER_SIMULATED_BALANCE_SOL} SOL"
        )
    if amount > MAX_PAPER_SIMULATED_BALANCE_SOL:
        raise ValueError(
            f"paper balance must be at most {MAX_PAPER_SIMULATED_BALANCE_SOL} SOL"
        )
    return amount


def normalize_live_tradeable_balance_sol(value: float) -> float:
    """Validate user-set live tradeable balance cap (SOL)."""
    amount = float(value)
    if amount < MIN_LIVE_TRADEABLE_BALANCE_SOL:
        raise ValueError(
            f"live tradeable balance must be at least "
            f"{MIN_LIVE_TRADEABLE_BALANCE_SOL} SOL"
        )
    if amount > MAX_LIVE_TRADEABLE_BALANCE_SOL:
        raise ValueError(
            f"live tradeable balance must be at most "
            f"{MAX_LIVE_TRADEABLE_BALANCE_SOL} SOL"
        )
    return amount


def _parse_float_list(env_val: str, default: list[float]) -> list[float]:
    if not env_val or not env_val.strip():
        return list(default)
    return [float(x.strip()) for x in env_val.split(",") if x.strip()]


def _parse_int_list(env_val: str, default: list[int]) -> list[int]:
    if not env_val or not env_val.strip():
        return list(default)
    return [int(x.strip()) for x in env_val.split(",") if x.strip()]


def normalize_ladder_early_exit_levels(levels: list[int]) -> list[int]:
    """Migrate stale 4-level ladder early-exit indices to 2-step default."""
    if levels == PREVIOUS_LADDER_EARLY_EXIT_LEVELS:
        return list(DEFAULT_LADDER_EARLY_EXIT_LEVELS)
    return list(levels)


def normalize_take_profit_portions(portions: list[float]) -> list[float]:
    """Return ladder portions; migrate stale partial-ladder overrides to instant-only."""
    if len(portions) == len(PREVIOUS_TAKE_PROFIT_PORTIONS) and all(
        abs(a - b) < 1e-9 for a, b in zip(portions, PREVIOUS_TAKE_PROFIT_PORTIONS)
    ):
        return list(DEFAULT_TAKE_PROFIT_PORTIONS)
    if len(portions) == len(STALE_LADDER_TAKE_PROFIT_PORTIONS) and all(
        abs(a - b) < 1e-9 for a, b in zip(portions, STALE_LADDER_TAKE_PROFIT_PORTIONS)
    ):
        return list(DEFAULT_TAKE_PROFIT_PORTIONS)
    return list(portions)


def normalize_take_profit_levels(levels: list[float]) -> list[float]:
    """Return ladder levels; migrate legacy env overrides to instant-only exits."""
    if len(levels) == len(LEGACY_TAKE_PROFIT_LEVELS) and all(
        abs(a - b) < 1e-9 for a, b in zip(levels, LEGACY_TAKE_PROFIT_LEVELS)
    ):
        return list(DEFAULT_TAKE_PROFIT_LEVELS)
    if len(levels) == len(PREVIOUS_TAKE_PROFIT_LEVELS) and all(
        abs(a - b) < 1e-9 for a, b in zip(levels, PREVIOUS_TAKE_PROFIT_LEVELS)
    ):
        return list(DEFAULT_TAKE_PROFIT_LEVELS)
    if len(levels) == len(STALE_TAKE_PROFIT_LEVELS) and all(
        abs(a - b) < 1e-9 for a, b in zip(levels, STALE_TAKE_PROFIT_LEVELS)
    ):
        return list(DEFAULT_TAKE_PROFIT_LEVELS)
    if len(levels) == len(STALE_LADDER_TAKE_PROFIT_LEVELS) and all(
        abs(a - b) < 1e-9 for a, b in zip(levels, STALE_LADDER_TAKE_PROFIT_LEVELS)
    ):
        return list(DEFAULT_TAKE_PROFIT_LEVELS)
    return list(levels)


class Config:
    SOLANA_NETWORK = os.getenv("SOLANA_NETWORK", "mainnet-beta")
    SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY", "")
    SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "")

    JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "")
    JUPITER_QUOTE_API = os.getenv(
        "JUPITER_QUOTE_API", "https://lite-api.jup.ag/swap/v1/quote"
    )
    JUPITER_SWAP_API = os.getenv(
        "JUPITER_SWAP_API", "https://lite-api.jup.ag/swap/v1/swap"
    )
    JUPITER_PRICE_API = os.getenv(
        "JUPITER_PRICE_API", "https://api.jup.ag/price/v3"
    )
    JUPITER_REQUEST_DELAY_SEC = float(
        os.getenv(
            "JUPITER_REQUEST_DELAY_SEC",
            str(DEFAULT_JUPITER_REQUEST_DELAY_SEC),
        )
    )
    JUPITER_PRICE_CACHE_TTL_SEC = int(
        os.getenv(
            "JUPITER_PRICE_CACHE_TTL_SEC",
            str(DEFAULT_JUPITER_PRICE_CACHE_TTL_SEC),
        )
    )
    JUPITER_QUOTE_CACHE_TTL_SEC = int(
        os.getenv(
            "JUPITER_QUOTE_CACHE_TTL_SEC",
            str(DEFAULT_JUPITER_QUOTE_CACHE_TTL_SEC),
        )
    )

    DEXSCREENER_BASE = "https://api.dexscreener.com"
    BIRDEYE_API_BASE = "https://public-api.birdeye.so"
    BIRDEYE_NEW_LISTING_PATH = "/defi/v2/tokens/new_listing"
    BIRDEYE_TRENDING_PATH = "/defi/token_trending"
    BIRDEYE_FIND_GEMS_PATH = "/defi/v3/token/meme/list"
    BIRDEYE_MEME_LIST_PATH = "/defi/v3/token/list"
    BIRDEYE_OVERVIEW_PATH = "/defi/token_overview"
    GMGN_API_BASE = "https://gmgn.ai"
    DEXSCREENER_API_KEY = os.getenv("DEXSCREENER_API_KEY", "")
    PUMPFUN_API_KEY = os.getenv("PUMPFUN_API_KEY", "")
    PUMPFUN_API_BASE = os.getenv(
        "PUMPFUN_API_BASE", "https://frontend-api.pump.fun"
    ).rstrip("/")
    MAX_POTENTIAL_MODE = os.getenv("MAX_POTENTIAL_MODE", "false").lower() == "true"

    ENTRY_MOMENTUM_PCT = float(
        os.getenv("ENTRY_MOMENTUM_PCT", str(DEFAULT_ENTRY_MOMENTUM_PCT))
    )
    TAKE_PROFIT_LEVELS = normalize_take_profit_levels(
        _parse_float_list(
            os.getenv("TAKE_PROFIT_LEVELS", ""),
            DEFAULT_TAKE_PROFIT_LEVELS,
        )
    )
    TAKE_PROFIT_PORTIONS = normalize_take_profit_portions(
        _parse_float_list(
            os.getenv("TAKE_PROFIT_PORTIONS", ""),
            DEFAULT_TAKE_PROFIT_PORTIONS,
        )
    )
    TARGET_NET_PROFIT_SOL = float(
        os.getenv("TARGET_NET_PROFIT_SOL", str(DEFAULT_TARGET_NET_PROFIT_SOL))
    )
    _fee_buffer = os.getenv("FEE_BUFFER_SOL", "").strip()
    FEE_BUFFER_SOL = float(_fee_buffer) if _fee_buffer else None
    STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", str(DEFAULT_STOP_LOSS_PCT)))
    TIME_STOP_MINUTES = int(os.getenv("TIME_STOP_MINUTES", "30"))
    LADDER_MISSED_POSITIVE_EXIT_MINUTES = int(
        os.getenv(
            "LADDER_MISSED_POSITIVE_EXIT_MINUTES",
            str(DEFAULT_LADDER_MISSED_POSITIVE_EXIT_MINUTES),
        )
    )
    LADDER_MISSED_NEGATIVE_DCA_MINUTES = int(
        os.getenv(
            "LADDER_MISSED_NEGATIVE_DCA_MINUTES",
            os.getenv("TIME_STOP_MINUTES", str(DEFAULT_LADDER_MISSED_NEGATIVE_DCA_MINUTES)),
        )
    )
    MAX_BUYS_PER_MINT = int(
        os.getenv("MAX_BUYS_PER_MINT", str(DEFAULT_MAX_BUYS_PER_MINT))
    )
    ENABLE_LADDER_TIME_EXITS = (
        os.getenv("ENABLE_LADDER_TIME_EXITS", "true").lower() == "true"
    )
    MAX_HOLD_MINUTES_NON_WBTC = int(
        os.getenv(
            "MAX_HOLD_MINUTES_NON_WBTC",
            str(DEFAULT_MAX_HOLD_MINUTES_NON_WBTC),
        )
    )
    MAX_HOLD_ENABLED = (
        os.getenv("MAX_HOLD_ENABLED", "true").lower() == "true"
    )

    MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", str(DEFAULT_MIN_LIQUIDITY_USD)))
    MIN_EXPECTED_NET_PROFIT_SOL = float(
        os.getenv("MIN_EXPECTED_NET_PROFIT_SOL", str(DEFAULT_MIN_EXPECTED_NET_PROFIT_SOL))
    )
    MIN_NET_WIN_SOL = float(
        os.getenv("MIN_NET_WIN_SOL", str(DEFAULT_MIN_NET_WIN_SOL))
    )
    LOSS_REENTRY_COOLDOWN_MINUTES = int(
        os.getenv(
            "LOSS_REENTRY_COOLDOWN_MINUTES",
            str(DEFAULT_LOSS_REENTRY_COOLDOWN_MINUTES),
        )
    )
    LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES = int(
        os.getenv(
            "LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES",
            str(DEFAULT_LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES),
        )
    )
    REENTRY_MIN_MOMENTUM_PCT = float(
        os.getenv(
            "REENTRY_MIN_MOMENTUM_PCT",
            str(DEFAULT_REENTRY_MIN_MOMENTUM_PCT),
        )
    )
    REENTRY_RETRY_ENABLED = (
        os.getenv(
            "REENTRY_RETRY_ENABLED",
            "true" if DEFAULT_REENTRY_RETRY_ENABLED else "false",
        ).lower()
        == "true"
    )
    REENTRY_RETRY_WINDOW_MINUTES = int(
        float(
            os.getenv(
                "REENTRY_RETRY_WINDOW_MINUTES",
                str(DEFAULT_REENTRY_RETRY_WINDOW_MINUTES),
            )
        )
    )
    REENTRY_RETRY_BLOCK_HOURS = int(
        float(
            os.getenv(
                "REENTRY_RETRY_BLOCK_HOURS",
                str(DEFAULT_REENTRY_RETRY_BLOCK_HOURS),
            )
        )
    )
    REENTRY_RETRY_MAX_ATTEMPTS = int(
        float(
            os.getenv(
                "REENTRY_RETRY_MAX_ATTEMPTS",
                str(DEFAULT_REENTRY_RETRY_MAX_ATTEMPTS),
            )
        )
    )
    WBTC_STOP_LOSS_PCT = float(
        os.getenv("WBTC_STOP_LOSS_PCT", str(DEFAULT_WBTC_STOP_LOSS_PCT))
    )
    WBTC_PROFIT_ONLY_EXITS = (
        os.getenv("WBTC_PROFIT_ONLY_EXITS", "true").lower() == "true"
    )
    WBTC_MIN_DAILY_GAIN_USD = float(
        os.getenv("WBTC_MIN_DAILY_GAIN_USD", str(DEFAULT_WBTC_MIN_DAILY_GAIN_USD))
    )
    WBTC_REQUIRE_POSITIVE_DAY = (
        os.getenv("WBTC_REQUIRE_POSITIVE_DAY", "true").lower() == "true"
    )
    WBTC_DAY_GAIN_SUSTAIN_MINUTES = int(
        float(
            os.getenv(
                "WBTC_DAY_GAIN_SUSTAIN_MINUTES",
                str(DEFAULT_WBTC_DAY_GAIN_SUSTAIN_MINUTES),
            )
        )
    )
    WBTC_STOP_LOSS_ENABLED = (
        os.getenv("WBTC_STOP_LOSS_ENABLED", "false").lower() == "true"
    )
    WBTC_ENTRY_SUSTAIN_PATH = str(
        resolve_data_path(
            os.getenv("WBTC_ENTRY_SUSTAIN_PATH", DEFAULT_WBTC_ENTRY_SUSTAIN_PATH)
        )
    )
    _wbtc_min_expected_gain = os.getenv("WBTC_MIN_EXPECTED_GAIN_PCT", "").strip()
    WBTC_MIN_EXPECTED_GAIN_PCT = (
        float(_wbtc_min_expected_gain) if _wbtc_min_expected_gain else None
    )
    JITOSOL_MIN_DAILY_GAIN_USD = float(
        os.getenv(
            "JITOSOL_MIN_DAILY_GAIN_USD", str(DEFAULT_JITOSOL_MIN_DAILY_GAIN_USD)
        )
    )
    JITOSOL_REQUIRE_POSITIVE_DAY = (
        os.getenv("JITOSOL_REQUIRE_POSITIVE_DAY", "true").lower() == "true"
    )
    _jitosol_min_expected_gain = os.getenv("JITOSOL_MIN_EXPECTED_GAIN_PCT", "").strip()
    JITOSOL_MIN_EXPECTED_GAIN_PCT = (
        float(_jitosol_min_expected_gain) if _jitosol_min_expected_gain else None
    )
    WETH_MIN_DAILY_GAIN_USD = float(
        os.getenv("WETH_MIN_DAILY_GAIN_USD", str(DEFAULT_WETH_MIN_DAILY_GAIN_USD))
    )
    WETH_REQUIRE_POSITIVE_DAY = (
        os.getenv("WETH_REQUIRE_POSITIVE_DAY", "true").lower() == "true"
    )
    _weth_min_expected_gain = os.getenv("WETH_MIN_EXPECTED_GAIN_PCT", "").strip()
    WETH_MIN_EXPECTED_GAIN_PCT = (
        float(_weth_min_expected_gain) if _weth_min_expected_gain else None
    )
    MAX_LOSS_PER_TRADE_SOL = float(
        os.getenv("MAX_LOSS_PER_TRADE_SOL", str(DEFAULT_MAX_LOSS_PER_TRADE_SOL))
    )
    MAX_ENTRY_PRICE_IMPACT_PCT = float(
        os.getenv("MAX_ENTRY_PRICE_IMPACT_PCT", str(DEFAULT_MAX_ENTRY_PRICE_IMPACT_PCT))
    )
    L1_PROTECTION_PCT = float(
        os.getenv("L1_PROTECTION_PCT", str(DEFAULT_L1_PROTECTION_PCT))
    )
    ENABLE_L1_PROTECTION = (
        os.getenv("ENABLE_L1_PROTECTION", "false").lower() == "true"
    )
    # Deprecated alias; L1 protection supersedes breakeven-at-0% after L1 partial.
    MOVE_SL_TO_BREAKEVEN_AFTER_L1 = ENABLE_L1_PROTECTION
    LADDER_EARLY_EXIT_LEVELS = normalize_ladder_early_exit_levels(
        _parse_int_list(
            os.getenv("LADDER_EARLY_EXIT_LEVELS", ""),
            DEFAULT_LADDER_EARLY_EXIT_LEVELS,
        )
    )
    MOMENTUM_SLOWDOWN_PCT = float(
        os.getenv("MOMENTUM_SLOWDOWN_PCT", str(DEFAULT_MOMENTUM_SLOWDOWN_PCT))
    )
    WEAKEN_EXIT_MIN_PROFIT_PCT = float(
        os.getenv(
            "WEAKEN_EXIT_MIN_PROFIT_PCT",
            str(DEFAULT_WEAKEN_EXIT_MIN_PROFIT_PCT),
        )
    )
    WEAKEN_EXIT_ENABLED = (
        os.getenv("WEAKEN_EXIT_ENABLED", "true").lower() == "true"
    )
    INSTANT_EXIT_3PCT = float(
        os.getenv(
            "INSTANT_EXIT_3PCT",
            str(DEFAULT_INSTANT_EXIT_3PCT),
        )
    )
    INSTANT_PROFIT_EXIT_PCT = float(
        os.getenv(
            "INSTANT_PROFIT_EXIT_PCT",
            str(DEFAULT_INSTANT_PROFIT_EXIT_PCT),
        )
    )
    INSTANT_PROFIT_EXIT_ENABLED = (
        os.getenv("INSTANT_PROFIT_EXIT_ENABLED", "true").lower() == "true"
    )
    MAX_CONSECUTIVE_LOSSES = int(
        os.getenv("MAX_CONSECUTIVE_LOSSES", str(DEFAULT_MAX_CONSECUTIVE_LOSSES))
    )
    CONSECUTIVE_LOSS_PAUSE_MINUTES = int(
        os.getenv(
            "CONSECUTIVE_LOSS_PAUSE_MINUTES",
            str(DEFAULT_CONSECUTIVE_LOSS_PAUSE_MINUTES),
        )
    )
    CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY = (
        os.getenv(
            "CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY",
            "true" if DEFAULT_CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY else "false",
        ).lower()
        == "true"
    )
    AUTO_STOP_ON_MAX_DAILY_LOSS = (
        os.getenv("AUTO_STOP_ON_MAX_DAILY_LOSS", "false").lower() == "true"
    )
    MAX_REALISTIC_TP_PCT = float(
        os.getenv("MAX_REALISTIC_TP_PCT", str(DEFAULT_MAX_REALISTIC_TP_PCT))
    )
    _pumpfun_min_liq = os.getenv("PUMPFUN_MIN_LIQUIDITY_USD")
    PUMPFUN_MIN_LIQUIDITY_USD = float(_pumpfun_min_liq) if _pumpfun_min_liq else None
    PUMPFUN_MIN_MARKET_CAP_USD = float(os.getenv("PUMPFUN_MIN_MARKET_CAP_USD", "5000"))
    PUMPFUN_MAX_AGE_MINUTES = int(os.getenv("PUMPFUN_MAX_AGE_MINUTES", "0"))
    _pumpfun_min_vol = os.getenv("PUMPFUN_MIN_VOLUME_24H_USD")
    PUMPFUN_MIN_VOLUME_24H_USD = float(_pumpfun_min_vol) if _pumpfun_min_vol else None
    _pumpfun_min_momentum = os.getenv("PUMPFUN_MIN_MOMENTUM_PCT")
    PUMPFUN_MIN_MOMENTUM_PCT = float(_pumpfun_min_momentum) if _pumpfun_min_momentum else None
    _scan_pf = os.getenv("SCAN_PUMPFUN")
    _include_pf = os.getenv("INCLUDE_PUMPFUN", "true")
    SCAN_PUMPFUN = (_scan_pf if _scan_pf is not None else _include_pf).lower() == "true"
    INCLUDE_PUMPFUN = SCAN_PUMPFUN

    BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
    _birdeye_min_liq = os.getenv("BIRDEYE_MIN_LIQUIDITY_USD")
    BIRDEYE_MIN_LIQUIDITY_USD = float(_birdeye_min_liq) if _birdeye_min_liq else None
    _birdeye_min_vol = os.getenv("BIRDEYE_MIN_VOLUME_24H_USD")
    BIRDEYE_MIN_VOLUME_24H_USD = float(_birdeye_min_vol) if _birdeye_min_vol else None
    _birdeye_min_momentum = os.getenv("BIRDEYE_MIN_MOMENTUM_PCT")
    BIRDEYE_MIN_MOMENTUM_PCT = float(_birdeye_min_momentum) if _birdeye_min_momentum else None
    SCAN_BIRDEYE = os.getenv("SCAN_BIRDEYE", "true").lower() == "true"
    BIRDEYE_FIND_GEMS_ENABLED = (
        os.getenv("BIRDEYE_FIND_GEMS_ENABLED", "true").lower() == "true"
    )
    BIRDEYE_GAINER_TIMEFRAME = os.getenv("BIRDEYE_GAINER_TIMEFRAME", "1h").strip().lower()
    GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
    _scan_gmgn = os.getenv("SCAN_GMGN")
    _gmgn_enabled = os.getenv("GMGN_ENABLED", "true")
    SCAN_GMGN = (_scan_gmgn if _scan_gmgn is not None else _gmgn_enabled).lower() == "true"
    GMGN_ENABLED = SCAN_GMGN
    GMGN_TIMEFRAME = os.getenv("GMGN_TIMEFRAME", DEFAULT_GMGN_TIMEFRAME).strip().lower()
    GMGN_REQUEST_DELAY_SEC = float(
        os.getenv("GMGN_REQUEST_DELAY_SEC", str(DEFAULT_GMGN_REQUEST_DELAY_SEC))
    )
    _gmgn_min_liq = os.getenv("GMGN_MIN_LIQUIDITY_USD")
    GMGN_MIN_LIQUIDITY_USD = float(_gmgn_min_liq) if _gmgn_min_liq else None
    _gmgn_min_vol = os.getenv("GMGN_MIN_VOLUME_24H_USD")
    GMGN_MIN_VOLUME_24H_USD = float(_gmgn_min_vol) if _gmgn_min_vol else None
    _gmgn_min_momentum = os.getenv("GMGN_MIN_MOMENTUM_PCT")
    GMGN_MIN_MOMENTUM_PCT = float(_gmgn_min_momentum) if _gmgn_min_momentum else None
    _gmgn_filters = os.getenv("GMGN_SAFETY_FILTERS", "not_honeypot").strip()
    GMGN_SAFETY_FILTERS = [f.strip() for f in _gmgn_filters.split(",") if f.strip()]
    MIN_VOLUME_24H_USD = float(
        os.getenv("MIN_VOLUME_24H_USD", str(DEFAULT_MIN_VOLUME_24H_USD))
    )
    _non_watchlist_min_vol = os.getenv("NON_WATCHLIST_MIN_VOLUME_24H_USD")
    NON_WATCHLIST_MIN_VOLUME_24H_USD = (
        float(_non_watchlist_min_vol)
        if _non_watchlist_min_vol
        else DEFAULT_NON_WATCHLIST_MIN_VOLUME_24H_USD
    )
    MIN_POOL_AGE_HOURS = float(os.getenv("MIN_POOL_AGE_HOURS", "1"))
    MAX_POOL_AGE_DAYS = float(os.getenv("MAX_POOL_AGE_DAYS", "30"))
    MIN_MOMENTUM_PCT = float(
        os.getenv("MIN_MOMENTUM_PCT", str(DEFAULT_MIN_MOMENTUM_PCT))
    )

    SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "10"))
    DEXSCREENER_REQUEST_DELAY_SEC = float(
        os.getenv(
            "DEXSCREENER_REQUEST_DELAY_SEC",
            str(DEFAULT_DEXSCREENER_REQUEST_DELAY_SEC),
        )
    )
    DEXSCREENER_PAIR_CACHE_TTL_SEC = int(
        os.getenv(
            "DEXSCREENER_PAIR_CACHE_TTL_SEC",
            str(DEFAULT_DEXSCREENER_PAIR_CACHE_TTL_SEC),
        )
    )
    DEXSCREENER_DEEP_SCAN_PER_CYCLE = int(
        os.getenv(
            "DEXSCREENER_DEEP_SCAN_PER_CYCLE",
            str(DEFAULT_DEXSCREENER_DEEP_SCAN_PER_CYCLE),
        )
    )
    FIRST_SCAN_DEEP_MINTS = int(
        os.getenv("FIRST_SCAN_DEEP_MINTS", str(DEFAULT_FIRST_SCAN_DEEP_MINTS))
    )
    FIRST_SCAN_FAST_MODE = os.getenv(
        "FIRST_SCAN_FAST_MODE", str(DEFAULT_FIRST_SCAN_FAST_MODE)
    ).lower() == "true"
    DEXSCREENER_MAX_SEED_MINTS = int(
        os.getenv(
            "DEXSCREENER_MAX_SEED_MINTS",
            str(
                MAX_POTENTIAL_DEXSCREENER_MAX_SEED_MINTS
                if MAX_POTENTIAL_MODE
                else DEFAULT_DEXSCREENER_MAX_SEED_MINTS
            ),
        )
    )
    WATCHLIST_TOP_N = int(
        os.getenv(
            "WATCHLIST_TOP_N",
            str(
                MAX_POTENTIAL_WATCHLIST_TOP_N
                if MAX_POTENTIAL_MODE
                else DEFAULT_WATCHLIST_TOP_N
            ),
        )
    )
    TRADE_CANDIDATE_TOP_N = int(
        os.getenv("TRADE_CANDIDATE_TOP_N", str(DEFAULT_TRADE_CANDIDATE_TOP_N))
    )
    BIRDEYE_TRENDING_LIMIT = int(
        os.getenv(
            "BIRDEYE_TRENDING_LIMIT",
            str(
                MAX_POTENTIAL_BIRDEYE_TRENDING_LIMIT
                if MAX_POTENTIAL_MODE
                else DEFAULT_BIRDEYE_TRENDING_LIMIT
            ),
        )
    )
    PUMPFUN_API_LIMIT = int(
        os.getenv(
            "PUMPFUN_API_LIMIT",
            str(
                MAX_POTENTIAL_PUMPFUN_API_LIMIT
                if MAX_POTENTIAL_MODE
                else DEFAULT_PUMPFUN_API_LIMIT
            ),
        )
    )
    GMGN_TRENDING_LIMIT = int(
        os.getenv(
            "GMGN_TRENDING_LIMIT",
            str(
                MAX_POTENTIAL_GMGN_TRENDING_LIMIT
                if MAX_POTENTIAL_MODE
                else DEFAULT_GMGN_TRENDING_LIMIT
            ),
        )
    )
    PRICE_POLL_SEC = int(os.getenv("PRICE_POLL_SEC", "5"))
    POSITION_MONITOR_SEC = int(os.getenv("POSITION_MONITOR_SEC", "1"))
    STOP_LOSS_QUOTE_CHECK = (
        os.getenv(
            "STOP_LOSS_QUOTE_CHECK",
            "true" if DEFAULT_STOP_LOSS_QUOTE_CHECK else "false",
        ).lower()
        == "true"
    )
    EMERGENCY_STOP_LOSS_PCT = float(
        os.getenv("EMERGENCY_STOP_LOSS_PCT", str(DEFAULT_EMERGENCY_STOP_LOSS_PCT))
    )
    CATASTROPHIC_STOP_LOSS_PCT = float(
        os.getenv(
            "CATASTROPHIC_STOP_LOSS_PCT",
            str(DEFAULT_CATASTROPHIC_STOP_LOSS_PCT),
        )
    )
    LOSS_FRESH_QUOTE_PCT = float(
        os.getenv("LOSS_FRESH_QUOTE_PCT", str(DEFAULT_LOSS_FRESH_QUOTE_PCT))
    )
    STOP_LOSS_NEVER_MISS = (
        os.getenv(
            "STOP_LOSS_NEVER_MISS",
            "true" if DEFAULT_STOP_LOSS_NEVER_MISS else "false",
        ).lower()
        == "true"
    )
    MAX_FULL_EXIT_SELL_PREVIEW_IMPACT_PCT = float(
        os.getenv(
            "MAX_FULL_EXIT_SELL_PREVIEW_IMPACT_PCT",
            str(DEFAULT_MAX_FULL_EXIT_SELL_PREVIEW_IMPACT_PCT),
        )
    )
    BASELINE_WINDOW_SEC = int(os.getenv("BASELINE_WINDOW_SEC", "30"))

    WATCHLIST_MINT = os.getenv("WATCHLIST_MINT", DEFAULT_WATCHLIST_MINT).strip()
    WATCHLIST_MIN_USD_GAIN = float(
        os.getenv("WATCHLIST_MIN_USD_GAIN", str(DEFAULT_WATCHLIST_MIN_USD_GAIN))
    )
    WATCHLIST_ENABLED = os.getenv("WATCHLIST_ENABLED", "true").lower() == "true"

    BLOCK_STOCK_RELATED_TOKENS = (
        os.getenv(
            "BLOCK_STOCK_RELATED_TOKENS",
            "true" if DEFAULT_BLOCK_STOCK_RELATED_TOKENS else "false",
        ).lower()
        == "true"
    )
    STOCK_TOKEN_BLOCKLIST_MINTS = _parse_mint_list(
        os.getenv("STOCK_TOKEN_BLOCKLIST_MINTS", "")
    )
    STOCK_TOKEN_ALLOWLIST_MINTS = _parse_mint_list(
        os.getenv("STOCK_TOKEN_ALLOWLIST_MINTS", "")
    )

    TRADE_SIZE_SOL = float(os.getenv("TRADE_SIZE_SOL", str(DEFAULT_TRADE_SIZE_SOL)))
    MAX_POSITION_SOL = float(os.getenv("MAX_POSITION_SOL", "0.5"))
    MAX_WALLET_TRADE_PCT = float(
        os.getenv("MAX_WALLET_TRADE_PCT", str(DEFAULT_MAX_WALLET_TRADE_PCT))
    )
    MAX_OPEN_POSITIONS = int(
        os.getenv("MAX_OPEN_POSITIONS", str(DEFAULT_MAX_OPEN_POSITIONS))
    )
    MAX_OPEN_POSITIONS_WBTC = int(
        os.getenv("MAX_OPEN_POSITIONS_WBTC", str(DEFAULT_MAX_OPEN_POSITIONS_WBTC))
    )
    COMPANION_TRADE_ENABLED = (
        os.getenv(
            "COMPANION_TRADE_ENABLED",
            "true" if DEFAULT_COMPANION_TRADE_ENABLED else "false",
        ).lower()
        == "true"
    )
    COMPANION_TRADE_MAX = int(
        os.getenv("COMPANION_TRADE_MAX", str(DEFAULT_COMPANION_TRADE_MAX))
    )
    REENTRY_DIP_PCT = float(
        os.getenv("REENTRY_DIP_PCT", str(DEFAULT_REENTRY_DIP_PCT))
    )
    SOL_TREND_FILTER_ENABLED = (
        os.getenv(
            "SOL_TREND_FILTER_ENABLED",
            "true" if DEFAULT_SOL_TREND_FILTER_ENABLED else "false",
        ).lower()
        == "true"
    )
    SOL_MIN_CHANGE_1H_PCT = float(
        os.getenv(
            "SOL_MIN_CHANGE_1H_PCT",
            str(DEFAULT_SOL_MIN_CHANGE_1H_PCT),
        )
    )
    SOL_MIN_CHANGE_4H_PCT = float(
        os.getenv(
            "SOL_MIN_CHANGE_4H_PCT",
            str(DEFAULT_SOL_MIN_CHANGE_4H_PCT),
        )
    )
    SOL_TREND_CACHE_TTL_SEC = int(
        os.getenv(
            "SOL_TREND_CACHE_TTL_SEC",
            str(DEFAULT_SOL_TREND_CACHE_TTL_SEC),
        )
    )
    SOL_TREND_QUALITY_OVERRIDE_ENABLED = (
        os.getenv(
            "SOL_TREND_QUALITY_OVERRIDE_ENABLED",
            "true" if DEFAULT_SOL_TREND_QUALITY_OVERRIDE_ENABLED else "false",
        ).lower()
        == "true"
    )
    LOSS_ONE_STRIKE_PER_SESSION = (
        os.getenv(
            "LOSS_ONE_STRIKE_PER_SESSION",
            "true" if DEFAULT_LOSS_ONE_STRIKE_PER_SESSION else "false",
        ).lower()
        == "true"
    )
    ENABLE_SOL_TRADING = (
        os.getenv(
            "ENABLE_SOL_TRADING",
            "true" if DEFAULT_ENABLE_SOL_TRADING else "false",
        ).lower()
        == "true"
    )
    SOL_TRADE_MINT = os.getenv("SOL_TRADE_MINT", DEFAULT_SOL_TRADE_MINT).strip()
    SOL_TRADE_MIN_MOMENTUM_1H_PCT = float(
        os.getenv(
            "SOL_TRADE_MIN_MOMENTUM_1H_PCT",
            str(DEFAULT_SOL_TRADE_MIN_MOMENTUM_1H_PCT),
        )
    )
    SOL_TRADE_INSTANT_EXIT_PCT = float(
        os.getenv(
            "SOL_TRADE_INSTANT_EXIT_PCT",
            str(DEFAULT_SOL_TRADE_INSTANT_EXIT_PCT),
        )
    )
    SOL_TRADE_EXIT_ON_TREND_COLD = (
        os.getenv(
            "SOL_TRADE_EXIT_ON_TREND_COLD",
            "true" if DEFAULT_SOL_TRADE_EXIT_ON_TREND_COLD else "false",
        ).lower()
        == "true"
    )
    SOL_TRADE_EXIT_COLD_1H_PCT = float(
        os.getenv(
            "SOL_TRADE_EXIT_COLD_1H_PCT",
            str(DEFAULT_SOL_TRADE_EXIT_COLD_1H_PCT),
        )
    )
    ENABLE_WETH_TRADING = (
        os.getenv(
            "ENABLE_WETH_TRADING",
            "true" if DEFAULT_ENABLE_WETH_TRADING else "false",
        ).lower()
        == "true"
    )
    WETH_MINT = os.getenv("WETH_MINT", WETH_MINT).strip()
    HOT_MARKET_MODE_ENABLED = (
        os.getenv(
            "HOT_MARKET_MODE_ENABLED",
            "true" if DEFAULT_HOT_MARKET_MODE_ENABLED else "false",
        ).lower()
        == "true"
    )
    HOT_MARKET_SOL_MIN_1H_PCT = float(
        os.getenv(
            "HOT_MARKET_SOL_MIN_1H_PCT",
            str(DEFAULT_HOT_MARKET_SOL_MIN_1H_PCT),
        )
    )
    HOT_MARKET_SOL_MIN_4H_PCT = float(
        os.getenv(
            "HOT_MARKET_SOL_MIN_4H_PCT",
            str(DEFAULT_HOT_MARKET_SOL_MIN_4H_PCT),
        )
    )
    HOT_MARKET_MIN_SCANNER_CANDIDATES = int(
        os.getenv(
            "HOT_MARKET_MIN_SCANNER_CANDIDATES",
            str(DEFAULT_HOT_MARKET_MIN_SCANNER_CANDIDATES),
        )
    )
    HOT_MARKET_MIN_GMGN_VOLUME_USD = float(
        os.getenv(
            "HOT_MARKET_MIN_GMGN_VOLUME_USD",
            str(DEFAULT_HOT_MARKET_MIN_GMGN_VOLUME_USD),
        )
    )
    HOT_MARKET_ENTRY_MOMENTUM_PCT = float(
        os.getenv(
            "HOT_MARKET_ENTRY_MOMENTUM_PCT",
            str(DEFAULT_HOT_MARKET_ENTRY_MOMENTUM_PCT),
        )
    )
    HOT_MARKET_MIN_MOMENTUM_PCT = float(
        os.getenv(
            "HOT_MARKET_MIN_MOMENTUM_PCT",
            str(DEFAULT_HOT_MARKET_MIN_MOMENTUM_PCT),
        )
    )
    HOT_MARKET_MIN_VOLUME_24H_USD = float(
        os.getenv(
            "HOT_MARKET_MIN_VOLUME_24H_USD",
            str(DEFAULT_HOT_MARKET_MIN_VOLUME_24H_USD),
        )
    )
    STEADY_TRADE_AUTO_ADJUST = (
        os.getenv(
            "STEADY_TRADE_AUTO_ADJUST",
            "true" if DEFAULT_STEADY_TRADE_AUTO_ADJUST else "false",
        ).lower()
        == "true"
    )
    NEUTRAL_MARKET_ENTRY_MOMENTUM_PCT = float(
        os.getenv(
            "NEUTRAL_MARKET_ENTRY_MOMENTUM_PCT",
            str(DEFAULT_NEUTRAL_MARKET_ENTRY_MOMENTUM_PCT),
        )
    )
    NEUTRAL_MARKET_MIN_MOMENTUM_PCT = float(
        os.getenv(
            "NEUTRAL_MARKET_MIN_MOMENTUM_PCT",
            str(DEFAULT_NEUTRAL_MARKET_MIN_MOMENTUM_PCT),
        )
    )
    NEUTRAL_MARKET_MIN_VOLUME_24H_USD = float(
        os.getenv(
            "NEUTRAL_MARKET_MIN_VOLUME_24H_USD",
            str(DEFAULT_NEUTRAL_MARKET_MIN_VOLUME_24H_USD),
        )
    )
    COLD_MARKET_ENTRY_MOMENTUM_PCT = float(
        os.getenv(
            "COLD_MARKET_ENTRY_MOMENTUM_PCT",
            str(DEFAULT_COLD_MARKET_ENTRY_MOMENTUM_PCT),
        )
    )
    COLD_MARKET_MIN_MOMENTUM_PCT = float(
        os.getenv(
            "COLD_MARKET_MIN_MOMENTUM_PCT",
            str(DEFAULT_COLD_MARKET_MIN_MOMENTUM_PCT),
        )
    )
    COLD_MARKET_MIN_VOLUME_24H_USD = float(
        os.getenv(
            "COLD_MARKET_MIN_VOLUME_24H_USD",
            str(DEFAULT_COLD_MARKET_MIN_VOLUME_24H_USD),
        )
    )
    HOT_MARKET_TARGET_WIN_RATE = float(
        os.getenv(
            "HOT_MARKET_TARGET_WIN_RATE",
            str(DEFAULT_HOT_MARKET_TARGET_WIN_RATE),
        )
    )
    NEUTRAL_MARKET_TARGET_WIN_RATE = float(
        os.getenv(
            "NEUTRAL_MARKET_TARGET_WIN_RATE",
            str(DEFAULT_NEUTRAL_MARKET_TARGET_WIN_RATE),
        )
    )
    COLD_MARKET_TARGET_WIN_RATE = float(
        os.getenv(
            "COLD_MARKET_TARGET_WIN_RATE",
            str(DEFAULT_COLD_MARKET_TARGET_WIN_RATE),
        )
    )
    SESSION_AUTO_TIGHTEN_ENABLED = (
        os.getenv(
            "SESSION_AUTO_TIGHTEN_ENABLED",
            "true" if DEFAULT_SESSION_AUTO_TIGHTEN_ENABLED else "false",
        ).lower()
        == "true"
    )
    SESSION_AUTO_TIGHTEN_MIN_TRADES = int(
        os.getenv(
            "SESSION_AUTO_TIGHTEN_MIN_TRADES",
            str(DEFAULT_SESSION_AUTO_TIGHTEN_MIN_TRADES),
        )
    )
    SESSION_AUTO_TIGHTEN_WIN_LEAN_STEP = float(
        os.getenv(
            "SESSION_AUTO_TIGHTEN_WIN_LEAN_STEP",
            str(DEFAULT_SESSION_AUTO_TIGHTEN_WIN_LEAN_STEP),
        )
    )
    SESSION_AUTO_TIGHTEN_WIN_LEAN_CAP = float(
        os.getenv(
            "SESSION_AUTO_TIGHTEN_WIN_LEAN_CAP",
            str(DEFAULT_SESSION_AUTO_TIGHTEN_WIN_LEAN_CAP),
        )
    )
    SESSION_AUTO_TIGHTEN_LIQUIDITY_STEP_USD = float(
        os.getenv(
            "SESSION_AUTO_TIGHTEN_LIQUIDITY_STEP_USD",
            str(DEFAULT_SESSION_AUTO_TIGHTEN_LIQUIDITY_STEP_USD),
        )
    )
    SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD = float(
        os.getenv(
            "SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD",
            str(DEFAULT_SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD),
        )
    )
    MAX_DAILY_LOSS_SOL = float(os.getenv("MAX_DAILY_LOSS_SOL", "1.0"))
    MIN_SOL_RESERVE = float(os.getenv("MIN_SOL_RESERVE", "0.02"))
    MIN_FUND_SOL = float(os.getenv("MIN_FUND_SOL", str(DEFAULT_MIN_FUND_SOL)))
    MIN_PAPER_FUND_SOL = float(
        os.getenv("MIN_PAPER_FUND_SOL", str(DEFAULT_MIN_PAPER_FUND_SOL))
    )
    MIN_FUND_WAIVER_HOURS = float(
        os.getenv("MIN_FUND_WAIVER_HOURS", str(DEFAULT_MIN_FUND_WAIVER_HOURS))
    )
    MIN_FUND_WAIVER_AFTER_SESSION_TRADE = os.getenv(
        "MIN_FUND_WAIVER_AFTER_SESSION_TRADE",
        "true" if DEFAULT_MIN_FUND_WAIVER_AFTER_SESSION_TRADE else "false",
    ).lower() in ("1", "true", "yes")

    DEFAULT_SLIPPAGE_BPS = int(os.getenv("DEFAULT_SLIPPAGE_BPS", "100"))
    MAX_EXIT_PRICE_IMPACT_PCT = float(
        os.getenv("MAX_EXIT_PRICE_IMPACT_PCT", str(DEFAULT_MAX_EXIT_PRICE_IMPACT_PCT))
    )
    MAX_ROUND_TRIP_IMPACT_PCT = float(
        os.getenv(
            "MAX_ROUND_TRIP_IMPACT_PCT",
            str(DEFAULT_MAX_ROUND_TRIP_IMPACT_PCT),
        )
    )
    MAX_ABSOLUTE_PRICE_IMPACT_PCT = float(
        os.getenv(
            "MAX_PRICE_IMPACT_PCT",
            os.getenv(
                "MAX_ABSOLUTE_PRICE_IMPACT_PCT",
                str(DEFAULT_MAX_ABSOLUTE_PRICE_IMPACT_PCT),
            ),
        )
    )
    # Backward-compatible alias: absolute execution ceiling (not entry/exit defer threshold).
    MAX_PRICE_IMPACT_PCT = MAX_ABSOLUTE_PRICE_IMPACT_PCT
    PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT = float(
        os.getenv(
            "PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT",
            str(DEFAULT_PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT),
        )
    )
    EXIT_IMPACT_FORCE_RETRIES = int(
        os.getenv("EXIT_IMPACT_FORCE_RETRIES", str(DEFAULT_EXIT_IMPACT_FORCE_RETRIES))
    )
    SOL_TX_FEE_LAMPORTS = int(
        os.getenv("SOL_TX_FEE_LAMPORTS", str(DEFAULT_SOL_TX_FEE_LAMPORTS))
    )
    SOL_PRIORITY_FEE_LAMPORTS = int(
        os.getenv(
            "SOL_PRIORITY_FEE_LAMPORTS",
            os.getenv("PRIORITY_FEE_LAMPORTS", str(DEFAULT_SOL_PRIORITY_FEE_LAMPORTS)),
        )
    )
    PRIORITY_FEE_LAMPORTS = SOL_PRIORITY_FEE_LAMPORTS
    FEE_BUFFER_PCT = float(os.getenv("FEE_BUFFER_PCT", str(DEFAULT_FEE_BUFFER_PCT)))
    DEFAULT_DEX_FEE_BPS = int(
        os.getenv("DEFAULT_DEX_FEE_BPS", str(DEFAULT_FALLBACK_DEX_FEE_BPS))
    )
    _dex_fee_map_raw = os.getenv("DEX_FEE_BPS_MAP", "").strip()
    DEX_FEE_BPS_BY_LABEL: dict = {}
    if _dex_fee_map_raw:
        for part in _dex_fee_map_raw.split(","):
            piece = part.strip()
            if "=" not in piece:
                continue
            name, bps = piece.split("=", 1)
            try:
                DEX_FEE_BPS_BY_LABEL[name.strip().lower()] = int(bps.strip())
            except ValueError:
                continue
    FEE_PREVIEW_MINT = os.getenv(
        "FEE_PREVIEW_MINT",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    )

    TRADE_COOLDOWN_CYCLES = int(os.getenv("TRADE_COOLDOWN_CYCLES", "5"))

    # Live-start product fee (charged once per Live start, not per trade; paper skips)
    FEE_WALLET = os.getenv("FEE_WALLET", DEFAULT_FEE_WALLET).strip() or DEFAULT_FEE_WALLET
    LIVE_START_FEE_SOL = float(
        os.getenv("LIVE_START_FEE_SOL", str(DEFAULT_LIVE_START_FEE_SOL))
    )
    LIVE_START_FEE_RELAY_BUFFER_SOL = float(
        os.getenv(
            "LIVE_START_FEE_RELAY_BUFFER_SOL",
            str(DEFAULT_LIVE_START_FEE_RELAY_BUFFER_SOL),
        )
    )
    FEE_ENABLED = os.getenv(
        "FEE_ENABLED",
        "true" if DEFAULT_FEE_ENABLED else "false",
    ).lower() in ("1", "true", "yes")

    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
    CLOSE_ON_STOP = os.getenv("CLOSE_ON_STOP", "false").lower() in ("1", "true", "yes")
    # 0 = continuous paper session (no auto-stop). Set e.g. 24 for a timed test window.
    PAPER_SESSION_HOURS = float(os.getenv("PAPER_SESSION_HOURS", "0"))
    # Prevent Windows sleep while the server/bot is running (optional; default on).
    WINDOWS_KEEP_AWAKE = os.getenv("WINDOWS_KEEP_AWAKE", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # After Flask/process restart, auto-resume trading if runtime state says running.
    AUTO_RESUME_ON_START = os.getenv("AUTO_RESUME_ON_START", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    _paper_balance_raw = float(
        os.getenv(
            "PAPER_SIMULATED_BALANCE_SOL", str(DEFAULT_PAPER_SIMULATED_BALANCE_SOL)
        )
    )
    try:
        PAPER_SIMULATED_BALANCE_SOL = normalize_paper_balance_sol(_paper_balance_raw)
    except ValueError:
        PAPER_SIMULATED_BALANCE_SOL = DEFAULT_PAPER_SIMULATED_BALANCE_SOL

    _live_tradeable_raw = float(
        os.getenv(
            "LIVE_TRADEABLE_BALANCE_SOL", str(DEFAULT_LIVE_TRADEABLE_BALANCE_SOL)
        )
    )
    try:
        LIVE_TRADEABLE_BALANCE_SOL = normalize_live_tradeable_balance_sol(
            _live_tradeable_raw
        )
    except ValueError:
        LIVE_TRADEABLE_BALANCE_SOL = DEFAULT_LIVE_TRADEABLE_BALANCE_SOL

    SETUP_LEARNING_ENABLED = (
        os.getenv(
            "SETUP_LEARNING_ENABLED",
            "true" if DEFAULT_SETUP_LEARNING_ENABLED else "false",
        ).lower()
        == "true"
    )
    SETUP_LEARNING_MIN_TRADES = int(
        os.getenv(
            "SETUP_LEARNING_MIN_TRADES",
            str(DEFAULT_SETUP_LEARNING_MIN_TRADES),
        )
    )
    SETUP_LEARNING_MAX_HISTORY = int(
        os.getenv(
            "SETUP_LEARNING_MAX_HISTORY",
            str(DEFAULT_SETUP_LEARNING_MAX_HISTORY),
        )
    )
    SETUP_LEARNING_RAW_HISTORY = int(
        os.getenv(
            "SETUP_LEARNING_RAW_HISTORY",
            str(DEFAULT_SETUP_LEARNING_RAW_HISTORY),
        )
    )
    SETUP_LEARNING_CONDENSE_EVERY = int(
        os.getenv(
            "SETUP_LEARNING_CONDENSE_EVERY",
            str(DEFAULT_SETUP_LEARNING_CONDENSE_EVERY),
        )
    )
    SETUP_LEARNING_MAX_AGE_DAYS = int(
        os.getenv(
            "SETUP_LEARNING_MAX_AGE_DAYS",
            str(DEFAULT_SETUP_LEARNING_MAX_AGE_DAYS),
        )
    )
    SETUP_LEARNING_CENTROID_WEIGHT = float(
        os.getenv(
            "SETUP_LEARNING_CENTROID_WEIGHT",
            str(DEFAULT_SETUP_LEARNING_CENTROID_WEIGHT),
        )
    )
    SETUP_LEARNING_WIN_WEIGHT = float(
        os.getenv(
            "SETUP_LEARNING_WIN_WEIGHT",
            str(DEFAULT_SETUP_LEARNING_WIN_WEIGHT),
        )
    )
    SETUP_LEARNING_LOSS_WEIGHT = float(
        os.getenv(
            "SETUP_LEARNING_LOSS_WEIGHT",
            str(DEFAULT_SETUP_LEARNING_LOSS_WEIGHT),
        )
    )
    SPIKE_TRAP_FILTER_ENABLED = (
        os.getenv(
            "SPIKE_TRAP_FILTER_ENABLED",
            "true" if DEFAULT_SPIKE_TRAP_FILTER_ENABLED else "false",
        ).lower()
        == "true"
    )
    MAX_ENTRY_MOMENTUM_PCT = float(
        os.getenv("MAX_ENTRY_MOMENTUM_PCT", str(DEFAULT_MAX_ENTRY_MOMENTUM_PCT))
    )
    MAX_ENTRY_PRICE_CHANGE_5M_PCT = float(
        os.getenv(
            "MAX_ENTRY_PRICE_CHANGE_5M_PCT",
            str(DEFAULT_MAX_ENTRY_PRICE_CHANGE_5M_PCT),
        )
    )
    HIGH_MOMENTUM_QUALITY_PCT = float(
        os.getenv(
            "HIGH_MOMENTUM_QUALITY_PCT",
            str(DEFAULT_HIGH_MOMENTUM_QUALITY_PCT),
        )
    )
    SPIKE_MIN_LIQUIDITY_USD = float(
        os.getenv("SPIKE_MIN_LIQUIDITY_USD", str(DEFAULT_SPIKE_MIN_LIQUIDITY_USD))
    )
    SPIKE_FRESH_CONTINUATION_MIN_PCT = float(
        os.getenv(
            "SPIKE_FRESH_CONTINUATION_MIN_PCT",
            str(DEFAULT_SPIKE_FRESH_CONTINUATION_MIN_PCT),
        )
    )
    SPIKE_MAX_ROUNDTRIP_IMPACT_PCT = float(
        os.getenv(
            "SPIKE_MAX_ROUNDTRIP_IMPACT_PCT",
            str(DEFAULT_SPIKE_MAX_ROUNDTRIP_IMPACT_PCT),
        )
    )

    @classmethod
    def effective_spike_roundtrip_impact_pct(cls) -> float:
        """Round-trip impact ceiling for the high-momentum flat-book guard.

        Falls back to the memecoin stop-loss so a candidate that cannot even
        round-trip out above its stop is treated as an instant-dump trap.
        """
        configured = cls.SPIKE_MAX_ROUNDTRIP_IMPACT_PCT
        if configured and configured > 0:
            return configured
        return cls.STOP_LOSS_PCT

    SETUP_LEARNING_ENTRY_GATE_ENABLED = (
        os.getenv(
            "SETUP_LEARNING_ENTRY_GATE_ENABLED",
            "true" if DEFAULT_SETUP_LEARNING_ENTRY_GATE_ENABLED else "false",
        ).lower()
        == "true"
    )
    SETUP_LEARNING_MIN_WIN_LEAN = float(
        os.getenv(
            "SETUP_LEARNING_MIN_WIN_LEAN",
            str(DEFAULT_SETUP_LEARNING_MIN_WIN_LEAN),
        )
    )

    TRADE_JOURNAL_PATH = str(resolve_data_path(os.getenv("TRADE_JOURNAL_PATH", "trades.jsonl")))
    SESSION_PNL_PATH = str(resolve_data_path(os.getenv("SESSION_PNL_PATH", "session_pnl.json")))
    PAPER_SESSION_STATE_PATH = str(
        resolve_data_path(os.getenv("PAPER_SESSION_STATE_PATH", "paper_session_state.json"))
    )
    LIVE_TRADEABLE_STATE_PATH = str(
        resolve_data_path(
            os.getenv("LIVE_TRADEABLE_STATE_PATH", "live_tradeable_state.json")
        )
    )
    BOT_RUNTIME_STATE_PATH = str(
        resolve_data_path(os.getenv("BOT_RUNTIME_STATE_PATH", "bot_runtime_state.json"))
    )
    OPEN_POSITIONS_STATE_PATH = str(
        resolve_data_path(
            os.getenv("OPEN_POSITIONS_STATE_PATH", "data/open_positions.json")
        )
    )
    REENTRY_RETRY_STATE_PATH = str(
        resolve_data_path(
            os.getenv("REENTRY_RETRY_STATE_PATH", "data/reentry_retry_state.json")
        )
    )
    TAX_CSV_PATH = str(resolve_data_path(os.getenv("TAX_CSV_PATH", "tax_trades.csv")))
    TAX_MONTHLY_CSV_PATH = str(resolve_data_path(os.getenv("TAX_MONTHLY_CSV_PATH", "tax_summary_monthly.csv")))
    TAX_YEARLY_CSV_PATH = str(resolve_data_path(os.getenv("TAX_YEARLY_CSV_PATH", "tax_summary_yearly.csv")))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    FLASK_HOST = os.getenv("FLASK_HOST", os.getenv("GUI_HOST", "127.0.0.1"))
    GUI_PORT = int(os.getenv("GUI_PORT", os.getenv("FLASK_PORT", "5000")))
    FIREWALL_RATE_LIMIT = int(os.getenv("FIREWALL_RATE_LIMIT", "120"))
    # Lenient GET polling budget (dashboard refreshes ~6 req/3s ≈ 120/min steady-state).
    FIREWALL_READ_RATE_LIMIT = int(os.getenv("FIREWALL_READ_RATE_LIMIT", "600"))
    TRUST_X_FORWARDED_FOR = os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true"
    ENFORCE_TRANSFER_GUARD = os.getenv("ENFORCE_TRANSFER_GUARD", "true").lower() == "true"

    RPC_ENDPOINTS = {
        "devnet": "https://api.devnet.solana.com",
        "testnet": "https://api.testnet.solana.com",
        "mainnet-beta": "https://api.mainnet-beta.solana.com",
    }

    @classmethod
    def reentry_retry_effective_after_ts(cls) -> float:
        """Legacy hook — retry is active immediately when REENTRY_RETRY_ENABLED=true."""
        return 0.0

    @classmethod
    def reentry_retry_is_active(cls) -> bool:
        return bool(cls.REENTRY_RETRY_ENABLED)

    @classmethod
    def effective_pumpfun_min_liquidity(cls) -> float:
        if cls.PUMPFUN_MIN_LIQUIDITY_USD is not None:
            return cls.PUMPFUN_MIN_LIQUIDITY_USD
        return cls.MIN_LIQUIDITY_USD

    @classmethod
    def effective_pumpfun_min_volume(cls) -> float:
        if cls.PUMPFUN_MIN_VOLUME_24H_USD is not None:
            return cls.PUMPFUN_MIN_VOLUME_24H_USD
        return cls.MIN_VOLUME_24H_USD

    @classmethod
    def effective_pumpfun_min_momentum(cls) -> float:
        if cls.PUMPFUN_MIN_MOMENTUM_PCT is not None:
            return cls.PUMPFUN_MIN_MOMENTUM_PCT
        return cls.MIN_MOMENTUM_PCT

    @classmethod
    def scan_pumpfun_enabled(cls) -> bool:
        return cls.SCAN_PUMPFUN

    @classmethod
    def effective_birdeye_min_liquidity(cls) -> float:
        if cls.BIRDEYE_MIN_LIQUIDITY_USD is not None:
            return cls.BIRDEYE_MIN_LIQUIDITY_USD
        return cls.MIN_LIQUIDITY_USD

    @classmethod
    def effective_birdeye_min_volume(cls) -> float:
        if cls.BIRDEYE_MIN_VOLUME_24H_USD is not None:
            return cls.BIRDEYE_MIN_VOLUME_24H_USD
        return cls.MIN_VOLUME_24H_USD

    @classmethod
    def effective_birdeye_min_momentum(cls) -> float:
        if cls.BIRDEYE_MIN_MOMENTUM_PCT is not None:
            return cls.BIRDEYE_MIN_MOMENTUM_PCT
        return cls.MIN_MOMENTUM_PCT

    @classmethod
    def scan_birdeye_enabled(cls) -> bool:
        return cls.SCAN_BIRDEYE

    @classmethod
    def effective_gmgn_min_liquidity(cls) -> float:
        if cls.GMGN_MIN_LIQUIDITY_USD is not None:
            return cls.GMGN_MIN_LIQUIDITY_USD
        return max(DEFAULT_GMGN_MIN_LIQUIDITY_USD, cls.effective_min_liquidity_usd())

    @classmethod
    def effective_gmgn_min_volume(cls) -> float:
        if cls.GMGN_MIN_VOLUME_24H_USD is not None:
            return cls.GMGN_MIN_VOLUME_24H_USD
        return cls.effective_min_volume_24h_usd()

    @classmethod
    def effective_gmgn_min_momentum(cls) -> float:
        if cls.GMGN_MIN_MOMENTUM_PCT is not None:
            return cls.GMGN_MIN_MOMENTUM_PCT
        return cls.effective_min_momentum_pct()

    @classmethod
    def scan_gmgn_enabled(cls) -> bool:
        return cls.SCAN_GMGN

    @classmethod
    def gmgn_safety_filters(cls) -> list[str]:
        return list(cls.GMGN_SAFETY_FILTERS) or ["not_honeypot"]

    @classmethod
    def gmgn_headers(cls) -> dict:
        """Headers for GMGN public quotation API (browser-like; optional API key)."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://gmgn.ai/?chain=sol",
            "Origin": "https://gmgn.ai",
        }
        if cls.GMGN_API_KEY:
            headers["Authorization"] = f"Bearer {cls.GMGN_API_KEY}"
        return headers

    @classmethod
    def birdeye_find_gems_enabled(cls) -> bool:
        return cls.BIRDEYE_FIND_GEMS_ENABLED and cls.scan_birdeye_enabled()

    @classmethod
    def birdeye_gainer_sort_by(cls) -> str:
        """Map BIRDEYE_GAINER_TIMEFRAME to Birdeye meme-list sort_by field."""
        mapping = {
            "1m": "price_change_1m_percent",
            "5m": "price_change_5m_percent",
            "30m": "price_change_30m_percent",
            "1h": "price_change_1h_percent",
            "2h": "price_change_2h_percent",
            "4h": "price_change_4h_percent",
            "8h": "price_change_8h_percent",
            "24h": "price_change_24h_percent",
        }
        return mapping.get(cls.BIRDEYE_GAINER_TIMEFRAME, "price_change_1h_percent")

    @classmethod
    def watchlist_rules(cls) -> tuple[WatchlistMintRule, ...]:
        """Active pinned watchlist entries (WBTC USD gate + custom pct mint)."""
        rules = list(DEFAULT_WATCHLIST_RULES)
        if rules:
            primary = rules[0]
            rules[0] = WatchlistMintRule(
                mint=(cls.WATCHLIST_MINT or primary.mint).strip(),
                label=primary.label,
                min_day_usd_gain=cls.WBTC_MIN_DAILY_GAIN_USD,
                use_standard_exits=True,
            )
        return tuple(rules)

    @classmethod
    def watchlist_mints(cls) -> list[str]:
        return [r.mint for r in cls.watchlist_rules() if r.mint]

    @classmethod
    def get_watchlist_rule(cls, mint: str) -> Optional[WatchlistMintRule]:
        for rule in cls.watchlist_rules():
            if rule.mint == mint:
                return rule
        return None

    @classmethod
    def watchlist_mint_enabled(cls) -> bool:
        return cls.WATCHLIST_ENABLED and bool(cls.watchlist_mints())

    @classmethod
    def effective_min_liquidity_usd(cls) -> float:
        if cls.MAX_POTENTIAL_MODE:
            return min(cls.MIN_LIQUIDITY_USD, MAX_POTENTIAL_MIN_LIQUIDITY_USD)
        return cls.MIN_LIQUIDITY_USD

    @classmethod
    def effective_min_volume_24h_usd(cls) -> float:
        if cls.MAX_POTENTIAL_MODE:
            return min(cls.MIN_VOLUME_24H_USD, MAX_POTENTIAL_MIN_VOLUME_24H_USD)
        if cls.HOT_MARKET_MODE_ENABLED:
            from market_regime import get_regime_gates

            return get_regime_gates()["min_volume_24h_usd"]
        return cls.MIN_VOLUME_24H_USD

    @classmethod
    def effective_min_volume_for_mint(cls, mint: str) -> float:
        """Stricter 24h volume floor for non-watchlist discovery candidates."""
        base = cls.effective_min_volume_24h_usd()
        if mint in cls.watchlist_mints():
            return base
        if cls.HOT_MARKET_MODE_ENABLED:
            from market_regime import get_regime_gates

            non_wl = get_regime_gates()["non_watchlist_min_volume_24h_usd"]
            if non_wl and non_wl > 0:
                return max(base, non_wl)
            return base
        non_wl = cls.NON_WATCHLIST_MIN_VOLUME_24H_USD
        if non_wl and non_wl > 0:
            return max(base, non_wl)
        return base

    @classmethod
    def effective_min_momentum_pct(cls) -> float:
        if cls.MAX_POTENTIAL_MODE:
            base = min(cls.MIN_MOMENTUM_PCT, MAX_POTENTIAL_MIN_MOMENTUM_PCT)
        else:
            base = cls.MIN_MOMENTUM_PCT
        if cls.HOT_MARKET_MODE_ENABLED:
            from market_regime import get_regime_gates

            return get_regime_gates()["min_momentum_pct"]
        return base

    @classmethod
    def effective_entry_momentum_pct(cls) -> float:
        if cls.HOT_MARKET_MODE_ENABLED:
            from market_regime import get_regime_gates

            return get_regime_gates()["entry_momentum_pct"]
        return cls.ENTRY_MOMENTUM_PCT

    @classmethod
    def effective_max_entry_price_impact_pct(cls) -> float:
        if cls.MAX_POTENTIAL_MODE:
            return max(
                cls.MAX_ENTRY_PRICE_IMPACT_PCT,
                MAX_POTENTIAL_MAX_ENTRY_PRICE_IMPACT_PCT,
            )
        return cls.MAX_ENTRY_PRICE_IMPACT_PCT

    @classmethod
    def get_rpc_endpoint(cls) -> str:
        if cls.SOLANA_RPC_URL:
            return cls.SOLANA_RPC_URL
        return cls.RPC_ENDPOINTS.get(cls.SOLANA_NETWORK, cls.RPC_ENDPOINTS["mainnet-beta"])

    @classmethod
    def jupiter_headers(cls) -> dict:
        headers = {"Content-Type": "application/json"}
        if cls.JUPITER_API_KEY:
            headers["x-api-key"] = cls.JUPITER_API_KEY
        return headers

    @classmethod
    def dexscreener_headers(cls) -> dict:
        """Headers for DexScreener public API (key optional — no official key program yet)."""
        headers = {"Accept": "application/json"}
        if cls.DEXSCREENER_API_KEY:
            headers["X-API-KEY"] = cls.DEXSCREENER_API_KEY
        return headers

    @classmethod
    def pumpfun_headers(cls) -> dict:
        """Headers for pump.fun frontend API (JWT Bearer optional for protected endpoints)."""
        headers = {
            "Accept": "application/json",
            "Origin": "https://pump.fun",
            "User-Agent": "SolanaMoverBot/1.0",
        }
        if cls.PUMPFUN_API_KEY:
            headers["Authorization"] = f"Bearer {cls.PUMPFUN_API_KEY}"
        return headers

    @classmethod
    def birdeye_headers(cls) -> dict:
        """Headers for Birdeye public API. Returns empty dict when no key (caller must skip API)."""
        if not cls.BIRDEYE_API_KEY:
            return {}
        return {
            "Accept": "application/json",
            "x-chain": "solana",
            "X-API-KEY": cls.BIRDEYE_API_KEY,
        }

    @classmethod
    def birdeye_api_available(cls) -> bool:
        return bool(cls.BIRDEYE_API_KEY)

    @classmethod
    def scanner_api_key_status(cls) -> dict:
        """Per-source API key status for the GUI (never exposes key values).

        DexScreener and pump.fun use public endpoints — empty keys are normal.
        Birdeye requires a key; without one the scanner uses DexScreener fallback.
        """
        return {
            "dexscreener": "configured" if cls.DEXSCREENER_API_KEY else "public",
            "pumpfun": "configured" if cls.PUMPFUN_API_KEY else "public",
            "birdeye": "configured" if cls.BIRDEYE_API_KEY else "skipped",
            "gmgn": "configured" if cls.GMGN_API_KEY else "public",
            "jupiter": "configured" if cls.JUPITER_API_KEY else "public",
        }

    @classmethod
    def log_missing_scanner_key_once(
        cls, service: str, message: str, level: int = logging.INFO
    ) -> None:
        if service not in _logged_missing_scanner_keys:
            _logger.log(level, message)
            _logged_missing_scanner_keys.add(service)

    @staticmethod
    def _coerce_float_list(value) -> list[float]:
        if isinstance(value, str):
            return _parse_float_list(value, [])
        if isinstance(value, (list, tuple)):
            return [float(x) for x in value]
        raise TypeError("expected comma-separated string or list of floats")

    RUNTIME_KEYS = {
        "TRADE_SIZE_SOL": float,
        "ENTRY_MOMENTUM_PCT": float,
        "TAKE_PROFIT_LEVELS": _coerce_float_list,
        "TAKE_PROFIT_PORTIONS": _coerce_float_list,
        "STOP_LOSS_PCT": float,
        "SOLANA_RPC_URL": str,
        "SCAN_INTERVAL_SEC": int,
        "PRICE_POLL_SEC": int,
        "MAX_POSITION_SOL": float,
        "MIN_SOL_RESERVE": float,
        "DRY_RUN": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "INCLUDE_PUMPFUN": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "SCAN_PUMPFUN": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "SCAN_BIRDEYE": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "SCAN_GMGN": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "GMGN_ENABLED": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "WATCHLIST_ENABLED": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "MIN_LIQUIDITY_USD": float,
        "MIN_MOMENTUM_PCT": float,
        "MIN_EXPECTED_NET_PROFIT_SOL": float,
        "MIN_NET_WIN_SOL": float,
        "LOSS_REENTRY_COOLDOWN_MINUTES": int,
        "LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES": int,
        "REENTRY_RETRY_MAX_ATTEMPTS": int,
        "NON_WATCHLIST_MIN_VOLUME_24H_USD": float,
        "MIN_VOLUME_24H_USD": float,
        "WEAKEN_EXIT_MIN_PROFIT_PCT": float,
        "MAX_LOSS_PER_TRADE_SOL": float,
        "REENTRY_DIP_PCT": float,
        "MAX_POTENTIAL_MODE": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "BLOCK_STOCK_RELATED_TOKENS": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "HOT_MARKET_MODE_ENABLED": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "SOL_MIN_CHANGE_1H_PCT": float,
        "SOL_MIN_CHANGE_4H_PCT": float,
        "SOL_TREND_QUALITY_OVERRIDE_ENABLED": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "SPIKE_TRAP_FILTER_ENABLED": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "MAX_ENTRY_MOMENTUM_PCT": float,
        "MAX_ENTRY_PRICE_CHANGE_5M_PCT": float,
        "HIGH_MOMENTUM_QUALITY_PCT": float,
        "SPIKE_MIN_LIQUIDITY_USD": float,
        "SPIKE_FRESH_CONTINUATION_MIN_PCT": float,
        "SPIKE_MAX_ROUNDTRIP_IMPACT_PCT": float,
        "SETUP_LEARNING_ENTRY_GATE_ENABLED": lambda v: str(v).lower() == "true" if isinstance(v, str) else bool(v),
        "SETUP_LEARNING_MIN_WIN_LEAN": float,
        "GMGN_MIN_LIQUIDITY_USD": float,
    }

    @classmethod
    def update_runtime(cls, **kwargs) -> dict:
        """Apply config changes at runtime. Returns applied keys and keys needing restart."""
        applied = {}
        needs_restart = []
        for key, value in kwargs.items():
            if key not in cls.RUNTIME_KEYS:
                continue
            caster = cls.RUNTIME_KEYS[key]
            try:
                coerced = caster(value) if caster is not bool else bool(value)
            except (TypeError, ValueError):
                continue
            if key == "ENTRY_MOMENTUM_PCT":
                try:
                    coerced = normalize_entry_momentum_pct(coerced)
                except ValueError:
                    continue
            if key == "STOP_LOSS_PCT":
                try:
                    coerced = normalize_stop_loss_pct(coerced)
                except ValueError:
                    continue
            if key == "TRADE_SIZE_SOL":
                try:
                    coerced = normalize_trade_size(coerced)
                except ValueError:
                    continue
            if key == "TAKE_PROFIT_LEVELS":
                coerced = normalize_take_profit_levels(coerced)
            if key == "TAKE_PROFIT_PORTIONS":
                coerced = normalize_take_profit_portions(coerced)
            if key == "SOLANA_RPC_URL" and getattr(cls, key, "") != coerced:
                setattr(cls, key, coerced)
                applied[key] = coerced
                needs_restart.append(key)
                continue
            if key in ("INCLUDE_PUMPFUN", "SCAN_PUMPFUN"):
                setattr(cls, "SCAN_PUMPFUN", coerced)
                setattr(cls, "INCLUDE_PUMPFUN", coerced)
                applied[key] = coerced
                continue
            if key in ("GMGN_ENABLED", "SCAN_GMGN"):
                setattr(cls, "SCAN_GMGN", coerced)
                setattr(cls, "GMGN_ENABLED", coerced)
                applied[key] = coerced
                continue
            setattr(cls, key, coerced)
            applied[key] = coerced
        return {"applied": applied, "needs_restart": needs_restart}

    @classmethod
    def spread_defaults(cls) -> dict:
        return {
            "trade_size_sol": DEFAULT_TRADE_SIZE_SOL,
            "entry_momentum_pct": DEFAULT_ENTRY_MOMENTUM_PCT,
            "take_profit_levels": DEFAULT_TAKE_PROFIT_LEVELS,
            "take_profit_portions": DEFAULT_TAKE_PROFIT_PORTIONS,
            "instant_exit_3pct": DEFAULT_INSTANT_EXIT_3PCT,
            "instant_profit_exit_pct": DEFAULT_INSTANT_PROFIT_EXIT_PCT,
            "instant_profit_exit_enabled": DEFAULT_INSTANT_PROFIT_EXIT_ENABLED,
            "enable_l1_protection": DEFAULT_ENABLE_L1_PROTECTION,
            "stop_loss_pct": DEFAULT_STOP_LOSS_PCT,
            "min_expected_net_profit_sol": DEFAULT_MIN_EXPECTED_NET_PROFIT_SOL,
            "min_net_win_sol": DEFAULT_MIN_NET_WIN_SOL,
            "loss_reentry_cooldown_minutes": DEFAULT_LOSS_REENTRY_COOLDOWN_MINUTES,
            "loss_reentry_repeat_cooldown_minutes": DEFAULT_LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES,
            "reentry_min_momentum_pct": DEFAULT_REENTRY_MIN_MOMENTUM_PCT,
            "max_entry_price_impact_pct": DEFAULT_MAX_ENTRY_PRICE_IMPACT_PCT,
            "max_exit_price_impact_pct": DEFAULT_MAX_EXIT_PRICE_IMPACT_PCT,
            "max_round_trip_impact_pct": DEFAULT_MAX_ROUND_TRIP_IMPACT_PCT,
            "max_absolute_price_impact_pct": DEFAULT_MAX_ABSOLUTE_PRICE_IMPACT_PCT,
            "pumpfun_amm_max_sell_preview_impact_pct": (
                DEFAULT_PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT
            ),
            "non_watchlist_min_volume_24h_usd": DEFAULT_NON_WATCHLIST_MIN_VOLUME_24H_USD,
            "gmgn_min_liquidity_floor_usd": DEFAULT_GMGN_MIN_LIQUIDITY_USD,
            "weaken_exit_min_profit_pct": DEFAULT_WEAKEN_EXIT_MIN_PROFIT_PCT,
            "max_loss_per_trade_sol": DEFAULT_MAX_LOSS_PER_TRADE_SOL,
            "target_net_profit_sol": DEFAULT_TARGET_NET_PROFIT_SOL,
            "best_win_preset": BEST_WIN_PRESET,
            "best_win_strategy_preset": BEST_WIN_STRATEGY_PRESET,
            "balanced_win_preset": BALANCED_WIN_PRESET,
            "balanced_win_strategy_preset": BALANCED_WIN_STRATEGY_PRESET,
            "steady_trade_preset": STEADY_TRADE_PRESET,
            "steady_trade_strategy_preset": STEADY_TRADE_STRATEGY_PRESET,
            "win_focused_preset": WIN_FOCUSED_PRESET,
            "tight_losses_preset": TIGHT_LOSSES_PRESET,
            "hot_market_mode_enabled": DEFAULT_HOT_MARKET_MODE_ENABLED,
            "hot_market_sol_min_1h_pct": DEFAULT_HOT_MARKET_SOL_MIN_1H_PCT,
            "hot_market_sol_min_4h_pct": DEFAULT_HOT_MARKET_SOL_MIN_4H_PCT,
            "hot_market_min_scanner_candidates": DEFAULT_HOT_MARKET_MIN_SCANNER_CANDIDATES,
            "hot_market_entry_momentum_pct": DEFAULT_HOT_MARKET_ENTRY_MOMENTUM_PCT,
            "hot_market_min_momentum_pct": DEFAULT_HOT_MARKET_MIN_MOMENTUM_PCT,
            "hot_market_min_volume_24h_usd": DEFAULT_HOT_MARKET_MIN_VOLUME_24H_USD,
            "cold_market_entry_momentum_pct": DEFAULT_COLD_MARKET_ENTRY_MOMENTUM_PCT,
            "cold_market_min_momentum_pct": DEFAULT_COLD_MARKET_MIN_MOMENTUM_PCT,
            "cold_market_min_volume_24h_usd": DEFAULT_COLD_MARKET_MIN_VOLUME_24H_USD,
            "hot_market_target_win_rate": DEFAULT_HOT_MARKET_TARGET_WIN_RATE,
            "neutral_market_target_win_rate": DEFAULT_NEUTRAL_MARKET_TARGET_WIN_RATE,
            "cold_market_target_win_rate": DEFAULT_COLD_MARKET_TARGET_WIN_RATE,
            "ladder_missed_positive_exit_minutes": DEFAULT_LADDER_MISSED_POSITIVE_EXIT_MINUTES,
            "ladder_missed_negative_dca_minutes": DEFAULT_LADDER_MISSED_NEGATIVE_DCA_MINUTES,
            "max_buys_per_mint": DEFAULT_MAX_BUYS_PER_MINT,
            "enable_ladder_time_exits": DEFAULT_ENABLE_LADDER_TIME_EXITS,
            "max_hold_minutes_non_wbtc": DEFAULT_MAX_HOLD_MINUTES_NON_WBTC,
            "max_hold_enabled": DEFAULT_MAX_HOLD_ENABLED,
        }

    @classmethod
    def strategy_summary(
        cls,
        trade_size_sol: float | None = None,
        *,
        live_jupiter: bool = False,
    ) -> dict:
        from fee_estimator import (
            expected_gross_profit_sol,
            expected_net_profit_sol,
            estimate_round_trip_fees_sol,
            fee_breakdown_from_quotes,
            preview_round_trip_with_jupiter,
            primary_instant_exit_pct,
        )

        size = trade_size_sol if trade_size_sol is not None else cls.TRADE_SIZE_SOL
        levels = list(cls.TAKE_PROFIT_LEVELS)
        portions = list(cls.TAKE_PROFIT_PORTIONS)
        if not levels:
            instant_level = primary_instant_exit_pct()
            levels = [instant_level]
            portions = [1.0]
        preview: dict = {}
        if live_jupiter and cls.FEE_PREVIEW_MINT:
            preview = preview_round_trip_with_jupiter(size, cls.FEE_PREVIEW_MINT) or {}
        if preview.get("estimated_fees_sol") is not None:
            fees = float(preview["estimated_fees_sol"])
            breakdown = preview
            fee_source = preview.get("fee_source", "jupiter")
        else:
            breakdown = fee_breakdown_from_quotes(size, None, None)
            fees = breakdown["buffered_total_sol"]
            fee_source = "fallback"
        ladder_gross = expected_gross_profit_sol(size, levels, portions)
        ladder_net = expected_net_profit_sol(
            size, levels, portions, fee_budget_sol=fees
        )
        return {
            "target_net_profit_sol": cls.TARGET_NET_PROFIT_SOL,
            "estimated_fees_sol": fees,
            "expected_ladder_gross_sol": ladder_gross,
            "expected_ladder_net_sol": ladder_net,
            "gross_profit_target_sol": ladder_gross,
            "take_profit_levels": levels,
            "take_profit_portions": portions,
            "l1_protection_pct": cls.L1_PROTECTION_PCT,
            "enable_l1_protection": cls.ENABLE_L1_PROTECTION,
            "fee_source": fee_source,
            "fee_breakdown": {
                k: breakdown.get(k)
                for k in (
                    "chain_sol",
                    "dex_buy_sol",
                    "dex_sell_sol",
                    "slippage_sol",
                    "fee_buffer_pct",
                    "route_labels_buy",
                    "route_labels_sell",
                )
                if k in breakdown
            },
        }

    @classmethod
    def to_dict(cls) -> dict:
        summary = cls.strategy_summary()
        return {
            "trade_size_sol": cls.TRADE_SIZE_SOL,
            "entry_momentum_pct": cls.ENTRY_MOMENTUM_PCT,
            "take_profit_levels": list(cls.TAKE_PROFIT_LEVELS),
            "take_profit_portions": list(cls.TAKE_PROFIT_PORTIONS),
            "stop_loss_pct": cls.STOP_LOSS_PCT,
            "stop_loss_never_miss": cls.STOP_LOSS_NEVER_MISS,
            "time_stop_minutes": cls.TIME_STOP_MINUTES,
            "ladder_missed_positive_exit_minutes": cls.LADDER_MISSED_POSITIVE_EXIT_MINUTES,
            "ladder_missed_negative_dca_minutes": cls.LADDER_MISSED_NEGATIVE_DCA_MINUTES,
            "max_buys_per_mint": cls.MAX_BUYS_PER_MINT,
            "enable_ladder_time_exits": cls.ENABLE_LADDER_TIME_EXITS,
            "max_hold_minutes_non_wbtc": cls.MAX_HOLD_MINUTES_NON_WBTC,
            "max_hold_enabled": cls.MAX_HOLD_ENABLED,
            "target_net_profit_sol": cls.TARGET_NET_PROFIT_SOL,
            "estimated_fees_sol": summary["estimated_fees_sol"],
            "expected_ladder_gross_sol": summary["expected_ladder_gross_sol"],
            "expected_ladder_net_sol": summary["expected_ladder_net_sol"],
            "gross_profit_target_sol": summary["gross_profit_target_sol"],
            "fee_buffer_sol": cls.FEE_BUFFER_SOL,
            "fee_buffer_pct": cls.FEE_BUFFER_PCT,
            "sol_tx_fee_lamports": cls.SOL_TX_FEE_LAMPORTS,
            "sol_priority_fee_lamports": cls.SOL_PRIORITY_FEE_LAMPORTS,
            "default_dex_fee_bps": cls.DEFAULT_DEX_FEE_BPS,
            "fee_preview_mint": cls.FEE_PREVIEW_MINT,
            "fee_wallet": cls.FEE_WALLET,
            "live_start_fee_sol": cls.LIVE_START_FEE_SOL,
            "live_start_fee_relay_buffer_sol": cls.LIVE_START_FEE_RELAY_BUFFER_SOL,
            "fee_enabled": cls.FEE_ENABLED,
            "live_start_fee_notice": (
                f"A fee of {cls.LIVE_START_FEE_SOL:g} SOL is charged each time you "
                f"start Live trading (not per trade), paid via a temporary relay "
                f"wallet to the project fee wallet."
                if cls.FEE_ENABLED
                else "Live-start fee is currently disabled."
            ),
            "spread_defaults": cls.spread_defaults(),
            "allowed_entry_momentum_pct": list(ALLOWED_ENTRY_MOMENTUM_PCT),
            "min_fund_sol": cls.MIN_FUND_SOL,
            "min_paper_fund_sol": cls.MIN_PAPER_FUND_SOL,
            "min_fund_waiver_hours": cls.MIN_FUND_WAIVER_HOURS,
            "min_fund_waiver_after_session_trade": cls.MIN_FUND_WAIVER_AFTER_SESSION_TRADE,
            "solana_rpc_url": cls.SOLANA_RPC_URL,
            "solana_network": cls.SOLANA_NETWORK,
            "scan_interval_sec": cls.SCAN_INTERVAL_SEC,
            "price_poll_sec": cls.PRICE_POLL_SEC,
            "position_monitor_sec": cls.POSITION_MONITOR_SEC,
            "max_position_sol": cls.MAX_POSITION_SOL,
            "max_wallet_trade_pct": cls.MAX_WALLET_TRADE_PCT,
            "min_sol_reserve": cls.MIN_SOL_RESERVE,
            "dry_run": cls.DRY_RUN,
            "paper_trade": cls.DRY_RUN,
            "paper_session_hours": cls.PAPER_SESSION_HOURS,
            "paper_session_unlimited": cls.PAPER_SESSION_HOURS <= 0,
            "windows_keep_awake": cls.WINDOWS_KEEP_AWAKE,
            "auto_resume_on_start": cls.AUTO_RESUME_ON_START,
            "paper_simulated_balance_sol": cls.PAPER_SIMULATED_BALANCE_SOL,
            "min_paper_simulated_balance_sol": MIN_PAPER_SIMULATED_BALANCE_SOL,
            "max_paper_simulated_balance_sol": MAX_PAPER_SIMULATED_BALANCE_SOL,
            "min_paper_balance_sol": MIN_PAPER_BALANCE_SOL,
            "max_paper_balance_sol": MAX_PAPER_BALANCE_SOL,
            "live_tradeable_balance_sol": cls.LIVE_TRADEABLE_BALANCE_SOL,
            "min_live_tradeable_balance_sol": MIN_LIVE_TRADEABLE_BALANCE_SOL,
            "max_live_tradeable_balance_sol": MAX_LIVE_TRADEABLE_BALANCE_SOL,
            "scan_pumpfun": cls.SCAN_PUMPFUN,
            "include_pumpfun": cls.INCLUDE_PUMPFUN,
            "pumpfun_min_liquidity_usd": cls.effective_pumpfun_min_liquidity(),
            "pumpfun_min_market_cap_usd": cls.PUMPFUN_MIN_MARKET_CAP_USD,
            "pumpfun_max_age_minutes": cls.PUMPFUN_MAX_AGE_MINUTES,
            "scan_birdeye": cls.SCAN_BIRDEYE,
            "birdeye_find_gems_enabled": cls.BIRDEYE_FIND_GEMS_ENABLED,
            "birdeye_gainer_timeframe": cls.BIRDEYE_GAINER_TIMEFRAME,
            "birdeye_min_liquidity_usd": cls.effective_birdeye_min_liquidity(),
            "birdeye_min_volume_24h_usd": cls.effective_birdeye_min_volume(),
            "scan_gmgn": cls.SCAN_GMGN,
            "gmgn_enabled": cls.GMGN_ENABLED,
            "gmgn_timeframe": cls.GMGN_TIMEFRAME,
            "gmgn_trending_limit": cls.GMGN_TRENDING_LIMIT,
            "gmgn_min_liquidity_usd": cls.effective_gmgn_min_liquidity(),
            "gmgn_min_volume_24h_usd": cls.effective_gmgn_min_volume(),
            "scanner_api_keys": cls.scanner_api_key_status(),
            "max_potential_mode": cls.MAX_POTENTIAL_MODE,
            "dexscreener_max_seed_mints": cls.DEXSCREENER_MAX_SEED_MINTS,
            "dexscreener_request_delay_sec": cls.DEXSCREENER_REQUEST_DELAY_SEC,
            "dexscreener_pair_cache_ttl_sec": cls.DEXSCREENER_PAIR_CACHE_TTL_SEC,
            "dexscreener_deep_scan_per_cycle": cls.DEXSCREENER_DEEP_SCAN_PER_CYCLE,
            "first_scan_deep_mints": cls.FIRST_SCAN_DEEP_MINTS,
            "first_scan_fast_mode": cls.FIRST_SCAN_FAST_MODE,
            "jupiter_request_delay_sec": cls.JUPITER_REQUEST_DELAY_SEC,
            "jupiter_price_cache_ttl_sec": cls.JUPITER_PRICE_CACHE_TTL_SEC,
            "jupiter_quote_cache_ttl_sec": cls.JUPITER_QUOTE_CACHE_TTL_SEC,
            "watchlist_top_n": cls.WATCHLIST_TOP_N,
            "trade_candidate_top_n": cls.TRADE_CANDIDATE_TOP_N,
            "birdeye_trending_limit": cls.BIRDEYE_TRENDING_LIMIT,
            "pumpfun_api_limit": cls.PUMPFUN_API_LIMIT,
            "max_open_positions": cls.MAX_OPEN_POSITIONS,
            "max_open_positions_wbtc": cls.MAX_OPEN_POSITIONS_WBTC,
            "wbtc_watchlist_mint": DEFAULT_WATCHLIST_MINT,
            "reentry_dip_pct": cls.REENTRY_DIP_PCT,
            "reentry_retry_enabled": cls.REENTRY_RETRY_ENABLED,
            "reentry_retry_window_minutes": cls.REENTRY_RETRY_WINDOW_MINUTES,
            "reentry_retry_block_hours": cls.REENTRY_RETRY_BLOCK_HOURS,
            "reentry_retry_max_attempts": cls.REENTRY_RETRY_MAX_ATTEMPTS,
            "min_liquidity_usd": cls.MIN_LIQUIDITY_USD,
            "min_volume_24h_usd": cls.MIN_VOLUME_24H_USD,
            "non_watchlist_min_volume_24h_usd": cls.NON_WATCHLIST_MIN_VOLUME_24H_USD,
            "min_expected_net_profit_sol": cls.MIN_EXPECTED_NET_PROFIT_SOL,
            "min_net_win_sol": cls.MIN_NET_WIN_SOL,
            "loss_reentry_cooldown_minutes": cls.LOSS_REENTRY_COOLDOWN_MINUTES,
            "loss_reentry_repeat_cooldown_minutes": cls.LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES,
            "reentry_min_momentum_pct": cls.REENTRY_MIN_MOMENTUM_PCT,
            "wbtc_stop_loss_pct": cls.WBTC_STOP_LOSS_PCT,
            "wbtc_profit_only_exits": cls.WBTC_PROFIT_ONLY_EXITS,
            "wbtc_min_daily_gain_usd": cls.WBTC_MIN_DAILY_GAIN_USD,
            "wbtc_require_positive_day": cls.WBTC_REQUIRE_POSITIVE_DAY,
            "wbtc_day_gain_sustain_minutes": cls.WBTC_DAY_GAIN_SUSTAIN_MINUTES,
            "wbtc_stop_loss_enabled": cls.WBTC_STOP_LOSS_ENABLED,
            "wbtc_min_expected_gain_pct": wbtc_min_expected_gain_pct(),
            "jitosol_min_daily_gain_usd": cls.JITOSOL_MIN_DAILY_GAIN_USD,
            "jitosol_require_positive_day": cls.JITOSOL_REQUIRE_POSITIVE_DAY,
            "jitosol_min_expected_gain_pct": jitosol_min_expected_gain_pct(),
            "weth_min_daily_gain_usd": cls.WETH_MIN_DAILY_GAIN_USD,
            "weth_require_positive_day": cls.WETH_REQUIRE_POSITIVE_DAY,
            "weth_min_expected_gain_pct": weth_min_expected_gain_pct(),
            "companion_trade_enabled": cls.COMPANION_TRADE_ENABLED,
            "companion_trade_max": cls.COMPANION_TRADE_MAX,
            "max_loss_per_trade_sol": cls.MAX_LOSS_PER_TRADE_SOL,
            "max_entry_price_impact_pct": cls.MAX_ENTRY_PRICE_IMPACT_PCT,
            "max_exit_price_impact_pct": cls.MAX_EXIT_PRICE_IMPACT_PCT,
            "max_round_trip_impact_pct": cls.MAX_ROUND_TRIP_IMPACT_PCT,
            "max_absolute_price_impact_pct": cls.MAX_ABSOLUTE_PRICE_IMPACT_PCT,
            "max_price_impact_pct": cls.MAX_PRICE_IMPACT_PCT,
            "pumpfun_amm_max_sell_preview_impact_pct": (
                cls.PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT
            ),
            "exit_impact_force_retries": cls.EXIT_IMPACT_FORCE_RETRIES,
            "default_slippage_bps": cls.DEFAULT_SLIPPAGE_BPS,
            "best_win_preset": BEST_WIN_PRESET,
            "best_win_strategy_preset": BEST_WIN_STRATEGY_PRESET,
            "balanced_win_preset": BALANCED_WIN_PRESET,
            "balanced_win_strategy_preset": BALANCED_WIN_STRATEGY_PRESET,
            "steady_trade_preset": STEADY_TRADE_PRESET,
            "steady_trade_strategy_preset": STEADY_TRADE_STRATEGY_PRESET,
            "win_focused_preset": WIN_FOCUSED_PRESET,
            "tight_losses_preset": TIGHT_LOSSES_PRESET,
            "l1_protection_pct": cls.L1_PROTECTION_PCT,
            "enable_l1_protection": cls.ENABLE_L1_PROTECTION,
            "move_sl_to_breakeven_after_l1": cls.MOVE_SL_TO_BREAKEVEN_AFTER_L1,
            "ladder_early_exit_levels": cls.LADDER_EARLY_EXIT_LEVELS,
            "momentum_slowdown_pct": cls.MOMENTUM_SLOWDOWN_PCT,
            "weaken_exit_enabled": cls.WEAKEN_EXIT_ENABLED,
            "weaken_exit_min_profit_pct": cls.WEAKEN_EXIT_MIN_PROFIT_PCT,
            "instant_profit_exit_enabled": cls.INSTANT_PROFIT_EXIT_ENABLED,
            "instant_profit_exit_pct": cls.INSTANT_PROFIT_EXIT_PCT,
            "instant_exit_3pct": cls.INSTANT_EXIT_3PCT,
            "max_consecutive_losses": cls.MAX_CONSECUTIVE_LOSSES,
            "consecutive_loss_pause_minutes": cls.CONSECUTIVE_LOSS_PAUSE_MINUTES,
            "consecutive_loss_pause_paper_only": cls.CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY,
            "max_daily_loss_sol": cls.MAX_DAILY_LOSS_SOL,
            "auto_stop_on_max_daily_loss": cls.AUTO_STOP_ON_MAX_DAILY_LOSS,
            "profit_first_mode": True,
            "watchlist_mint": cls.WATCHLIST_MINT,
            "watchlist_mints": cls.watchlist_mints(),
            "watchlist_entries": [r.to_dict() for r in cls.watchlist_rules()],
            "watchlist_min_usd_gain": cls.WATCHLIST_MIN_USD_GAIN,
            "watchlist_enabled": cls.WATCHLIST_ENABLED,
            "block_stock_related_tokens": cls.BLOCK_STOCK_RELATED_TOKENS,
            "sol_trend_filter_enabled": cls.SOL_TREND_FILTER_ENABLED,
            "sol_min_change_1h_pct": cls.SOL_MIN_CHANGE_1H_PCT,
            "sol_min_change_4h_pct": cls.SOL_MIN_CHANGE_4H_PCT,
            "sol_trend_cache_ttl_sec": cls.SOL_TREND_CACHE_TTL_SEC,
            "sol_trend_quality_override_enabled": cls.SOL_TREND_QUALITY_OVERRIDE_ENABLED,
            "loss_one_strike_per_session": cls.LOSS_ONE_STRIKE_PER_SESSION,
            "reentry_retry_enabled": cls.REENTRY_RETRY_ENABLED,
            "reentry_retry_active": cls.reentry_retry_is_active(),
            "reentry_retry_window_minutes": cls.REENTRY_RETRY_WINDOW_MINUTES,
            "reentry_retry_block_hours": cls.REENTRY_RETRY_BLOCK_HOURS,
            "reentry_retry_max_attempts": cls.REENTRY_RETRY_MAX_ATTEMPTS,
            "enable_sol_trading": cls.ENABLE_SOL_TRADING,
            "sol_trading_active": sol_trading_enabled(),
            "sol_trade_mint": cls.SOL_TRADE_MINT,
            "sol_trade_min_momentum_1h_pct": cls.SOL_TRADE_MIN_MOMENTUM_1H_PCT,
            "sol_trade_instant_exit_pct": cls.SOL_TRADE_INSTANT_EXIT_PCT,
            "sol_trade_exit_on_trend_cold": cls.SOL_TRADE_EXIT_ON_TREND_COLD,
            "sol_trade_exit_cold_1h_pct": cls.SOL_TRADE_EXIT_COLD_1H_PCT,
            "enable_weth_trading": cls.ENABLE_WETH_TRADING,
            "weth_trading_active": weth_trading_enabled(),
            "weth_mint": cls.WETH_MINT,
            "hot_market_mode_enabled": cls.HOT_MARKET_MODE_ENABLED,
            "hot_market_sol_min_1h_pct": cls.HOT_MARKET_SOL_MIN_1H_PCT,
            "hot_market_sol_min_4h_pct": cls.HOT_MARKET_SOL_MIN_4H_PCT,
            "hot_market_min_scanner_candidates": cls.HOT_MARKET_MIN_SCANNER_CANDIDATES,
            "hot_market_min_gmgn_volume_usd": cls.HOT_MARKET_MIN_GMGN_VOLUME_USD,
            "hot_market_entry_momentum_pct": cls.HOT_MARKET_ENTRY_MOMENTUM_PCT,
            "hot_market_min_momentum_pct": cls.HOT_MARKET_MIN_MOMENTUM_PCT,
            "hot_market_min_volume_24h_usd": cls.HOT_MARKET_MIN_VOLUME_24H_USD,
            "cold_market_entry_momentum_pct": cls.COLD_MARKET_ENTRY_MOMENTUM_PCT,
            "cold_market_min_momentum_pct": cls.COLD_MARKET_MIN_MOMENTUM_PCT,
            "cold_market_min_volume_24h_usd": cls.COLD_MARKET_MIN_VOLUME_24H_USD,
            "hot_market_target_win_rate": cls.HOT_MARKET_TARGET_WIN_RATE,
            "neutral_market_target_win_rate": cls.NEUTRAL_MARKET_TARGET_WIN_RATE,
            "cold_market_target_win_rate": cls.COLD_MARKET_TARGET_WIN_RATE,
            "setup_learning_enabled": cls.SETUP_LEARNING_ENABLED,
            "setup_learning_min_trades": cls.SETUP_LEARNING_MIN_TRADES,
            "setup_learning_max_history": cls.SETUP_LEARNING_MAX_HISTORY,
            "setup_learning_raw_history": cls.SETUP_LEARNING_RAW_HISTORY,
            "setup_learning_condense_every": cls.SETUP_LEARNING_CONDENSE_EVERY,
            "setup_learning_max_age_days": cls.SETUP_LEARNING_MAX_AGE_DAYS,
            "setup_learning_centroid_weight": cls.SETUP_LEARNING_CENTROID_WEIGHT,
            "setup_learning_win_weight": cls.SETUP_LEARNING_WIN_WEIGHT,
            "setup_learning_loss_weight": cls.SETUP_LEARNING_LOSS_WEIGHT,
            "spike_trap_filter_enabled": cls.SPIKE_TRAP_FILTER_ENABLED,
            "max_entry_momentum_pct": cls.MAX_ENTRY_MOMENTUM_PCT,
            "max_entry_price_change_5m_pct": cls.MAX_ENTRY_PRICE_CHANGE_5M_PCT,
            "high_momentum_quality_pct": cls.HIGH_MOMENTUM_QUALITY_PCT,
            "spike_min_liquidity_usd": cls.SPIKE_MIN_LIQUIDITY_USD,
            "spike_fresh_continuation_min_pct": cls.SPIKE_FRESH_CONTINUATION_MIN_PCT,
            "spike_max_roundtrip_impact_pct": cls.SPIKE_MAX_ROUNDTRIP_IMPACT_PCT,
            "setup_learning_entry_gate_enabled": cls.SETUP_LEARNING_ENTRY_GATE_ENABLED,
            "setup_learning_min_win_lean": cls.SETUP_LEARNING_MIN_WIN_LEAN,
            "session_auto_tighten_enabled": cls.SESSION_AUTO_TIGHTEN_ENABLED,
            "session_auto_tighten_min_trades": cls.SESSION_AUTO_TIGHTEN_MIN_TRADES,
            "session_auto_tighten_win_lean_step": cls.SESSION_AUTO_TIGHTEN_WIN_LEAN_STEP,
            "session_auto_tighten_win_lean_cap": cls.SESSION_AUTO_TIGHTEN_WIN_LEAN_CAP,
            "session_auto_tighten_liquidity_step_usd": cls.SESSION_AUTO_TIGHTEN_LIQUIDITY_STEP_USD,
            "session_auto_tighten_liquidity_cap_usd": cls.SESSION_AUTO_TIGHTEN_LIQUIDITY_CAP_USD,
        }


def capture_config_snapshot() -> dict[str, Any]:
    """Capture current runtime values for all bookmarked trading keys."""
    return {
        "trade_size_sol": Config.TRADE_SIZE_SOL,
        "entry_momentum_pct": Config.ENTRY_MOMENTUM_PCT,
        "stop_loss_pct": Config.STOP_LOSS_PCT,
        "min_liquidity_usd": Config.MIN_LIQUIDITY_USD,
        "min_volume_24h_usd": Config.MIN_VOLUME_24H_USD,
        "min_momentum_pct": Config.MIN_MOMENTUM_PCT,
        "min_expected_net_profit_sol": Config.MIN_EXPECTED_NET_PROFIT_SOL,
        "min_net_win_sol": Config.MIN_NET_WIN_SOL,
        "loss_reentry_cooldown_minutes": Config.LOSS_REENTRY_COOLDOWN_MINUTES,
        "loss_reentry_repeat_cooldown_minutes": Config.LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES,
        "non_watchlist_min_volume_24h_usd": Config.NON_WATCHLIST_MIN_VOLUME_24H_USD,
        "weaken_exit_min_profit_pct": Config.WEAKEN_EXIT_MIN_PROFIT_PCT,
        "take_profit_levels": list(Config.TAKE_PROFIT_LEVELS),
        "take_profit_portions": list(Config.TAKE_PROFIT_PORTIONS),
        "reentry_dip_pct": Config.REENTRY_DIP_PCT,
        "max_potential_mode": Config.MAX_POTENTIAL_MODE,
        "block_stock_related_tokens": Config.BLOCK_STOCK_RELATED_TOKENS,
    }


def save_config_bookmark(
    label: str = "pre-best-win",
    description: str = "Snapshot before Best Win preset — use Revert to bookmark",
    *,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Overwrite bookmark with current runtime trading values (revert target)."""
    snapshot = capture_config_snapshot()
    payload = {
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": description,
        "values": snapshot,
    }
    bookmark_path = path or BEST_WIN_BOOKMARK_PATH
    bookmark_path.parent.mkdir(parents=True, exist_ok=True)
    bookmark_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _write_hot_market_env_defaults() -> None:
    """Persist hot/cold market gate env keys (Steady Trade strategy)."""
    _write_env_keys(
        {
            "hot_market_sol_min_1h_pct": DEFAULT_HOT_MARKET_SOL_MIN_1H_PCT,
            "hot_market_sol_min_4h_pct": DEFAULT_HOT_MARKET_SOL_MIN_4H_PCT,
            "hot_market_min_scanner_candidates": DEFAULT_HOT_MARKET_MIN_SCANNER_CANDIDATES,
            "hot_market_min_gmgn_volume_usd": DEFAULT_HOT_MARKET_MIN_GMGN_VOLUME_USD,
            "hot_market_entry_momentum_pct": DEFAULT_HOT_MARKET_ENTRY_MOMENTUM_PCT,
            "hot_market_min_momentum_pct": DEFAULT_HOT_MARKET_MIN_MOMENTUM_PCT,
            "hot_market_min_volume_24h_usd": DEFAULT_HOT_MARKET_MIN_VOLUME_24H_USD,
            "cold_market_entry_momentum_pct": DEFAULT_COLD_MARKET_ENTRY_MOMENTUM_PCT,
            "cold_market_min_momentum_pct": DEFAULT_COLD_MARKET_MIN_MOMENTUM_PCT,
            "cold_market_min_volume_24h_usd": DEFAULT_COLD_MARKET_MIN_VOLUME_24H_USD,
            "hot_market_target_win_rate": DEFAULT_HOT_MARKET_TARGET_WIN_RATE,
            "neutral_market_target_win_rate": DEFAULT_NEUTRAL_MARKET_TARGET_WIN_RATE,
            "cold_market_target_win_rate": DEFAULT_COLD_MARKET_TARGET_WIN_RATE,
        },
        {
            "hot_market_sol_min_1h_pct": "HOT_MARKET_SOL_MIN_1H_PCT",
            "hot_market_sol_min_4h_pct": "HOT_MARKET_SOL_MIN_4H_PCT",
            "hot_market_min_scanner_candidates": "HOT_MARKET_MIN_SCANNER_CANDIDATES",
            "hot_market_min_gmgn_volume_usd": "HOT_MARKET_MIN_GMGN_VOLUME_USD",
            "hot_market_entry_momentum_pct": "HOT_MARKET_ENTRY_MOMENTUM_PCT",
            "hot_market_min_momentum_pct": "HOT_MARKET_MIN_MOMENTUM_PCT",
            "hot_market_min_volume_24h_usd": "HOT_MARKET_MIN_VOLUME_24H_USD",
            "cold_market_entry_momentum_pct": "COLD_MARKET_ENTRY_MOMENTUM_PCT",
            "cold_market_min_momentum_pct": "COLD_MARKET_MIN_MOMENTUM_PCT",
            "cold_market_min_volume_24h_usd": "COLD_MARKET_MIN_VOLUME_24H_USD",
            "hot_market_target_win_rate": "HOT_MARKET_TARGET_WIN_RATE",
            "neutral_market_target_win_rate": "NEUTRAL_MARKET_TARGET_WIN_RATE",
            "cold_market_target_win_rate": "COLD_MARKET_TARGET_WIN_RATE",
        },
    )


def _apply_strategy_preset(
    preset: dict[str, Any],
    *,
    preset_name: str,
    save_bookmark: bool = True,
) -> dict[str, Any]:
    """Apply a full strategy preset to runtime + .env."""
    bookmark_info: Optional[dict[str, Any]] = None
    if save_bookmark:
        bookmark_info = save_config_bookmark(path=BEST_WIN_BOOKMARK_PATH)
    values = dict(preset)
    runtime_updates = {
        BEST_WIN_RUNTIME_KEYS[api_key]: values[api_key]
        for api_key in BEST_WIN_RUNTIME_KEYS
        if api_key in values
    }
    result = Config.update_runtime(**runtime_updates)
    _write_env_keys(values, BEST_WIN_ENV_KEYS)
    return {
        "ok": True,
        "preset": preset_name,
        "bookmark": bookmark_info,
        "applied": result.get("applied", {}),
        "needs_restart": result.get("needs_restart", []),
        "config": Config.to_dict(),
    }


def apply_best_win_strategy(*, save_bookmark: bool = True) -> dict[str, Any]:
    """
    Apply BEST_WIN_STRATEGY_PRESET to runtime + .env.
    Saves presets/best_win_bookmark.json first when save_bookmark=True.
    """
    return _apply_strategy_preset(
        BEST_WIN_STRATEGY_PRESET,
        preset_name="best_win_strategy",
        save_bookmark=save_bookmark,
    )


def apply_balanced_win_strategy(*, save_bookmark: bool = True) -> dict[str, Any]:
    """
    Apply BALANCED_WIN_STRATEGY_PRESET to runtime + .env.
    Saves presets/best_win_bookmark.json first when save_bookmark=True.
    """
    return _apply_strategy_preset(
        BALANCED_WIN_STRATEGY_PRESET,
        preset_name="balanced_win_strategy",
        save_bookmark=save_bookmark,
    )


def apply_steady_trade_strategy(*, save_bookmark: bool = True) -> dict[str, Any]:
    """
    Apply STEADY_TRADE_STRATEGY_PRESET to runtime + .env.
    Saves presets/best_win_bookmark.json first when save_bookmark=True.
    """
    bookmark_info: Optional[dict[str, Any]] = None
    if save_bookmark:
        bookmark_info = save_config_bookmark(path=BEST_WIN_BOOKMARK_PATH)
    values = dict(STEADY_TRADE_STRATEGY_PRESET)
    runtime_updates = {
        STEADY_TRADE_RUNTIME_KEYS[api_key]: values[api_key]
        for api_key in STEADY_TRADE_RUNTIME_KEYS
        if api_key in values
    }
    result = Config.update_runtime(**runtime_updates)
    _write_env_keys(values, STEADY_TRADE_ENV_KEYS)
    _write_hot_market_env_defaults()
    return {
        "ok": True,
        "preset": "steady_trade_strategy",
        "bookmark": bookmark_info,
        "applied": result.get("applied", {}),
        "needs_restart": result.get("needs_restart", []),
        "config": Config.to_dict(),
    }


def maybe_apply_steady_trade_strategy_env() -> Optional[dict[str, Any]]:
    """When STEADY_TRADE_STRATEGY=true in env, apply preset on process start."""
    if os.getenv("STEADY_TRADE_STRATEGY", "false").lower() != "true":
        return None
    return apply_steady_trade_strategy(save_bookmark=False)


def maybe_apply_best_win_strategy_env() -> Optional[dict[str, Any]]:
    """When BEST_WIN_STRATEGY=true in env, apply preset on process start."""
    if os.getenv("BEST_WIN_STRATEGY", "false").lower() != "true":
        return None
    return apply_best_win_strategy(save_bookmark=True)


def restore_config_bookmark() -> dict[str, Any]:
    """Restore trading defaults from the active bookmark file (runtime + .env)."""
    info = get_config_bookmark_info()
    if not info.get("exists"):
        raise FileNotFoundError(
            f"No config bookmark at {get_active_bookmark_path()}. "
            "Apply Best Win strategy or run restore_bookmark.bat first."
        )
    values = dict(info.get("values") or PRE_WIN_PRESET_BOOKMARK)
    key_map = (
        BEST_WIN_RUNTIME_KEYS
        if info.get("bookmark_kind") == "best_win"
        else BOOKMARK_RUNTIME_KEYS
    )
    env_map = BEST_WIN_ENV_KEYS if info.get("bookmark_kind") == "best_win" else BOOKMARK_ENV_KEYS
    runtime_updates = {
        key_map[api_key]: values[api_key]
        for api_key in key_map
        if api_key in values
    }
    result = Config.update_runtime(**runtime_updates)
    _write_env_keys(values, env_map)
    return {
        "ok": True,
        "label": info.get("label"),
        "created_at": info.get("created_at"),
        "applied": result.get("applied", {}),
        "config": Config.to_dict(),
    }
