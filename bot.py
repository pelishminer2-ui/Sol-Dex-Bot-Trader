import asyncio
import logging
import signal
import threading
import time
from typing import List, Optional

from config import Config, SOL_MINT, instant_profit_exempt_from_min_net_win, is_sol_trade_mint, is_wbtc_watchlist_mint, is_weth_trade_mint, sol_trading_enabled, wbtc_min_net_win_threshold, wbtc_profit_gate_applies, wbtc_companion_slot_open, weth_trading_enabled
from dexscreener_client import get_dexscreener_client
from jupiter_client import get_jupiter_client
from jupiter import JupiterExecutor, SwapQuote
from paper_session import paper_session_manager
from pnl_tracker import pnl_tracker
from tax_export import append_tax_row
from fee_estimator import (
    estimate_chain_fee_per_tx_sol,
    estimate_full_exit_fees_sol,
    estimate_leg_fees_sol,
    estimate_round_trip_fees_sol,
    fee_breakdown_from_quotes,
)
from trade_utils import (
    build_buy_journal,
    build_sell_journal,
    entry_sol_basis,
    estimate_token_ui,
    quote_sol_flow,
)
from price_feed import PriceFeed
from reentry_tracker import ReentryTracker
from risk import RiskManager
from scanner import MoverCandidate, scan_unified
from similarity import SimilarityScorer
from solana_client import SolanaClient
from strategy import ExitSignal, MomentumStrategy, Position, SignalType
from trading_lock import trading_lock
from watchlist_scanner import (
    compute_entry_watchlist_gains,
    fetch_all_watchlist_candidates,
    is_pinned_watchlist_mint,
    probe_all_watchlist_statuses,
)
from sol_trading import merge_sol_trade_watchlist, probe_sol_trade_status
from weth_trading import merge_weth_trade_watchlist, probe_weth_trade_status

logger = logging.getLogger(__name__)

TP_LEVEL_REASONS = {
    0: "sell_take_profit_l1",
    1: "sell_take_profit_l2",
    2: "sell_take_profit_l3",
    3: "sell_take_profit_l4",
}

SLOWDOWN_REASONS = {
    2: "ladder_slowdown_after_l2",
    3: "ladder_slowdown_after_l3",
}

FORCED_EXIT_TYPES = frozenset({
    SignalType.SELL_SL,
    SignalType.SELL_L1_PROTECTION,
    SignalType.SELL_TIME,
    SignalType.SELL_LADDER_MISSED_30M,
    SignalType.SELL_INSTANT_PROFIT,
})


class TradingBot:
    def __init__(
        self,
        dry_run: Optional[bool] = None,
        private_key: Optional[str] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self.dry_run = Config.DRY_RUN if dry_run is None else dry_run
        self._private_key = private_key
        self._stop_event = stop_event
        self.running = True
        self.last_scan_time: Optional[float] = None
        self.last_dexscreener_count: int = 0
        self.last_pumpfun_count: int = 0
        self.last_birdeye_count: int = 0
        self.last_gmgn_count: int = 0
        self.last_birdeye_scan_status: str = "idle"
        self.last_pumpfun_scan_status: str = "idle"
        self.last_gmgn_scan_status: str = "idle"
        self.zero_streak_dexscreener: int = 0
        self.zero_streak_pumpfun: int = 0
        self.zero_streak_birdeye: int = 0
        self.zero_streak_gmgn: int = 0
        self.last_dexscreener_health: dict = {"status": "idle"}
        self.last_jupiter_health: dict = {"status": "idle"}
        self.last_action: str = "Bot initialized"
        self.last_action_time: Optional[float] = None
        self.last_entry_skip_reason: Optional[str] = None
        self._cycle_entry_skip_reason: Optional[str] = None
        self._cycle_entry_gate_counts: dict[str, int] = {}
        self.entry_gate_summary: dict = {}
        self._dominant_gate: Optional[str] = None
        self._dominant_gate_since: Optional[float] = None
        self._exit_impact_defer_counts: dict[str, int] = {}
        self._forced_high_impact_exits: set[str] = set()
        self.price_feed = PriceFeed()
        self.strategy = MomentumStrategy()
        self.similarity = SimilarityScorer()
        self.risk = RiskManager()
        self.reentry_tracker = ReentryTracker()
        self.solana: Optional[SolanaClient] = None
        self.jupiter: Optional[JupiterExecutor] = None
        self.watchlist: List[MoverCandidate] = []
        self.watchlist_mint_statuses: List[dict] = []
        self.watchlist_mint_status: Optional[dict] = None
        self.sol_trade_status: Optional[dict] = None
        self.weth_trade_status: Optional[dict] = None
        self.scan_count: int = 0
        self.scan_in_progress: bool = False
        self.sol_trend_snapshot: dict = {}
        self.market_regime_snapshot: dict = {}

    def _record_action(self, message: str) -> None:
        self.last_action = message
        self.last_action_time = time.time()
        logger.info("Action: %s", message)

    def _entry_momentum(
        self,
        mint: str,
        current_price: float,
        candidate: MoverCandidate,
    ) -> Optional[float]:
        """30s feed momentum; use best of feed vs scanner short-term signal."""
        feed_mom = self.price_feed.get_momentum(mint, current_price)
        scanner_mom = candidate.scanner_discovery_momentum() or candidate.momentum_pct
        if feed_mom is None:
            return scanner_mom
        if scanner_mom is None:
            return feed_mom
        return max(feed_mom, scanner_mom)

    @staticmethod
    def _entry_gate_category(reason: str) -> str:
        """Map a skip reason to a stable gate bucket for entry_gate_summary."""
        lower = (reason or "").lower()
        if "expected net" in lower or "trade too small" in lower or "trade size too small" in lower:
            return "expected_net_profit"
        if "price impact" in lower:
            return "price_impact"
        if "liquidity" in lower:
            return "liquidity"
        if "entry momentum" in lower or "dip re-entry momentum" in lower:
            return "entry_momentum"
        if "no momentum data" in lower:
            return "no_momentum_data"
        if "loss re-entry cooldown" in lower or "loss cooldown" in lower or "one-strike" in lower:
            return "loss_cooldown"
        if "sol macro" in lower:
            return "sol_trend"
        if "trade cooldown" in lower:
            return "trade_cooldown"
        if "watchlist gain" in lower or "watchlist mint excluded" in lower:
            return "watchlist_gain"
        if "sol trade" in lower:
            return "sol_trade"
        if "stock-related" in lower:
            return "stock_filter"
        if "max open positions" in lower:
            return "max_positions"
        if "no jupiter route" in lower:
            return "no_jupiter_route"
        if "trade size is zero" in lower or "wallet below" in lower:
            return "funding"
        return "other"

    def _note_entry_skip(self, reason: str) -> None:
        """Record the first entry skip reason for the current entry cycle."""
        gate = self._entry_gate_category(reason)
        self._cycle_entry_gate_counts[gate] = (
            self._cycle_entry_gate_counts.get(gate, 0) + 1
        )
        if self._cycle_entry_skip_reason is None:
            self._cycle_entry_skip_reason = reason

    def _finalize_entry_gate_summary(self) -> None:
        """Publish per-cycle gate counts and track a sustained dominant blocker."""
        counts = dict(self._cycle_entry_gate_counts)
        total = sum(counts.values())
        dominant = max(counts, key=counts.get) if counts else None
        now = time.time()
        if dominant and counts.get(dominant, 0) == total and total > 0:
            if self._dominant_gate == dominant:
                since = self._dominant_gate_since or now
            else:
                since = now
                self._dominant_gate = dominant
            self._dominant_gate_since = since
        elif total == 0:
            self._dominant_gate = None
            self._dominant_gate_since = None
        else:
            self._dominant_gate = None
            self._dominant_gate_since = None

        blocked_minutes = (
            (now - self._dominant_gate_since) / 60.0
            if self._dominant_gate_since is not None
            else 0.0
        )
        suggestion = None
        if (
            self._dominant_gate == "expected_net_profit"
            and blocked_minutes >= 10
        ):
            suggestion = "balanced_win"
        elif (
            self._dominant_gate == "entry_momentum"
            and blocked_minutes >= 10
        ):
            suggestion = "balanced_win"

        self.entry_gate_summary = {
            "counts": counts,
            "total_blocked": total,
            "dominant_gate": self._dominant_gate,
            "dominant_gate_minutes": round(blocked_minutes, 1),
            "suggested_preset": suggestion,
        }
        if (
            self._dominant_gate
            and blocked_minutes >= 10
            and total > 0
        ):
            logger.info(
                "Entry gates: all %d candidate(s) blocked by '%s' for %.0f min — consider %s preset",
                total,
                self._dominant_gate,
                blocked_minutes,
                suggestion or "loosening that gate",
            )

    def _handle_shutdown(self, signum, frame):
        logger.info("Shutdown signal received (%s)", signum)
        self.stop()

    def should_run(self) -> bool:
        if not self.running:
            return False
        if self._stop_event is not None and self._stop_event.is_set():
            return False
        return True

    async def _interruptible_sleep(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while self.should_run() and time.time() < deadline:
            await asyncio.sleep(min(0.5, max(0.0, deadline - time.time())))

    async def initialize(self, setup_signals: bool = True):
        key = self._private_key or Config.SOLANA_PRIVATE_KEY or None
        self.solana = SolanaClient(private_key=key, dry_run=self.dry_run)
        self.jupiter = JupiterExecutor(str(self.solana.public_key), dry_run=self.dry_run)
        balance = await self.solana.get_balance()
        effective_balance = self.risk.effective_wallet_balance(balance, self.dry_run)
        banner = self.risk.startup_banner(
            str(self.solana.public_key), effective_balance, self.dry_run
        )
        logger.info(banner)
        mode = "paper" if self.dry_run else "live"
        self._record_action(f"Started in {mode} mode — scanning for movers")

        if not self.dry_run and not key:
            raise RuntimeError("Private key required for live trading")

        ok, reason = self.risk.can_start_trading(balance, dry_run=self.dry_run)
        if not ok:
            raise RuntimeError(reason)

        if setup_signals:
            signal.signal(signal.SIGINT, self._handle_shutdown)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, self._handle_shutdown)

        from sol_trend_filter import get_sol_trend_snapshot, reset_session_baseline
        from market_regime import update_market_regime

        reset_session_baseline()
        self.sol_trend_snapshot = get_sol_trend_snapshot(force_refresh=True)
        self.market_regime_snapshot = update_market_regime(
            self.sol_trend_snapshot, []
        )

    def _update_scan_zero_streaks(
        self,
        dex_count: int,
        pumpfun_count: int,
        birdeye_count: int,
        gmgn_count: int,
    ) -> None:
        """Track consecutive zero-result scans per source for status/GUI warnings."""
        from birdeye_scanner import get_last_birdeye_scan_status
        from gmgn_scanner import get_last_gmgn_scan_status
        from pumpfun_scanner import get_last_pumpfun_scan_status

        streaks = (
            ("dexscreener", dex_count, "zero_streak_dexscreener"),
            ("pumpfun", pumpfun_count, "zero_streak_pumpfun"),
            ("birdeye", birdeye_count, "zero_streak_birdeye"),
            ("gmgn", gmgn_count, "zero_streak_gmgn"),
        )
        birdeye_fallback = get_last_birdeye_scan_status() == "fallback"
        pumpfun_fallback = get_last_pumpfun_scan_status() in ("fallback", "add_key")
        gmgn_failed = get_last_gmgn_scan_status() == "failed"
        for source, count, attr in streaks:
            if source == "birdeye" and birdeye_fallback:
                setattr(self, attr, 0)
                continue
            if source == "pumpfun" and pumpfun_fallback:
                setattr(self, attr, 0)
                continue
            if source == "gmgn" and gmgn_failed:
                setattr(self, attr, 0)
                continue
            if count == 0:
                streak = getattr(self, attr) + 1
                setattr(self, attr, streak)
                if streak >= 3:
                    logger.warning(
                        "%s returned 0 movers for %d consecutive scans — check API keys or filters",
                        source,
                        streak,
                    )
            else:
                setattr(self, attr, 0)

    def _refresh_weth_trade_status(self) -> None:
        if not weth_trading_enabled():
            self.weth_trade_status = {"enabled": False}
            return
        held = {p.mint for p in self.strategy.positions}
        self.weth_trade_status = probe_weth_trade_status(
            self.price_feed,
            held_mints=held,
        )

    def _merge_weth_trade_candidate(self) -> None:
        if not weth_trading_enabled():
            self.weth_trade_status = {"enabled": False}
            return
        self._refresh_weth_trade_status()
        self.watchlist = merge_weth_trade_watchlist(
            self.watchlist,
            self.price_feed,
        )

    def _refresh_sol_trade_status(self) -> None:
        if not sol_trading_enabled():
            self.sol_trade_status = {"enabled": False}
            return
        held = {p.mint for p in self.strategy.positions}
        self.sol_trade_status = probe_sol_trade_status(
            self.price_feed,
            sol_snapshot=self.sol_trend_snapshot,
            held_mints=held,
        )

    def _merge_sol_trade_candidate(self) -> None:
        if not sol_trading_enabled():
            self.sol_trade_status = {"enabled": False}
            return
        self._refresh_sol_trade_status()
        self.watchlist = merge_sol_trade_watchlist(
            self.watchlist,
            self.price_feed,
            sol_snapshot=self.sol_trend_snapshot,
        )

    def _refresh_pinned_watchlist_status(self) -> None:
        """Refresh pinned-mint statuses for GUI and entry gating."""
        if not Config.watchlist_mint_enabled():
            self.watchlist_mint_statuses = []
            self.watchlist_mint_status = {"enabled": False}
            return
        held = {p.mint for p in self.strategy.positions}
        self.watchlist_mint_statuses = probe_all_watchlist_statuses(
            self.price_feed, held_mints=held
        )
        self.watchlist_mint_status = (
            self.watchlist_mint_statuses[0] if self.watchlist_mint_statuses else {"enabled": False}
        )

    def _poll_pinned_watchlist_mint(self) -> None:
        """Keep pinned mints in the price feed between full scans."""
        mints: List[str] = []
        if Config.watchlist_mint_enabled():
            mints.extend(Config.watchlist_mints())
        if sol_trading_enabled():
            mints.append(Config.SOL_TRADE_MINT)
        if weth_trading_enabled():
            mints.append(Config.WETH_MINT)
        if mints:
            self.price_feed.update(mints)
            if Config.watchlist_mint_enabled():
                self._refresh_pinned_watchlist_status()
            if sol_trading_enabled():
                self._refresh_sol_trade_status()
            if weth_trading_enabled():
                self._refresh_weth_trade_status()

    def _merge_pinned_watchlist_mint(self) -> None:
        """Poll every pinned mint each scan; trade only when entry gate qualifies."""
        if not Config.watchlist_mint_enabled():
            self.watchlist_mint_statuses = []
            self.watchlist_mint_status = {"enabled": False}
            return
        self._refresh_pinned_watchlist_status()
        pinned = set(Config.watchlist_mints())
        self.watchlist = [c for c in self.watchlist if c.mint not in pinned]
        for candidate in reversed(fetch_all_watchlist_candidates(self.price_feed)):
            self.watchlist.insert(0, candidate)

    def _apply_partial_watchlist(
        self,
        movers: List[MoverCandidate],
        dex_count: int,
        pumpfun_count: int,
        birdeye_count: int,
        gmgn_count: int,
    ) -> None:
        """Publish incremental scan results to the dashboard before the full cycle finishes."""
        self.last_dexscreener_count = dex_count
        self.last_pumpfun_count = pumpfun_count
        self.last_birdeye_count = birdeye_count
        self.last_gmgn_count = gmgn_count
        ranked = self.similarity.rank(movers)
        self.watchlist = ranked[: Config.WATCHLIST_TOP_N]
        self._merge_pinned_watchlist_mint()
        self._merge_sol_trade_candidate()
        self._merge_weth_trade_candidate()
        for candidate in self.watchlist:
            self.price_feed.set_dex_price(candidate.mint, candidate.price_usd)
        if self.watchlist:
            self._record_action(
                f"Scanning… {len(self.watchlist)} movers found so far"
            )
        else:
            self._record_action("Scanning for movers…")

    async def _refresh_watchlist(self):
        first_scan = self.scan_count == 0
        self.scan_in_progress = True
        if first_scan and Config.FIRST_SCAN_FAST_MODE:
            self._record_action("Scanning for movers…")

        on_partial = self._apply_partial_watchlist if first_scan else None
        movers, dex_count, pumpfun_count, birdeye_count, gmgn_count = scan_unified(
            include_pumpfun=Config.scan_pumpfun_enabled(),
            include_birdeye=Config.scan_birdeye_enabled(),
            include_gmgn=Config.scan_gmgn_enabled(),
            first_scan=first_scan,
            on_partial=on_partial,
        )
        self.scan_count += 1
        self.scan_in_progress = False
        self.last_dexscreener_count = dex_count
        self.last_pumpfun_count = pumpfun_count
        self.last_birdeye_count = birdeye_count
        self.last_gmgn_count = gmgn_count
        from birdeye_scanner import get_last_birdeye_scan_status
        from gmgn_scanner import get_last_gmgn_scan_status
        from pumpfun_scanner import get_last_pumpfun_scan_status

        self.last_birdeye_scan_status = get_last_birdeye_scan_status()
        self.last_pumpfun_scan_status = get_last_pumpfun_scan_status()
        self.last_gmgn_scan_status = get_last_gmgn_scan_status()
        self.last_dexscreener_health = get_dexscreener_client().get_health()
        self.last_jupiter_health = get_jupiter_client().get_health()
        from sol_trend_filter import get_sol_trend_snapshot
        from market_regime import update_market_regime

        self.sol_trend_snapshot = get_sol_trend_snapshot()
        self._update_scan_zero_streaks(dex_count, pumpfun_count, birdeye_count, gmgn_count)
        ranked = self.similarity.rank(movers)
        self.watchlist = ranked[: Config.WATCHLIST_TOP_N]

        self._merge_pinned_watchlist_mint()
        self._merge_sol_trade_candidate()
        self._merge_weth_trade_candidate()
        self.market_regime_snapshot = update_market_regime(
            self.sol_trend_snapshot, self.watchlist
        )

        self.last_scan_time = time.time()
        for candidate in self.watchlist:
            self.price_feed.set_dex_price(candidate.mint, candidate.price_usd)
        if self.watchlist:
            top = self.watchlist[0]
            wl_note = ""
            if self.watchlist_mint_statuses:
                parts = []
                for wl in self.watchlist_mint_statuses:
                    if wl.get("usd_gain") is None and wl.get("day_pct_gain") is None:
                        continue
                    sym = wl.get("symbol") or (wl.get("mint") or "")[:8]
                    if wl.get("day_pct_gain") is not None:
                        gain = f"+{wl['day_pct_gain'] * 100:.1f}%"
                    else:
                        gain = f"+${wl.get('usd_gain', 0):.2f}"
                    qual = " eligible" if wl.get("qualifies") else " standby"
                    parts.append(f"{sym} {gain}{qual}")
                if parts:
                    wl_note = " | Watchlist: " + "; ".join(parts)
            logger.info(
                "Top mover: %s momentum=%.2f%% liq=$%.0f",
                top.symbol,
                top.momentum_pct * 100,
                top.liquidity_usd,
            )
            self._record_action(
                f"Scanned {len(self.watchlist)} movers (top: {top.symbol} +{top.momentum_pct * 100:.1f}%){wl_note}"
            )
        else:
            health = self.last_dexscreener_health or {}
            if health.get("status") == "rate_limited":
                self._record_action(
                    "Scan throttled — DexScreener rate limit (staggered retry)"
                )
            else:
                self._record_action("Scan complete — no qualified movers (filters may be strict)")

    def _trade_candidates(self) -> List[MoverCandidate]:
        """Ranked watchlist slice considered for new entries (max concurrent positions unchanged)."""
        pinned = set(Config.watchlist_mints()) if Config.watchlist_mint_enabled() else set()
        if sol_trading_enabled():
            pinned.add(Config.SOL_TRADE_MINT)
        if weth_trading_enabled():
            pinned.add(Config.WETH_MINT)
        pool = [c for c in self.watchlist if c.mint not in pinned]
        candidates = pool[: Config.TRADE_CANDIDATE_TOP_N]
        qualified_pinned: List[MoverCandidate] = []
        if sol_trading_enabled() and self.sol_trade_status and self.sol_trade_status.get("qualifies"):
            sol_candidate = next(
                (c for c in self.watchlist if is_sol_trade_mint(c.mint)),
                None,
            )
            if sol_candidate:
                qualified_pinned.append(sol_candidate)
        if weth_trading_enabled() and self.weth_trade_status and self.weth_trade_status.get("qualifies"):
            weth_candidate = next(
                (c for c in self.watchlist if is_weth_trade_mint(c.mint)),
                None,
            )
            if weth_candidate:
                qualified_pinned.append(weth_candidate)
        for wl_status in self.watchlist_mint_statuses:
            if not wl_status.get("qualifies"):
                continue
            wl = next((c for c in self.watchlist if c.mint == wl_status.get("mint")), None)
            if wl:
                qualified_pinned.append(wl)
        for wl in qualified_pinned:
            candidates = [wl] + [c for c in candidates if c.mint != wl.mint]
            candidates = candidates[: Config.TRADE_CANDIDATE_TOP_N]
        return candidates

    def _sol_price_usd(self) -> Optional[float]:
        latest = self.price_feed.get_latest(SOL_MINT)
        if latest:
            return latest
        prices = self.price_feed.update([SOL_MINT])
        return prices.get(SOL_MINT)

    def _fetch_mint_liquidity_usd(self, mint: str) -> Optional[float]:
        """Best-pool USD liquidity from DexScreener (cached client)."""
        pairs = get_dexscreener_client().get_token_pairs(mint)
        best_liq = -1.0
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            liq = float((pair.get("liquidity") or {}).get("usd") or 0)
            if liq > best_liq:
                best_liq = liq
        return best_liq if best_liq >= 0 else None

    def _sell_amount_for_exit(self, position: Position, exit_signal: ExitSignal) -> int:
        if exit_signal.is_partial and exit_signal.tp_level_index is not None:
            return self.strategy.partial_sell_amount_raw(position, exit_signal.tp_level_index)
        return position.remaining_token_amount_raw

    async def _execute_sell(
        self, position: Position, token_raw: int, quote: Optional[SwapQuote] = None
    ) -> Optional[str]:
        assert self.solana and self.jupiter

        if not self.dry_run:
            wallet_raw = await self.solana.get_token_balance_raw(position.mint)
            if wallet_raw <= 0:
                logger.error("No token balance to sell for %s", position.symbol)
                self.strategy.positions = [
                    p for p in self.strategy.positions if p.mint != position.mint
                ]
                return None
            token_raw = min(token_raw, wallet_raw)

        if token_raw <= 0:
            logger.warning("Sell amount is zero for %s", position.symbol)
            return None

        if quote is None:
            quote = self.jupiter.sell_token(position.mint, token_raw)
        if not quote:
            return None

        ok, reason = self.risk.pre_trade_check(
            await self.solana.get_balance(),
            quote.price_impact_pct,
            dry_run=self.dry_run,
        )
        if not ok:
            logger.warning("Sell blocked: %s", reason)
            return None

        return await self.jupiter.execute_quote(quote, self.solana)

    def _preview_l1_sell_quote(
        self, mint: str, buy_quote: SwapQuote
    ) -> Optional[SwapQuote]:
        """Preview L1 partial sell for round-trip fee estimation at entry."""
        assert self.jupiter
        portions = Config.TAKE_PROFIT_PORTIONS
        if not portions or buy_quote.out_amount <= 0:
            return None
        token_l1 = int(buy_quote.out_amount * portions[0])
        if token_l1 <= 0:
            return None
        return self.jupiter.sell_token(mint, token_l1, use_cache=True)

    def _entry_fee_estimate(
        self, trade_size: float, buy_quote: SwapQuote, sell_preview: Optional[SwapQuote]
    ) -> tuple[float, dict]:
        breakdown = fee_breakdown_from_quotes(
            trade_size, buy_quote.raw, sell_preview.raw if sell_preview else None
        )
        return breakdown["buffered_total_sol"], breakdown

    def _quote_actual_fees_sol(self, quote: SwapQuote) -> float:
        return estimate_chain_fee_per_tx_sol() + quote.swap_fees_sol()

    def _allocate_sell_fees(
        self, position: Position, level_index: Optional[int] = None
    ) -> float:
        fees = self._preview_sell_fees(position, level_index)
        position.fees_allocated_sol += fees
        return fees

    def _preview_sell_fees(
        self, position: Position, level_index: Optional[int] = None
    ) -> float:
        if level_index is not None:
            return estimate_leg_fees_sol(
                position.size_sol,
                level_index,
                position.tp_levels,
                position.tp_portions,
            )
        remaining_fraction = (
            position.remaining_token_amount_raw / position.initial_token_amount_raw
            if position.initial_token_amount_raw > 0
            else 1.0
        )
        return estimate_full_exit_fees_sol(
            position.size_sol,
            remaining_fraction,
            position.fees_allocated_sol,
            position.fee_budget_sol,
        )

    @staticmethod
    def _is_forced_exit(exit_signal: ExitSignal) -> bool:
        return exit_signal.signal_type in FORCED_EXIT_TYPES

    def _should_defer_exit_for_impact(
        self, position: Position, quote: SwapQuote, exit_signal: ExitSignal
    ) -> bool:
        defer, self._exit_impact_defer_counts, forced = (
            self.risk.should_defer_exit_for_impact(
                position.mint,
                position.symbol,
                quote.price_impact_pct,
                is_stop_loss=exit_signal.signal_type == SignalType.SELL_SL,
                defer_counts=self._exit_impact_defer_counts,
                signal_name=exit_signal.signal_type.value,
            )
        )
        if forced:
            self._forced_high_impact_exits.add(position.mint)
        return defer

    def _quote_min_net_threshold(
        self, position: Position, exit_signal: ExitSignal
    ) -> float:
        """Min net SOL required at quote time; 0 = no gate."""
        if self._is_forced_exit(exit_signal):
            return 0.0
        if (
            exit_signal.signal_type == SignalType.SELL_INSTANT_PROFIT
            and instant_profit_exempt_from_min_net_win(position.mint)
        ):
            return 0.0
        if wbtc_profit_gate_applies(position.mint, exit_signal.signal_type.value):
            return wbtc_min_net_win_threshold()
        if Config.MIN_NET_WIN_SOL <= 0:
            return 0.0
        return Config.MIN_NET_WIN_SOL

    def _quote_meets_min_net(
        self,
        position: Position,
        token_raw: int,
        quote: SwapQuote,
        exit_signal: ExitSignal,
    ) -> bool:
        threshold = self._quote_min_net_threshold(position, exit_signal)
        if threshold <= 0:
            return True
        level_idx = (
            exit_signal.tp_level_index if exit_signal.is_partial else None
        )
        fees = self._preview_sell_fees(position, level_idx)
        sol_basis = entry_sol_basis(
            position.size_sol, token_raw, position.initial_token_amount_raw
        )
        _, sol_out = quote_sol_flow(quote)
        net = sol_out - sol_basis - fees
        if net < threshold:
            if is_wbtc_watchlist_mint(position.mint) and Config.WBTC_PROFIT_ONLY_EXITS:
                logger.info(
                    "WBTC hold: net %.4f SOL below min %.4f SOL after fees — skipping %s",
                    net,
                    threshold,
                    exit_signal.signal_type.value,
                )
            else:
                logger.info(
                    "Fee-aware hold (quote): %s net %.4f SOL < min %.4f SOL — skipping %s",
                    position.symbol,
                    net,
                    threshold,
                    exit_signal.signal_type.value,
                )
            return False
        return True

    def _consume_forced_high_impact(self, mint: str) -> bool:
        if mint in self._forced_high_impact_exits:
            self._forced_high_impact_exits.discard(mint)
            return True
        return False

    def _annotate_max_loss_alert(
        self, journal: dict, *, forced_high_impact: bool = False
    ) -> None:
        if forced_high_impact:
            journal["max_loss_alert"] = True
            journal["high_exit_slippage"] = True
            logger.warning(
                "MAX LOSS ALERT: %s forced exit with high slippage (impact %.2f%%)",
                journal.get("symbol", "?"),
                float(journal.get("price_impact_pct") or 0.0),
            )
        net = journal.get("net_pnl_sol", journal.get("pnl_sol"))
        if net is None:
            return
        if float(net) < -Config.MAX_LOSS_PER_TRADE_SOL:
            journal["max_loss_alert"] = True
            logger.warning(
                "MAX LOSS ALERT: %s net %.4f SOL exceeds cap %.4f SOL",
                journal.get("symbol", "?"),
                float(net),
                Config.MAX_LOSS_PER_TRADE_SOL,
            )

    def _record_completed_trade_outcome(
        self, mint: str, symbol: str, net_pnl_sol: float
    ) -> None:
        if net_pnl_sol < 0:
            self.strategy.record_loss_reentry_cooldown(mint)
        self.risk.record_trade_outcome(net_pnl_sol)

    def _fetch_position_prices(self, mints: List[str]) -> dict:
        """Fetch prices for open positions with retry (never silently skip)."""
        return self.price_feed.update_with_retry(mints)

    async def _ladder_dca_gates(
        self, position: Position, current_price: float
    ) -> tuple[bool, bool]:
        """Return (can_afford_dca, jupiter_route_ok) for 30m ladder-timeout evaluation."""
        if not Config.ENABLE_LADDER_TIME_EXITS or position.tp_levels_hit:
            return True, True

        hold_sec = time.time() - position.entry_time
        dca_sec = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60
        if hold_sec < dca_sec or position.pnl_pct(current_price) > 0:
            return True, True

        if position.buy_count >= Config.MAX_BUYS_PER_MINT:
            return False, False

        balance = await self.solana.get_balance()
        trade_size = self.risk.compute_trade_size(balance, dry_run=self.dry_run)
        can_afford = trade_size > 0
        if can_afford and self.dry_run:
            can_afford = not paper_session_manager.is_balance_insufficient_for_entry(
                trade_size
            )
        if can_afford and not self.dry_run:
            can_afford = balance >= Config.MIN_SOL_RESERVE + trade_size

        jupiter_ok = False
        if can_afford:
            quote = self.jupiter.buy_token(position.mint, trade_size)
            jupiter_ok = quote is not None
        return can_afford, jupiter_ok

    async def _monitor_open_position(
        self, position: Position, current_price: Optional[float] = None, *, allow_stopped: bool = False
    ):
        assert self.solana and self.jupiter

        if current_price is None:
            prices = self._fetch_position_prices([position.mint])
            current_price = prices.get(position.mint)
        if not current_price:
            logger.warning(
                "No price for open position %s (%s) — exit check skipped after retries",
                position.symbol,
                position.mint[:8],
            )
            return

        peak_price = self.price_feed.get_peak_price_since(
            position.mint, position.entry_time
        )
        if peak_price and peak_price > 0:
            position.update_peak_pnl(peak_price)
        position.update_peak_pnl(current_price)

        while allow_stopped or self.should_run():
            can_afford_dca, jupiter_route_ok = await self._ladder_dca_gates(
                position, current_price
            )
            current_liquidity_usd: Optional[float] = None
            if (
                Config.ENABLE_LADDER_TIME_EXITS
                and not position.tp_levels_hit
                and time.time() - position.entry_time
                >= Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60
                and position.pnl_pct(current_price) <= 0
            ):
                current_liquidity_usd = self._fetch_mint_liquidity_usd(position.mint)
            exit_signal = self.strategy.evaluate_exit(
                position,
                current_price,
                price_feed=self.price_feed,
                mint=position.mint,
                current_liquidity_usd=current_liquidity_usd,
                can_afford_dca=can_afford_dca,
                jupiter_route_ok=jupiter_route_ok,
                sol_trend_snapshot=self.sol_trend_snapshot,
            )
            if exit_signal is None:
                pnl = position.pnl_pct(current_price)
                logger.debug(
                    "Holding %s pnl=%.4f peak=%.4f levels=%d/%d",
                    position.symbol,
                    pnl,
                    position.peak_pnl_pct,
                    len(position.tp_levels_hit),
                    len(Config.TAKE_PROFIT_LEVELS),
                )
                return

            if exit_signal.signal_type == SignalType.BUY_DCA_LADDER_TIMEOUT:
                dca_ok = await self._execute_dca_entry(position, current_price)
                if dca_ok:
                    return
                logger.info(
                    "DCA blocked for %s — falling back to ladder-timeout sell",
                    position.symbol,
                )
                exit_signal = ExitSignal(SignalType.SELL_LADDER_MISSED_30M)

            token_raw = self._sell_amount_for_exit(position, exit_signal)
            quote = self.jupiter.sell_token(position.mint, token_raw)
            if not quote:
                return

            if not self._quote_meets_min_net(position, token_raw, quote, exit_signal):
                return

            if self._should_defer_exit_for_impact(position, quote, exit_signal):
                return

            signature = await self._execute_sell(position, token_raw, quote)
            if not signature:
                return

            pnl = position.pnl_pct(current_price)
            sol_price = self._sol_price_usd()
            token_decimals = position.token_decimals or quote.output_decimals

            if exit_signal.is_partial and exit_signal.tp_level_index is not None:
                level_idx = exit_signal.tp_level_index
                levels = position.tp_levels or Config.TAKE_PROFIT_LEVELS
                level_pct = levels[level_idx]
                reason = TP_LEVEL_REASONS.get(level_idx, exit_signal.signal_type.value)
                leg_fees = self._allocate_sell_fees(position, level_idx)
                profile = self.strategy.apply_partial_tp(
                    position, level_idx, token_raw, current_price
                )
                partial_journal = build_sell_journal(
                    position=position,
                    quote=quote,
                    token_raw=token_raw,
                    current_price=current_price,
                    pnl_pct=pnl,
                    reason=reason,
                    signature=signature,
                    dry_run=self.dry_run,
                    action="sell_partial",
                    tp_level=level_idx + 1,
                    tp_level_pct=level_pct,
                    remaining_token_raw=position.remaining_token_amount_raw,
                    sol_price_usd=sol_price,
                    token_decimals=token_decimals,
                    estimated_fees_sol=leg_fees,
                    actual_fees_sol=self._quote_actual_fees_sol(quote),
                )
                self._annotate_max_loss_alert(
                    partial_journal,
                    forced_high_impact=self._consume_forced_high_impact(position.mint),
                )
                self.risk.journal_write(partial_journal)
                pnl_tracker.record_from_journal(partial_journal)
                if self.dry_run:
                    paper_session_manager.record_sell(partial_journal.get("sol_out", 0.0))
                append_tax_row(partial_journal, str(self.solana.public_key))
                position.realized_net_pnl_sol += partial_journal.get("net_pnl_sol", 0.0)
                logger.info(
                    "Partial exit %s L%d: sold %d tokens, %d/%d levels hit",
                    position.symbol,
                    level_idx + 1,
                    token_raw,
                    len(position.tp_levels_hit),
                    len(Config.TAKE_PROFIT_LEVELS),
                )
                if profile:
                    if profile.profitable:
                        self.similarity.set_reference(profile)
                    self._record_completed_trade_outcome(
                        position.mint, position.symbol, position.realized_net_pnl_sol
                    )
                    self.reentry_tracker.record_exit(
                        position.mint, current_price, position.symbol
                    )
                    return
                continue

            profile = self.strategy.close_position(
                position, current_price, exit_signal.signal_type
            )
            self.reentry_tracker.record_exit(
                position.mint, current_price, position.symbol
            )
            if profile.profitable:
                self.similarity.set_reference(profile)
            remaining_fraction = (
                position.remaining_token_amount_raw / position.initial_token_amount_raw
                if position.initial_token_amount_raw > 0
                else 1.0
            )
            loss_sol = max(0.0, -pnl * position.size_sol * remaining_fraction)
            self.risk.record_loss(loss_sol)
            exit_fees = self._allocate_sell_fees(position)
            if exit_signal.signal_type == SignalType.SELL_SLOWDOWN:
                reason = SLOWDOWN_REASONS.get(
                    exit_signal.slowdown_after_level,
                    exit_signal.signal_type.value,
                )
            elif exit_signal.signal_type == SignalType.SELL_WEAKEN:
                reason = "sell_trend_weaken_2pct"
            elif exit_signal.signal_type == SignalType.SELL_INSTANT_PROFIT:
                reason = "sell_instant_5pct"
            elif exit_signal.signal_type == SignalType.SELL_L1_PROTECTION:
                reason = "sell_l1_protection"
            elif exit_signal.signal_type == SignalType.SELL_LADDER_MISSED_10M:
                reason = "sell_ladder_missed_10m_positive"
            elif exit_signal.signal_type == SignalType.SELL_LADDER_MISSED_30M:
                reason = "sell_ladder_missed_30m_negative"
            elif exit_signal.signal_type == SignalType.SELL_WATCHLIST_TARGET:
                rule = Config.get_watchlist_rule(position.mint)
                pct = rule.sell_at_pct * 100 if rule and rule.sell_at_pct else 20
                reason = f"sell_watchlist_target_{pct:.0f}pct"
            elif exit_signal.signal_type == SignalType.SELL_SOL_TREND_COLD:
                reason = "sell_sol_trend_cold"
            else:
                reason = exit_signal.signal_type.value
            sell_journal = build_sell_journal(
                position=position,
                quote=quote,
                token_raw=token_raw,
                current_price=current_price,
                pnl_pct=pnl,
                reason=reason,
                signature=signature,
                dry_run=self.dry_run,
                remaining_token_raw=0,
                sol_price_usd=sol_price,
                token_decimals=token_decimals,
                estimated_fees_sol=exit_fees,
                actual_fees_sol=self._quote_actual_fees_sol(quote),
            )
            self._annotate_max_loss_alert(
                sell_journal,
                forced_high_impact=self._consume_forced_high_impact(position.mint),
            )
            self.risk.journal_write(sell_journal)
            pnl_tracker.record_from_journal(sell_journal)
            if self.dry_run:
                paper_session_manager.record_sell(sell_journal.get("sol_out", 0.0))
            append_tax_row(sell_journal, str(self.solana.public_key))
            position.realized_net_pnl_sol += sell_journal.get("net_pnl_sol", 0.0)
            self._record_completed_trade_outcome(
                position.mint, position.symbol, position.realized_net_pnl_sol
            )
            return

    async def force_sell_position(
        self,
        position: Position,
        reason: str = "sell_manual",
        *,
        current_price: Optional[float] = None,
    ) -> Optional[dict]:
        """Emergency 100% exit — bypasses exit evaluation and min-net profit gates."""
        assert self.solana and self.jupiter

        if current_price is None:
            prices = self._fetch_position_prices([position.mint])
            current_price = prices.get(position.mint) or position.entry_price

        peak_price = self.price_feed.get_peak_price_since(
            position.mint, position.entry_time
        )
        if peak_price and peak_price > 0:
            position.update_peak_pnl(peak_price)
        position.update_peak_pnl(current_price)

        token_raw = position.remaining_token_amount_raw
        if token_raw <= 0:
            logger.warning("Force sell: no tokens remaining for %s", position.symbol)
            return None

        quote = self.jupiter.sell_token(position.mint, token_raw)
        if not quote:
            logger.warning("Force sell: no quote for %s", position.symbol)
            return None

        signature = await self._execute_sell(position, token_raw, quote)
        if not signature:
            return None

        pnl = position.pnl_pct(current_price)
        sol_price = self._sol_price_usd()
        token_decimals = position.token_decimals or quote.output_decimals

        profile = self.strategy.close_position(
            position, current_price, SignalType.SELL_TIME
        )
        self.reentry_tracker.record_exit(
            position.mint, current_price, position.symbol
        )
        if profile.profitable:
            self.similarity.set_reference(profile)

        exit_fees = self._allocate_sell_fees(position)
        sell_journal = build_sell_journal(
            position=position,
            quote=quote,
            token_raw=token_raw,
            current_price=current_price,
            pnl_pct=pnl,
            reason=reason,
            signature=signature,
            dry_run=self.dry_run,
            remaining_token_raw=0,
            sol_price_usd=sol_price,
            token_decimals=token_decimals,
            estimated_fees_sol=exit_fees,
            actual_fees_sol=self._quote_actual_fees_sol(quote),
        )
        self._annotate_max_loss_alert(sell_journal)
        self.risk.journal_write(sell_journal)
        pnl_tracker.record_from_journal(sell_journal)
        if self.dry_run:
            paper_session_manager.record_sell(sell_journal.get("sol_out", 0.0))
        append_tax_row(sell_journal, str(self.solana.public_key))
        position.realized_net_pnl_sol += sell_journal.get("net_pnl_sol", 0.0)
        self._record_completed_trade_outcome(
            position.mint, position.symbol, position.realized_net_pnl_sol
        )
        self._record_action(
            f"Force sold {position.symbol}: {reason} net {sell_journal.get('net_pnl_sol', 0):+.4f} SOL"
        )
        return sell_journal

    async def _monitor_all_open_positions(self):
        if not self.should_run():
            return
        positions = self.strategy.get_open_positions()
        if not positions:
            return
        mints = [p.mint for p in positions]
        prices = self._fetch_position_prices(mints)
        for position in list(positions):
            if not self.should_run():
                return
            current_price = prices.get(position.mint)
            if not current_price:
                logger.warning(
                    "Skipping exit check for %s — no price after retries",
                    position.symbol,
                )
                continue
            await self._monitor_open_position(position, current_price)

    async def _close_for_session_expiry(self):
        """Force-close all open paper positions when the 24h session expires."""
        positions = self.strategy.get_open_positions()
        if not positions or not self.solana or not self.jupiter:
            return

        for position in list(positions):
            logger.info(
                "Paper session expired after %.1f hours — closing %s",
                Config.PAPER_SESSION_HOURS,
                position.symbol,
            )

            prices = self.price_feed.update([position.mint])
            current_price = prices.get(position.mint) or position.entry_price
            token_raw = position.remaining_token_amount_raw
            if token_raw <= 0:
                continue

            quote = self.jupiter.sell_token(position.mint, token_raw)
            if not quote:
                logger.warning("Session expiry: no sell quote for %s", position.symbol)
                continue

            signature = await self._execute_sell(position, token_raw, quote)
            if not signature:
                continue

            pnl = position.pnl_pct(current_price)
            sol_price = self._sol_price_usd()
            token_decimals = position.token_decimals or quote.output_decimals

            profile = self.strategy.close_position(position, current_price, SignalType.SELL_TIME)
            self.reentry_tracker.record_exit(position.mint, current_price, position.symbol)
            if profile.profitable:
                self.similarity.set_reference(profile)

            exit_fees = self._allocate_sell_fees(position)
            sell_journal = build_sell_journal(
                position=position,
                quote=quote,
                token_raw=token_raw,
                current_price=current_price,
                pnl_pct=pnl,
                reason="paper_session_expired",
                signature=signature,
                dry_run=self.dry_run,
                remaining_token_raw=0,
                sol_price_usd=sol_price,
                token_decimals=token_decimals,
                estimated_fees_sol=exit_fees,
                actual_fees_sol=self._quote_actual_fees_sol(quote),
            )
            self.risk.journal_write(sell_journal)
            pnl_tracker.record_from_journal(sell_journal)
            if self.dry_run:
                paper_session_manager.record_sell(sell_journal.get("sol_out", 0.0))
            position.realized_net_pnl_sol += sell_journal.get("net_pnl_sol", 0.0)
            self.risk.record_trade_outcome(position.realized_net_pnl_sol)

    async def _stop_for_paper_balance_depletion(self) -> None:
        """End paper session and stop when simulated SOL is exhausted."""
        paper_session_manager.end_session(stop_reason="balance_depleted")
        self._record_action("Paper session stopped: simulated SOL depleted")
        logger.info("Stopping bot: paper_balance_depleted")
        self.stop()

    async def _execute_entry(
        self,
        candidate: MoverCandidate,
        current_price: float,
        momentum: Optional[float],
        *,
        is_dip_reentry: bool = False,
    ) -> bool:
        assert self.solana and self.jupiter

        if not self.should_run():
            return False

        if self.dry_run:
            trade_size_preview = self.risk.compute_trade_size(
                await self.solana.get_balance(), dry_run=True
            )
            if paper_session_manager.is_balance_insufficient_for_entry(trade_size_preview):
                await self._stop_for_paper_balance_depletion()
                return False

        balance = await self.solana.get_balance()
        effective_balance = self.risk.effective_wallet_balance(balance, self.dry_run)
        open_mints = [p.mint for p in self.strategy.positions]
        can_trade, reason = self.risk.can_open_position(
            len(self.strategy.positions),
            balance,
            dry_run=self.dry_run,
            open_mints=open_mints,
            candidate_mint=candidate.mint,
        )
        if not can_trade:
            self._note_entry_skip(reason)
            self._record_action(f"Entry skipped: {reason}")
            logger.info("Entry blocked: %s", reason)
            return False

        trade_size = self.risk.compute_trade_size(balance, dry_run=self.dry_run)
        if trade_size <= 0:
            self._note_entry_skip("trade size is zero")
            self._record_action("Entry skipped: trade size is zero")
            logger.info("Entry blocked: trade size is zero")
            return False

        wallet_pct = (trade_size / effective_balance * 100) if effective_balance > 0 else 0.0
        entry_kind = "dip re-entry" if is_dip_reentry else "mover"
        logger.info(
            "Trade size (%s): %.4f SOL (%.1f%% of wallet, cap %.0f%%)",
            entry_kind,
            trade_size,
            wallet_pct,
            Config.MAX_WALLET_TRADE_PCT * 100,
        )

        quote = self.jupiter.buy_token(candidate.mint, trade_size)
        if not quote:
            if candidate.source == "pumpfun":
                logger.info(
                    "Skipping pump.fun token %s (%s): no Jupiter route",
                    candidate.symbol,
                    candidate.mint,
                )
            self._note_entry_skip(f"no Jupiter route for {candidate.symbol}")
            self._record_action(f"Entry skipped: no Jupiter route for {candidate.symbol}")
            return False

        sell_preview = self._preview_l1_sell_quote(candidate.mint, quote)
        fee_budget, fee_breakdown = self._entry_fee_estimate(
            trade_size, quote, sell_preview
        )

        ok, check_reason = self.risk.check_entry_eligibility(
            trade_size,
            candidate.liquidity_usd,
            quote.price_impact_pct,
            skip_liquidity=(candidate.source == "reentry"),
            mint=candidate.mint,
            symbol=candidate.symbol,
            name=candidate.name,
            jupiter_quote_buy=quote.raw,
            jupiter_quote_sell=sell_preview.raw if sell_preview else None,
            sell_preview_impact_pct=(
                sell_preview.price_impact_pct if sell_preview else None
            ),
        )
        if not ok:
            self._note_entry_skip(check_reason)
            self._record_action(check_reason)
            logger.warning("Buy blocked: %s", check_reason)
            return False

        ok, check_reason = self.risk.pre_trade_check(
            balance,
            quote.price_impact_pct,
            dry_run=self.dry_run,
            max_impact_pct=Config.effective_max_entry_price_impact_pct(),
        )
        if not ok:
            self._note_entry_skip(check_reason)
            self._record_action(f"Entry skipped: {check_reason}")
            logger.warning("Buy blocked: %s", check_reason)
            return False

        if not self.should_run():
            return False

        signature = await self.jupiter.execute_quote(quote, self.solana)
        if not signature:
            self._record_action(f"Entry failed: swap execution for {candidate.symbol}")
            return False

        token_raw = quote.out_amount
        if not self.dry_run:
            await asyncio.sleep(2)
            token_raw = await self.solana.get_token_balance_raw(candidate.mint)

        sol_price = self._sol_price_usd()
        sol_in, _ = quote_sol_flow(quote)
        if sol_in <= 0:
            sol_in = trade_size
        token_ui = estimate_token_ui(
            token_raw, quote.output_decimals, sol_in, current_price, sol_price
        )
        if token_ui > 0 and sol_price and sol_price > 0:
            entry_price = (sol_in * sol_price) / token_ui
        else:
            entry_price = current_price

        self.strategy.open_position(
            candidate=candidate,
            entry_price=entry_price,
            size_sol=trade_size,
            momentum=momentum or 0.0,
            token_amount_raw=token_raw,
            token_decimals=quote.output_decimals,
            fee_budget_sol=fee_budget,
            estimated_fees_sol=fee_budget,
        )
        if is_dip_reentry:
            self.strategy.traded_mints_cooldown.pop(candidate.mint, None)

        self.risk.journal_write(
            build_buy_journal(
                candidate=candidate,
                entry_price=entry_price,
                quote=quote,
                trade_size=trade_size,
                momentum=momentum,
                signature=signature,
                dry_run=self.dry_run,
                sol_price_usd=self._sol_price_usd(),
                token_decimals=quote.output_decimals,
                estimated_fees_sol=fee_budget,
                fee_breakdown=fee_breakdown,
            )
        )
        if self.dry_run:
            paper_session_manager.record_buy(trade_size)
        kind = "dip re-entry" if is_dip_reentry else (
            "SOL trade" if candidate.source == "sol_trade" else (
                "WETH trade" if candidate.source == "weth_trade" else (
                    "watchlist" if candidate.source == "watchlist_mint" else "momentum"
                )
            )
        )
        self._record_action(
            f"Bought {candidate.symbol} ({kind}) — {trade_size:.4f} SOL @ ${entry_price:.8f}"
        )
        return True

    async def _execute_dca_entry(self, position: Position, current_price: float) -> bool:
        """Scale into an open position when ladder-timeout DCA path is chosen."""
        assert self.solana and self.jupiter

        if not self.should_run():
            return False
        if position.buy_count >= Config.MAX_BUYS_PER_MINT:
            logger.info("DCA blocked: max buys (%d) for %s", Config.MAX_BUYS_PER_MINT, position.symbol)
            return False

        balance = await self.solana.get_balance()
        trade_size = self.risk.compute_trade_size(balance, dry_run=self.dry_run)
        if trade_size <= 0:
            logger.info("DCA blocked: zero trade size for %s", position.symbol)
            return False

        if self.dry_run:
            if paper_session_manager.is_balance_insufficient_for_entry(trade_size):
                logger.info("DCA blocked: insufficient paper balance for %s", position.symbol)
                return False

        ok, check_reason = self.risk.pre_trade_check(
            balance, price_impact_pct=0.0, dry_run=self.dry_run
        )
        if not ok:
            logger.info("DCA blocked: %s", check_reason)
            return False

        quote = self.jupiter.buy_token(position.mint, trade_size)
        if not quote:
            logger.info("DCA blocked: no Jupiter route for %s", position.symbol)
            return False

        sell_preview = self._preview_l1_sell_quote(position.mint, quote)

        ok, check_reason = self.risk.check_entry_eligibility(
            trade_size,
            position.profile.get("liquidity_usd", 0.0),
            quote.price_impact_pct,
            skip_liquidity=position.profile.get("liquidity_usd", 0.0) <= 0,
            mint=position.mint,
            symbol=position.symbol,
            jupiter_quote_buy=quote.raw,
            jupiter_quote_sell=sell_preview.raw if sell_preview else None,
            sell_preview_impact_pct=(
                sell_preview.price_impact_pct if sell_preview else None
            ),
        )
        if not ok:
            logger.info("DCA blocked: %s", check_reason)
            return False

        ok, check_reason = self.risk.pre_trade_check(
            balance,
            quote.price_impact_pct,
            dry_run=self.dry_run,
            max_impact_pct=Config.effective_max_entry_price_impact_pct(),
        )
        if not ok:
            logger.info("DCA blocked: %s", check_reason)
            return False

        signature = await self.jupiter.execute_quote(quote, self.solana)
        if not signature:
            return False

        token_raw = quote.out_amount
        if not self.dry_run:
            await asyncio.sleep(2)
            token_raw = await self.solana.get_token_balance_raw(position.mint)
            wallet_raw = token_raw
            added_raw = max(0, wallet_raw - position.remaining_token_amount_raw)
            if added_raw > 0:
                token_raw = added_raw

        sol_price = self._sol_price_usd()
        sol_in, _ = quote_sol_flow(quote)
        if sol_in <= 0:
            sol_in = trade_size
        token_ui = estimate_token_ui(
            token_raw, quote.output_decimals, sol_in, current_price, sol_price
        )
        if token_ui > 0 and sol_price and sol_price > 0:
            add_entry_price = (sol_in * sol_price) / token_ui
        else:
            add_entry_price = current_price

        self.strategy.apply_dca_to_position(
            position, sol_in, token_raw, add_entry_price
        )

        candidate = MoverCandidate(
            mint=position.mint,
            symbol=position.symbol,
            name=position.symbol,
            pair_address="",
            dex="",
            price_usd=current_price,
            liquidity_usd=position.profile.get("liquidity_usd", 0.0),
            volume_24h_usd=position.profile.get("volume_24h_usd", 0.0),
            momentum_pct=position.momentum_at_entry,
            price_change_5m=position.profile.get("price_change_5m", 0.0),
            price_change_1h=position.profile.get("price_change_1h", 0.0),
        )
        self.risk.journal_write(
            build_buy_journal(
                candidate=candidate,
                entry_price=add_entry_price,
                quote=quote,
                trade_size=sol_in,
                momentum=position.momentum_at_entry,
                signature=signature,
                dry_run=self.dry_run,
                sol_price_usd=sol_price,
                token_decimals=quote.output_decimals,
                reason="buy_dca_3rd_ladder_timeout",
                buy_count=position.buy_count,
            )
        )
        if self.dry_run:
            paper_session_manager.record_buy(sol_in)
        self._record_action(
            f"DCA buy #{position.buy_count} {position.symbol} — {sol_in:.4f} SOL @ ${add_entry_price:.8f}"
        )
        return True

    async def _try_entry(self):
        assert self.solana and self.jupiter

        if not self.should_run():
            return

        if not self.strategy.can_open_more():
            return

        self._cycle_entry_skip_reason = None
        self._cycle_entry_gate_counts = {}

        held_mints = {p.mint for p in self.strategy.positions}
        watchlist_by_mint = {c.mint: c for c in self.watchlist}
        dip_mints = [
            m for m in self.reentry_tracker.get_tracked_mints() if m not in held_mints
        ]
        trade_candidates = self._trade_candidates()
        scan_mints = [c.mint for c in trade_candidates if c.mint not in held_mints]
        if Config.watchlist_mint_enabled():
            for pinned in Config.watchlist_mints():
                if pinned not in held_mints and pinned not in scan_mints:
                    scan_mints.append(pinned)
        if sol_trading_enabled():
            sol_mint = Config.SOL_TRADE_MINT
            if sol_mint not in held_mints and sol_mint not in scan_mints:
                scan_mints.append(sol_mint)
        if weth_trading_enabled():
            weth_mint = Config.WETH_MINT
            if weth_mint not in held_mints and weth_mint not in scan_mints:
                scan_mints.append(weth_mint)
        all_mints = list(dict.fromkeys(dip_mints + scan_mints))
        if not all_mints:
            return

        prices = self.price_feed.update(all_mints)
        if Config.watchlist_mint_enabled():
            self._refresh_pinned_watchlist_status()
        if sol_trading_enabled():
            self._refresh_sol_trade_status()
        if weth_trading_enabled():
            self._refresh_weth_trade_status()

        for mint in dip_mints:
            if not self.should_run():
                return
            if not self.strategy.can_open_more(mint):
                continue
            current_price = prices.get(mint)
            if not current_price or not self.reentry_tracker.is_dip_reentry(mint, current_price):
                continue

            candidate = watchlist_by_mint.get(mint)
            if not candidate:
                candidate = self.reentry_tracker.to_candidate(mint, current_price)
            if not candidate:
                continue

            momentum = self.price_feed.get_momentum(mint, current_price)
            signal = self.strategy.evaluate_dip_reentry(
                candidate, current_price, True, momentum=momentum,
                sol_trend_snapshot=self.sol_trend_snapshot,
            )
            if signal != SignalType.BUY:
                skip_reason = self.strategy.dip_reentry_skip_reason(
                    candidate, True, momentum=momentum,
                    sol_trend_snapshot=self.sol_trend_snapshot,
                )
                if skip_reason:
                    self._note_entry_skip(skip_reason)
                continue

            if await self._execute_entry(
                candidate, current_price, momentum=momentum, is_dip_reentry=True
            ):
                held_mints.add(mint)

        for candidate in trade_candidates:
            if not self.should_run():
                return
            if candidate.mint in held_mints:
                continue
            if not self.strategy.can_open_more(candidate.mint):
                continue

            current_price = prices.get(candidate.mint)
            if not current_price:
                continue

            momentum = self._entry_momentum(candidate.mint, current_price, candidate)
            usd_gain = None
            if is_pinned_watchlist_mint(candidate.mint):
                gain_info = compute_entry_watchlist_gains(
                    self.price_feed, candidate.mint, current_price
                )
                candidate.usd_gain_baseline = gain_info.get("day_usd_gain")
                candidate.session_usd_gain = gain_info.get("session_usd_gain")
                candidate.day_usd_gain = gain_info.get("day_usd_gain")
                candidate.day_pct_gain = gain_info.get("day_pct_gain")
            signal = self.strategy.evaluate_entry(
                candidate,
                current_price,
                momentum,
                usd_gain=candidate.day_usd_gain if is_pinned_watchlist_mint(candidate.mint) else None,
                sol_trend_snapshot=self.sol_trend_snapshot,
            )
            if signal != SignalType.BUY:
                skip_reason = self.strategy.entry_skip_reason(
                    candidate,
                    momentum,
                    usd_gain=candidate.day_usd_gain if is_pinned_watchlist_mint(candidate.mint) else None,
                    sol_trend_snapshot=self.sol_trend_snapshot,
                )
                if skip_reason:
                    self._note_entry_skip(skip_reason)
                continue

            if await self._execute_entry(candidate, current_price, momentum):
                held_mints.add(candidate.mint)

        if self._cycle_entry_skip_reason is not None:
            self.last_entry_skip_reason = self._cycle_entry_skip_reason
        self._finalize_entry_gate_summary()

    async def run(self, setup_signals: bool = True):
        trading_lock.register_bot_thread()
        try:
            try:
                await self.initialize(setup_signals=setup_signals)
            except Exception:
                self.running = False
                raise
            last_scan = 0.0

            while self.should_run():
                try:
                    if self.dry_run and paper_session_manager.is_session_expired():
                        await self._close_for_session_expiry()
                        paper_session_manager.end_session(stop_reason="session_expired")
                        self._record_action("Paper session stopped: 24h test period ended")
                        logger.info("Stopping bot: paper_session_expired")
                        self.stop()
                        break

                    if self.risk.should_auto_stop_daily_loss():
                        logger.info("Stopping bot: daily net loss limit exceeded")
                        self._record_action("Stopped: daily net loss limit exceeded")
                        self.stop()
                        break

                    now = asyncio.get_event_loop().time()
                    self.strategy.tick_cooldowns()

                    scan_interval = Config.SCAN_INTERVAL_SEC + get_dexscreener_client().get_scan_interval_boost()
                    if self.should_run() and now - last_scan >= scan_interval:
                        await self._refresh_watchlist()
                        last_scan = now

                    if not self.should_run():
                        break

                    open_mints = [p.mint for p in self.strategy.positions]
                    open_count = len(open_mints)
                    from config import max_allowed_open_positions

                    position_limit = max_allowed_open_positions(open_mints)
                    logger.info(
                        "nonstop mode: %d/%d positions open",
                        open_count,
                        position_limit,
                    )
                    if wbtc_companion_slot_open(open_mints):
                        logger.info(
                            "WBTC companion slot open — seeking 2nd trade"
                        )

                    await self._monitor_all_open_positions()

                    if not self.should_run():
                        break

                    self.last_jupiter_health = get_jupiter_client().get_health()

                    if self.strategy.can_open_more():
                        await self._try_entry()
                    elif Config.watchlist_mint_enabled():
                        self._poll_pinned_watchlist_mint()

                    sleep_sec = (
                        Config.POSITION_MONITOR_SEC
                        if open_count > 0
                        else Config.PRICE_POLL_SEC
                    )
                    sleep_sec += get_jupiter_client().get_poll_interval_boost()
                    await self._interruptible_sleep(sleep_sec)
                except Exception as exc:
                    logger.exception("Bot loop error: %s", exc)
                    await self._interruptible_sleep(Config.PRICE_POLL_SEC)

            await self._shutdown()
            if self.solana:
                await self.solana.close()
        finally:
            trading_lock.unregister_bot_thread()

    def stop(self):
        self.running = False
        if self._stop_event is not None:
            self._stop_event.set()

    async def _shutdown(self):
        if not Config.CLOSE_ON_STOP:
            positions = self.strategy.get_open_positions()
            if positions:
                logger.info(
                    "Stop requested — holding %d open position(s) (CLOSE_ON_STOP=false)",
                    len(positions),
                )
            return
        positions = self.strategy.get_open_positions()
        if not positions or not self.solana or not self.jupiter:
            return
        for position in list(positions):
            logger.info("Attempting to close open position on shutdown: %s", position.symbol)
            await self._monitor_open_position(position, allow_stopped=True)
