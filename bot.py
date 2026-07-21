import asyncio
import logging
import signal
import threading
import time
from typing import List, Optional

from config import (
    Config,
    SOL_MINT,
    companion_slot_open,
    effective_stop_loss_pct,
    instant_profit_exempt_from_min_net_win,
    is_jitosol_trade_mint,
    is_sol_trade_mint,
    is_wbtc_watchlist_mint,
    is_weth_trade_mint,
    proxy_companion_slot_open,
    sol_trading_enabled,
    stable_quote_sol_wsol_entries_allowed,
    stable_quote_sol_wsol_path_active,
    stable_quote_mint,
    is_stable_quote_wsol_mint,
    stop_loss_applies_for_mint,
    wbtc_min_net_win_threshold,
    wbtc_profit_gate_applies,
    wbtc_companion_slot_open,
    weth_trading_enabled,
)
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
from reentry_retry import reentry_retry_manager
from session_entry_tuning import (
    maybe_auto_tighten,
    record_exit as record_session_exit,
    reset_session as reset_session_entry_tuning,
    status_snapshot as session_entry_tuning_status,
)
from risk import RiskManager
from scanner import MoverCandidate, scan_unified
from setup_learner import SetupLearner, features_from_position_profile
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
from sol_trading import (
    merge_sol_trade_watchlist,
    merge_stable_quote_wsol_watchlist,
    probe_sol_trade_status,
    probe_stable_quote_wsol_status,
)
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
    SignalType.SELL_MAX_HOLD_PROFIT,
    SignalType.SELL_PROXY_GREEN_HOLD,
    SignalType.SELL_LADDER_MISSED_30M,
    SignalType.SELL_LADDER_MISSED_10M,
    SignalType.SELL_INSTANT_PROFIT,
    SignalType.SELL_WATCHLIST_TARGET,
    SignalType.SELL_TP_PARTIAL,
})

FORCED_SELL_QUOTE_RETRIES = 10
FORCED_SELL_QUOTE_BACKOFF_SEC = 0.2
INSTANT_EXIT_NEAR_THRESHOLD_PCT = 0.02
LOSS_MONITOR_SEC = 0.5
INSTANT_PROFIT_MONITOR_SEC = 0.5
INSTANT_EXIT_CYCLE_RETRIES = 5
INSTANT_EXIT_CYCLE_BACKOFF_SEC = 0.15
INSTANT_EXIT_CRITICAL_CYCLES = 2
FORCED_EXIT_CRITICAL_CYCLES = 2
FORCED_EXIT_RETRY_INTERVAL_SEC = 0.5
STOP_APPROACHING_PCT = 0.01


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
        self._instant_exit_pending_cycles: dict[str, int] = {}
        self._pending_forced_exit: dict[str, int] = {}
        self._stop_alert_state: dict[str, str] = {}
        self.price_feed = PriceFeed()
        self.strategy = MomentumStrategy()
        self.strategy._persist_dry_run = self.dry_run
        self.similarity = SimilarityScorer()
        self.setup_learner = SetupLearner()
        self.risk = RiskManager()
        self.reentry_tracker = ReentryTracker()
        self.solana: Optional[SolanaClient] = None
        self.jupiter: Optional[JupiterExecutor] = None
        self.watchlist: List[MoverCandidate] = []
        self.watchlist_mint_statuses: List[dict] = []
        self.watchlist_mint_status: Optional[dict] = None
        self.sol_trade_status: Optional[dict] = None
        self.stable_quote_sol_status: Optional[dict] = None
        self.weth_trade_status: Optional[dict] = None
        self.scan_count: int = 0
        self.scan_in_progress: bool = False
        self.sol_trend_snapshot: dict = {}
        self.market_regime_snapshot: dict = {}
        self.session_entry_tuning: dict = {}

    def apply_session_key(self, private_key: str) -> None:
        """Hot-apply a session private key for live Jupiter auto-sign.

        Decodes base58 (or JSON byte array) into the SolanaClient keypair so
        swaps sign locally — no browser wallet popup. Safe to call while the
        bot loop is running; does not log the key.
        """
        key = (private_key or "").strip()
        if not key:
            raise ValueError("Private key is required")
        self._private_key = key
        if self.solana is not None:
            self.solana.apply_keypair(key)
            if self.jupiter is not None:
                self.jupiter.public_key = str(self.solana.public_key)
            self._record_action(
                f"Session wallet attached for auto-sign ({str(self.solana.public_key)[:8]}…)"
            )
        else:
            # initialize() has not run yet — key is retained for first SolanaClient build
            self._record_action("Session wallet stored for auto-sign (pending init)")

    def apply_rpc_endpoint(self, endpoint: Optional[str] = None) -> None:
        """Hot-apply RPC URL to the live SolanaClient (Config when endpoint omitted)."""
        if self.solana is None:
            return
        ep = self.solana.apply_rpc_endpoint(endpoint)
        self._record_action(f"RPC endpoint updated ({ep})")

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
        if "spike trap" in lower or "price spike" in lower:
            return "spike_trap"
        if "win-lean" in lower:
            return "win_lean"
        if "non-memecoin proxy" in lower or "asset sanity" in lower:
            return "asset_sanity"
        if "no momentum data" in lower:
            return "no_momentum_data"
        if "loss re-entry cooldown" in lower or "loss cooldown" in lower or "one-strike" in lower:
            return "loss_cooldown"
        if "re-chase" in lower:
            return "reentry_retry"
        if "sol macro" in lower:
            return "sol_trend"
        if "trade cooldown" in lower:
            return "trade_cooldown"
        if "watchlist gain" in lower or "watchlist mint excluded" in lower:
            return "watchlist_gain"
        if "wbtc:" in lower:
            return "wbtc_gate"
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
        reset_session_entry_tuning()
        reentry_retry_manager.reset_session()
        self.sol_trend_snapshot = get_sol_trend_snapshot(force_refresh=True)
        self.market_regime_snapshot = update_market_regime(
            self.sol_trend_snapshot, []
        )

        await self._restore_persisted_positions()

    async def _restore_persisted_positions(self) -> None:
        """Reload open books from disk and continue exit monitoring.

        Paper: restore simulated positions as stored.
        Live: reconcile remaining token qty against wallet; drop if gone.
        Exit rules (SL / instant profit / 15m / forced) are unchanged.
        """
        from position_store import load_open_positions, save_open_positions

        stored = load_open_positions(dry_run=self.dry_run)
        if not stored:
            return

        restored: List[Position] = []
        for pos in stored:
            if self.dry_run:
                restored.append(pos)
                continue
            if not self.solana:
                restored.append(pos)
                continue
            try:
                wallet_raw = await self.solana.get_token_balance_raw(pos.mint)
            except Exception as exc:
                logger.warning(
                    "Resume reconcile failed for %s: %s — keeping stored qty",
                    pos.symbol,
                    exc,
                )
                restored.append(pos)
                continue
            if wallet_raw <= 0:
                logger.warning(
                    "Resume: wallet has 0 tokens for %s (%s) — dropping from book",
                    pos.symbol,
                    pos.mint[:8],
                )
                continue
            if wallet_raw != pos.remaining_token_amount_raw:
                logger.info(
                    "Resume: reconciling %s token qty %d -> %d (wallet)",
                    pos.symbol,
                    pos.remaining_token_amount_raw,
                    wallet_raw,
                )
                pos.remaining_token_amount_raw = wallet_raw
                pos.token_amount_raw = wallet_raw
            restored.append(pos)

        count = self.strategy.restore_positions(restored)
        save_open_positions(restored, dry_run=self.dry_run)
        if count:
            symbols = ", ".join(p.symbol for p in restored)
            mode = "paper" if self.dry_run else "live"
            msg = f"Resumed {count} open {mode} position(s): {symbols}"
            logger.info(msg)
            self._record_action(msg)
            if not self.dry_run and not self._private_key and not Config.SOLANA_PRIVATE_KEY:
                logger.warning(
                    "Live positions restored for monitoring, but no session key — "
                    "re-Set Wallet (or set SOLANA_PRIVATE_KEY) before exits can sign"
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
        else:
            self._refresh_sol_trade_status()
            self.watchlist = merge_sol_trade_watchlist(
                self.watchlist,
                self.price_feed,
                sol_snapshot=self.sol_trend_snapshot,
            )
        self._merge_stable_quote_wsol_candidate()

    def _refresh_stable_quote_wsol_status(self) -> None:
        if not stable_quote_sol_wsol_path_active():
            self.stable_quote_sol_status = {"enabled": False, "path_active": False}
            return
        held = {p.mint for p in self.strategy.positions}
        self.stable_quote_sol_status = probe_stable_quote_wsol_status(
            self.price_feed,
            sol_snapshot=self.sol_trend_snapshot,
            held_mints=held,
            dry_run=self.dry_run,
        )

    def _merge_stable_quote_wsol_candidate(self) -> None:
        if not stable_quote_sol_wsol_path_active():
            self.stable_quote_sol_status = {"enabled": False, "path_active": False}
            return
        self._refresh_stable_quote_wsol_status()
        self.watchlist = merge_stable_quote_wsol_watchlist(
            self.watchlist,
            self.price_feed,
            sol_snapshot=self.sol_trend_snapshot,
            dry_run=self.dry_run,
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
        ranked = self._rank_movers(movers)
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
        ranked = self._rank_movers(movers)
        self.watchlist = ranked[: Config.WATCHLIST_TOP_N]

        self._merge_pinned_watchlist_mint()
        self._merge_sol_trade_candidate()
        self._merge_weth_trade_candidate()
        self.market_regime_snapshot = update_market_regime(
            self.sol_trend_snapshot, self.watchlist
        )
        target_wr = float(self.market_regime_snapshot.get("target_win_rate") or 0.55)
        tighten = maybe_auto_tighten(target_wr)
        if tighten.get("action") == "tightened":
            self._record_action(
                f"Session auto-tighten L{tighten.get('tighten_level')}: "
                f"WR {tighten.get('win_rate', 0) * 100:.0f}% < {target_wr * 100:.0f}% target — "
                f"win-lean={tighten.get('win_lean'):.2f}"
            )
        self.session_entry_tuning = session_entry_tuning_status(target_wr)

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

    def _companion_entry_pinned_mints(self) -> set[str]:
        """Pinned / proxy mints excluded from the companion memecoin candidate pool."""
        pinned = set(Config.watchlist_mints()) if Config.watchlist_mint_enabled() else set()
        if sol_trading_enabled():
            pinned.add(Config.SOL_TRADE_MINT)
        if weth_trading_enabled():
            pinned.add(Config.WETH_MINT)
        return pinned

    def _has_companion_memecoin_candidates(self, held_mints: set[str]) -> bool:
        """True when the watchlist still has non-anchor movers to evaluate for a 2nd leg."""
        pinned = self._companion_entry_pinned_mints()
        return any(
            c.mint not in pinned and c.mint not in held_mints for c in self.watchlist
        )

    def _trade_candidates(self) -> List[MoverCandidate]:
        """Ranked watchlist slice considered for new entries (max concurrent positions unchanged)."""
        pinned = self._companion_entry_pinned_mints()
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
        if (
            stable_quote_sol_wsol_entries_allowed(dry_run=self.dry_run)
            and self.stable_quote_sol_status
            and self.stable_quote_sol_status.get("qualifies")
        ):
            wsol_candidate = next(
                (
                    c
                    for c in self.watchlist
                    if c.mint == SOL_MINT
                    and getattr(c, "source", None) == "stable_quote_sol"
                ),
                None,
            )
            if wsol_candidate is None:
                wsol_candidate = next(
                    (c for c in self.watchlist if c.mint == SOL_MINT),
                    None,
                )
            if wsol_candidate and wsol_candidate not in qualified_pinned:
                qualified_pinned.append(wsol_candidate)
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

    def _stable_quote_mint_for_trade(self, mint: str) -> Optional[str]:
        """USDC/USDT mint when trading WSOL (So1111…112) under stable-quote mode."""
        if not is_stable_quote_wsol_mint(mint):
            return None
        return stable_quote_mint()

    def _jupiter_buy(
        self,
        token_mint: str,
        sol_amount: float,
        *,
        use_cache: bool = True,
    ):
        assert self.jupiter
        quote_mint = self._stable_quote_mint_for_trade(token_mint)
        if quote_mint:
            return self.jupiter.buy_token(
                token_mint,
                sol_amount,
                use_cache=use_cache,
                quote_mint=quote_mint,
                sol_price_usd=self._sol_price_usd(),
            )
        return self.jupiter.buy_token(token_mint, sol_amount, use_cache=use_cache)

    def _jupiter_sell(
        self,
        token_mint: str,
        token_amount_raw: int,
        *,
        use_cache: bool = False,
        allow_high_impact: bool = False,
    ):
        assert self.jupiter
        quote_mint = self._stable_quote_mint_for_trade(token_mint)
        if quote_mint:
            return self.jupiter.sell_token(
                token_mint,
                token_amount_raw,
                use_cache=use_cache,
                allow_high_impact=allow_high_impact,
                quote_mint=quote_mint,
                sol_price_usd=self._sol_price_usd(),
            )
        return self.jupiter.sell_token(
            token_mint,
            token_amount_raw,
            use_cache=use_cache,
            allow_high_impact=allow_high_impact,
        )

    def _quote_sol_flow(self, quote) -> tuple[float, float]:
        return quote_sol_flow(quote, sol_price_usd=self._sol_price_usd())

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

    async def _fetch_sell_quote(
        self,
        position: Position,
        token_raw: int,
        exit_signal: ExitSignal,
        *,
        preview_quote: Optional[SwapQuote] = None,
        use_cache: bool = True,
    ) -> Optional[SwapQuote]:
        """Fetch a sell quote; forced exits retry and accept high impact."""
        assert self.jupiter
        forced = self._is_forced_exit(exit_signal)

        if (
            preview_quote
            and token_raw == position.remaining_token_amount_raw
            and preview_quote.in_amount == token_raw
        ):
            if forced or self.jupiter.validate_quote(preview_quote):
                return preview_quote

        attempts = FORCED_SELL_QUOTE_RETRIES if forced else 1
        for attempt in range(1, attempts + 1):
            quote = self._jupiter_sell(
                position.mint,
                token_raw,
                use_cache=use_cache and attempt == 1,
                allow_high_impact=forced,
            )
            if quote:
                if attempt > 1:
                    logger.info(
                        "Sell quote recovered for %s %s on attempt %d/%d",
                        position.symbol,
                        exit_signal.signal_type.value,
                        attempt,
                        attempts,
                    )
                return quote
            if attempt < attempts:
                logger.warning(
                    "Sell quote failed for %s %s (attempt %d/%d) — retrying in %.0fms",
                    position.symbol,
                    exit_signal.signal_type.value,
                    attempt,
                    attempts,
                    FORCED_SELL_QUOTE_BACKOFF_SEC * 1000,
                )
                await asyncio.sleep(FORCED_SELL_QUOTE_BACKOFF_SEC)

        logger.error(
            "Sell stalled: no Jupiter quote for %s %s after %d attempt(s)",
            position.symbol,
            exit_signal.signal_type.value,
            attempts,
        )
        return None

    async def _execute_sell(
        self,
        position: Position,
        token_raw: int,
        quote: Optional[SwapQuote] = None,
        *,
        forced_exit: bool = False,
        exit_signal: Optional[ExitSignal] = None,
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
            if exit_signal is None:
                quote = self._jupiter_sell(
                    position.mint, token_raw, allow_high_impact=forced_exit
                )
            else:
                quote = await self._fetch_sell_quote(
                    position, token_raw, exit_signal, use_cache=False
                )
        if not quote:
            return None

        if not forced_exit:
            ok, reason = self.risk.pre_trade_check(
                await self.solana.get_balance(),
                quote.price_impact_pct,
                dry_run=self.dry_run,
            )
            if not ok:
                logger.warning(
                    "Sell blocked (non-forced): %s — %s",
                    position.symbol,
                    reason,
                )
                return None

        return await self.jupiter.execute_quote(
            quote, self.solana, forced_exit=forced_exit
        )

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
        return self._jupiter_sell(mint, token_l1, use_cache=True)

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
        fee_positive_only = (
            wbtc_profit_gate_applies(position.mint, exit_signal.signal_type.value)
            and threshold <= 0
        )
        if threshold <= 0 and not fee_positive_only:
            return True
        level_idx = (
            exit_signal.tp_level_index if exit_signal.is_partial else None
        )
        fees = self._preview_sell_fees(position, level_idx)
        sol_basis = entry_sol_basis(
            position.size_sol, token_raw, position.initial_token_amount_raw
        )
        _, sol_out = self._quote_sol_flow(quote)
        net = sol_out - sol_basis - fees
        effective_threshold = threshold if threshold > 0 else 1e-6
        if net < effective_threshold:
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

    def _rank_movers(self, movers: List[MoverCandidate]) -> List[MoverCandidate]:
        """Rank scanner candidates: setup learner when active, else similarity."""
        if Config.SETUP_LEARNING_ENABLED and self.setup_learner.learning_active:
            return self.setup_learner.rank(movers)
        return self.similarity.rank(movers)

    def _record_setup_learning(
        self,
        position: Position,
        profile,
        *,
        net_pnl_sol: float,
        exit_reason: Optional[str] = None,
        entry_route_labels=None,
        entry_price_impact_pct: Optional[float] = None,
    ) -> None:
        if not Config.SETUP_LEARNING_ENABLED:
            return
        hold_time_sec = time.time() - position.entry_time
        if profile is not None:
            hold_time_sec = getattr(profile, "hold_duration_sec", hold_time_sec)
        features = features_from_position_profile(
            position.profile,
            hold_time_sec=hold_time_sec,
            entry_price_impact_pct=entry_price_impact_pct
            or position.profile.get("entry_price_impact_pct"),
            route_labels=entry_route_labels,
            scanner_source=position.profile.get("scanner_source"),
        )
        profile_data = position.profile or {}
        features["h24_usd_gain"] = profile_data.get("day_usd_gain")
        features["dollar_gain_at_entry"] = profile_data.get("day_usd_gain")
        buy_impact = profile_data.get("entry_price_impact_pct")
        sell_impact = profile_data.get("exit_price_impact_pct")
        try:
            if buy_impact is not None and sell_impact is not None:
                features["round_trip_slippage_pct"] = float(buy_impact) + float(sell_impact)
            elif buy_impact is not None:
                features["round_trip_slippage_pct"] = float(buy_impact) * 2.0
        except (TypeError, ValueError):
            pass
        pnl_pct = profile.pnl_pct if profile is not None else position.pnl_pct(
            position.entry_price
        )
        self.setup_learner.record_completed_trade(
            features,
            net_pnl_sol,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            mint=position.mint,
            symbol=position.symbol,
        )

    def _record_completed_trade_outcome(
        self, mint: str, symbol: str, net_pnl_sol: float, *, loss_features: Optional[dict] = None
    ) -> None:
        if net_pnl_sol < 0:
            if reentry_retry_manager.is_active():
                handled = reentry_retry_manager.record_retry_outcome(
                    mint,
                    symbol=symbol,
                    won=False,
                    loss_signature=loss_features,
                )
                if handled:
                    self._record_action(
                        f"Re-chase retry loss on {symbol} — "
                        f"blocked {Config.REENTRY_RETRY_BLOCK_HOURS}h"
                    )
                    pending = reentry_retry_manager.get_pending_actions()
                    if any(p["mint"] == mint for p in pending):
                        self._record_action(
                            f"Action required: allow/deny re-chase for {symbol}"
                        )
            self.strategy.record_loss_reentry_cooldown(mint)
        elif net_pnl_sol > 0 and reentry_retry_manager.is_active():
            if reentry_retry_manager.record_retry_outcome(
                mint, symbol=symbol, won=True
            ):
                self.strategy.clear_mint_blocks(mint)
                self._record_action(f"Re-chase retry win on {symbol} — blocks cleared")
        self.risk.record_trade_outcome(net_pnl_sol, dry_run=self.dry_run)
        record_session_exit(net_pnl_sol)

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
            quote = self._jupiter_buy(position.mint, trade_size)
            jupiter_ok = quote is not None
        return can_afford, jupiter_ok

    def _executable_pnl_pct(
        self, position: Position, token_raw: int, quote: SwapQuote
    ) -> Optional[float]:
        """Net SOL PnL % from a Jupiter sell quote (executable, not chart mark)."""
        if token_raw <= 0 or position.initial_token_amount_raw <= 0:
            return None
        sol_basis = entry_sol_basis(
            position.size_sol, token_raw, position.initial_token_amount_raw
        )
        if sol_basis <= 0:
            return None
        fees = self._preview_sell_fees(position, None)
        _, sol_out = self._quote_sol_flow(quote)
        return (sol_out - sol_basis - fees) / sol_basis

    def _near_instant_profit_threshold(
        self, position: Position, current_price: float
    ) -> bool:
        """True when mark or peak is near the instant-exit zone — skip quote cache."""
        mark = position.pnl_pct(current_price)
        return (
            mark >= INSTANT_EXIT_NEAR_THRESHOLD_PCT
            or position.peak_pnl_pct >= INSTANT_EXIT_NEAR_THRESHOLD_PCT
        )

    def _worst_position_loss_pnl(
        self,
        position: Position,
        current_price: float,
        executable_pnl_pct: Optional[float] = None,
    ) -> float:
        """Worst (most negative) of mark, quote, and trough PnL."""
        mark = position.pnl_pct(current_price)
        sources = [mark, position.trough_pnl_pct]
        if executable_pnl_pct is not None:
            sources.append(executable_pnl_pct)
        return min(sources)

    def _at_stop_threshold(
        self,
        position: Position,
        current_price: float,
        executable_pnl_pct: Optional[float] = None,
    ) -> bool:
        """True when worst PnL source is at or past configured stop-loss."""
        if not stop_loss_applies_for_mint(position.mint):
            return False
        stop = effective_stop_loss_pct(position.mint)
        return self._worst_position_loss_pnl(
            position, current_price, executable_pnl_pct
        ) <= -stop

    def _track_stop_loss_alerts(
        self,
        position: Position,
        current_price: float,
        executable_pnl_pct: Optional[float] = None,
        *,
        at_stop_pending: bool = False,
    ) -> None:
        """WARNING at -1% crossing; ERROR when at stop without sell within 2 cycles."""
        if not Config.STOP_LOSS_NEVER_MISS:
            return
        if not stop_loss_applies_for_mint(position.mint):
            return
        mint = position.mint
        worst = self._worst_position_loss_pnl(
            position, current_price, executable_pnl_pct
        )
        stop = effective_stop_loss_pct(mint)
        prev = self._stop_alert_state.get(mint, "ok")
        if worst <= -stop:
            state = "at_stop"
        elif worst <= -STOP_APPROACHING_PCT:
            state = "approaching"
        else:
            state = "ok"
        if state == "approaching" and prev == "ok":
            logger.warning(
                "Approaching stop: %s mark=%.2f%% trough=%.2f%% quote=%s",
                position.symbol,
                position.pnl_pct(current_price) * 100,
                position.trough_pnl_pct * 100,
                f"{executable_pnl_pct * 100:.2f}%"
                if executable_pnl_pct is not None
                else "n/a",
            )
        if state == "at_stop" and at_stop_pending:
            cycles = self._pending_forced_exit.get(mint, 0)
            if cycles >= FORCED_EXIT_CRITICAL_CYCLES:
                logger.error(
                    "STOP LOSS NOT EXECUTED: %s still open after %d cycle(s) "
                    "at mark=%.2f%% trough=%.2f%% quote=%s stop=%.2f%%",
                    position.symbol,
                    cycles,
                    position.pnl_pct(current_price) * 100,
                    position.trough_pnl_pct * 100,
                    f"{executable_pnl_pct * 100:.2f}%"
                    if executable_pnl_pct is not None
                    else "n/a",
                    stop * 100,
                )
        self._stop_alert_state[mint] = state

    def _in_loss_zone(
        self,
        position: Position,
        current_price: float,
        executable_pnl_pct: Optional[float] = None,
    ) -> bool:
        """True when mark, quote, or trough shows loss past fresh-quote threshold."""
        if not stop_loss_applies_for_mint(position.mint):
            return False
        if not Config.STOP_LOSS_NEVER_MISS:
            mark = position.pnl_pct(current_price)
            threshold = -Config.LOSS_FRESH_QUOTE_PCT
            if mark <= threshold or position.trough_pnl_pct <= threshold:
                return True
            if executable_pnl_pct is not None and executable_pnl_pct <= threshold:
                return True
            return False
        threshold = -Config.LOSS_FRESH_QUOTE_PCT
        worst = self._worst_position_loss_pnl(
            position, current_price, executable_pnl_pct
        )
        return worst <= threshold

    def _refresh_executable_pnl(
        self,
        position: Position,
        token_raw: int,
        *,
        use_cache: bool,
    ) -> tuple[Optional[SwapQuote], Optional[float]]:
        preview_quote = self._jupiter_sell(
            position.mint,
            token_raw,
            use_cache=use_cache,
            allow_high_impact=True,
        )
        executable_pnl_pct = (
            self._executable_pnl_pct(position, token_raw, preview_quote)
            if preview_quote
            else None
        )
        if executable_pnl_pct is not None:
            position.bump_peak_pnl(executable_pnl_pct)
            position.bump_trough_pnl(executable_pnl_pct)
        return preview_quote, executable_pnl_pct

    def _position_monitor_interval(self, positions: List[Position]) -> float:
        """Poll faster when any open position is near instant-profit or in loss zone."""
        base = (
            Config.POSITION_MONITOR_SEC
            if positions
            else Config.PRICE_POLL_SEC
        )
        for position in positions:
            if position.peak_pnl_pct >= INSTANT_EXIT_NEAR_THRESHOLD_PCT:
                return min(float(base), INSTANT_PROFIT_MONITOR_SEC)
            if Config.STOP_LOSS_NEVER_MISS:
                if position.trough_pnl_pct <= -Config.LOSS_FRESH_QUOTE_PCT:
                    return min(float(base), LOSS_MONITOR_SEC)
                if self._pending_forced_exit.get(position.mint, 0) > 0:
                    return min(float(base), LOSS_MONITOR_SEC)
            elif position.trough_pnl_pct <= -Config.LOSS_FRESH_QUOTE_PCT:
                return min(float(base), LOSS_MONITOR_SEC)
        return float(base)

    def _track_forced_exit_pending(
        self, position: Position, *, executed: bool, current_price: float
    ) -> None:
        if executed:
            if position.remaining_token_amount_raw <= 0:
                self._pending_forced_exit.pop(position.mint, None)
                self._stop_alert_state.pop(position.mint, None)
            return
        cycles = self._pending_forced_exit.get(position.mint, 0) + 1
        self._pending_forced_exit[position.mint] = cycles
        stop = effective_stop_loss_pct(position.mint)
        if cycles >= FORCED_EXIT_CRITICAL_CYCLES and self._at_stop_threshold(
            position, current_price
        ):
            logger.critical(
                "FORCED EXIT NOT EXECUTED: %s still open after %d cycle(s) "
                "at mark=%.2f%% trough=%.2f%% — retrying every %.1fs",
                position.symbol,
                cycles,
                position.pnl_pct(current_price) * 100,
                position.trough_pnl_pct * 100,
                FORCED_EXIT_RETRY_INTERVAL_SEC,
            )

    def _track_instant_exit_pending(
        self, position: Position, *, executed: bool
    ) -> None:
        if executed:
            self._instant_exit_pending_cycles.pop(position.mint, None)
            return
        cycles = self._instant_exit_pending_cycles.get(position.mint, 0) + 1
        self._instant_exit_pending_cycles[position.mint] = cycles
        if cycles >= INSTANT_EXIT_CRITICAL_CYCLES:
            logger.critical(
                "INSTANT EXIT NOT EXECUTED: %s still open after %d monitor cycle(s) "
                "with peak=%.2f%% — profit target missed, retrying aggressively",
                position.symbol,
                cycles,
                position.peak_pnl_pct * 100,
            )

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
        trough_price = self.price_feed.get_trough_price_since(
            position.mint, position.entry_time
        )
        if trough_price and trough_price > 0:
            position.update_peak_pnl(trough_price)
        position.update_peak_pnl(current_price)

        preview_quote: Optional[SwapQuote] = None
        executable_pnl_pct: Optional[float] = None

        while allow_stopped or self.should_run():
            token_raw = position.remaining_token_amount_raw
            if token_raw <= 0:
                self._track_forced_exit_pending(
                    position, executed=True, current_price=current_price
                )
                return

            near_threshold = self._near_instant_profit_threshold(
                position, current_price
            )
            pending_instant = (
                self._instant_exit_pending_cycles.get(position.mint, 0) > 0
            )
            pending_forced = self._pending_forced_exit.get(position.mint, 0) > 0
            in_loss_zone = self._in_loss_zone(
                position, current_price, executable_pnl_pct
            )
            force_fresh_quote = (
                near_threshold or pending_instant or in_loss_zone or pending_forced
            )
            preview_quote, executable_pnl_pct = self._refresh_executable_pnl(
                position, token_raw, use_cache=not force_fresh_quote
            )
            in_loss_zone = self._in_loss_zone(
                position, current_price, executable_pnl_pct
            )
            at_stop = self._at_stop_threshold(
                position, current_price, executable_pnl_pct
            )
            if at_stop:
                self._pending_forced_exit[position.mint] = max(
                    1, self._pending_forced_exit.get(position.mint, 0)
                )
            self._track_stop_loss_alerts(
                position,
                current_price,
                executable_pnl_pct,
                at_stop_pending=at_stop
                or self._pending_forced_exit.get(position.mint, 0) > 0,
            )

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
                executable_pnl_pct=executable_pnl_pct,
            )
            if exit_signal is None and (
                at_stop or self._pending_forced_exit.get(position.mint, 0) > 0
            ):
                if stop_loss_applies_for_mint(position.mint):
                    exit_signal = ExitSignal(SignalType.SELL_SL)
            elif exit_signal is None and pending_forced:
                if stop_loss_applies_for_mint(position.mint):
                    exit_signal = ExitSignal(SignalType.SELL_SL)
            if exit_signal is None:
                pnl = position.pnl_pct(current_price)
                logger.debug(
                    "Holding %s pnl=%.4f peak=%.4f trough=%.4f quote=%s levels=%d/%d",
                    position.symbol,
                    pnl,
                    position.peak_pnl_pct,
                    position.trough_pnl_pct,
                    f"{executable_pnl_pct:.4f}"
                    if executable_pnl_pct is not None
                    else "n/a",
                    len(position.tp_levels_hit),
                    len(Config.TAKE_PROFIT_LEVELS),
                )
                return

            if exit_signal.signal_type == SignalType.SELL_INSTANT_PROFIT:
                logger.info(
                    "Instant profit exit executing: %s mark=%.4f peak=%.4f quote=%s",
                    position.symbol,
                    position.pnl_pct(current_price),
                    position.peak_pnl_pct,
                    f"{executable_pnl_pct:.4f}"
                    if executable_pnl_pct is not None
                    else "n/a",
                )

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
            forced = self._is_forced_exit(exit_signal)
            is_instant = exit_signal.signal_type == SignalType.SELL_INSTANT_PROFIT
            quote_attempts = (
                INSTANT_EXIT_CYCLE_RETRIES
                if is_instant
                else (FORCED_SELL_QUOTE_RETRIES if forced else 1)
            )
            quote_backoff = (
                INSTANT_EXIT_CYCLE_BACKOFF_SEC
                if is_instant
                else FORCED_SELL_QUOTE_BACKOFF_SEC
            )
            quote = None
            for quote_attempt in range(1, quote_attempts + 1):
                quote = await self._fetch_sell_quote(
                    position,
                    token_raw,
                    exit_signal,
                    preview_quote=preview_quote if not forced else None,
                    use_cache=False if forced else not force_fresh_quote,
                )
                preview_quote = None
                if quote:
                    break
                if quote_attempt < quote_attempts:
                    logger.warning(
                        "Sell quote failed for %s %s (cycle attempt %d/%d) — retrying in %.0fms",
                        position.symbol,
                        exit_signal.signal_type.value,
                        quote_attempt,
                        quote_attempts,
                        quote_backoff * 1000,
                    )
                    prices = self._fetch_position_prices([position.mint])
                    refreshed = prices.get(position.mint)
                    if refreshed:
                        current_price = refreshed
                        position.update_peak_pnl(current_price)
                    await asyncio.sleep(quote_backoff)
            if not quote:
                if is_instant:
                    self._track_instant_exit_pending(position, executed=False)
                if forced:
                    self._track_forced_exit_pending(
                        position, executed=False, current_price=current_price
                    )
                    logger.error(
                        "Sell stalled: no Jupiter quote for %s %s after %d attempt(s) — retrying",
                        position.symbol,
                        exit_signal.signal_type.value,
                        quote_attempts,
                    )
                    await asyncio.sleep(FORCED_EXIT_RETRY_INTERVAL_SEC)
                    prices = self._fetch_position_prices([position.mint])
                    refreshed = prices.get(position.mint)
                    if refreshed:
                        current_price = refreshed
                        position.update_peak_pnl(current_price)
                    continue
                logger.error(
                    "Sell stalled: no Jupiter quote for %s %s after %d cycle attempt(s)",
                    position.symbol,
                    exit_signal.signal_type.value,
                    quote_attempts,
                )
                return

            if not self._quote_meets_min_net(position, token_raw, quote, exit_signal):
                if wbtc_profit_gate_applies(
                    position.mint, exit_signal.signal_type.value
                ):
                    logger.info(
                        "WBTC hold: quote net not fee-positive — skipping %s",
                        exit_signal.signal_type.value,
                    )
                    return
                if exit_signal.signal_type == SignalType.SELL_INSTANT_PROFIT:
                    logger.warning(
                        "Instant exit min-net gate bypass failed for %s — forcing sell",
                        position.symbol,
                    )
                elif forced:
                    logger.warning(
                        "Forced exit min-net gate bypass for %s %s",
                        position.symbol,
                        exit_signal.signal_type.value,
                    )
                else:
                    logger.info(
                        "Exit skipped (min-net gate): %s %s",
                        position.symbol,
                        exit_signal.signal_type.value,
                    )
                    return

            if self._should_defer_exit_for_impact(position, quote, exit_signal):
                if is_instant or forced:
                    logger.warning(
                        "Exit impact defer rejected for %s — forcing sell",
                        position.symbol,
                    )
                else:
                    logger.warning(
                        "Exit skipped (impact defer — unexpected): %s %s impact=%.2f%%",
                        position.symbol,
                        exit_signal.signal_type.value,
                        quote.price_impact_pct,
                    )
                    return

            exec_attempts = (
                INSTANT_EXIT_CYCLE_RETRIES
                if is_instant
                else (FORCED_SELL_QUOTE_RETRIES if forced else 1)
            )
            signature = None
            for attempt in range(1, exec_attempts + 1):
                signature = await self._execute_sell(
                    position,
                    token_raw,
                    quote,
                    forced_exit=forced,
                )
                if signature:
                    if is_instant:
                        self._track_instant_exit_pending(position, executed=True)
                    if forced:
                        self._track_forced_exit_pending(
                            position, executed=True, current_price=current_price
                        )
                    break
                if attempt < exec_attempts:
                    logger.warning(
                        "Sell execution failed for %s %s (attempt %d/%d) — retrying",
                        position.symbol,
                        exit_signal.signal_type.value,
                        attempt,
                        exec_attempts,
                    )
                    quote = await self._fetch_sell_quote(
                        position, token_raw, exit_signal, use_cache=False
                    )
                    if not quote:
                        break
                    backoff = (
                        INSTANT_EXIT_CYCLE_BACKOFF_SEC
                        if is_instant
                        else FORCED_SELL_QUOTE_BACKOFF_SEC
                    )
                    await asyncio.sleep(backoff)
            if not signature:
                if is_instant:
                    self._track_instant_exit_pending(position, executed=False)
                if forced:
                    self._track_forced_exit_pending(
                        position, executed=False, current_price=current_price
                    )
                    logger.error(
                        "Sell stalled: execution failed for %s %s after %d attempt(s) — retrying",
                        position.symbol,
                        exit_signal.signal_type.value,
                        exec_attempts,
                    )
                    await asyncio.sleep(FORCED_EXIT_RETRY_INTERVAL_SEC)
                    prices = self._fetch_position_prices([position.mint])
                    refreshed = prices.get(position.mint)
                    if refreshed:
                        current_price = refreshed
                        position.update_peak_pnl(current_price)
                    continue
                logger.error(
                    "Sell stalled: execution failed for %s %s after %d attempt(s)",
                    position.symbol,
                    exit_signal.signal_type.value,
                    exec_attempts,
                )
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
                    self._record_setup_learning(
                        position,
                        profile,
                        net_pnl_sol=position.realized_net_pnl_sol,
                        exit_reason=partial_journal.get("reason"),
                    )
                    self._record_completed_trade_outcome(
                        position.mint,
                        position.symbol,
                        position.realized_net_pnl_sol,
                        loss_features=(
                            self._trade_loss_signature(position)
                            if position.realized_net_pnl_sol < 0
                            else None
                        ),
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
            self._record_setup_learning(
                position,
                profile,
                net_pnl_sol=position.realized_net_pnl_sol,
                exit_reason=sell_journal.get("reason"),
            )
            self._record_completed_trade_outcome(
                position.mint,
                position.symbol,
                position.realized_net_pnl_sol,
                loss_features=(
                    self._trade_loss_signature(position)
                    if position.realized_net_pnl_sol < 0
                    else None
                ),
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

        forced_signal = ExitSignal(SignalType.SELL_TIME)
        quote = await self._fetch_sell_quote(position, token_raw, forced_signal)
        if not quote:
            logger.warning("Force sell: no quote for %s", position.symbol)
            return None

        signature = await self._execute_sell(
            position, token_raw, quote, forced_exit=True
        )
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
        self._record_setup_learning(
            position,
            profile,
            net_pnl_sol=position.realized_net_pnl_sol,
            exit_reason=sell_journal.get("reason"),
        )
        self._record_completed_trade_outcome(
            position.mint,
            position.symbol,
            position.realized_net_pnl_sol,
            loss_features=(
                self._trade_loss_signature(position)
                if position.realized_net_pnl_sol < 0
                else None
            ),
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

            quote = await self._fetch_sell_quote(
                position,
                token_raw,
                ExitSignal(SignalType.SELL_TIME),
                use_cache=False,
            )
            if not quote:
                logger.warning("Session expiry: no sell quote for %s", position.symbol)
                continue

            signature = await self._execute_sell(
                position, token_raw, quote, forced_exit=True
            )
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
            self._record_setup_learning(
                position,
                profile,
                net_pnl_sol=position.realized_net_pnl_sol,
                exit_reason=sell_journal.get("reason"),
            )
            self._record_completed_trade_outcome(
                position.mint,
                position.symbol,
                position.realized_net_pnl_sol,
                loss_features=(
                    self._trade_loss_signature(position)
                    if position.realized_net_pnl_sol < 0
                    else None
                ),
            )

    def _trade_loss_signature(self, position: Position) -> dict:
        from setup_learner import features_from_position_profile, normalize_setup_features

        features = features_from_position_profile(
            position.profile,
            entry_price_impact_pct=position.profile.get("entry_price_impact_pct"),
            scanner_source=position.profile.get("scanner_source"),
        )
        return normalize_setup_features(features)

    async def _reentry_retry_slippage_ok(
        self, candidate: MoverCandidate
    ) -> tuple[bool, Optional[str]]:
        """Fresh Jupiter slippage / orderflow check before a re-chase retry entry."""
        assert self.solana and self.jupiter

        balance = await self.solana.get_balance()
        trade_size = self.risk.compute_trade_size(balance, dry_run=self.dry_run)
        if trade_size <= 0:
            return False, "trade size is zero"

        quote = self._jupiter_buy(candidate.mint, trade_size)
        if not quote:
            return False, f"no Jupiter route for {candidate.symbol}"

        sell_preview = self._preview_l1_sell_quote(candidate.mint, quote)
        full_sell_preview = self._jupiter_sell(
            candidate.mint, quote.out_amount, use_cache=True
        )
        if full_sell_preview:
            full_impact = abs(full_sell_preview.price_impact_pct)
            if full_impact > Config.MAX_FULL_EXIT_SELL_PREVIEW_IMPACT_PCT:
                return (
                    False,
                    f"full exit preview impact {full_impact:.2f}% > "
                    f"{Config.MAX_FULL_EXIT_SELL_PREVIEW_IMPACT_PCT:.1f}%",
                )
            stop = effective_stop_loss_pct(candidate.mint)
            sol_in, _ = self._quote_sol_flow(quote)
            _, sol_out = self._quote_sol_flow(full_sell_preview)
            if sol_in > 0 and stop_loss_applies_for_mint(candidate.mint):
                flat_fees = estimate_round_trip_fees_sol(
                    trade_size, quote.raw, full_sell_preview.raw
                )
                flat_pnl_pct = (sol_out - sol_in - flat_fees) / sol_in
                if flat_pnl_pct <= -stop:
                    return (
                        False,
                        f"flat-book sell preview loss {flat_pnl_pct * 100:.2f}% "
                        f"> stop {stop * 100:.2f}%",
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
            return False, check_reason or "entry eligibility failed"

        ok, check_reason = self.risk.pre_trade_check(
            balance,
            quote.price_impact_pct,
            dry_run=self.dry_run,
            max_impact_pct=Config.effective_max_entry_price_impact_pct(),
        )
        if not ok:
            return False, check_reason or "pre-trade impact check failed"
        return True, None

    async def _maybe_open_reentry_retry_window(
        self,
        candidate: MoverCandidate,
        current_price: float,
        momentum: Optional[float],
        *,
        usd_gain: Optional[float] = None,
    ) -> None:
        if not reentry_retry_manager.is_active():
            return
        if not self.strategy.is_on_loss_reentry_cooldown(
            candidate.mint, ignore_retry_bypass=True
        ):
            return
        if not reentry_retry_manager.can_open_retry_window(candidate.mint):
            return
        signal = self.strategy.evaluate_entry(
            candidate,
            current_price,
            momentum,
            usd_gain=usd_gain,
            sol_trend_snapshot=self.sol_trend_snapshot,
            setup_learner=self.setup_learner,
            skip_loss_cooldown_check=True,
        )
        if signal != SignalType.BUY:
            return
        slippage_ok, slip_reason = await self._reentry_retry_slippage_ok(candidate)
        if not slippage_ok:
            logger.debug(
                "Re-chase window not opened for %s: %s",
                candidate.symbol,
                slip_reason,
            )
            return
        signature = reentry_retry_manager.loss_signature_from_candidate(candidate)
        if reentry_retry_manager.open_retry_window(
            candidate.mint, candidate.symbol, signature
        ):
            self._record_action(
                f"Smart re-chase: 1h retry window for {candidate.symbol}"
            )

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
        is_reentry_retry: bool = False,
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

        quote = self._jupiter_buy(candidate.mint, trade_size)
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
        full_sell_preview = self._jupiter_sell(
            candidate.mint, quote.out_amount, use_cache=True
        )
        if full_sell_preview:
            full_impact = abs(full_sell_preview.price_impact_pct)
            if full_impact > Config.MAX_FULL_EXIT_SELL_PREVIEW_IMPACT_PCT:
                reason = (
                    f"full exit preview impact {full_impact:.2f}% > "
                    f"{Config.MAX_FULL_EXIT_SELL_PREVIEW_IMPACT_PCT:.1f}% "
                    f"for {candidate.symbol}"
                )
                self._note_entry_skip(reason)
                self._record_action(f"Entry skipped: {reason}")
                logger.warning("Buy blocked: %s", reason)
                return False
            stop = effective_stop_loss_pct(candidate.mint)
            sol_in, _ = self._quote_sol_flow(quote)
            _, sol_out = self._quote_sol_flow(full_sell_preview)
            if sol_in > 0 and stop_loss_applies_for_mint(candidate.mint):
                flat_fees = estimate_round_trip_fees_sol(
                    trade_size, quote.raw, full_sell_preview.raw
                )
                flat_pnl_pct = (sol_out - sol_in - flat_fees) / sol_in
                if flat_pnl_pct <= -stop:
                    reason = (
                        f"flat-book full sell preview loss {flat_pnl_pct * 100:.2f}% "
                        f"> stop {stop * 100:.2f}% for {candidate.symbol}"
                    )
                    self._note_entry_skip(reason)
                    self._record_action(f"Entry skipped: {reason}")
                    logger.warning("Buy blocked: %s", reason)
                    return False

        fee_budget, fee_breakdown = self._entry_fee_estimate(
            trade_size, quote, sell_preview
        )

        if is_wbtc_watchlist_mint(candidate.mint):
            from proxy_entry_gate import wbtc_instant_gain_feasible_from_quotes

            wbtc_ok, wbtc_reason = wbtc_instant_gain_feasible_from_quotes(
                trade_size,
                fee_budget,
                buy_impact_pct=quote.price_impact_pct,
                sell_preview_impact_pct=(
                    sell_preview.price_impact_pct if sell_preview else None
                ),
            )
            if not wbtc_ok:
                reason = wbtc_reason or "WBTC: instant target not feasible"
                self._note_entry_skip(reason)
                self._record_action(f"Entry skipped: {reason}")
                logger.warning("Buy blocked: %s", reason)
                return False

        if is_jitosol_trade_mint(candidate.mint):
            from proxy_entry_gate import jitosol_instant_gain_feasible_from_quotes

            jito_ok, jito_reason = jitosol_instant_gain_feasible_from_quotes(
                trade_size,
                fee_budget,
                buy_impact_pct=quote.price_impact_pct,
                sell_preview_impact_pct=(
                    sell_preview.price_impact_pct if sell_preview else None
                ),
            )
            if not jito_ok:
                reason = jito_reason or "JitoSOL: instant target not feasible"
                self._note_entry_skip(reason)
                self._record_action(f"Entry skipped: {reason}")
                logger.warning("Buy blocked: %s", reason)
                return False

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
        sol_in, _ = self._quote_sol_flow(quote)
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
        open_position = self.strategy.get_open_position()
        if open_position and open_position.mint == candidate.mint:
            from fee_estimator import extract_route_labels

            route_labels = extract_route_labels(quote.raw)
            open_position.profile["entry_price_impact_pct"] = quote.price_impact_pct
            open_position.profile["scanner_source"] = candidate.source
            open_position.profile["is_pumpfun_route"] = any(
                "Pump.fun" in str(label) for label in route_labels
            )
        if is_dip_reentry:
            self.strategy.traded_mints_cooldown.pop(candidate.mint, None)
        if is_reentry_retry:
            reentry_retry_manager.mark_retry_entry(candidate.mint)

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
            "re-chase retry" if is_reentry_retry else (
                "SOL trade" if candidate.source == "sol_trade" else (
                    "WETH trade" if candidate.source == "weth_trade" else (
                        "watchlist" if candidate.source == "watchlist_mint" else "momentum"
                    )
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

        quote = self._jupiter_buy(position.mint, trade_size)
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
        sol_in, _ = self._quote_sol_flow(quote)
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
                setup_learner=self.setup_learner,
            )
            if signal != SignalType.BUY:
                skip_reason = self.strategy.dip_reentry_skip_reason(
                    candidate, True, momentum=momentum,
                    sol_trend_snapshot=self.sol_trend_snapshot,
                    setup_learner=self.setup_learner,
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
            if reentry_retry_manager.is_active():
                denied, deny_reason = reentry_retry_manager.entry_denied_for_candidate(
                    candidate
                )
                if denied:
                    self._note_entry_skip(deny_reason)
                    continue
                await self._maybe_open_reentry_retry_window(
                    candidate,
                    current_price,
                    momentum,
                    usd_gain=candidate.day_usd_gain
                    if is_pinned_watchlist_mint(candidate.mint)
                    else None,
                )
            signal = self.strategy.evaluate_entry(
                candidate,
                current_price,
                momentum,
                usd_gain=candidate.day_usd_gain if is_pinned_watchlist_mint(candidate.mint) else None,
                sol_trend_snapshot=self.sol_trend_snapshot,
                setup_learner=self.setup_learner,
            )
            if signal != SignalType.BUY:
                skip_reason = self.strategy.entry_skip_reason(
                    candidate,
                    momentum,
                    usd_gain=candidate.day_usd_gain if is_pinned_watchlist_mint(candidate.mint) else None,
                    sol_trend_snapshot=self.sol_trend_snapshot,
                    setup_learner=self.setup_learner,
                )
                if skip_reason:
                    self._note_entry_skip(skip_reason)
                continue

            is_reentry_retry = reentry_retry_manager.is_retry_entry_pending(
                candidate.mint
            )
            if is_reentry_retry:
                slippage_ok, slip_reason = await self._reentry_retry_slippage_ok(
                    candidate
                )
                if not slippage_ok:
                    reason = (
                        f"re-chase retry slippage check failed: "
                        f"{slip_reason or 'unknown'} ({candidate.symbol})"
                    )
                    self._note_entry_skip(reason)
                    continue

            if await self._execute_entry(
                candidate,
                current_price,
                momentum,
                is_reentry_retry=is_reentry_retry,
            ):
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
                    if companion_slot_open(open_mints):
                        anchor = open_mints[0][:8]
                        if wbtc_companion_slot_open(open_mints):
                            logger.info(
                                "WBTC companion slot open — seeking 2nd trade"
                            )
                        elif proxy_companion_slot_open(open_mints):
                            logger.info(
                                "Proxy companion slot open — seeking 2nd trade"
                            )
                        else:
                            logger.info(
                                "Companion slot open (%s…) — seeking 2nd trade",
                                anchor,
                            )

                    await self._monitor_all_open_positions()
                    # Peak/trough and partial updates land here — keep disk book fresh.
                    try:
                        from position_store import save_open_positions

                        save_open_positions(
                            self.strategy.get_open_positions(),
                            dry_run=self.dry_run,
                        )
                    except Exception:
                        logger.exception("Failed to persist open positions after monitor")

                    if not self.should_run():
                        break

                    self.last_jupiter_health = get_jupiter_client().get_health()

                    if self.strategy.can_open_more():
                        held = {p.mint for p in self.strategy.positions}
                        if companion_slot_open(open_mints) and not self._has_companion_memecoin_candidates(
                            held
                        ):
                            await self._refresh_watchlist()
                            last_scan = now
                        await self._try_entry()
                    elif Config.watchlist_mint_enabled():
                        self._poll_pinned_watchlist_mint()

                    sleep_sec = self._position_monitor_interval(
                        self.strategy.positions
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
