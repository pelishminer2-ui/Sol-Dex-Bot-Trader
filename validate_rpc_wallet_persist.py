"""Validate dashboard credentials are session-only and cleared on Stop."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import base58
from solders.keypair import Keypair

from bot_manager import bot_manager
from config import Config
from solana_client import SolanaClient


def _ephemeral_base58() -> tuple[str, str]:
    keypair = Keypair()
    return base58.b58encode(bytes(keypair)).decode("ascii"), str(keypair.pubkey())


def test_dashboard_rpc_is_session_only_and_stop_clears_credentials() -> None:
    key, pubkey = _ephemeral_base58()
    custom_rpc = "https://dashboard-rpc.example.invalid/"
    previous = {
        "key": bot_manager._private_key,
        "pub": bot_manager._public_key,
        "session_rpc": bot_manager._session_rpc_url,
        "baseline": bot_manager._rpc_env_baseline,
        "config_rpc": Config.SOLANA_RPC_URL,
        "env_rpc": os.environ.get("SOLANA_RPC_URL"),
        "status": bot_manager._status,
        "bot": bot_manager._bot,
        "thread": bot_manager._thread,
    }
    try:
        bot_manager._status = "stopped"
        bot_manager._bot = None
        bot_manager._thread = None
        bot_manager._rpc_env_baseline = "https://env-baseline.example.invalid/"
        Config.update_runtime(SOLANA_RPC_URL=bot_manager._rpc_env_baseline)

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            with patch("config.PROJECT_ROOT", Path(tmp)):
                bot_manager.set_wallet(key)
                result = bot_manager.update_config({"solana_rpc_url": custom_rpc})
                assert result["rpc_persisted"] is False
                assert result["session_rpc_url"] == custom_rpc
                assert Config.SOLANA_RPC_URL == custom_rpc
                assert bot_manager.get_session_rpc_url() == custom_rpc
                assert not env_path.exists(), "dashboard RPC must not write .env"

                stopped = bot_manager.stop()
                assert stopped["status"] == "stopped"
                assert bot_manager._private_key is None
                assert bot_manager.get_session_public_key() is None
                assert bot_manager.get_session_rpc_url() == ""
                assert Config.SOLANA_RPC_URL == bot_manager._rpc_env_baseline
                assert not env_path.exists(), "Stop must not write .env"

                bot_manager.set_wallet(key)
                bot_manager.update_config({"solana_rpc_url": custom_rpc})
                forced = bot_manager.force_reset()
                assert forced["status"] == "stopped"
                assert bot_manager._private_key is None
                assert bot_manager.get_session_rpc_url() == ""
                assert Config.SOLANA_RPC_URL == bot_manager._rpc_env_baseline
                assert not env_path.exists(), "Force Reset must not write .env"
                assert pubkey != ""
    finally:
        bot_manager._private_key = previous["key"]
        bot_manager._public_key = previous["pub"]
        bot_manager._session_rpc_url = previous["session_rpc"]
        bot_manager._rpc_env_baseline = previous["baseline"]
        Config.SOLANA_RPC_URL = previous["config_rpc"]
        if previous["env_rpc"] is None:
            os.environ.pop("SOLANA_RPC_URL", None)
        else:
            os.environ["SOLANA_RPC_URL"] = previous["env_rpc"]
        bot_manager._status = previous["status"]
        bot_manager._bot = previous["bot"]
        bot_manager._thread = previous["thread"]
    print("PASS: Stop/Force Reset clear session wallet/RPC; dashboard RPC never writes .env")


def test_rpc_hot_swap_on_solana_client() -> None:
    client = SolanaClient(private_key=None, dry_run=True)
    old = client.rpc_endpoint
    assert client.apply_rpc_endpoint("https://rpc.hot-swap.test/") == "https://rpc.hot-swap.test/"
    client.apply_rpc_endpoint(old)
    print("PASS: hot-swap")


if __name__ == "__main__":
    test_dashboard_rpc_is_session_only_and_stop_clears_credentials()
    test_rpc_hot_swap_on_solana_client()
    print("All RPC/wallet session checks passed.")
