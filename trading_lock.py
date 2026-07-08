"""Trading execution lock — only the bot strategy thread may execute swaps."""

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class TradingLock:
    """Ensures Jupiter swap execution is limited to the active bot strategy loop."""

    def __init__(self):
        self._lock = threading.Lock()
        self._bot_thread_id: Optional[int] = None

    def register_bot_thread(self) -> None:
        with self._lock:
            self._bot_thread_id = threading.get_ident()
            logger.debug("Trading lock registered for bot thread %s", self._bot_thread_id)

    def unregister_bot_thread(self) -> None:
        with self._lock:
            self._bot_thread_id = None

    def is_authorized(
        self,
        is_running: Callable[[], bool],
        *,
        silent: bool = False,
        dry_run: bool = False,
    ) -> bool:
        """Return True only when the bot is running and the caller is the bot thread."""
        if dry_run:
            return True
        with self._lock:
            if not is_running():
                if not silent:
                    logger.warning("SECURITY: swap blocked — bot is not running")
                return False
            if self._bot_thread_id is None:
                if not silent:
                    logger.warning("SECURITY: swap blocked — no bot thread registered")
                return False
            if threading.get_ident() != self._bot_thread_id:
                if not silent:
                    logger.warning(
                        "SECURITY: swap blocked — caller thread %s is not bot thread %s",
                        threading.get_ident(),
                        self._bot_thread_id,
                    )
                return False
            return True


trading_lock = TradingLock()
