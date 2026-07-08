"""Persist user-configured live tradeable balance (SOL cap for sizing)."""

import json
import logging
import threading
from pathlib import Path

from config import (
    Config,
    PROJECT_ROOT,
    normalize_live_tradeable_balance_sol,
)


logger = logging.getLogger(__name__)


class LiveTradeableBalanceManager:
    """Thread-safe configured live tradeable balance."""

    def __init__(self):
        self._lock = threading.RLock()
        self._path = Path(Config.LIVE_TRADEABLE_STATE_PATH)
        self._balance_sol = Config.LIVE_TRADEABLE_BALANCE_SOL
        self._load()

    def get_balance(self) -> float:
        with self._lock:
            return self._balance_sol

    def set_balance(self, amount: float) -> float:
        normalized = normalize_live_tradeable_balance_sol(amount)
        with self._lock:
            self._balance_sol = normalized
            Config.LIVE_TRADEABLE_BALANCE_SOL = normalized
        self._persist()
        self._save_to_env(normalized)
        return normalized

    def _persist(self) -> None:
        with self._lock:
            payload = {"tradeable_balance_sol": self._balance_sol}
        try:
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to persist live tradeable balance: %s", exc)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if "tradeable_balance_sol" not in data:
            return
        try:
            normalized = normalize_live_tradeable_balance_sol(
                float(data["tradeable_balance_sol"])
            )
        except (TypeError, ValueError):
            return
        with self._lock:
            self._balance_sol = normalized
            Config.LIVE_TRADEABLE_BALANCE_SOL = normalized

    @staticmethod
    def _save_to_env(amount: float) -> None:
        path = PROJECT_ROOT / ".env"
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
        key = "LIVE_TRADEABLE_BALANCE_SOL"
        found = False
        new_lines: list[str] = []
        for line in lines:
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={amount}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={amount}")
        try:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not save %s to .env: %s", key, exc)


live_tradeable_balance_manager = LiveTradeableBalanceManager()
