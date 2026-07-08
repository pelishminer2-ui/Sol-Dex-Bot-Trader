import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from config import (
    Config,
    WatchlistMintRule,
    can_open_more_positions,
    effective_stop_loss_pct,
    is_memecoin_standard_special_mint,
    is_non_memecoin_proxy_mint,
    is_sol_trade_mint,
    is_weth_trade_mint,
    is_wsol_trade_mint,
    is_wbtc_watchlist_mint,
    wbtc_min_net_win_threshold,
    wbtc_profit_gate_applies,
)
from entry_filters import entry_winrate_skip_reason
from fee_estimator import (
    compute_take_profit_levels,
    estimate_full_exit_net_sol,
    estimate_partial_net_win_sol,
    get_fee_budget,
)
from scanner import MoverCandidate
from watchlist_scanner import (
    get_watchlist_rule,
    is_pinned_watchlist_mint,
    watchlist_entry_qualifies,
)

logger = logging.getLogger(__name__)


class SignalType(Enum):
    NONE = "none"
    BUY = "buy"
    SELL_TP_PARTIAL = "sell_take_profit_partial"
    SELL_SL = "sell_stop_loss"
    SELL_L1_PROTECTION = "sell_l1_protection"
    SELL_TIME = "sell_time_stop"
    SELL_SLOWDOWN = "sell_slowdown"
    SELL_WEAKEN = "sell_trend_weaken_2pct"
    SELL_INSTANT_PROFIT = "sell_instant_5pct"
    SELL_MAX_HOLD_PROFIT = "sell_max_hold_profit"
    SELL_LADDER_MISSED_10M = "sell_ladder_missed_10m_positive"
    SELL_LADDER_MISSED_30M = "sell_ladder_missed_30m_negative"
    SELL_WATCHLIST_TARGET = "sell_watchlist_target"
    SELL_SOL_TREND_COLD = "sell_sol_trend_cold"
    BUY_DCA_LADDER_TIMEOUT = "buy_dca_3rd_ladder_timeout"


@dataclass
class ExitSignal:
    signal_type: SignalType
    tp_level_index: Optional[int] = None
    slowdown_after_level: Optional[int] = None
    needs_quote_check: bool = False

    @property
    def is_partial(self) -> bool:
        return self.signal_type == SignalType.SELL_TP_PARTIAL

    def with_wbtc_quote_check(self, position: "Position") -> "ExitSignal":
        """Flag quote verification when WBTC profit-only gate applies."""
        if wbtc_profit_gate_applies(position.mint, self.signal_type.value):
            self.needs_quote_check = True
        return self


@dataclass
class Position:
    mint: str
    symbol: str
    entry_price: float
    entry_time: float
    size_sol: float
    token_amount_raw: int = 0
    initial_token_amount_raw: int = 0
    remaining_token_amount_raw: int = 0
    token_decimals: Optional[int] = None
    tp_levels_hit: List[int] = field(default_factory=list)
    tp_levels: List[float] = field(default_factory=list)
    tp_portions: List[float] = field(default_factory=list)
    target_net_profit_sol: float = 0.0
    fee_budget_sol: float = 0.0
    estimated_fees_sol: float = 0.0
    fees_allocated_sol: float = 0.0
    realized_net_pnl_sol: float = 0.0
    momentum_at_entry: float = 0.0
    l1_protection_armed: bool = False
    peak_pnl_pct: float = 0.0
    trough_pnl_pct: float = 0.0
    profile: Dict[str, float] = field(default_factory=dict)
    buy_count: int = 1

    def update_peak_pnl(self, current_price: float) -> float:
        """Track highest PnL seen since entry (catches spikes between polls)."""
        pnl = self.pnl_pct(current_price)
        self.bump_peak_pnl(pnl)
        self.bump_trough_pnl(pnl)
        return pnl

    def bump_peak_pnl(self, pnl_pct: float) -> None:
        """Record highest PnL from any source (mark price, quote, etc.)."""
        if pnl_pct > self.peak_pnl_pct:
            self.peak_pnl_pct = pnl_pct

    def bump_trough_pnl(self, pnl_pct: float) -> None:
        """Record worst (most negative) PnL from any source."""
        if pnl_pct < self.trough_pnl_pct:
            self.trough_pnl_pct = pnl_pct

    @property
    def tp_level_count(self) -> int:
        return len(self.tp_levels) if self.tp_levels else len(Config.TAKE_PROFIT_LEVELS)

    @property
    def sol_invested(self) -> float:
        return self.size_sol

    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price

    @property
    def remaining_pct(self) -> float:
        if self.initial_token_amount_raw <= 0:
            return 0.0
        return self.remaining_token_amount_raw / self.initial_token_amount_raw


@dataclass
class TradeProfile:
    mint: str
    symbol: str
    momentum_pct: float
    liquidity_usd: float
    volume_24h_usd: float
    price_change_5m: float
    price_change_1h: float
    hold_duration_sec: float
    pnl_pct: float
    profitable: bool
    closed_at: float = field(default_factory=time.time)

    def to_vector(self) -> Dict[str, float]:
        return {
            "momentum_pct": self.momentum_pct,
            "liquidity_usd": math.log10(max(self.liquidity_usd, 1)),
            "volume_24h_usd": math.log10(max(self.volume_24h_usd, 1)),
            "price_change_5m": self.price_change_5m,
            "price_change_1h": self.price_change_1h,
            "hold_duration_sec": min(self.hold_duration_sec / 3600, 24),
        }


class MomentumStrategy:
    def __init__(self):
        self.positions: List[Position] = []
        self.last_profitable_profile: Optional[TradeProfile] = None
        self.traded_mints_cooldown: Dict[str, int] = {}
        self._loss_reentry_until: Dict[str, float] = {}
        self._loss_session_count: Dict[str, int] = {}
        self._one_strike_blocked: set[str] = set()

    def tick_cooldowns(self):
        expired = [mint for mint, cycles in self.traded_mints_cooldown.items() if cycles <= 1]
        for mint in expired:
            del self.traded_mints_cooldown[mint]
        for mint in list(self.traded_mints_cooldown.keys()):
            self.traded_mints_cooldown[mint] -= 1
        now = time.time()
        for mint in [m for m, until in self._loss_reentry_until.items() if until <= now]:
            del self._loss_reentry_until[mint]

    def record_loss_reentry_cooldown(self, mint: str):
        if Config.LOSS_REENTRY_COOLDOWN_MINUTES <= 0 and not Config.LOSS_ONE_STRIKE_PER_SESSION:
            return
        count = self._loss_session_count.get(mint, 0) + 1
        self._loss_session_count[mint] = count
        if Config.LOSS_ONE_STRIKE_PER_SESSION:
            self._one_strike_blocked.add(mint)
            logger.info(
                "One-strike rule: %s blocked for remainder of session (loss #%d)",
                mint[:8],
                count,
            )
        if Config.LOSS_REENTRY_COOLDOWN_MINUTES <= 0:
            return
        minutes = (
            Config.LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES
            if count >= 2 and Config.LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES > 0
            else Config.LOSS_REENTRY_COOLDOWN_MINUTES
        )
        until = time.time() + minutes * 60
        self._loss_reentry_until[mint] = until
        logger.info(
            "Loss re-entry cooldown: %s blocked for %d min (session loss #%d)",
            mint[:8],
            minutes,
            count,
        )

    def is_on_loss_reentry_cooldown(self, mint: str) -> bool:
        if Config.LOSS_ONE_STRIKE_PER_SESSION and mint in self._one_strike_blocked:
            return True
        until = self._loss_reentry_until.get(mint, 0.0)
        if until > time.time():
            return True
        if mint in self._loss_reentry_until:
            del self._loss_reentry_until[mint]
        return False

    def record_trade_cooldown(self, mint: str):
        self.traded_mints_cooldown[mint] = Config.TRADE_COOLDOWN_CYCLES

    def is_on_cooldown(self, mint: str) -> bool:
        return mint in self.traded_mints_cooldown

    def mint_block_status(
        self, mint: str, *, symbol: str = "", name: str = ""
    ) -> Dict[str, Any]:
        """Return active session-level blocks for a mint."""
        from stock_token_filter import is_stock_related_token

        blocks: List[str] = []
        if is_stock_related_token(mint=mint, symbol=symbol, name=name):
            blocks.append("stock_filter")
        if mint in self.traded_mints_cooldown:
            blocks.append("trade_cooldown")
        if Config.LOSS_ONE_STRIKE_PER_SESSION and mint in self._one_strike_blocked:
            blocks.append("one_strike")
        until = self._loss_reentry_until.get(mint, 0.0)
        if until > time.time():
            blocks.append("loss_reentry_cooldown")
        if self.is_holding_mint(mint):
            blocks.append("holding")
        return {
            "mint": mint,
            "blocked": bool(blocks),
            "blocks": blocks,
            "trade_cooldown_cycles": self.traded_mints_cooldown.get(mint),
            "loss_reentry_until": until if until > time.time() else None,
            "loss_session_count": self._loss_session_count.get(mint, 0),
        }

    def clear_mint_blocks(self, mint: str) -> Dict[str, Any]:
        """Clear session cooldowns and loss blocks for a mint."""
        cleared: List[str] = []
        if mint in self.traded_mints_cooldown:
            del self.traded_mints_cooldown[mint]
            cleared.append("trade_cooldown")
        if mint in self._loss_reentry_until:
            del self._loss_reentry_until[mint]
            cleared.append("loss_reentry_cooldown")
        if mint in self._one_strike_blocked:
            self._one_strike_blocked.discard(mint)
            cleared.append("one_strike")
        if mint in self._loss_session_count:
            del self._loss_session_count[mint]
            cleared.append("loss_session_count")
        if cleared:
            logger.info("Cleared mint blocks for %s: %s", mint[:8], ", ".join(cleared))
        return {"mint": mint, "cleared": cleared}

    def has_open_position(self) -> bool:
        return len(self.positions) > 0

    def get_open_position(self) -> Optional[Position]:
        return self.positions[0] if self.positions else None

    def get_open_positions(self) -> List[Position]:
        return list(self.positions)

    def open_position_count(self) -> int:
        return len(self.positions)

    def is_holding_mint(self, mint: str) -> bool:
        return any(p.mint == mint for p in self.positions)

    def can_open_more(self, candidate_mint: Optional[str] = None) -> bool:
        open_mints = [p.mint for p in self.positions]
        return can_open_more_positions(open_mints, candidate_mint)

    def evaluate_entry(
        self,
        candidate: MoverCandidate,
        current_price: float,
        momentum: Optional[float],
        usd_gain: Optional[float] = None,
        *,
        sol_trend_snapshot: Optional[dict] = None,
        setup_learner=None,
    ) -> SignalType:
        from sol_trend_filter import memecoin_entry_allowed_by_sol_trend
        from stock_token_filter import is_stock_related_token, log_skipped_stock_token

        if is_stock_related_token(
            mint=candidate.mint,
            symbol=candidate.symbol,
            name=candidate.name,
        ):
            log_skipped_stock_token(candidate.mint, candidate.symbol)
            return SignalType.NONE
        if self.is_holding_mint(candidate.mint):
            return SignalType.NONE
        if not self.can_open_more(candidate.mint):
            return SignalType.NONE
        if self.is_on_cooldown(candidate.mint):
            return SignalType.NONE
        if self.is_on_loss_reentry_cooldown(candidate.mint):
            logger.debug(
                "Entry blocked (loss cooldown): %s",
                candidate.symbol,
            )
            return SignalType.NONE
        if is_wsol_trade_mint(candidate.mint) or is_weth_trade_mint(candidate.mint):
            if momentum is None:
                return SignalType.NONE
            if momentum >= Config.effective_entry_momentum_pct():
                label = "WSOL" if is_wsol_trade_mint(candidate.mint) else "WETH"
                logger.info(
                    "Buy signal (%s): momentum=%.4f price=%.8f",
                    label,
                    momentum,
                    current_price,
                )
                return SignalType.BUY
            return SignalType.NONE
        if is_sol_trade_mint(candidate.mint):
            from sol_trading import sol_entry_qualifies

            if sol_entry_qualifies(sol_trend_snapshot):
                snap = sol_trend_snapshot or {}
                logger.info(
                    "Buy signal (SOL trade): proxy %s SOL 1h=%+.2f%% price=%.8f",
                    candidate.mint[:8],
                    snap.get("sol_trend_1h_pct") or 0.0,
                    current_price,
                )
                return SignalType.BUY
            return SignalType.NONE
        if not is_pinned_watchlist_mint(candidate.mint) and not is_memecoin_standard_special_mint(
            candidate.mint
        ):
            allowed, _reason = memecoin_entry_allowed_by_sol_trend(
                sol_trend_snapshot, candidate=candidate
            )
            if not allowed:
                logger.info(
                    "Entry blocked (SOL macro): %s — %s",
                    candidate.symbol,
                    _reason,
                )
                return SignalType.NONE
        if is_pinned_watchlist_mint(candidate.mint):
            rule = get_watchlist_rule(candidate.mint)
            day_gain = (
                usd_gain
                if usd_gain is not None
                else getattr(candidate, "day_usd_gain", None)
            )
            if day_gain is None:
                day_gain = candidate.usd_gain_baseline
            day_pct = getattr(candidate, "day_pct_gain", None)
            if watchlist_entry_qualifies(
                rule,
                day_usd_gain=day_gain,
                day_pct_gain=day_pct,
                usd_gain=day_gain,
            ):
                if rule and rule.min_day_pct_gain is not None:
                    logger.info(
                        "Buy signal (watchlist mint): %s day_pct_gain=%.2f%% price=%.8f",
                        candidate.symbol,
                        (day_pct or 0.0) * 100,
                        current_price,
                    )
                else:
                    logger.info(
                        "Buy signal (watchlist mint): %s day_usd_gain=$%.2f price=%.8f",
                        candidate.symbol,
                        day_gain if day_gain is not None else 0.0,
                        current_price,
                    )
                return SignalType.BUY
            return SignalType.NONE
        if momentum is None:
            return SignalType.NONE
        if momentum >= Config.effective_entry_momentum_pct():
            # Route/asset sanity: SOL/JitoSOL/WETH proxies must only enter via
            # their dedicated enabled paths, never as random momentum picks.
            if is_non_memecoin_proxy_mint(candidate.mint):
                logger.info(
                    "Entry blocked (asset sanity): %s is a non-memecoin proxy",
                    candidate.symbol,
                )
                return SignalType.NONE
            reason = entry_winrate_skip_reason(candidate, setup_learner)
            if reason:
                logger.info("Entry blocked (win-rate filter): %s", reason)
                return SignalType.NONE
            logger.info(
                "Buy signal (mover): %s momentum=%.4f price=%.8f",
                candidate.symbol,
                momentum,
                current_price,
            )
            return SignalType.BUY
        return SignalType.NONE

    def entry_skip_reason(
        self,
        candidate: MoverCandidate,
        momentum: Optional[float],
        usd_gain: Optional[float] = None,
        *,
        sol_trend_snapshot: Optional[dict] = None,
        setup_learner=None,
    ) -> Optional[str]:
        """Human-readable reason when evaluate_entry would not buy."""
        from sol_trend_filter import memecoin_entry_allowed_by_sol_trend
        from stock_token_filter import is_stock_related_token

        if is_stock_related_token(
            mint=candidate.mint,
            symbol=candidate.symbol,
            name=candidate.name,
        ):
            return f"skipped stock-related token: {candidate.symbol}"
        if self.is_holding_mint(candidate.mint):
            return f"already holding {candidate.symbol}"
        if not self.can_open_more(candidate.mint):
            return "max open positions reached"
        if self.is_on_cooldown(candidate.mint):
            return f"trade cooldown active: {candidate.symbol}"
        if self.is_on_loss_reentry_cooldown(candidate.mint):
            if (
                Config.LOSS_ONE_STRIKE_PER_SESSION
                and candidate.mint in self._one_strike_blocked
            ):
                return f"one-strike loss block: {candidate.symbol}"
            return f"loss re-entry cooldown: {candidate.symbol}"
        if is_wsol_trade_mint(candidate.mint) or is_weth_trade_mint(candidate.mint):
            if momentum is None:
                return f"no momentum data: {candidate.symbol}"
            if momentum < Config.effective_entry_momentum_pct():
                label = "WSOL" if is_wsol_trade_mint(candidate.mint) else "WETH"
                return (
                    f"entry momentum {momentum * 100:.2f}% < "
                    f"{Config.effective_entry_momentum_pct() * 100:.2f}%: {label}"
                )
            return None
        if is_sol_trade_mint(candidate.mint):
            from sol_trading import sol_entry_skip_reason

            reason = sol_entry_skip_reason(sol_trend_snapshot)
            return reason
        if not is_pinned_watchlist_mint(candidate.mint) and not is_memecoin_standard_special_mint(
            candidate.mint
        ):
            allowed, sol_reason = memecoin_entry_allowed_by_sol_trend(
                sol_trend_snapshot, candidate=candidate
            )
            if not allowed and sol_reason:
                return sol_reason
        if is_pinned_watchlist_mint(candidate.mint):
            rule = get_watchlist_rule(candidate.mint)
            day_gain = (
                usd_gain
                if usd_gain is not None
                else getattr(candidate, "day_usd_gain", None)
            )
            if day_gain is None:
                day_gain = candidate.usd_gain_baseline
            day_pct = getattr(candidate, "day_pct_gain", None)
            if watchlist_entry_qualifies(
                rule,
                day_usd_gain=day_gain,
                day_pct_gain=day_pct,
                usd_gain=day_gain,
            ):
                return None
            return f"watchlist gain below threshold: {candidate.symbol}"
        if momentum is None:
            return f"no momentum data: {candidate.symbol}"
        if momentum < Config.effective_entry_momentum_pct():
            return (
                f"entry momentum {momentum * 100:.2f}% < "
                f"{Config.effective_entry_momentum_pct() * 100:.2f}%: {candidate.symbol}"
            )
        if is_non_memecoin_proxy_mint(candidate.mint):
            return (
                f"non-memecoin proxy excluded from momentum entry: {candidate.symbol}"
            )
        return entry_winrate_skip_reason(candidate, setup_learner)

    def dip_reentry_skip_reason(
        self,
        candidate: MoverCandidate,
        dip_triggered: bool,
        momentum: Optional[float] = None,
        *,
        sol_trend_snapshot: Optional[dict] = None,
        setup_learner=None,
    ) -> Optional[str]:
        """Human-readable reason when evaluate_dip_reentry would not buy."""
        from sol_trend_filter import memecoin_entry_allowed_by_sol_trend
        from stock_token_filter import is_stock_related_token

        if not dip_triggered:
            return None
        if is_stock_related_token(
            mint=candidate.mint,
            symbol=candidate.symbol,
            name=candidate.name,
        ):
            return f"skipped stock-related token: {candidate.symbol}"
        if is_pinned_watchlist_mint(candidate.mint):
            return f"watchlist mint excluded from dip re-entry: {candidate.symbol}"
        if is_sol_trade_mint(candidate.mint):
            return f"SOL trade proxy excluded from dip re-entry: {candidate.symbol}"
        if is_weth_trade_mint(candidate.mint):
            return f"WETH excluded from dip re-entry: {candidate.symbol}"
        if self.is_on_loss_reentry_cooldown(candidate.mint):
            if (
                Config.LOSS_ONE_STRIKE_PER_SESSION
                and candidate.mint in self._one_strike_blocked
            ):
                return f"one-strike loss block: {candidate.symbol}"
            return f"loss re-entry cooldown: {candidate.symbol}"
        allowed, sol_reason = memecoin_entry_allowed_by_sol_trend(
            sol_trend_snapshot, candidate=candidate
        )
        if not allowed and sol_reason:
            return sol_reason
        min_reentry_momentum = Config.REENTRY_MIN_MOMENTUM_PCT
        if min_reentry_momentum > 0 and (
            momentum is None or momentum < min_reentry_momentum
        ):
            mom = momentum if momentum is not None else -1.0
            return (
                f"dip re-entry momentum {mom * 100:.2f}% < "
                f"{min_reentry_momentum * 100:.2f}%: {candidate.symbol}"
            )
        if self.is_holding_mint(candidate.mint):
            return f"already holding {candidate.symbol}"
        if not self.can_open_more(candidate.mint):
            return "max open positions reached"
        return entry_winrate_skip_reason(candidate, setup_learner)

    def evaluate_dip_reentry(
        self,
        candidate: MoverCandidate,
        current_price: float,
        dip_triggered: bool,
        momentum: Optional[float] = None,
        *,
        sol_trend_snapshot: Optional[dict] = None,
        setup_learner=None,
    ) -> SignalType:
        from sol_trend_filter import memecoin_entry_allowed_by_sol_trend
        from stock_token_filter import is_stock_related_token, log_skipped_stock_token

        if not dip_triggered:
            return SignalType.NONE
        if is_stock_related_token(
            mint=candidate.mint,
            symbol=candidate.symbol,
            name=candidate.name,
        ):
            log_skipped_stock_token(candidate.mint, candidate.symbol)
            return SignalType.NONE
        if is_pinned_watchlist_mint(candidate.mint):
            return SignalType.NONE
        if is_sol_trade_mint(candidate.mint):
            return SignalType.NONE
        if is_weth_trade_mint(candidate.mint):
            return SignalType.NONE
        if self.is_on_loss_reentry_cooldown(candidate.mint):
            logger.debug(
                "Dip re-entry blocked (loss cooldown): %s",
                candidate.symbol,
            )
            return SignalType.NONE
        allowed, _reason = memecoin_entry_allowed_by_sol_trend(
            sol_trend_snapshot, candidate=candidate
        )
        if not allowed:
            logger.info(
                "Dip re-entry blocked (SOL macro): %s — %s",
                candidate.symbol,
                _reason,
            )
            return SignalType.NONE
        min_reentry_momentum = Config.REENTRY_MIN_MOMENTUM_PCT
        if min_reentry_momentum > 0:
            if momentum is None or momentum < min_reentry_momentum:
                logger.debug(
                    "Dip re-entry blocked (momentum): %s %.4f < %.4f",
                    candidate.symbol,
                    momentum if momentum is not None else -1.0,
                    min_reentry_momentum,
                )
                return SignalType.NONE
        if self.is_holding_mint(candidate.mint):
            return SignalType.NONE
        if not self.can_open_more(candidate.mint):
            return SignalType.NONE
        reason = entry_winrate_skip_reason(candidate, setup_learner)
        if reason:
            logger.info("Dip re-entry blocked (win-rate filter): %s", reason)
            return SignalType.NONE
        logger.info(
            "Dip re-entry signal: %s price=%.8f (-%.0f%% from last exit)",
            candidate.symbol,
            current_price,
            Config.REENTRY_DIP_PCT * 100,
        )
        return SignalType.BUY

    def _position_tp_levels(self, position: Position) -> List[float]:
        return position.tp_levels or Config.TAKE_PROFIT_LEVELS

    def _position_tp_portions(self, position: Position) -> List[float]:
        return position.tp_portions or Config.TAKE_PROFIT_PORTIONS

    def _next_tp_level_index(self, position: Position) -> Optional[int]:
        levels = self._position_tp_levels(position)
        for i in range(len(levels)):
            if i not in position.tp_levels_hit:
                return i
        return None

    def partial_sell_amount_raw(self, position: Position, level_index: int) -> int:
        portions = self._position_tp_portions(position)
        if level_index < 0 or level_index >= len(portions):
            return 0
        portion = portions[level_index]
        target = int(position.initial_token_amount_raw * portion)
        return min(target, position.remaining_token_amount_raw)

    def _remaining_fraction(self, position: Position) -> float:
        if position.initial_token_amount_raw <= 0:
            return 0.0
        return position.remaining_token_amount_raw / position.initial_token_amount_raw

    def _min_net_win_threshold(self, position: Position) -> float:
        """Min net SOL before voluntary exit; 0 disables the gate."""
        if is_wbtc_watchlist_mint(position.mint) and Config.WBTC_PROFIT_ONLY_EXITS:
            return wbtc_min_net_win_threshold()
        if Config.MIN_NET_WIN_SOL <= 0:
            return 0.0
        return Config.MIN_NET_WIN_SOL

    def _meets_min_net_win(
        self,
        position: Position,
        pnl: float,
        *,
        level_index: Optional[int] = None,
        level_pct: Optional[float] = None,
    ) -> bool:
        """True when estimated net profit meets the voluntary-exit threshold."""
        threshold = self._min_net_win_threshold(position)
        if threshold <= 0:
            return True
        if level_index is not None and level_pct is not None:
            est_net = estimate_partial_net_win_sol(
                position.size_sol,
                level_index,
                level_pct,
                position.tp_levels,
                position.tp_portions,
                fee_budget_sol=position.fee_budget_sol,
            )
        else:
            est_net = estimate_full_exit_net_sol(
                position.size_sol,
                self._remaining_fraction(position),
                pnl,
                position.fees_allocated_sol,
                position.fee_budget_sol,
            )
        if est_net < threshold:
            if is_wbtc_watchlist_mint(position.mint) and Config.WBTC_PROFIT_ONLY_EXITS:
                logger.info(
                    "WBTC hold: est net %.4f SOL < min %.4f SOL after fees",
                    est_net,
                    threshold,
                )
            else:
                logger.info(
                    "Fee-aware hold: %s est net %.4f SOL < min %.4f SOL",
                    position.symbol,
                    est_net,
                    threshold,
                )
            return False
        return True

    def _early_exit_level_indices(self) -> List[int]:
        """0-based TP indices that trigger momentum slowdown checks (L2 -> 1, L3 -> 2)."""
        return [level - 1 for level in Config.LADDER_EARLY_EXIT_LEVELS if level >= 2]

    def _detect_momentum_weakening(
        self,
        mint: str,
        position: Position,
        price_feed,
        current_price: float,
    ) -> bool:
        """Core trend-weakening detection shared by ladder and global profit exits."""
        recent = price_feed.get_window_momentum(mint, end_offset_sec=0, window_sec=30)
        prior = price_feed.get_window_momentum(mint, end_offset_sec=30, window_sec=30)
        if recent is None:
            return False

        peak = price_feed.get_peak_momentum_since(mint, position.entry_time)
        peak_candidates = [
            abs(x)
            for x in (
                peak,
                position.momentum_at_entry,
                abs(prior) if prior is not None else None,
                abs(recent),
            )
            if x is not None and x > 0
        ]
        if not peak_candidates:
            return False
        peak_momentum = max(peak_candidates)

        threshold = Config.MOMENTUM_SLOWDOWN_PCT
        if abs(recent) < peak_momentum * threshold:
            logger.info(
                "Momentum slowdown: %s recent=%.4f peak=%.4f (<%d%%)",
                position.symbol,
                recent,
                peak_momentum,
                int(threshold * 100),
            )
            return True

        if prior is not None and prior > 0 and recent < prior * threshold:
            logger.info(
                "Momentum slowdown: %s recent=%.4f prior=%.4f (decay >%d%%)",
                position.symbol,
                recent,
                prior,
                int((1 - threshold) * 100),
            )
            return True

        if price_feed.momentum_declining_streak(mint, Config.PRICE_POLL_SEC, min_streak=2):
            logger.info(
                "Momentum slowdown: %s consecutive poll decline",
                position.symbol,
            )
            return True

        return False

    def _ladder_never_hit(self, position: Position) -> bool:
        return len(position.tp_levels_hit) == 0

    def _prefer_dca_on_ladder_timeout(
        self,
        position: Position,
        current_price: float,
        price_feed=None,
        mint: Optional[str] = None,
        current_liquidity_usd: Optional[float] = None,
        *,
        can_afford_dca: bool = True,
        jupiter_route_ok: bool = True,
    ) -> bool:
        """True = scale in (DCA); False = full exit (least resistance)."""
        if position.buy_count >= Config.MAX_BUYS_PER_MINT:
            return False
        if not can_afford_dca or not jupiter_route_ok:
            return False

        entry_liq = position.profile.get("liquidity_usd", 0.0)
        if (
            current_liquidity_usd is not None
            and entry_liq > 0
            and current_liquidity_usd < entry_liq * 0.85
        ):
            logger.info(
                "Ladder timeout sell: %s liquidity dropped %.0f -> %.0f",
                position.symbol,
                entry_liq,
                current_liquidity_usd,
            )
            return False

        if price_feed and mint:
            if self._detect_momentum_weakening(mint, position, price_feed, current_price):
                return False
            recent = price_feed.get_window_momentum(mint, end_offset_sec=0, window_sec=30)
            if recent is not None and recent < Config.effective_entry_momentum_pct() * 0.5:
                return False
            prior = price_feed.get_window_momentum(mint, end_offset_sec=30, window_sec=30)
            if recent is not None and recent >= Config.effective_entry_momentum_pct():
                return True
            if (
                prior is not None
                and recent is not None
                and recent > prior
                and recent > 0
            ):
                return True

        pnl = position.pnl_pct(current_price)
        if pnl > -0.01:
            return True

        return False

    def evaluate_momentum_slowdown(
        self,
        mint: str,
        position: Position,
        price_feed,
        current_price: float,
    ) -> bool:
        if not position.tp_levels_hit:
            return False
        if position.pnl_pct(current_price) <= 0:
            return False

        last_hit = max(position.tp_levels_hit)
        if last_hit not in self._early_exit_level_indices():
            return False

        return self._detect_momentum_weakening(mint, position, price_feed, current_price)

    def _evaluate_instant_exits(
        self,
        position: Position,
        pnl: float,
        effective_pnl: float,
        *,
        executable_pnl_pct: Optional[float] = None,
    ) -> Optional[ExitSignal]:
        """Full exit at +5% (fast spike) or +3.25% — mark, peak, or quote PnL."""
        if not Config.INSTANT_PROFIT_EXIT_ENABLED:
            return None
        trigger_pnl = effective_pnl
        if executable_pnl_pct is not None:
            trigger_pnl = max(trigger_pnl, executable_pnl_pct)
        quote_label = (
            f"{executable_pnl_pct:.4f}" if executable_pnl_pct is not None else "n/a"
        )
        if trigger_pnl >= Config.INSTANT_PROFIT_EXIT_PCT:
            logger.info(
                "INSTANT EXIT TRIGGERED (+5%%): %s mark=%.4f peak=%.4f quote=%s >= %.2f%%",
                position.symbol,
                pnl,
                position.peak_pnl_pct,
                quote_label,
                Config.INSTANT_PROFIT_EXIT_PCT * 100,
            )
            return ExitSignal(SignalType.SELL_INSTANT_PROFIT)
        if trigger_pnl >= Config.INSTANT_EXIT_3PCT:
            logger.info(
                "INSTANT EXIT TRIGGERED (+3.25%%): %s mark=%.4f peak=%.4f quote=%s >= %.2f%%",
                position.symbol,
                pnl,
                position.peak_pnl_pct,
                quote_label,
                Config.INSTANT_EXIT_3PCT * 100,
            )
            return ExitSignal(SignalType.SELL_INSTANT_PROFIT)
        return None

    def _log_hold_reason_if_profitable(
        self,
        position: Position,
        pnl: float,
        hold_sec: float,
        *,
        executable_pnl_pct: Optional[float] = None,
    ) -> None:
        """Debug why a position with meaningful profit is still held."""
        peak = position.peak_pnl_pct
        if pnl < 0.03 and peak < 0.03:
            if executable_pnl_pct is None or executable_pnl_pct < 0.03:
                return

        reasons: List[str] = []
        if Config.INSTANT_PROFIT_EXIT_ENABLED:
            effective = max(pnl, peak)
            if executable_pnl_pct is not None:
                effective = max(effective, executable_pnl_pct)
            if effective < Config.INSTANT_EXIT_3PCT:
                quote_note = (
                    f" quote={executable_pnl_pct:.2%}"
                    if executable_pnl_pct is not None
                    else ""
                )
                reasons.append(
                    f"instant needs >={Config.INSTANT_EXIT_3PCT:.2%} "
                    f"(+5% spike at {Config.INSTANT_PROFIT_EXIT_PCT:.2%}) "
                    f"(current={pnl:.2%} peak={peak:.2%}{quote_note})"
                )
        else:
            reasons.append("instant exit disabled")

        next_level = self._next_tp_level_index(position)
        if next_level is not None:
            levels = self._position_tp_levels(position)
            reasons.append(f"next ladder L{next_level + 1} at {levels[next_level]:.2%}")

        if position.l1_protection_armed:
            reasons.append(
                f"L1 protection armed (+{Config.L1_PROTECTION_PCT * 100:.2f}% floor)"
            )

        logger.info(
            "Holding %s pnl=%.2f%% peak=%.2f%% held=%.0fs — %s",
            position.symbol,
            pnl * 100,
            peak * 100,
            hold_sec,
            "; ".join(reasons) if reasons else "no exit rule matched",
        )

    def _position_is_green_at_max_hold(
        self,
        pnl: float,
        *,
        executable_pnl_pct: Optional[float] = None,
        peak_pnl: float = 0.0,
    ) -> bool:
        """True when mark, quote, or peak PnL is positive at max-hold evaluation."""
        if pnl > 0:
            return True
        if executable_pnl_pct is not None and executable_pnl_pct > 0:
            return True
        if peak_pnl > 0:
            return True
        return False

    def _evaluate_max_hold_exit(
        self,
        position: Position,
        pnl: float,
        hold_sec: float,
        *,
        executable_pnl_pct: Optional[float] = None,
        trough_pnl: Optional[float] = None,
    ) -> Optional[ExitSignal]:
        """Force exit non-WBTC positions after MAX_HOLD_MINUTES_NON_WBTC.

        At the time cap: honor stop loss first (worst of mark/quote/trough), then
        sell regardless of green/red when above stop — not a stop-loss exit.
        """
        if not Config.MAX_HOLD_ENABLED:
            return None
        if is_wbtc_watchlist_mint(position.mint):
            return None
        max_hold_sec = Config.MAX_HOLD_MINUTES_NON_WBTC * 60
        if hold_sec < max_hold_sec:
            return None

        stop_signal = self._evaluate_stop_loss(
            position,
            pnl,
            executable_pnl_pct=executable_pnl_pct,
            trough_pnl=trough_pnl,
        )
        if stop_signal is not None:
            return stop_signal

        if self._position_is_green_at_max_hold(
            pnl,
            executable_pnl_pct=executable_pnl_pct,
            peak_pnl=position.peak_pnl_pct,
        ):
            logger.info(
                "Max hold time exit (profit): %s held=%.0fs mark=%.4f peak=%.4f quote=%s",
                position.symbol,
                hold_sec,
                pnl,
                position.peak_pnl_pct,
                f"{executable_pnl_pct:.4f}" if executable_pnl_pct is not None else "n/a",
            )
            return ExitSignal(SignalType.SELL_MAX_HOLD_PROFIT)

        logger.info(
            "Max hold time exit: %s held=%.0fs pnl=%.4f",
            position.symbol,
            hold_sec,
            pnl,
        )
        return ExitSignal(SignalType.SELL_TIME)

    def _evaluate_stop_loss(
        self,
        position: Position,
        mark_pnl: float,
        *,
        executable_pnl_pct: Optional[float] = None,
        trough_pnl: Optional[float] = None,
    ) -> Optional[ExitSignal]:
        """Stop on worst of mark, quote, and trough PnL (catches stale feeds)."""
        stop = effective_stop_loss_pct(position.mint)
        emergency = Config.EMERGENCY_STOP_LOSS_PCT
        catastrophic = Config.CATASTROPHIC_STOP_LOSS_PCT

        sources: List[float] = [mark_pnl]
        if executable_pnl_pct is not None:
            sources.append(executable_pnl_pct)
        if trough_pnl is not None:
            sources.append(trough_pnl)
        worst = min(sources)

        def _fmt_sources() -> str:
            parts = [f"mark={mark_pnl:.4f}"]
            if executable_pnl_pct is not None:
                parts.append(f"quote={executable_pnl_pct:.4f}")
            if trough_pnl is not None:
                parts.append(f"trough={trough_pnl:.4f}")
            return " ".join(parts)

        if worst <= -catastrophic:
            logger.critical(
                "CATASTROPHIC stop loss: %s %s catastrophic=%.2f%% — force sell",
                position.symbol,
                _fmt_sources(),
                catastrophic * 100,
            )
            return ExitSignal(SignalType.SELL_SL)

        if worst <= -emergency:
            logger.warning(
                "Emergency stop loss: %s %s emergency=%.2f%%",
                position.symbol,
                _fmt_sources(),
                emergency * 100,
            )
            return ExitSignal(SignalType.SELL_SL)

        if worst <= -stop:
            logger.info(
                "Stop loss: %s %s stop=%.2f%%",
                position.symbol,
                _fmt_sources(),
                stop * 100,
            )
            return ExitSignal(SignalType.SELL_SL)

        return None

    def _evaluate_watchlist_override_exit(
        self,
        position: Position,
        pnl: float,
        rule: WatchlistMintRule,
        *,
        executable_pnl_pct: Optional[float] = None,
    ) -> Optional[ExitSignal]:
        """Custom exit: hold until sell_at_pct; stop-loss still applies."""
        stop_signal = self._evaluate_stop_loss(
            position,
            pnl,
            executable_pnl_pct=executable_pnl_pct,
            trough_pnl=position.trough_pnl_pct,
        )
        if stop_signal is not None:
            return stop_signal
        if rule.sell_at_pct is not None and pnl >= rule.sell_at_pct - 1e-9:
            logger.info(
                "Watchlist target exit: %s pnl=%.4f >= %.2f%%",
                position.symbol,
                pnl,
                rule.sell_at_pct * 100,
            )
            return ExitSignal(SignalType.SELL_WATCHLIST_TARGET)
        return None

    def evaluate_exit(
        self,
        position: Position,
        current_price: float,
        price_feed=None,
        mint: Optional[str] = None,
        current_liquidity_usd: Optional[float] = None,
        *,
        can_afford_dca: bool = True,
        jupiter_route_ok: bool = True,
        sol_trend_snapshot: Optional[dict] = None,
        executable_pnl_pct: Optional[float] = None,
    ) -> Optional[ExitSignal]:
        if position.remaining_token_amount_raw <= 0:
            return None

        pnl = position.update_peak_pnl(current_price)
        effective_pnl = max(pnl, position.peak_pnl_pct)
        if executable_pnl_pct is not None:
            position.bump_peak_pnl(executable_pnl_pct)
            position.bump_trough_pnl(executable_pnl_pct)
            effective_pnl = max(effective_pnl, executable_pnl_pct)
        hold_sec = time.time() - position.entry_time
        ladder_missed = self._ladder_never_hit(position)

        trough_pnl = position.trough_pnl_pct

        # Stop loss ALWAYS wins. Evaluate it FIRST — before L1 protection,
        # instant-profit, max-hold, and every other profit/time/peak exit —
        # on the worst of mark/quote/trough PnL. A position that previously
        # peaked green but has since reversed to/through the stop must exit as
        # SELL_SL and can never be diverted into an instant-profit (peak-based)
        # or other voluntary exit. Never miss a stop.
        stop_signal = self._evaluate_stop_loss(
            position,
            pnl,
            executable_pnl_pct=executable_pnl_pct,
            trough_pnl=trough_pnl,
        )
        if stop_signal is not None:
            return stop_signal

        if (
            Config.ENABLE_L1_PROTECTION
            and position.l1_protection_armed
            and self._position_tp_levels(position)
            and pnl <= Config.L1_PROTECTION_PCT
        ):
            logger.info(
                "L1 protection: %s pnl=%.4f <= +%.2f%% floor",
                position.symbol,
                pnl,
                Config.L1_PROTECTION_PCT * 100,
            )
            return ExitSignal(SignalType.SELL_L1_PROTECTION)

        if is_sol_trade_mint(position.mint) and not is_wsol_trade_mint(position.mint):
            from sol_trading import sol_trend_exit_cold

            instant_signal = self._evaluate_instant_exits(
                position, pnl, effective_pnl, executable_pnl_pct=executable_pnl_pct
            )
            if instant_signal is not None:
                return instant_signal
            if sol_trend_exit_cold(sol_trend_snapshot):
                snap = sol_trend_snapshot or {}
                logger.info(
                    "SOL trade trend-cold exit: %s pnl=%.4f SOL 1h=%s",
                    position.symbol,
                    pnl,
                    snap.get("sol_trend_1h_pct"),
                )
                return ExitSignal(SignalType.SELL_SOL_TREND_COLD)
        else:
            instant_signal = self._evaluate_instant_exits(
                position, pnl, effective_pnl, executable_pnl_pct=executable_pnl_pct
            )
            if instant_signal is not None:
                return instant_signal

        max_hold_signal = self._evaluate_max_hold_exit(
            position,
            pnl,
            hold_sec,
            executable_pnl_pct=executable_pnl_pct,
            trough_pnl=trough_pnl,
        )
        if max_hold_signal is not None:
            return max_hold_signal

        rule = get_watchlist_rule(position.mint)
        if rule and rule.override_ladder:
            return self._evaluate_watchlist_override_exit(
                position, pnl, rule, executable_pnl_pct=executable_pnl_pct
            )

        if Config.ENABLE_LADDER_TIME_EXITS and ladder_missed:
            dca_sec = Config.LADDER_MISSED_NEGATIVE_DCA_MINUTES * 60
            pos_exit_sec = Config.LADDER_MISSED_POSITIVE_EXIT_MINUTES * 60
            if hold_sec >= dca_sec and pnl <= 0:
                if self._prefer_dca_on_ladder_timeout(
                    position,
                    current_price,
                    price_feed=price_feed,
                    mint=mint,
                    current_liquidity_usd=current_liquidity_usd,
                    can_afford_dca=can_afford_dca,
                    jupiter_route_ok=jupiter_route_ok,
                ):
                    logger.info(
                        "Ladder timeout DCA: %s held=%.0fs pnl=%.4f buy_count=%d",
                        position.symbol,
                        hold_sec,
                        pnl,
                        position.buy_count,
                    )
                    return ExitSignal(SignalType.BUY_DCA_LADDER_TIMEOUT)
                else:
                    logger.info(
                        "Ladder timeout sell (negative): %s held=%.0fs pnl=%.4f",
                        position.symbol,
                        hold_sec,
                        pnl,
                    )
                    return ExitSignal(SignalType.SELL_LADDER_MISSED_30M)
            if hold_sec >= pos_exit_sec and pnl > 0:
                if not self._meets_min_net_win(position, pnl):
                    self._log_hold_reason_if_profitable(position, pnl, hold_sec, executable_pnl_pct=executable_pnl_pct)
                    return None
                logger.info(
                    "Ladder timeout sell (positive): %s held=%.0fs pnl=%.4f",
                    position.symbol,
                    hold_sec,
                    pnl,
                )
                return ExitSignal(SignalType.SELL_LADDER_MISSED_10M).with_wbtc_quote_check(
                    position
                )

        if hold_sec >= Config.TIME_STOP_MINUTES * 60:
            if not (
                is_wbtc_watchlist_mint(position.mint)
                and ladder_missed
                and pnl <= 0
            ):
                if pnl > 0 and not self._meets_min_net_win(position, pnl):
                    self._log_hold_reason_if_profitable(position, pnl, hold_sec, executable_pnl_pct=executable_pnl_pct)
                    return None
                logger.info("Time stop: %s held=%.0fs pnl=%.4f", position.symbol, hold_sec, pnl)
                return ExitSignal(SignalType.SELL_TIME).with_wbtc_quote_check(position)

        if (
            price_feed
            and mint
            and position.tp_levels_hit
            and self._position_tp_levels(position)
        ):
            last_hit = max(position.tp_levels_hit)
            if last_hit in self._early_exit_level_indices():
                if self.evaluate_momentum_slowdown(mint, position, price_feed, current_price):
                    if not self._meets_min_net_win(position, pnl):
                        self._log_hold_reason_if_profitable(position, pnl, hold_sec, executable_pnl_pct=executable_pnl_pct)
                        return None
                    return ExitSignal(
                        SignalType.SELL_SLOWDOWN,
                        slowdown_after_level=last_hit + 1,
                    ).with_wbtc_quote_check(position)

        if (
            Config.WEAKEN_EXIT_ENABLED
            and price_feed
            and mint
            and pnl >= Config.WEAKEN_EXIT_MIN_PROFIT_PCT
            and self._detect_momentum_weakening(mint, position, price_feed, current_price)
        ):
            if not self._meets_min_net_win(position, pnl):
                self._log_hold_reason_if_profitable(position, pnl, hold_sec, executable_pnl_pct=executable_pnl_pct)
                return None
            logger.info(
                "Trend weaken exit: %s pnl=%.4f >= %.2f%%",
                position.symbol,
                pnl,
                Config.WEAKEN_EXIT_MIN_PROFIT_PCT * 100,
            )
            return ExitSignal(SignalType.SELL_WEAKEN).with_wbtc_quote_check(position)

        next_level = self._next_tp_level_index(position)
        if next_level is None:
            return None

        levels = self._position_tp_levels(position)
        level_pct = levels[next_level]
        if pnl >= level_pct:
            if not self._meets_min_net_win(
                position, pnl, level_index=next_level, level_pct=level_pct
            ):
                self._log_hold_reason_if_profitable(position, pnl, hold_sec, executable_pnl_pct=executable_pnl_pct)
                return None
            logger.info(
                "Take profit ladder level %d: %s pnl=%.4f target=%.4f net_target=%.4f fees=%.4f",
                next_level,
                position.symbol,
                pnl,
                level_pct,
                position.target_net_profit_sol,
                position.fee_budget_sol,
            )
            return ExitSignal(
                SignalType.SELL_TP_PARTIAL, tp_level_index=next_level
            ).with_wbtc_quote_check(position)

        self._log_hold_reason_if_profitable(position, pnl, hold_sec, executable_pnl_pct=executable_pnl_pct)
        return None

    def open_position(
        self,
        candidate: MoverCandidate,
        entry_price: float,
        size_sol: float,
        momentum: float,
        token_amount_raw: int = 0,
        token_decimals: Optional[int] = None,
        *,
        fee_budget_sol: Optional[float] = None,
        estimated_fees_sol: Optional[float] = None,
    ) -> Position:
        tp_levels = compute_take_profit_levels(size_sol)
        tp_portions = list(Config.TAKE_PROFIT_PORTIONS)
        fees = fee_budget_sol if fee_budget_sol is not None else get_fee_budget(size_sol)
        position = Position(
            mint=candidate.mint,
            symbol=candidate.symbol,
            entry_price=entry_price,
            entry_time=time.time(),
            size_sol=size_sol,
            token_amount_raw=token_amount_raw,
            initial_token_amount_raw=token_amount_raw,
            remaining_token_amount_raw=token_amount_raw,
            token_decimals=token_decimals,
            tp_levels=tp_levels,
            tp_portions=tp_portions,
            target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
            fee_budget_sol=fees,
            estimated_fees_sol=estimated_fees_sol if estimated_fees_sol is not None else fees,
            momentum_at_entry=momentum,
            profile=candidate.to_profile(),
            buy_count=1,
        )
        self.positions.append(position)
        self.record_trade_cooldown(candidate.mint)
        return position

    def apply_dca_to_position(
        self,
        position: Position,
        add_sol: float,
        add_token_raw: int,
        add_entry_price: float,
    ) -> None:
        """Scale into an existing position (same mint, increments buy_count)."""
        decimals = position.token_decimals or 0
        scale = 10**decimals if decimals else 1
        old_ui = position.remaining_token_amount_raw / scale
        add_ui = add_token_raw / scale
        total_ui = old_ui + add_ui
        if total_ui > 0 and add_entry_price > 0:
            position.entry_price = (
                old_ui * position.entry_price + add_ui * add_entry_price
            ) / total_ui
        position.size_sol += add_sol
        position.initial_token_amount_raw += add_token_raw
        position.remaining_token_amount_raw += add_token_raw
        position.token_amount_raw = position.remaining_token_amount_raw
        position.buy_count += 1
        position.entry_time = time.time()
        position.peak_pnl_pct = 0.0
        position.trough_pnl_pct = 0.0
        position.tp_levels = compute_take_profit_levels(position.size_sol)
        position.fee_budget_sol = get_fee_budget(position.size_sol)
        logger.info(
            "DCA applied to %s: buy_count=%d size_sol=%.4f tokens=%d",
            position.symbol,
            position.buy_count,
            position.size_sol,
            position.remaining_token_amount_raw,
        )

    def apply_partial_tp(
        self,
        position: Position,
        level_index: int,
        sold_raw: int,
        exit_price: float,
    ) -> Optional[TradeProfile]:
        if level_index not in position.tp_levels_hit:
            position.tp_levels_hit.append(level_index)
            position.tp_levels_hit.sort()

        position.remaining_token_amount_raw = max(0, position.remaining_token_amount_raw - sold_raw)
        position.token_amount_raw = position.remaining_token_amount_raw

        if (
            level_index == 0
            and Config.ENABLE_L1_PROTECTION
            and position.remaining_token_amount_raw > 0
        ):
            position.l1_protection_armed = True
            logger.info(
                "L1 hit — protection armed at +%.2f%% on remaining %.0f%% of %s",
                Config.L1_PROTECTION_PCT * 100,
                position.remaining_pct * 100,
                position.symbol,
            )

        all_levels_hit = len(position.tp_levels_hit) >= position.tp_level_count
        if position.remaining_token_amount_raw <= 0 or all_levels_hit:
            return self.close_position(position, exit_price, SignalType.SELL_TP_PARTIAL)
        return None

    def close_position(self, position: Position, exit_price: float, reason: SignalType) -> TradeProfile:
        hold_duration = time.time() - position.entry_time
        pnl_pct = position.pnl_pct(exit_price)
        profile = TradeProfile(
            mint=position.mint,
            symbol=position.symbol,
            momentum_pct=position.profile.get("momentum_pct", position.momentum_at_entry),
            liquidity_usd=position.profile.get("liquidity_usd", 0),
            volume_24h_usd=position.profile.get("volume_24h_usd", 0),
            price_change_5m=position.profile.get("price_change_5m", 0),
            price_change_1h=position.profile.get("price_change_1h", 0),
            hold_duration_sec=hold_duration,
            pnl_pct=pnl_pct,
            profitable=pnl_pct > 0,
        )
        if profile.profitable:
            self.last_profitable_profile = profile
        self.positions = [p for p in self.positions if p.mint != position.mint]
        logger.info(
            "Closed %s reason=%s pnl=%.4f hold=%.0fs",
            position.symbol,
            reason.value,
            pnl_pct,
            hold_duration,
        )
        return profile
