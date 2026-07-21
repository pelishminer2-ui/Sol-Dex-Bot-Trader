import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import Config, max_allowed_open_positions, wbtc_min_net_win_threshold
from fee_estimator import (
    compute_take_profit_levels,
    expected_net_profit_sol,
    estimate_round_trip_fees_sol,
    trade_covers_l1_fees,
)

logger = logging.getLogger(__name__)

SKIP_REASON_PREFIX = "skipped: insufficient edge / high impact / low liquidity"


def _route_labels_list(labels: Optional[list]) -> list[str]:
    if not labels:
        return []
    return [str(label) for label in labels if label]


def is_pumpfun_amm_route(labels: Optional[list]) -> bool:
    """True when a Jupiter route plan includes Pump.fun Amm."""
    for label in _route_labels_list(labels):
        lower = label.lower()
        if "pump.fun" in lower or lower.startswith("pump"):
            return True
    return False


def round_trip_impact_pct(buy_impact_pct: float, sell_impact_pct: float) -> float:
    """Sum of absolute buy + sell-preview impacts for entry gating."""
    return abs(float(buy_impact_pct or 0.0)) + abs(float(sell_impact_pct or 0.0))


@dataclass
class RiskState:
    daily_loss_sol: float = 0.0
    day_start: float = field(default_factory=time.time)
    trades_today: int = 0
    consecutive_losses: int = 0
    consecutive_loss_pause_until: float = 0.0


class RiskManager:
    def __init__(self):
        self.state = RiskState()
        self.journal_path = Path(Config.TRADE_JOURNAL_PATH)

    def _reset_daily_if_needed(self):
        now = time.time()
        if now - self.state.day_start >= 86400:
            self.state = RiskState(day_start=now)

    @staticmethod
    def _uses_timed_consecutive_loss_pause(dry_run: bool) -> bool:
        if Config.MAX_CONSECUTIVE_LOSSES <= 0 or Config.CONSECUTIVE_LOSS_PAUSE_MINUTES <= 0:
            return False
        if Config.CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY:
            return dry_run
        return True

    @staticmethod
    def _uses_indefinite_consecutive_loss_pause(dry_run: bool) -> bool:
        if Config.MAX_CONSECUTIVE_LOSSES <= 0:
            return False
        return Config.CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY and not dry_run

    def _expire_consecutive_loss_pause_if_needed(self, dry_run: bool = True) -> None:
        if not self._uses_timed_consecutive_loss_pause(dry_run):
            return
        until = self.state.consecutive_loss_pause_until
        if until > 0 and time.time() >= until:
            self.state.consecutive_loss_pause_until = 0.0
            self.state.consecutive_losses = 0
            logger.info("Consecutive loss pause expired; counter reset")

    def _is_consecutive_loss_pause_active(self, dry_run: bool = True) -> bool:
        if Config.MAX_CONSECUTIVE_LOSSES <= 0:
            return False
        if self._uses_timed_consecutive_loss_pause(dry_run):
            return self.state.consecutive_loss_pause_until > time.time()
        if self._uses_indefinite_consecutive_loss_pause(dry_run):
            return self.state.consecutive_losses >= Config.MAX_CONSECUTIVE_LOSSES
        return False

    def _consecutive_loss_pause_reason(self, dry_run: bool = True) -> str:
        losses = self.state.consecutive_losses
        if self._uses_timed_consecutive_loss_pause(dry_run):
            minutes = self._pause_remaining_minutes(
                self.state.consecutive_loss_pause_until
            )
            return (
                f"paused after {losses} consecutive losses ({minutes}m remaining)"
            )
        return f"paused after {losses} consecutive losses (Stop/Start required)"

    @staticmethod
    def _pause_remaining_minutes(pause_until: float) -> int:
        remaining_sec = max(0.0, pause_until - time.time())
        return max(1, math.ceil(remaining_sec / 60.0))

    def consecutive_loss_pause_status(self, dry_run: bool = True) -> dict:
        """Expose pause state for GUI / status API."""
        self._expire_consecutive_loss_pause_if_needed(dry_run)
        until = self.state.consecutive_loss_pause_until
        active = self._is_consecutive_loss_pause_active(dry_run)
        timed = self._uses_timed_consecutive_loss_pause(dry_run)
        remaining_sec = max(0.0, until - time.time()) if active and timed else 0.0
        status = {
            "active": active,
            "remaining_sec": remaining_sec,
            "pause_until": until if active and timed else None,
            "consecutive_losses": self.state.consecutive_losses,
            "max_consecutive_losses": Config.MAX_CONSECUTIVE_LOSSES,
            "pause_minutes": Config.CONSECUTIVE_LOSS_PAUSE_MINUTES,
            "paper_only_timed_pause": Config.CONSECUTIVE_LOSS_PAUSE_PAPER_ONLY,
            "timed_pause": timed,
        }
        if active:
            status["message"] = self._consecutive_loss_pause_reason(dry_run)
        return status

    @staticmethod
    def effective_wallet_balance(
        wallet_balance_sol: Optional[float], dry_run: bool = False
    ) -> float:
        """Paper mode uses simulated balance; live uses min(wallet, tradeable cap)."""
        if dry_run:
            from paper_session import paper_session_manager

            return paper_session_manager.get_simulated_balance()
        wallet = float(wallet_balance_sol or 0.0)
        return min(wallet, Config.LIVE_TRADEABLE_BALANCE_SOL)

    @staticmethod
    def _min_fund_waiver_reason() -> str:
        from trade_activity import trade_activity

        if trade_activity.has_trades_in_last_hour():
            hours = Config.MIN_FUND_WAIVER_HOURS
            return (
                f"{Config.MIN_FUND_SOL:.2f} SOL minimum waived "
                f"(trade in journal within last {hours:g}h)"
            )
        if trade_activity.session_has_trades():
            return f"{Config.MIN_FUND_SOL:.2f} SOL minimum waived (active session)"
        return f"{Config.MIN_FUND_SOL:.2f} SOL minimum waived (recent trade activity)"

    def check_minimum_funding(
        self, wallet_balance_sol: Optional[float], dry_run: bool = False
    ) -> tuple[bool, str]:
        """Require MIN_FUND_SOL to start a trading session (not per-entry)."""
        from trade_activity import trade_activity

        trade_activity.refresh_from_journal()
        waived = self.min_fund_waived()
        if waived:
            if not dry_run and wallet_balance_sol is None:
                return (
                    False,
                    "cannot verify wallet balance for trade sizing "
                    f"({self._min_fund_waiver_reason()})",
                )
            balance = self.effective_wallet_balance(wallet_balance_sol, dry_run)
            trade_size = self.compute_trade_size(
                wallet_balance_sol if wallet_balance_sol is not None else 0.0,
                dry_run=dry_run,
            )
            if trade_size <= 0:
                return False, "insufficient SOL balance"
            reserve_plus_trade = Config.MIN_SOL_RESERVE + trade_size
            if not dry_run:
                if wallet_balance_sol < reserve_plus_trade:
                    return (
                        False,
                        f"insufficient SOL balance ({wallet_balance_sol:.4f} SOL < "
                        f"{reserve_plus_trade:.4f} SOL reserve + trade size; "
                        f"{self._min_fund_waiver_reason()})",
                    )
            elif balance < reserve_plus_trade:
                return (
                    False,
                    f"insufficient SOL balance ({balance:.4f} SOL < "
                    f"{reserve_plus_trade:.4f} SOL reserve + trade size; "
                    f"{self._min_fund_waiver_reason()})",
                )
            return True, f"ok ({self._min_fund_waiver_reason()})"
        balance = self.effective_wallet_balance(wallet_balance_sol, dry_run)
        if dry_run:
            paper_min = float(getattr(Config, "MIN_PAPER_FUND_SOL", 2.0) or 2.0)
            if balance < paper_min:
                from trade_activity import trade_activity

                detail = trade_activity.waiver_block_detail()
                return (
                    False,
                    f"paper simulated balance {balance:.4f} SOL is below minimum "
                    f"{paper_min:.2f} SOL required to start trading "
                    f"(waiver not applied: {detail})",
                )
            return True, "ok"
        if wallet_balance_sol is None:
            from trade_activity import trade_activity

            detail = trade_activity.waiver_block_detail()
            return (
                False,
                f"cannot verify wallet balance; minimum {Config.MIN_FUND_SOL:.2f} SOL "
                f"required to start live trading (waiver not applied: {detail})",
            )
        if wallet_balance_sol < Config.MIN_FUND_SOL:
            from trade_activity import trade_activity

            detail = trade_activity.waiver_block_detail()
            return (
                False,
                f"wallet balance {wallet_balance_sol:.4f} SOL is below minimum "
                f"{Config.MIN_FUND_SOL:.2f} SOL required to start live trading "
                f"(waiver not applied: {detail})",
            )
        return True, "ok"

    @staticmethod
    def min_fund_waived() -> bool:
        from trade_activity import trade_activity

        trade_activity.refresh_from_journal()
        return trade_activity.min_fund_waived()

    def can_start_trading(
        self, wallet_balance_sol: Optional[float], dry_run: bool = False
    ) -> tuple[bool, str]:
        """Gate Start Bot / session init only — not checked on each entry."""
        return self.check_minimum_funding(wallet_balance_sol, dry_run=dry_run)

    def can_open_position(
        self,
        open_positions: int,
        wallet_balance_sol: float,
        dry_run: bool = False,
        *,
        open_mints: Optional[list[str]] = None,
        candidate_mint: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Gate new entries — MIN_FUND_SOL applies at session start only (see min_fund_waived)."""
        self._reset_daily_if_needed()
        mints = open_mints or []
        position_limit = max_allowed_open_positions(mints, candidate_mint)
        if open_positions >= position_limit:
            return False, "max open positions reached"
        if self.state.daily_loss_sol >= Config.MAX_DAILY_LOSS_SOL:
            return False, "daily loss cap reached"
        blocked, pause_reason = self._consecutive_loss_entry_gate(dry_run)
        if blocked:
            return False, pause_reason
        effective = self.effective_wallet_balance(wallet_balance_sol, dry_run)
        trade_size = self.compute_trade_size(wallet_balance_sol, dry_run=dry_run)
        if trade_size <= 0:
            return False, "insufficient SOL balance"
        if not dry_run and wallet_balance_sol < Config.MIN_SOL_RESERVE + trade_size:
            return False, "insufficient SOL balance"
        if dry_run and effective < Config.MIN_SOL_RESERVE + trade_size:
            return False, "insufficient SOL balance"
        return True, "ok"

    def _consecutive_loss_entry_gate(
        self, dry_run: bool = True
    ) -> tuple[bool, str]:
        """Return (blocked, reason) for consecutive-loss entry pause."""
        self._expire_consecutive_loss_pause_if_needed(dry_run)
        if self._is_consecutive_loss_pause_active(dry_run):
            return True, self._consecutive_loss_pause_reason(dry_run)
        return False, ""

    def can_enter(
        self,
        open_positions: int,
        wallet_balance_sol: float,
        dry_run: bool = False,
        *,
        open_mints: Optional[list[str]] = None,
        candidate_mint: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Gate new entries including consecutive-loss pause (dry_run-aware)."""
        return self.can_open_position(
            open_positions,
            wallet_balance_sol,
            dry_run=dry_run,
            open_mints=open_mints,
            candidate_mint=candidate_mint,
        )

    def compute_trade_size(self, wallet_balance_sol: float, dry_run: bool = False) -> float:
        """Paper mode sizes from simulated balance; live uses min(wallet, tradeable cap)."""
        balance = self.effective_wallet_balance(wallet_balance_sol, dry_run)
        available = max(balance - Config.MIN_SOL_RESERVE, 0)
        wallet_cap = available * Config.MAX_WALLET_TRADE_PCT
        return min(Config.TRADE_SIZE_SOL, Config.MAX_POSITION_SOL, wallet_cap)

    def record_loss(self, loss_sol: float):
        self._reset_daily_if_needed()
        if loss_sol > 0:
            self.state.daily_loss_sol += loss_sol

    def record_trade_outcome(self, net_pnl_sol: float, dry_run: bool = False):
        """Track consecutive losing completed trades for entry pause."""
        self._reset_daily_if_needed()
        self.state.trades_today += 1
        if net_pnl_sol < 0:
            self.state.consecutive_losses += 1
            if Config.MAX_CONSECUTIVE_LOSSES > 0:
                logger.info(
                    "Consecutive losses: %d/%d",
                    self.state.consecutive_losses,
                    Config.MAX_CONSECUTIVE_LOSSES,
                )
                if self.state.consecutive_losses >= Config.MAX_CONSECUTIVE_LOSSES:
                    if self._uses_timed_consecutive_loss_pause(dry_run):
                        self.state.consecutive_loss_pause_until = (
                            time.time()
                            + Config.CONSECUTIVE_LOSS_PAUSE_MINUTES * 60
                        )
                        logger.info(
                            "Consecutive loss pause for %d minutes (paper)",
                            Config.CONSECUTIVE_LOSS_PAUSE_MINUTES,
                        )
                    elif self._uses_indefinite_consecutive_loss_pause(dry_run):
                        logger.info(
                            "Consecutive loss entry block — Stop/Start required (live)"
                        )
        else:
            self.state.consecutive_losses = 0
            self.state.consecutive_loss_pause_until = 0.0

    def should_auto_stop_daily_loss(self) -> bool:
        self._reset_daily_if_needed()
        return (
            Config.AUTO_STOP_ON_MAX_DAILY_LOSS
            and self.state.daily_loss_sol >= Config.MAX_DAILY_LOSS_SOL
        )

    def check_entry_eligibility(
        self,
        trade_size_sol: float,
        liquidity_usd: float,
        price_impact_pct: float,
        *,
        skip_liquidity: bool = False,
        mint: str = "",
        symbol: str = "",
        name: str = "",
        dex_labels: Optional[list] = None,
        jupiter_quote_buy=None,
        jupiter_quote_sell=None,
        sell_preview_impact_pct: Optional[float] = None,
        route_labels_buy: Optional[list] = None,
        route_labels_sell: Optional[list] = None,
    ) -> tuple[bool, str]:
        """
        Profit-first entry gate: expected net edge, entry impact, and liquidity.
        Returns (ok, reason) where reason uses the standard skip prefix when blocked.
        """
        from fee_estimator import extract_route_labels
        from stock_token_filter import is_stock_related_token, log_skipped_stock_token

        if mint and is_stock_related_token(
            mint=mint,
            symbol=symbol,
            name=name,
            dex_labels=dex_labels,
        ):
            log_skipped_stock_token(mint, symbol or mint[:8])
            return False, f"skipped stock-related token: {symbol or mint[:8]}"

        buy_impact = abs(float(price_impact_pct or 0.0))
        sell_impact = abs(
            float(
                sell_preview_impact_pct
                if sell_preview_impact_pct is not None
                else 0.0
            )
        )
        if route_labels_buy is None and jupiter_quote_buy is not None:
            route_labels_buy = extract_route_labels(jupiter_quote_buy)
        if route_labels_sell is None and jupiter_quote_sell is not None:
            route_labels_sell = extract_route_labels(jupiter_quote_sell)
        if sell_preview_impact_pct is None and jupiter_quote_sell is not None:
            raw_sell = jupiter_quote_sell
            if not isinstance(raw_sell, dict):
                raw_sell = getattr(jupiter_quote_sell, "raw", None)
            if isinstance(raw_sell, dict):
                sell_impact = abs(float(raw_sell.get("priceImpactPct") or 0.0))

        entry_max = Config.effective_max_entry_price_impact_pct()
        if buy_impact > entry_max:
            return (
                False,
                f"{SKIP_REASON_PREFIX} — buy price impact {buy_impact:.2f}% > "
                f"{entry_max:.1f}%",
            )
        if sell_impact > entry_max:
            return (
                False,
                f"{SKIP_REASON_PREFIX} — sell-preview price impact {sell_impact:.2f}% > "
                f"{entry_max:.1f}%",
            )

        round_trip = round_trip_impact_pct(buy_impact, sell_impact)
        if round_trip > Config.MAX_ROUND_TRIP_IMPACT_PCT:
            return (
                False,
                f"{SKIP_REASON_PREFIX} — round-trip impact {round_trip:.2f}% > "
                f"{Config.MAX_ROUND_TRIP_IMPACT_PCT:.1f}%",
            )

        pump_preview_max = Config.PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT
        if is_pumpfun_amm_route(route_labels_sell) and sell_impact > pump_preview_max:
            return (
                False,
                f"{SKIP_REASON_PREFIX} — Pump.fun Amm sell-preview impact "
                f"{sell_impact:.2f}% > {pump_preview_max:.2f}%",
            )
        if is_pumpfun_amm_route(route_labels_buy) and buy_impact > pump_preview_max:
            return (
                False,
                f"{SKIP_REASON_PREFIX} — Pump.fun Amm buy-route impact "
                f"{buy_impact:.2f}% > {pump_preview_max:.2f}%",
            )

        if not skip_liquidity and liquidity_usd > 0:
            min_liq = Config.effective_min_liquidity_usd()
            if liquidity_usd < min_liq:
                return (
                    False,
                    f"{SKIP_REASON_PREFIX} — liquidity ${liquidity_usd:,.0f} < "
                    f"${min_liq:,.0f}",
                )

        if trade_size_sol <= 0:
            return False, f"{SKIP_REASON_PREFIX} — trade size is zero"

        levels = compute_take_profit_levels(trade_size_sol)
        fee_budget = estimate_round_trip_fees_sol(
            trade_size_sol, jupiter_quote_buy, jupiter_quote_sell
        )
        expected_net = expected_net_profit_sol(
            trade_size_sol,
            levels,
            fee_budget_sol=fee_budget,
            jupiter_quote_buy=jupiter_quote_buy,
            jupiter_quote_sell=jupiter_quote_sell,
        )
        if expected_net < Config.MIN_EXPECTED_NET_PROFIT_SOL:
            return (
                False,
                f"{SKIP_REASON_PREFIX} — expected net {expected_net:.4f} SOL < "
                f"{Config.MIN_EXPECTED_NET_PROFIT_SOL:.4f} SOL (fees ~{fee_budget:.4f})",
            )

        l1_ok, l1_gross, l1_required = trade_covers_l1_fees(
            trade_size_sol,
            fee_budget_sol=fee_budget,
            jupiter_quote_buy=jupiter_quote_buy,
            jupiter_quote_sell=jupiter_quote_sell,
        )
        if not l1_ok:
            return (
                False,
                f"{SKIP_REASON_PREFIX} — trade too small: L1 gross {l1_gross:.4f} SOL < "
                f"L1 leg fees {l1_required:.4f} SOL",
            )

        max_level = max(levels) if levels else 0.0
        if max_level > Config.MAX_REALISTIC_TP_PCT:
            return (
                False,
                f"{SKIP_REASON_PREFIX} — trade size too small for realistic "
                f"{Config.MIN_EXPECTED_NET_PROFIT_SOL:.4f} SOL net target "
                f"(L4 would require +{max_level * 100:.0f}%)",
            )

        return True, "ok"

    def journal_write(self, event: dict):
        event["timestamp"] = time.time()
        try:
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except OSError as exc:
            logger.error("Failed to write trade journal: %s", exc)
            return
        try:
            from trade_activity import trade_activity

            trade_activity.record_trade(event)
        except Exception as exc:
            logger.debug("Trade activity record skipped: %s", exc)

    def pre_trade_check(
        self,
        wallet_balance_sol: float,
        price_impact_pct: float,
        dry_run: bool = False,
        *,
        max_impact_pct: Optional[float] = None,
    ) -> tuple[bool, str]:
        if not dry_run and wallet_balance_sol < Config.MIN_SOL_RESERVE:
            return False, "wallet below minimum SOL reserve"
        limit = (
            float(max_impact_pct)
            if max_impact_pct is not None
            else Config.MAX_ABSOLUTE_PRICE_IMPACT_PCT
        )
        if abs(price_impact_pct) > limit:
            return False, f"price impact {price_impact_pct:.2f}% too high (max {limit:.1f}%)"
        return True, "ok"

    @staticmethod
    def should_defer_exit_for_impact(
        mint: str,
        symbol: str,
        impact_pct: float,
        *,
        is_stop_loss: bool,
        defer_counts: dict[str, int],
        signal_name: str = "sell",
    ) -> tuple[bool, dict[str, int], bool]:
        """
        Return (defer, updated_counts, forced_high_impact).
        Exits execute immediately — log high slippage but never defer.
        """
        impact = abs(impact_pct)
        max_impact = Config.MAX_EXIT_PRICE_IMPACT_PCT
        counts = dict(defer_counts)
        counts.pop(mint, None)
        if impact > max_impact:
            logger.warning(
                "High exit impact for %s %s — impact %.2f%% > %.1f%% "
                "(executing immediately; slippage accepted)",
                signal_name,
                symbol,
                impact,
                max_impact,
            )
            return False, counts, impact > max_impact
        return False, counts, False

    def startup_banner(self, wallet: str, balance: float, dry_run: bool) -> str:
        mode = "DRY RUN" if dry_run else "LIVE"
        effective = self.effective_wallet_balance(balance, dry_run)
        if dry_run:
            balance_line = f"  Paper Balance: {effective:.4f} SOL (simulated)\n"
        else:
            balance_line = (
                f"  Wallet Balance: {balance:.4f} SOL\n"
                f"  Tradeable Balance: {effective:.4f} SOL (configured cap)\n"
            )
        return (
            f"\n{'=' * 50}\n"
            f"  Solana Mover Trading Bot [{mode}]\n"
            f"  Network: {Config.SOLANA_NETWORK}\n"
            f"  Wallet: {wallet}\n"
            f"{balance_line}"
            f"  Min funding (live): {Config.MIN_FUND_SOL:.2f} SOL\n"
            f"  Min funding (paper): {float(getattr(Config, 'MIN_PAPER_FUND_SOL', 2.0) or 2.0):.2f} SOL\n"
            f"  Max per trade: {Config.MAX_WALLET_TRADE_PCT * 100:.0f}% of wallet\n"
            f"  Max open positions: {Config.MAX_OPEN_POSITIONS} "
            f"({Config.MAX_OPEN_POSITIONS_WBTC} when WBTC in play)\n"
            f"  Re-entry dip: -{Config.REENTRY_DIP_PCT * 100:.0f}% from last exit\n"
            f"  Entry: +{Config.ENTRY_MOMENTUM_PCT * 100:.2f}% | "
            f"Min net edge: {Config.MIN_EXPECTED_NET_PROFIT_SOL:.4f} SOL | "
            f"Min net win: {Config.MIN_NET_WIN_SOL:.4f} SOL | "
            f"Loss re-entry cooldown: {Config.LOSS_REENTRY_COOLDOWN_MINUTES} min "
            f"(repeat {Config.LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES} min) | "
            f"Net TP target: {Config.TARGET_NET_PROFIT_SOL:.4f} SOL | "
            f"Entry impact max: {Config.effective_max_entry_price_impact_pct():.1f}% | "
            f"Exit impact log: {Config.MAX_EXIT_PRICE_IMPACT_PCT:.1f}% (no defer) | "
            f"Round-trip max: {Config.MAX_ROUND_TRIP_IMPACT_PCT:.1f}% | "
            f"Pool liquidity min: ${Config.effective_min_liquidity_usd():,.0f} | "
            f"Instant exits: +{Config.INSTANT_EXIT_3PCT * 100:.2f}% / +{Config.INSTANT_PROFIT_EXIT_PCT * 100:.0f}% full sell | "
            f"Early exit L{','.join(str(l) for l in Config.LADDER_EARLY_EXIT_LEVELS)} slowdown | "
            f"SL: -{Config.STOP_LOSS_PCT * 100:.2f}% (WBTC -{Config.WBTC_STOP_LOSS_PCT * 100:.2f}%)"
            + (
                f" | WBTC profit-only exits: min net {wbtc_min_net_win_threshold():.4f} SOL"
                if Config.WBTC_PROFIT_ONLY_EXITS
                else ""
            )
            + "\n"
            f"{'=' * 50}"
        )
