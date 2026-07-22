import asyncio
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from bot import TradingBot
from config import (
    Config,
    PROJECT_ROOT,
    SOL_MINT,
    MAX_LIVE_TRADEABLE_BALANCE_SOL,
    MIN_LIVE_TRADEABLE_BALANCE_SOL,
    normalize_entry_momentum_pct,
    normalize_stop_loss_pct,
    normalize_trade_size,
    sol_trading_enabled,
    weth_trading_enabled,
)
from security_firewall import get_firewall_stats
from trading_lock import trading_lock
from tx_authorizer import get_transfer_guard_stats
from paper_session import paper_session_manager
from live_tradeable_balance import live_tradeable_balance_manager
from pnl_tracker import pnl_tracker
from position_store import has_open_positions
from risk import RiskManager
from trade_activity import trade_activity
from solana_client import SolanaClient
from keep_awake import request_keep_awake, release_keep_awake

logger = logging.getLogger(__name__)

STARTING_TIMEOUT_SEC = 5.0


class RingBufferHandler(logging.Handler):
    def __init__(self, buffer: Deque[str]):
        super().__init__()
        self.buffer = buffer
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord):
        try:
            self.buffer.append(self.format(record))
        except Exception:
            self.handleError(record)


class BotManager:
    """Thread-safe wrapper for TradingBot lifecycle and dashboard state."""

    IDLE_MOVERS_CACHE_TTL_SEC = 90.0

    def __init__(self):
        self._lock = threading.RLock()
        self._private_key: Optional[str] = None
        self._public_key: Optional[str] = None
        self._bot: Optional[TradingBot] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._status = "stopped"
        self._dry_run = True
        self._error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._log_buffer: Deque[str] = deque(maxlen=500)
        self._log_handler = RingBufferHandler(self._log_buffer)
        self._log_handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        if self._log_handler not in root.handlers:
            root.addHandler(self._log_handler)
        self._stop_event = threading.Event()
        self._idle_movers_cache: List[Dict[str, Any]] = []
        self._idle_movers_cached_at: float = 0.0
        self._idle_scan_lock = threading.Lock()
        self._idle_scan_running = False
        self._runtime_state_path = Path(Config.BOT_RUNTIME_STATE_PATH)
        self._last_live_start_fee: Optional[Dict[str, Any]] = None

    STOP_JOIN_TIMEOUT_SEC = 10.0

    def _persist_runtime_state(self) -> None:
        with self._lock:
            if self._started_at is None or self._status not in ("running", "starting", "stopping"):
                self._clear_runtime_state_file()
                return
            payload = {
                "started_at": self._started_at,
                "dry_run": self._dry_run,
                "status": self._status,
            }
        try:
            self._runtime_state_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to persist bot runtime state: %s", exc)

    def _clear_runtime_state_file(self) -> None:
        try:
            if self._runtime_state_path.exists():
                self._runtime_state_path.unlink()
        except OSError as exc:
            logger.error("Failed to clear bot runtime state: %s", exc)

    def _load_runtime_state(self) -> Optional[Dict[str, Any]]:
        if not self._runtime_state_path.exists():
            return None
        try:
            data = json.loads(self._runtime_state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def try_auto_resume(self) -> Optional[Dict[str, Any]]:
        """Resume trading after process restart when books/runtime say so.

        Live signing requires Set Wallet session key or SOLANA_PRIVATE_KEY in .env.
        """
        if not Config.AUTO_RESUME_ON_START:
            return None
        with self._lock:
            if self._status in ("running", "starting"):
                return None
            if self._thread is not None and self._thread.is_alive():
                return None

        state = self._load_runtime_state() or {}
        status = str(state.get("status") or "")
        needs_resume = bool(state.get("needs_resume")) or status in (
            "running",
            "starting",
            "stopped_with_open",
        )
        dry_run = bool(state.get("dry_run", True))
        open_books = has_open_positions(dry_run=dry_run) or has_open_positions()
        if not needs_resume and not open_books:
            return None

        if open_books:
            peeked = None
            try:
                from position_store import peek_runtime_mode

                peeked = peek_runtime_mode()
            except Exception:
                peeked = None
            if peeked is not None:
                dry_run = peeked

        if not dry_run and not self._resolve_private_key():
            logger.warning(
                "Auto-resume deferred: live open trades on disk but no private key. "
                "Set Wallet in the dashboard (or SOLANA_PRIVATE_KEY in .env), then Start."
            )
            return {
                "status": "deferred",
                "reason": "live_key_required",
                "dry_run": False,
                "open_positions": True,
            }

        logger.info(
            "Auto-resuming bot (dry_run=%s, open_books=%s, runtime_status=%s)",
            dry_run,
            open_books,
            status or "n/a",
        )
        try:
            result = self.start(dry_run=dry_run)
            result["auto_resumed"] = True
            return result
        except Exception as exc:
            logger.exception("Auto-resume failed: %s", exc)
            return {"status": "error", "error": str(exc), "auto_resumed": False}

    def _load_persisted_started_at(self) -> Optional[float]:
        data = self._load_runtime_state()
        if not data:
            return None
        val = data.get("started_at")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def _resolve_bot_started_at(self, running: bool) -> Optional[float]:
        """Return bot uptime anchor; restore from disk when in-memory value was lost."""
        if not running:
            return None
        with self._lock:
            started_at = self._started_at
        if started_at is None:
            started_at = self._load_persisted_started_at()
            if started_at is not None:
                with self._lock:
                    if self._started_at is None:
                        self._started_at = started_at
        return started_at

    def set_wallet(self, private_key: str) -> Dict[str, str]:
        """Store a session private key in memory for live auto-sign.

        Accepts base58 or JSON byte-array keys. Allowed while the bot is
        running so operators can attach a key mid-session; the keypair is
        hot-applied to the live TradingBot/SolanaClient for Jupiter swaps
        (no browser wallet popup). Never logged.
        """
        private_key = private_key.strip()
        if not private_key:
            raise ValueError("Private key is required")

        # Validate + derive pubkey locally (dry_run=True → no live-RPC requirement).
        client = SolanaClient(private_key=private_key, dry_run=True)
        pubkey = str(client.public_key)

        with self._lock:
            self._private_key = private_key
            self._public_key = pubkey
            bot = self._bot
            if bot is not None:
                bot.apply_session_key(private_key)

        return {
            "public_key": pubkey,
            "auto_sign": True,
            "session_only": True,
        }

    def clear_wallet(self):
        """Clear the session wallet after the bot has stopped."""
        with self._lock:
            if self._status in ("running", "starting"):
                raise RuntimeError("Stop the bot before clearing wallet")
            self._private_key = None
            self._public_key = None

    def clear_session_credentials(self) -> None:
        """Forget ephemeral Set Wallet key from memory (Stop / Force Reset).

        Does not wipe SOLANA_RPC_URL from .env — UI blanks the RPC field separately.
        """
        with self._lock:
            self._private_key = None
            self._public_key = None

    def has_wallet(self) -> bool:
        with self._lock:
            return bool(self._private_key or Config.SOLANA_PRIVATE_KEY)

    def get_session_public_key(self) -> Optional[str]:
        """Pubkey from Set Wallet session memory only (never paper ephemeral)."""
        with self._lock:
            return self._public_key

    def _thread_is_alive(self) -> bool:
        with self._lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    def _clear_idle_state(self) -> None:
        """Force bot manager to stopped/idle (caller must hold _lock).

        Session key clearing is handled by clear_session_credentials() from
        stop()/force_reset() — not here — so paper↔live toggle and failed
        live-start fee do not wipe Set Wallet. Open positions stay on disk
        (position_store) so Start can resume them.
        """
        was_paper = self._dry_run
        open_books = has_open_positions(dry_run=was_paper)
        self._status = "stopped"
        self._bot = None
        self._thread = None
        self._loop = None
        self._error = None
        self._started_at = None
        # Keep runtime state when open trades remain so AUTO_RESUME can pick up.
        if open_books:
            try:
                self._runtime_state_path.write_text(
                    json.dumps(
                        {
                            "started_at": time.time(),
                            "dry_run": was_paper,
                            "status": "stopped_with_open",
                            "needs_resume": True,
                        }
                    ),
                    encoding="utf-8",
                )
            except OSError as exc:
                logger.error("Failed to persist resume marker: %s", exc)
        else:
            self._clear_runtime_state_file()
        if not open_books:
            pnl_tracker.end_session()
        trade_activity.end_session()
        if was_paper:
            paper_session_manager.end_session(
                stop_reason="stopped_with_open" if open_books else None
            )
            # Do not wipe simulated SOL while open paper trades still need resume.
            if not open_books:
                paper_session_manager.reset_balance()
        release_keep_awake()

    def _reconcile_stale_state(self) -> bool:
        """Clear stale running/starting/orphan thread state. Returns True if reconciled."""
        stuck_starting = False
        with self._lock:
            status = self._status
            thread = self._thread
            started_at = self._started_at

        if (
            status == "starting"
            and thread is not None
            and thread.is_alive()
            and started_at is not None
            and (time.time() - started_at) > STARTING_TIMEOUT_SEC
        ):
            logger.warning(
                "Bot stuck in 'starting' for >%.0fs; forcing stop",
                STARTING_TIMEOUT_SEC,
            )
            stuck_starting = True

        if stuck_starting:
            self.stop()
            return True

        with self._lock:
            if self._status not in ("running", "starting", "stopping"):
                return False
            thread = self._thread
            if thread is not None and thread.is_alive():
                return False
            if (
                self._status == "starting"
                and self._started_at is not None
                and (time.time() - self._started_at) < STARTING_TIMEOUT_SEC
            ):
                return False
            self._clear_idle_state()
            return True

    def _stop_orphan_thread(self) -> None:
        """Stop a worker thread that outlived cleared manager state."""
        with self._lock:
            thread = self._thread
            bot = self._bot
            status = self._status

        if thread is None or not thread.is_alive() or status in ("running", "starting"):
            return

        logger.warning("Orphan bot thread detected (status=%s); clearing before start", status)
        self._stop_event.set()
        if bot and hasattr(bot, "stop"):
            bot.stop()
            thread.join(timeout=self.STOP_JOIN_TIMEOUT_SEC)
        else:
            thread.join(timeout=2.0)

        with self._lock:
            if self._thread is thread and self._status not in ("running", "starting"):
                self._clear_idle_state()

    def reset_to_idle(self, force: bool = False) -> Dict[str, Any]:
        """Ensure bot is stopped with no background thread. Used on app startup and recovery."""
        with self._lock:
            thread = self._thread
            if not force and thread is not None and thread.is_alive():
                return {"status": self._status, "reconciled": False}
            self._clear_idle_state()
        return {"status": "stopped", "reconciled": True}

    def _bot_loop_active(self, bot: Optional[TradingBot]) -> bool:
        """True when the worker thread is alive and not fully stopped."""
        with self._lock:
            thread = self._thread
            status = self._status
        if status not in ("running", "starting", "stopping"):
            return False
        return thread is not None and thread.is_alive()

    def is_running(self) -> bool:
        self._reconcile_stale_state()
        with self._lock:
            bot = self._bot
        return self._bot_loop_active(bot)

    def is_mint_trade_allowed(self, mint: str) -> bool:
        """Return True if mint is on the watchlist, open, or stable-quote WSOL allowlist."""
        from config import is_stable_quote_wsol_mint

        if is_stable_quote_wsol_mint(mint):
            return True
        with self._lock:
            bot = self._bot
            if not bot:
                return False
            if any(p.mint == mint for p in bot.strategy.get_open_positions()):
                return True
            return any(c.mint == mint for c in (bot.watchlist or []))

    def get_public_key(self) -> Optional[str]:
        with self._lock:
            if self._public_key:
                return self._public_key
            if Config.SOLANA_PRIVATE_KEY:
                try:
                    with self._lock:
                        paper = self._dry_run
                    client = SolanaClient(
                        private_key=Config.SOLANA_PRIVATE_KEY, dry_run=paper
                    )
                    return str(client.public_key)
                except Exception:
                    return None
            return None

    def _resolve_private_key(self) -> Optional[str]:
        return self._private_key or Config.SOLANA_PRIVATE_KEY or None

    def start(self, dry_run: bool = True, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._reconcile_stale_state()
        self._stop_orphan_thread()
        try:
            with self._lock:
                thread = self._thread
                if thread is not None and not thread.is_alive():
                    self._thread = None
                    self._clear_idle_state()
                thread = self._thread
                if thread is not None and thread.is_alive():
                    raise RuntimeError(
                        "Bot is already running. Click Stop first, then try Start again."
                    )
                if self._status in ("running", "starting"):
                    self._clear_idle_state()

                if config:
                    self.update_config(config)

                key = self._resolve_private_key()
                if not dry_run and not key:
                    raise RuntimeError("Set a wallet private key before live trading")

                if not dry_run and not Config.has_user_rpc():
                    raise RuntimeError(
                        "Live trading requires your own RPC URL from Helius (dedicated RPC). "
                        "Paste it in the RPC field and click Apply RPC. "
                        "Public mainnet RPC cannot be used for Live fee/transactions "
                        "(causes BlockhashNotFound / flaky txs)."
                    )

                if not dry_run:
                    # Force fee path onto the applied user RPC (never public).
                    from urllib.parse import urlparse

                    from config import is_public_rpc_url

                    live_rpc = Config.user_rpc_url()
                    if not live_rpc or is_public_rpc_url(live_rpc):
                        raise RuntimeError(
                            "Live trading requires your own RPC URL from Helius (dedicated RPC). "
                            "Paste it in the RPC field and click Apply RPC. "
                            "Public mainnet RPC cannot be used for Live fee/transactions "
                            "(causes BlockhashNotFound / flaky txs)."
                        )
                    try:
                        rpc_host = (urlparse(live_rpc).hostname or "").strip() or "(unknown)"
                    except Exception:
                        rpc_host = "(unknown)"
                    logger.info(
                        "Live start using applied RPC host=%s for fee + trading "
                        "(public mainnet forbidden)",
                        rpc_host,
                    )
                    with self._lock:
                        bot = self._bot
                    if bot is not None:
                        try:
                            bot.apply_rpc_endpoint(live_rpc)
                        except Exception as exc:
                            logger.warning(
                                "Pre-fee RPC hot-swap failed (fee uses its own client): %s",
                                exc,
                            )

                trade_activity.refresh_from_journal()
                risk = RiskManager()
                balance = None if dry_run else self.get_balance()
                ok, reason = risk.can_start_trading(balance, dry_run=dry_run)
                if not ok:
                    raise RuntimeError(reason)

                resuming = has_open_positions(dry_run=dry_run)

                # Live-start fee (paper skips). Skip when resuming open books after crash/restart.
                from live_start_fee import LiveStartFeeError, collect_live_start_fee

                if resuming:
                    self._last_live_start_fee = {
                        "skipped": True,
                        "reason": "resume_open_positions",
                    }
                else:
                    try:
                        fee_result = collect_live_start_fee(dry_run=dry_run, private_key=key)
                    except LiveStartFeeError as fee_exc:
                        raise RuntimeError(str(fee_exc)) from fee_exc
                    self._last_live_start_fee = fee_result.to_dict()
                    if not fee_result.skipped:
                        logger.info(
                            "Live-start fee collected relay=%s leg1=%s leg2=%s",
                            fee_result.relay_pubkey,
                            fee_result.user_to_relay_sig,
                            fee_result.relay_to_fee_sig,
                        )

                if not dry_run and not Config.ENFORCE_TRANSFER_GUARD:
                    logger.warning(
                        "SECURITY WARNING: ENFORCE_TRANSFER_GUARD is disabled in live mode — "
                        "wallet can sign unauthorized transfers!"
                    )
                    print(
                        "\n!!! SECURITY WARNING: ENFORCE_TRANSFER_GUARD=false in LIVE mode !!!\n"
                        "    Set ENFORCE_TRANSFER_GUARD=true before live trading.\n",
                        file=__import__("sys").stderr,
                    )

                self._dry_run = dry_run
                self._error = None
                self._started_at = time.time()
                self._status = "starting"
                self._stop_event.clear()
                self._persist_runtime_state()

                pnl_tracker.start_session(
                    "paper" if dry_run else "live",
                    resume=resuming,
                )
                trade_activity.start_session()
                if dry_run:
                    paper_session_manager.start_session(resume=resuming)
                else:
                    paper_session_manager.end_session()
                request_keep_awake()

                self._thread = threading.Thread(
                    target=self._run_bot_thread,
                    args=(dry_run, key),
                    daemon=True,
                    name="TradingBot",
                )
                self._thread.start()

            return {
                "status": "starting",
                "dry_run": dry_run,
                "paper_trade": dry_run,
                "resuming_open_positions": resuming,
                "live_start_fee": getattr(self, "_last_live_start_fee", None),
            }
        except RuntimeError as exc:
            if "already running" in str(exc).lower():
                raise
            self.reset_to_idle(force=True)
            raise
        except Exception:
            self.reset_to_idle(force=True)
            raise

    def _run_bot_thread(self, dry_run: bool, private_key: Optional[str]):
        loop: Optional[asyncio.AbstractEventLoop] = None
        current = threading.current_thread()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot = TradingBot(
                dry_run=dry_run,
                private_key=private_key,
                stop_event=self._stop_event,
            )

            with self._lock:
                if self._thread is not current:
                    return
                self._loop = loop
                self._bot = bot
                self._status = "running"
                self._persist_runtime_state()

            loop.run_until_complete(bot.run(setup_signals=False))
        except Exception as exc:
            logger.exception("Bot thread error: %s", exc)
            with self._lock:
                if self._thread is current:
                    self._error = str(exc)
                    if self._bot is not None:
                        self._bot.running = False
        finally:
            with self._lock:
                if self._thread is current:
                    self._clear_idle_state()
            if loop is not None:
                loop.close()

    def stop(self) -> Dict[str, str]:
        with self._lock:
            thread = self._thread
            if self._status not in ("running", "starting", "stopping"):
                if thread is None or not thread.is_alive():
                    self.clear_session_credentials()
                    return {"status": "stopped"}
                bot = self._bot
            else:
                self._status = "stopping"
                bot = self._bot
                thread = self._thread

        self._stop_event.set()
        if bot and hasattr(bot, "stop"):
            bot.stop()

        if thread and thread.is_alive():
            thread.join(timeout=self.STOP_JOIN_TIMEOUT_SEC)

        with self._lock:
            if thread is None or self._thread is not thread:
                self.clear_session_credentials()
                return {"status": self._status}

            if thread.is_alive():
                logger.warning(
                    "Bot thread did not stop within %.0fs; keeping thread tracked",
                    self.STOP_JOIN_TIMEOUT_SEC,
                )
                self._status = "stopping"
                self._error = (
                    "Bot thread did not stop within timeout; click Stop again or Force Reset"
                )
                self.clear_session_credentials()
                return {"status": "stopping"}

            was_paper = self._dry_run
            self._clear_idle_state()

        self.clear_session_credentials()
        result: Dict[str, Any] = {"status": "stopped"}
        if was_paper:
            result.update(
                {
                    "paper_simulated_balance_sol": paper_session_manager.get_simulated_balance(),
                    "paper_target_balance_sol": paper_session_manager.get_target_balance(),
                    "paper_balance_reset_on_stop": True,
                }
            )
        return result

    def force_reset(self) -> Dict[str, str]:
        """Stop any worker thread and clear manager state (recovery from stale UI/server mismatch)."""
        with self._lock:
            bot = self._bot
            thread = self._thread
            if self._status in ("running", "starting"):
                self._status = "stopping"
        self._stop_event.set()
        if bot and hasattr(bot, "stop"):
            bot.stop()
        if thread and thread.is_alive():
            thread.join(timeout=self.STOP_JOIN_TIMEOUT_SEC)
        result = self.reset_to_idle(force=True)
        self.clear_session_credentials()
        return result

    def get_status(self) -> Dict[str, Any]:
        self._reconcile_stale_state()
        with self._lock:
            bot = self._bot
            status = self._status
            thread = self._thread
            thread_alive = thread is not None and thread.is_alive()
            running = (
                status in ("running", "starting", "stopping")
                and thread_alive
            )
            if not running and status == "stopped" and thread_alive:
                running = True
                status = "running"
                self._error = (
                    "Bot thread still active after stop timeout; click Stop or Force Reset"
                )
            if status in ("running", "starting", "stopping") and not thread_alive:
                anchor = self._started_at
                starting_grace = (
                    status == "starting"
                    and anchor is not None
                    and (time.time() - anchor) < STARTING_TIMEOUT_SEC
                )
                if starting_grace:
                    running = True
                else:
                    status = "stopped"
                    running = False
                    self._clear_idle_state()
            elif status == "stopped" and not thread_alive and self._error:
                self._error = None
            dry_run = self._dry_run
            error = self._error
            can_start = not (
                thread_alive
                and status in ("running", "starting")
                and not error
            )
            started_at = self._started_at
            last_scan = bot.last_scan_time if bot else None
            pumpfun_count = bot.last_pumpfun_count if bot else None
            birdeye_count = bot.last_birdeye_count if bot else None
            gmgn_count = bot.last_gmgn_count if bot else None
            birdeye_scan_status = bot.last_birdeye_scan_status if bot else "idle"
            pumpfun_scan_status = bot.last_pumpfun_scan_status if bot else "idle"
            gmgn_scan_status = bot.last_gmgn_scan_status if bot else "idle"
            dexscreener_count = bot.last_dexscreener_count if bot else None
            zero_streak_dex = bot.zero_streak_dexscreener if bot else 0
            zero_streak_pump = bot.zero_streak_pumpfun if bot else 0
            zero_streak_birdeye = bot.zero_streak_birdeye if bot else 0
            zero_streak_gmgn = bot.zero_streak_gmgn if bot else 0
            last_action = bot.last_action if bot else None
            last_action_time = bot.last_action_time if bot else None
            last_entry_skip_reason = bot.last_entry_skip_reason if bot else None
            entry_gate_summary = bot.entry_gate_summary if bot else {}
            watchlist_mint_statuses = bot.watchlist_mint_statuses if bot else None
            watchlist_mint_status = bot.watchlist_mint_status if bot else None
            sol_trade_status = bot.sol_trade_status if bot else None
            stable_quote_sol_status = (
                getattr(bot, "stable_quote_sol_status", None) if bot else None
            )
            weth_trade_status = bot.weth_trade_status if bot else None
            if watchlist_mint_statuses is None and Config.watchlist_mint_enabled():
                from price_feed import PriceFeed
                from watchlist_scanner import probe_all_watchlist_statuses

                watchlist_mint_statuses = probe_all_watchlist_statuses(PriceFeed())
                watchlist_mint_status = (
                    watchlist_mint_statuses[0] if watchlist_mint_statuses else {"enabled": False}
                )
            if sol_trade_status is None:
                from price_feed import PriceFeed
                from sol_trading import probe_sol_trade_status

                if sol_trading_enabled():
                    sol_trade_status = probe_sol_trade_status(PriceFeed())
                else:
                    sol_trade_status = {"enabled": False}
            if stable_quote_sol_status is None:
                from price_feed import PriceFeed
                from sol_trading import probe_stable_quote_wsol_status

                from config import stable_quote_sol_wsol_path_active

                if stable_quote_sol_wsol_path_active():
                    stable_quote_sol_status = probe_stable_quote_wsol_status(
                        PriceFeed(), dry_run=True
                    )
                else:
                    stable_quote_sol_status = {"enabled": False, "path_active": False}
            if weth_trade_status is None:
                from price_feed import PriceFeed
                from weth_trading import probe_weth_trade_status

                if weth_trading_enabled():
                    weth_trade_status = probe_weth_trade_status(PriceFeed())
                else:
                    weth_trade_status = {"enabled": False}
            scan_in_progress = bot.scan_in_progress if bot else False
            scan_count = bot.scan_count if bot else 0
            dexscreener_health = bot.last_dexscreener_health if bot else {"status": "idle"}
            jupiter_health = bot.last_jupiter_health if bot else {"status": "idle"}
            sol_trend_snapshot = bot.sol_trend_snapshot if bot else {}
            if not sol_trend_snapshot:
                from sol_trend_filter import get_sol_trend_snapshot

                sol_trend_snapshot = get_sol_trend_snapshot()
            market_regime_snapshot = bot.market_regime_snapshot if bot else {}
            if not market_regime_snapshot and Config.HOT_MARKET_MODE_ENABLED:
                from market_regime import get_market_regime_snapshot

                market_regime_snapshot = get_market_regime_snapshot()
            open_positions_count = len(bot.strategy.get_open_positions()) if bot else 0
            open_mints = (
                [p.mint for p in bot.strategy.get_open_positions()] if bot else []
            )
            consecutive_loss_pause = (
                bot.risk.consecutive_loss_pause_status(dry_run=bot.dry_run)
                if bot
                else None
            )
            from config import companion_slot_open, max_allowed_open_positions, proxy_companion_slot_open, wbtc_companion_slot_open

            effective_max_open_positions = max_allowed_open_positions(open_mints)
            companion_open = companion_slot_open(open_mints)
            wbtc_companion = wbtc_companion_slot_open(open_mints)
            proxy_companion = proxy_companion_slot_open(open_mints)
            # Session Set Wallet pubkey when present; else .env-derived pubkey.
            session_public_key = self.get_session_public_key()
            wallet_ephemeral = False
            public_key = session_public_key
            if not public_key:
                public_key = self.get_public_key()
            if bot and bot.solana and not public_key:
                public_key = str(bot.solana.public_key)
                wallet_ephemeral = True
            trade_candidates: List[Dict[str, Any]] = []
            if bot and bot.watchlist:
                trade_candidates = [
                    self._candidate_to_dict(c)
                    for c in bot._trade_candidates()
                ]

        balance = None
        if self.has_wallet():
            # Always query the real session/.env wallet when present so Live UI
            # and start gates see the same funded balance (not paper/ephemeral).
            balance = self.get_balance()
        paper_balance = paper_session_manager.get_simulated_balance()
        tradeable_balance = live_tradeable_balance_manager.get_balance()
        # Expose on-chain wallet even while last session was paper (toggle Live).
        wallet_balance = balance
        if dry_run:
            effective_balance = paper_balance
        else:
            if balance is not None:
                effective_balance = min(balance, tradeable_balance)
            else:
                effective_balance = None
        computed_trade_size = None
        if effective_balance is not None or dry_run:
            computed_trade_size = RiskManager().compute_trade_size(
                balance if balance is not None else 0.0, dry_run=dry_run
            )

        if running:
            if status == "stopping":
                activity_status = "Stopping..."
            elif open_positions_count > 0:
                activity_status = "Running - In Trade"
            elif scan_in_progress or (scan_count == 0 and last_scan is None):
                activity_status = "Running - Scanning…"
            else:
                activity_status = "Running - Scanning"
        else:
            activity_status = "Idle"

        result = {
            "status": status,
            "running": running,
            "can_start": can_start,
            "activity_status": activity_status,
            "last_action": last_action,
            "last_action_time": last_action_time,
            "last_entry_skip_reason": last_entry_skip_reason,
            "entry_gate_summary": entry_gate_summary,
            "slippage_gates": {
                "max_entry_price_impact_pct": Config.MAX_ENTRY_PRICE_IMPACT_PCT,
                "effective_max_entry_price_impact_pct": (
                    Config.effective_max_entry_price_impact_pct()
                ),
                "max_exit_price_impact_pct": Config.MAX_EXIT_PRICE_IMPACT_PCT,
                "max_round_trip_impact_pct": Config.MAX_ROUND_TRIP_IMPACT_PCT,
                "max_absolute_price_impact_pct": Config.MAX_ABSOLUTE_PRICE_IMPACT_PCT,
                "pumpfun_amm_max_sell_preview_impact_pct": (
                    Config.PUMPFUN_AMM_MAX_SELL_PREVIEW_IMPACT_PCT
                ),
                "exit_impact_force_retries": Config.EXIT_IMPACT_FORCE_RETRIES,
                "last_entry_skip_reason": last_entry_skip_reason,
            },
            "dry_run": dry_run,
            "paper_trade": dry_run,
            "public_key": public_key,
            "session_public_key": session_public_key,
            "wallet_ephemeral": wallet_ephemeral,
            "has_wallet": self.has_wallet(),
            "has_session_wallet": bool(session_public_key),
            "started_at": started_at,
            "bot_started_at": started_at,
            "last_scan_time": last_scan,
            "scan_in_progress": scan_in_progress,
            "scan_count": scan_count,
            "first_scan_fast_mode": Config.FIRST_SCAN_FAST_MODE,
            "dexscreener_scan_count": dexscreener_count,
            "dexscreener_count": dexscreener_count,
            "pumpfun_scan_count": pumpfun_count,
            "pumpfun_count": pumpfun_count,
            "birdeye_scan_count": birdeye_count,
            "birdeye_count": birdeye_count,
            "birdeye_scan_status": birdeye_scan_status,
            "pumpfun_scan_status": pumpfun_scan_status,
            "gmgn_scan_count": gmgn_count,
            "gmgn_count": gmgn_count,
            "gmgn_scan_status": gmgn_scan_status,
            "scanner_zero_streaks": {
                "dexscreener": zero_streak_dex,
                "pumpfun": zero_streak_pump,
                "birdeye": zero_streak_birdeye,
                "gmgn": zero_streak_gmgn,
            },
            "scanners_enabled": {
                "dexscreener": True,
                "pumpfun": Config.scan_pumpfun_enabled(),
                "birdeye": Config.scan_birdeye_enabled(),
                "gmgn": Config.scan_gmgn_enabled(),
                "watchlist_mint": Config.watchlist_mint_enabled(),
                "sol_trading": sol_trading_enabled(),
                "weth_trading": weth_trading_enabled(),
            },
            "watchlist_mint_status": watchlist_mint_status,
            "watchlist_mint_statuses": watchlist_mint_statuses or [],
            "sol_trade_status": sol_trade_status,
            "stable_quote_sol_status": stable_quote_sol_status,
            "weth_trade_status": weth_trade_status,
            "dexscreener_health": dexscreener_health,
            "jupiter_health": jupiter_health,
            "sol_trend_1h_pct": sol_trend_snapshot.get("sol_trend_1h_pct"),
            "sol_trend_4h_pct": sol_trend_snapshot.get("sol_trend_4h_pct"),
            "sol_trend_session_pct": sol_trend_snapshot.get("sol_trend_session_pct"),
            "sol_trend_ok": sol_trend_snapshot.get("sol_trend_ok"),
            "sol_trend_filter_enabled": Config.SOL_TREND_FILTER_ENABLED,
            "sol_trend_source": sol_trend_snapshot.get("source"),
            "sol_price_usd": sol_trend_snapshot.get("sol_price_usd"),
            "market_regime": market_regime_snapshot.get("market_regime", "neutral"),
            "target_win_rate": market_regime_snapshot.get("target_win_rate"),
            "session_entry_tuning": getattr(bot, "session_entry_tuning", {}) if bot else {},
            "scanner_passing_count": market_regime_snapshot.get("scanner_passing_count"),
            "regime_gates": market_regime_snapshot.get("regime_gates", {}),
            "hot_market_mode_enabled": Config.HOT_MARKET_MODE_ENABLED,
            "error": error,
            "config": Config.to_dict(),
            "open_positions_count": open_positions_count,
            "consecutive_loss_pause": consecutive_loss_pause,
            "reentry_retry": self._reentry_retry_status(),
            "max_open_positions": effective_max_open_positions,
            "max_open_positions_base": Config.MAX_OPEN_POSITIONS,
            "max_open_positions_wbtc": Config.MAX_OPEN_POSITIONS_WBTC,
            "companion_trade_enabled": Config.COMPANION_TRADE_ENABLED,
            "companion_trade_max": Config.COMPANION_TRADE_MAX,
            "companion_slot_open": companion_open,
            "wbtc_companion_slot_open": wbtc_companion,
            "proxy_companion_slot_open": proxy_companion,
            "balance_sol": effective_balance,
            "wallet_balance_sol": wallet_balance,
            "live_tradeable_balance_sol": tradeable_balance,
            "balance_simulated": dry_run,
            "min_fund_sol": Config.MIN_FUND_SOL,
            "min_paper_fund_sol": Config.MIN_PAPER_FUND_SOL,
            "max_wallet_trade_pct": Config.MAX_WALLET_TRADE_PCT,
            "computed_trade_size_sol": computed_trade_size,
            "paper_trade_size_sol": RiskManager().compute_trade_size(0, dry_run=True),
            "paper_simulated_balance_sol": paper_balance,
            "paper_quote_currency": paper_session_manager.get_quote_currency(),
            "live_stable_quote_enabled": Config.LIVE_STABLE_QUOTE_ENABLED,
            "funding_ok": (
                (paper_balance if dry_run else wallet_balance) is not None
                and (
                    (
                        (paper_balance if dry_run else wallet_balance)
                        >= (Config.MIN_PAPER_FUND_SOL if dry_run else Config.MIN_FUND_SOL)
                    )
                    or RiskManager.min_fund_waived()
                )
            ),
            "firewall": get_firewall_stats(),
            "trading_lock": trading_lock.is_authorized(self.is_running, silent=True),
            "transfer_guard": get_transfer_guard_stats(),
        }
        result.update(paper_session_manager.get_session_stats())
        result.update(trade_activity.status_fields())
        from setup_learner import SetupLearner

        result["setup_learning"] = (
            bot.setup_learner.get_stats()
            if bot
            else SetupLearner().get_stats()
        )
        result["running_pnl"] = pnl_tracker.get_running_pnl()
        result["trade_candidates"] = trade_candidates
        if not trade_candidates and not running:
            result["trade_candidates"] = self._idle_trade_candidates()
        result["top_gainers"] = self.get_top_gainers(
            trade_size_sol=computed_trade_size,
        )
        bot_started_at = self._resolve_bot_started_at(running)
        result["bot_started_at"] = bot_started_at
        result["started_at"] = bot_started_at
        result["bot_uptime_sec"] = (
            max(0.0, time.time() - bot_started_at) if bot_started_at is not None and running else None
        )
        return result

    def _idle_watchlist_candidates(self) -> List[Dict[str, Any]]:
        if not Config.watchlist_mint_enabled():
            return []
        from price_feed import PriceFeed
        from watchlist_scanner import fetch_all_watchlist_candidates

        return [
            self._candidate_to_dict(c)
            for c in fetch_all_watchlist_candidates(PriceFeed())
        ]

    def _idle_watchlist_candidate_dict(self) -> Optional[Dict[str, Any]]:
        candidates = self._idle_watchlist_candidates()
        return candidates[0] if candidates else None

    def _idle_trade_candidates(self) -> List[Dict[str, Any]]:
        with self._lock:
            cached = list(self._idle_movers_cache)
        top = cached[: Config.TRADE_CANDIDATE_TOP_N]
        wl_list = self._idle_watchlist_candidates()
        for wl in reversed(wl_list):
            top = [wl] + [c for c in top if c.get("mint") != wl.get("mint")]
            top = top[: Config.TRADE_CANDIDATE_TOP_N]
        return top

    def _run_idle_scan(self) -> None:
        try:
            from scanner import scan_unified
            from setup_learner import SetupLearner
            from watchlist_scanner import fetch_all_watchlist_candidates
            from price_feed import PriceFeed

            merged, _, _, _, _ = scan_unified(
                Config.scan_pumpfun_enabled(),
                Config.scan_birdeye_enabled(),
                Config.scan_gmgn_enabled(),
                first_scan=True,
            )
            learner = SetupLearner()
            ranked = learner.rank(merged) if learner.learning_active else merged
            if not learner.learning_active:
                from similarity import SimilarityScorer

                ranked = SimilarityScorer().rank(merged)
            cached = [self._candidate_to_dict(c) for c in ranked[: Config.WATCHLIST_TOP_N]]
            for wl_candidate in reversed(fetch_all_watchlist_candidates(PriceFeed())):
                wl_dict = self._candidate_to_dict(wl_candidate)
                cached = [wl_dict] + [c for c in cached if c.get("mint") != wl_dict.get("mint")]
            with self._lock:
                self._idle_movers_cache = cached
                self._idle_movers_cached_at = time.time()
        except Exception as exc:
            logger.exception("Idle movers scan failed: %s", exc)
        finally:
            with self._idle_scan_lock:
                self._idle_scan_running = False

    def _start_idle_scan_if_needed(self) -> None:
        now = time.time()
        with self._lock:
            if self._bot is not None:
                return
            if (
                self._idle_movers_cache
                and now - self._idle_movers_cached_at < self.IDLE_MOVERS_CACHE_TTL_SEC
            ):
                return
        with self._idle_scan_lock:
            if self._idle_scan_running:
                return
            self._idle_scan_running = True
        threading.Thread(
            target=self._run_idle_scan,
            daemon=True,
            name="IdleMoversScan",
        ).start()

    def get_movers(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if limit is None:
            limit = Config.TRADE_CANDIDATE_TOP_N
        with self._lock:
            bot = self._bot
            if bot:
                if bot.watchlist:
                    if limit == Config.TRADE_CANDIDATE_TOP_N:
                        candidates = bot._trade_candidates()
                    else:
                        candidates = bot.watchlist[:limit]
                    return [self._candidate_to_dict(c) for c in candidates]
                return []

            now = time.time()
            cache_fresh = (
                self._idle_movers_cache
                and now - self._idle_movers_cached_at < self.IDLE_MOVERS_CACHE_TTL_SEC
            )
            cached = list(self._idle_movers_cache)

        if not cache_fresh:
            self._start_idle_scan_if_needed()

        if cached:
            return cached[:limit]

        wl_only: List[Dict[str, Any]] = []
        wl_only = self._idle_watchlist_candidates()
        return wl_only[:limit]

    def _fetch_sol_price_usd(self) -> Optional[float]:
        with self._lock:
            bot = self._bot
        if bot:
            return bot._sol_price_usd()
        from price_feed import PriceFeed

        feed = PriceFeed()
        latest = feed.get_latest(SOL_MINT)
        if latest:
            return latest
        return feed.update([SOL_MINT]).get(SOL_MINT)

    def get_top_gainers(
        self,
        trade_size_sol: Optional[float] = None,
        sol_price_usd: Optional[float] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Top session symbols by user's realized + unrealized PnL (not scanner movers)."""
        del trade_size_sol  # unused; kept for API compatibility
        if sol_price_usd is None:
            sol_price_usd = self._fetch_sol_price_usd()
        return pnl_tracker.get_session_top_gainers(
            open_positions=self.get_positions(),
            sol_price_usd=sol_price_usd,
            limit=limit,
        )

    @staticmethod
    def _candidate_to_dict(candidate) -> Dict[str, Any]:
        from watchlist_scanner import watchlist_candidate_qualifies

        usd_gain = getattr(candidate, "day_usd_gain", None)
        if usd_gain is None:
            usd_gain = getattr(candidate, "usd_gain_baseline", None)
        session_gain = getattr(candidate, "session_usd_gain", None)
        day_gain = getattr(candidate, "day_usd_gain", None)
        day_pct_gain = getattr(candidate, "day_pct_gain", None)
        qualifies = (
            candidate.source == "watchlist_mint"
            and watchlist_candidate_qualifies(candidate)
        )
        return {
            "mint": candidate.mint,
            "symbol": candidate.symbol,
            "name": candidate.name,
            "price_usd": candidate.price_usd,
            "liquidity_usd": candidate.liquidity_usd,
            "volume_24h_usd": candidate.volume_24h_usd,
            "momentum_pct": candidate.momentum_pct,
            "price_change_5m": candidate.price_change_5m,
            "price_change_1h": candidate.price_change_1h,
            "dex": candidate.dex,
            "source": candidate.source,
            "usd_gain_baseline": usd_gain,
            "session_usd_gain": session_gain,
            "day_usd_gain": day_gain,
            "day_pct_gain": day_pct_gain,
            "qualifies": qualifies,
            "entry_status": "eligible" if qualifies else ("standby" if candidate.source == "watchlist_mint" else None),
        }

    def _position_to_dict(self, position, current_price: float) -> Dict[str, Any]:
        pnl_pct = position.pnl_pct(current_price)
        levels = position.tp_levels or Config.TAKE_PROFIT_LEVELS
        tp_levels = [
            {"index": i, "pct": pct, "hit": i in position.tp_levels_hit}
            for i, pct in enumerate(levels)
        ]
        levels_hit = list(position.tp_levels_hit)
        return {
            "mint": position.mint,
            "symbol": position.symbol,
            "entry_price": position.entry_price,
            "current_price": current_price,
            "size_sol": position.size_sol,
            "sol_invested": position.sol_invested,
            "token_decimals": position.token_decimals,
            "token_amount": position.remaining_token_amount_raw / (10 ** (position.token_decimals or 0))
            if position.token_decimals is not None and position.remaining_token_amount_raw
            else None,
            "pnl_pct": pnl_pct,
            "peak_pnl_pct": position.peak_pnl_pct,
            "trough_pnl_pct": position.trough_pnl_pct,
            "entry_time": position.entry_time,
            "hold_sec": time.time() - position.entry_time,
            "tp_levels": tp_levels,
            "levels_hit": levels_hit,
            "l1_hit": 0 in levels_hit,
            "l2_hit": 1 in levels_hit,
            "instant_hit": position.peak_pnl_pct >= Config.INSTANT_EXIT_3PCT,
            "instant_5_hit": position.peak_pnl_pct >= Config.INSTANT_PROFIT_EXIT_PCT,
            "remaining_pct": position.remaining_pct,
            "remaining_token_amount_raw": position.remaining_token_amount_raw,
            "initial_token_amount_raw": position.initial_token_amount_raw,
            "take_profit_levels": levels,
            "take_profit_portions": position.tp_portions or Config.TAKE_PROFIT_PORTIONS,
            "target_net_profit_sol": position.target_net_profit_sol,
            "fee_budget_sol": position.fee_budget_sol,
            "estimated_fees_sol": position.estimated_fees_sol,
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            bot = self._bot
            if not bot:
                return []
            positions = bot.strategy.get_open_positions()
            if not positions:
                return []
            mints = [p.mint for p in positions]
            prices = bot.price_feed.update_with_retry(mints)

        result = []
        for position in positions:
            current_price = prices.get(position.mint) or position.entry_price
            peak_price = bot.price_feed.get_peak_price_since(
                position.mint, position.entry_time
            )
            if peak_price and peak_price > 0:
                position.update_peak_pnl(peak_price)
            trough_price = bot.price_feed.get_trough_price_since(
                position.mint, position.entry_time
            )
            if trough_price and trough_price > 0:
                position.update_peak_pnl(trough_price)
            position.update_peak_pnl(current_price)
            result.append(self._position_to_dict(position, current_price))
        return result

    def get_position(self) -> Optional[Dict[str, Any]]:
        positions = self.get_positions()
        return positions[0] if positions else None

    @staticmethod
    def find_unclosed_journal_buy(
        *,
        mint: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the latest unclosed buy from trades.jsonl matching mint or symbol."""
        path = Path(Config.TRADE_JOURNAL_PATH)
        if not path.exists():
            return None
        sym_key = (symbol or "").strip().upper()
        open_buys: Dict[str, Dict[str, Any]] = {}
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return None
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            action = event.get("action")
            m = event.get("mint")
            if not m:
                continue
            if action == "buy":
                open_buys[m] = event
            elif action in ("sell", "sell_partial"):
                open_buys.pop(m, None)
        if mint and mint in open_buys:
            return open_buys[mint]
        if sym_key:
            for buy in reversed(list(open_buys.values())):
                if str(buy.get("symbol", "")).upper() in (sym_key, sym_key.replace("O", "0"), sym_key.replace("0", "O")):
                    return buy
        return None

    def force_sell(
        self,
        *,
        mint: Optional[str] = None,
        symbol: Optional[str] = None,
        reason: str = "sell_manual",
    ) -> Dict[str, Any]:
        """Force-close a position by mint/symbol (in-memory or journal-rehydrated)."""
        from scanner import MoverCandidate

        target_mint = mint
        target_symbol = symbol
        with self._lock:
            bot = self._bot
            loop = self._loop
            running = bot is not None and self._bot_loop_active(bot)
            dry_run = self._dry_run

        if running and bot and loop:
            positions = bot.strategy.get_open_positions()
            position = None
            for pos in positions:
                if target_mint and pos.mint == target_mint:
                    position = pos
                    break
                if target_symbol and pos.symbol.upper() == target_symbol.upper():
                    position = pos
                    break
            if position:
                future = asyncio.run_coroutine_threadsafe(
                    bot.force_sell_position(position, reason=reason),
                    loop,
                )
                try:
                    journal = future.result(timeout=90)
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}
                if not journal:
                    return {"ok": False, "error": "sell_failed"}
                return {
                    "ok": True,
                    "mint": position.mint,
                    "symbol": position.symbol,
                    "reason": reason,
                    "pnl_pct": journal.get("pnl_pct"),
                    "net_pnl_sol": journal.get("net_pnl_sol"),
                    "sol_out": journal.get("sol_out"),
                    "source": "running_bot",
                }

        buy = self.find_unclosed_journal_buy(mint=target_mint, symbol=target_symbol)
        if not buy:
            return {"ok": False, "error": "no_open_position_found"}

        async def _bootstrap_sell() -> Dict[str, Any]:
            bot = TradingBot(dry_run=dry_run if running else True)
            await bot.initialize(setup_signals=False)
            try:
                candidate = MoverCandidate(
                    mint=buy["mint"],
                    symbol=buy.get("symbol", "?"),
                    name=buy.get("name", buy.get("symbol", "?")),
                    pair_address="",
                    dex="journal",
                    price_usd=float(buy.get("entry_price") or 0),
                    liquidity_usd=50_000.0,
                    volume_24h_usd=100_000.0,
                    momentum_pct=float(buy.get("momentum") or 0),
                    price_change_5m=0.0,
                    price_change_1h=0.0,
                    source="journal",
                )
                position = bot.strategy.open_position(
                    candidate,
                    float(buy["entry_price"]),
                    float(buy.get("size_sol") or buy.get("sol_in") or 0.1),
                    float(buy.get("momentum") or 0),
                    token_amount_raw=int(buy.get("token_amount_raw") or 0),
                    fee_budget_sol=buy.get("estimated_fees_sol"),
                    estimated_fees_sol=buy.get("estimated_fees_sol"),
                )
                position.entry_time = float(buy.get("timestamp") or time.time())
                journal = await bot.force_sell_position(position, reason=reason)
                if not journal:
                    return {"ok": False, "error": "sell_failed"}
                return {
                    "ok": True,
                    "mint": buy["mint"],
                    "symbol": buy.get("symbol"),
                    "reason": reason,
                    "pnl_pct": journal.get("pnl_pct"),
                    "net_pnl_sol": journal.get("net_pnl_sol"),
                    "sol_out": journal.get("sol_out"),
                    "source": "journal_bootstrap",
                }
            finally:
                if bot.solana:
                    await bot.solana.close()

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_bootstrap_sell())
        finally:
            loop.close()

    def get_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        path = Path(Config.TRADE_JOURNAL_PATH)
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return []
        trades = []
        for line in lines[-limit:]:
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        trades.reverse()
        return trades

    def get_logs(self, limit: int = 100) -> List[str]:
        with self._lock:
            return list(self._log_buffer)[-limit:]

    async def _fetch_balance(self) -> float:
        """Query native SOL for the session/.env wallet pubkey (never an ephemeral key)."""
        key = self._resolve_private_key()
        if not key:
            raise RuntimeError("No wallet key configured for balance fetch")
        # dry_run=False so SolanaClient never invents an ephemeral keypair for this query.
        client = SolanaClient(private_key=key, dry_run=False)
        try:
            # Confirm pubkey matches session display address when set.
            session_pk = self.get_session_public_key()
            if session_pk and str(client.public_key) != session_pk:
                logger.warning(
                    "Balance pubkey mismatch: client=%s session=%s — using session keypair",
                    client.public_key,
                    session_pk,
                )
            return await client.get_balance()
        finally:
            await client.close()

    def get_balance(self) -> Optional[float]:
        """Return wallet SOL balance, or None when RPC/key lookup fails (not 0.0)."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._fetch_balance())
        except Exception as exc:
            logger.error("Balance fetch failed: %s", exc)
            return None
        finally:
            loop.close()

    def get_wallet_balance(self, paper_mode: Optional[bool] = None) -> Dict[str, Any]:
        """Return wallet balance; paper mode uses simulated paper session balance."""
        with self._lock:
            if paper_mode is None:
                paper_mode = self._dry_run
        if paper_mode:
            balance = paper_session_manager.get_simulated_balance()
            return {
                "balance": balance,
                "simulated": True,
                "target_balance": paper_session_manager.get_target_balance(),
                "min_fund_sol": Config.MIN_PAPER_FUND_SOL,
                "min_paper_fund_sol": Config.MIN_PAPER_FUND_SOL,
                "min_fund_waiver_active": RiskManager.min_fund_waived(),
            }
        balance = self.get_balance()
        tradeable = live_tradeable_balance_manager.get_balance()
        return {
            "balance": balance,
            "simulated": False,
            "tradeable_balance": tradeable,
            "effective_balance": (
                min(balance, tradeable) if balance is not None else None
            ),
            "min_fund_sol": Config.MIN_FUND_SOL,
            "min_fund_waiver_active": RiskManager.min_fund_waived(),
            "min_live_tradeable_balance_sol": MIN_LIVE_TRADEABLE_BALANCE_SOL,
            "max_live_tradeable_balance_sol": MAX_LIVE_TRADEABLE_BALANCE_SOL,
        }

    def set_paper_balance(
        self,
        amount: float,
        quote_currency: Optional[str] = None,
        trade_sol_wsol: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Set configured paper balance (SOL-equivalent); persists across restarts."""
        from config import normalize_paper_balance_sol

        if quote_currency is not None:
            paper_session_manager.set_quote_currency(quote_currency)
        if trade_sol_wsol is not None:
            paper_session_manager.set_trade_sol_wsol(bool(trade_sol_wsol))
        normalized = paper_session_manager.set_target_balance(
            normalize_paper_balance_sol(amount)
        )
        return {
            "paper_simulated_balance_sol": paper_session_manager.get_simulated_balance(),
            "paper_target_balance_sol": normalized,
            "paper_quote_currency": paper_session_manager.get_quote_currency(),
            "stable_quote_trade_sol_wsol": paper_session_manager.get_trade_sol_wsol(),
        }

    def set_paper_quote_currency(
        self,
        currency: str,
        trade_sol_wsol: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Persist paper display/quote currency without changing SOL balance."""
        normalized = paper_session_manager.set_quote_currency(currency)
        if trade_sol_wsol is not None:
            paper_session_manager.set_trade_sol_wsol(bool(trade_sol_wsol))
        return {
            "paper_quote_currency": normalized,
            "paper_target_balance_sol": paper_session_manager.get_target_balance(),
            "paper_simulated_balance_sol": paper_session_manager.get_simulated_balance(),
            "stable_quote_trade_sol_wsol": paper_session_manager.get_trade_sol_wsol(),
            "live_stable_quote_enabled": Config.LIVE_STABLE_QUOTE_ENABLED,
        }

    def reset_paper_balance(self) -> Dict[str, Any]:
        """Reset running paper balance to the configured target amount."""
        target = paper_session_manager.reset_balance()
        return {
            "paper_simulated_balance_sol": paper_session_manager.get_simulated_balance(),
            "paper_target_balance_sol": target,
            "paper_quote_currency": paper_session_manager.get_quote_currency(),
            "stable_quote_trade_sol_wsol": paper_session_manager.get_trade_sol_wsol(),
        }

    def set_live_tradeable_balance(self, amount: float) -> Dict[str, Any]:
        """Set configured live tradeable balance cap (SOL); persists across restarts."""
        from config import normalize_live_tradeable_balance_sol

        normalized = live_tradeable_balance_manager.set_balance(
            normalize_live_tradeable_balance_sol(amount)
        )
        wallet_balance = self.get_balance()
        effective = (
            min(wallet_balance, normalized) if wallet_balance is not None else None
        )
        return {
            "live_tradeable_balance_sol": normalized,
            "wallet_balance_sol": wallet_balance,
            "effective_balance_sol": effective,
        }

    def get_live_tradeable_balance(self) -> Dict[str, Any]:
        tradeable = live_tradeable_balance_manager.get_balance()
        wallet_balance = self.get_balance()
        effective = (
            min(wallet_balance, tradeable) if wallet_balance is not None else None
        )
        return {
            "live_tradeable_balance_sol": tradeable,
            "wallet_balance_sol": wallet_balance,
            "effective_balance_sol": effective,
            "min_live_tradeable_balance_sol": MIN_LIVE_TRADEABLE_BALANCE_SOL,
            "max_live_tradeable_balance_sol": MAX_LIVE_TRADEABLE_BALANCE_SOL,
        }

    def update_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if "stop_loss_pct" in payload and payload["stop_loss_pct"] is not None:
            payload = dict(payload)
            payload["stop_loss_pct"] = normalize_stop_loss_pct(float(payload["stop_loss_pct"]))
        if "trade_size_sol" in payload and payload["trade_size_sol"] is not None:
            payload = dict(payload)
            payload["trade_size_sol"] = normalize_trade_size(float(payload["trade_size_sol"]))
        if "entry_momentum_pct" in payload and payload["entry_momentum_pct"] is not None:
            payload = dict(payload)
            payload["entry_momentum_pct"] = normalize_entry_momentum_pct(
                float(payload["entry_momentum_pct"])
            )
        mapping = {
            "trade_size_sol": "TRADE_SIZE_SOL",
            "entry_momentum_pct": "ENTRY_MOMENTUM_PCT",
            "take_profit_levels": "TAKE_PROFIT_LEVELS",
            "take_profit_portions": "TAKE_PROFIT_PORTIONS",
            "stop_loss_pct": "STOP_LOSS_PCT",
            "solana_rpc_url": "SOLANA_RPC_URL",
            "scan_interval_sec": "SCAN_INTERVAL_SEC",
            "price_poll_sec": "PRICE_POLL_SEC",
            "max_position_sol": "MAX_POSITION_SOL",
            "min_sol_reserve": "MIN_SOL_RESERVE",
            "dry_run": "DRY_RUN",
            "include_pumpfun": "INCLUDE_PUMPFUN",
            "scan_pumpfun": "SCAN_PUMPFUN",
            "scan_birdeye": "SCAN_BIRDEYE",
            "scan_gmgn": "SCAN_GMGN",
            "gmgn_enabled": "GMGN_ENABLED",
            "min_liquidity_usd": "MIN_LIQUIDITY_USD",
            "min_volume_24h_usd": "MIN_VOLUME_24H_USD",
            "non_watchlist_min_volume_24h_usd": "NON_WATCHLIST_MIN_VOLUME_24H_USD",
            "min_momentum_pct": "MIN_MOMENTUM_PCT",
            "min_expected_net_profit_sol": "MIN_EXPECTED_NET_PROFIT_SOL",
            "min_net_win_sol": "MIN_NET_WIN_SOL",
            "loss_reentry_cooldown_minutes": "LOSS_REENTRY_COOLDOWN_MINUTES",
            "loss_reentry_repeat_cooldown_minutes": "LOSS_REENTRY_REPEAT_COOLDOWN_MINUTES",
            "weaken_exit_min_profit_pct": "WEAKEN_EXIT_MIN_PROFIT_PCT",
            "max_loss_per_trade_sol": "MAX_LOSS_PER_TRADE_SOL",
            "min_momentum_pct": "MIN_MOMENTUM_PCT",
            "reentry_dip_pct": "REENTRY_DIP_PCT",
            "max_potential_mode": "MAX_POTENTIAL_MODE",
            "setup_learning_min_win_lean": "SETUP_LEARNING_MIN_WIN_LEAN",
            "spike_min_liquidity_usd": "SPIKE_MIN_LIQUIDITY_USD",
            "gmgn_min_liquidity_usd": "GMGN_MIN_LIQUIDITY_USD",
            "reentry_retry_max_attempts": "REENTRY_RETRY_MAX_ATTEMPTS",
        }
        updates = {}
        for api_key, config_key in mapping.items():
            if api_key in payload and payload[api_key] is not None:
                updates[config_key] = payload[api_key]
        result = Config.update_runtime(**updates)
        if "SETUP_LEARNING_MIN_WIN_LEAN" in result.get("applied", {}):
            from session_entry_tuning import apply_runtime_win_lean

            apply_runtime_win_lean(result["applied"]["SETUP_LEARNING_MIN_WIN_LEAN"])
        if "SOLANA_RPC_URL" in result.get("applied", {}):
            with self._lock:
                bot = self._bot
            if bot is not None:
                try:
                    bot.apply_rpc_endpoint(result["applied"]["SOLANA_RPC_URL"] or None)
                except Exception as exc:
                    logger.warning("RPC hot-apply on running bot failed: %s", exc)
            result["rpc_endpoint"] = Config.get_rpc_endpoint()
            result["rpc_persisted"] = True
        return result

    def apply_user_rpc(self, rpc_url: Optional[str]) -> Dict[str, Any]:
        """Validate + persist dedicated RPC (Helius), hot-swap client, return masked host.

        Rejects empty and public mainnet URLs. Does not start trading.
        """
        from urllib.parse import urlparse

        from config import is_public_rpc_url, normalize_user_rpc_url

        raw = str(rpc_url or "").strip()
        if not raw:
            raise ValueError(
                "Paste your Helius (dedicated) RPC URL in the RPC field, then click Apply RPC."
            )
        if is_public_rpc_url(raw):
            raise ValueError(
                "Public mainnet RPC cannot be applied for Live. "
                "Use a Helius dedicated RPC URL, then click Apply RPC."
            )
        cleaned = normalize_user_rpc_url(raw)
        if not cleaned:
            raise ValueError(
                "Invalid RPC URL. Paste a Helius https://... URL, then click Apply RPC."
            )
        result = self.update_config({"solana_rpc_url": cleaned})
        try:
            host = (urlparse(cleaned).hostname or "").strip() or "(unknown)"
        except Exception:
            host = "(unknown)"
        helius_like = "helius" in host.lower()
        message = (
            f"Helius RPC applied ({host})"
            if helius_like
            else f"RPC applied ({host})"
        )
        logger.info("Apply RPC persisted host=%s hot_swapped=%s", host, bool(result.get("rpc_persisted")))
        return {
            **result,
            "rpc_applied": True,
            "rpc_host": host,
            "rpc_message": message,
        }

    def restore_config_bookmark(self) -> Dict[str, Any]:
        from config import restore_config_bookmark

        return restore_config_bookmark()

    def apply_best_win_strategy(self, *, save_bookmark: bool = True) -> Dict[str, Any]:
        from config import apply_best_win_strategy

        return apply_best_win_strategy(save_bookmark=save_bookmark)

    def apply_balanced_win_strategy(self, *, save_bookmark: bool = True) -> Dict[str, Any]:
        from config import apply_balanced_win_strategy

        return apply_balanced_win_strategy(save_bookmark=save_bookmark)

    def apply_steady_trade_strategy(self, *, save_bookmark: bool = True) -> Dict[str, Any]:
        from config import apply_steady_trade_strategy

        return apply_steady_trade_strategy(save_bookmark=save_bookmark)

    def save_config_bookmark(self, label: str = "pre-best-win", description: str = "") -> Dict[str, Any]:
        from config import save_config_bookmark as _save

        return _save(
            label=label,
            description=description or "Snapshot before Best Win preset — use Revert to bookmark",
        )

    def save_key_to_env(self, env_path: str | None = None) -> None:
        with self._lock:
            if not self._private_key:
                raise RuntimeError("No session wallet key to save")
            key = self._private_key

        path = Path(env_path) if env_path else PROJECT_ROOT / ".env"
        lines: List[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()

        found = False
        new_lines = []
        for line in lines:
            if line.startswith("SOLANA_PRIVATE_KEY="):
                new_lines.append(f"SOLANA_PRIVATE_KEY={key}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"SOLANA_PRIVATE_KEY={key}")

        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    @staticmethod
    def _reentry_retry_status() -> dict:
        from reentry_retry import reentry_retry_manager

        return reentry_retry_manager.status_snapshot()

    def reentry_retry_status(self) -> dict:
        return self._reentry_retry_status()

    def get_pending_actions(self) -> List[Dict[str, Any]]:
        from reentry_retry import reentry_retry_manager

        return reentry_retry_manager.get_pending_actions()

    def decide_reentry_action(
        self,
        mint: str,
        *,
        allow: bool,
        deny_similar_pattern: bool = False,
    ) -> Dict[str, Any]:
        from reentry_retry import reentry_retry_manager

        mint = (mint or "").strip()
        if not mint:
            raise ValueError("mint is required")
        result = reentry_retry_manager.apply_decision(
            mint,
            allow=allow,
            deny_similar_pattern=deny_similar_pattern,
        )
        if allow:
            with self._lock:
                bot = self._bot
                if bot:
                    bot.strategy.clear_mint_blocks(mint)
                    bot._record_action(
                        f"User allowed re-chase for {result.get('symbol') or mint[:8]}"
                    )
        else:
            with self._lock:
                bot = self._bot
                if bot:
                    bot._record_action(
                        f"User denied re-chase for {result.get('symbol') or mint[:8]}"
                    )
        return result

    def unblock_mint(
        self, mint: str, *, symbol: str = "", name: str = ""
    ) -> Dict[str, Any]:
        from reentry_retry import reentry_retry_manager
        from stock_token_filter import add_stock_allowlist_mint, is_stock_related_token

        mint = (mint or "").strip()
        if not mint:
            raise ValueError("mint is required")

        before = self._mint_block_snapshot(mint, symbol=symbol, name=name)
        add_stock_allowlist_mint(mint)

        session_cleared: List[str] = []
        with self._lock:
            bot = self._bot
            if bot:
                session_cleared = bot.strategy.clear_mint_blocks(mint).get("cleared", [])

        reentry_cleared = reentry_retry_manager.clear_mint_denial(mint)
        after = self._mint_block_snapshot(mint, symbol=symbol, name=name)
        return {
            "mint": mint,
            "symbol": symbol or None,
            "before_blocks": before["blocks"],
            "session_cleared": session_cleared,
            "reentry_cleared": reentry_cleared,
            "after_blocks": after["blocks"],
            "unblocked": not after["blocked"],
        }

    def _mint_block_snapshot(
        self, mint: str, *, symbol: str = "", name: str = ""
    ) -> Dict[str, Any]:
        from stock_token_filter import is_stock_related_token

        blocks: List[str] = []
        if is_stock_related_token(mint=mint, symbol=symbol, name=name):
            blocks.append("stock_filter")
        with self._lock:
            bot = self._bot
            if bot:
                status = bot.strategy.mint_block_status(mint, symbol=symbol, name=name)
                for block in status["blocks"]:
                    if block not in blocks:
                        blocks.append(block)
        return {"mint": mint, "blocked": bool(blocks), "blocks": blocks}


bot_manager = BotManager()
