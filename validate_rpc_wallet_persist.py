"""Validate RPC .env persistence + Stop clears session wallet (UI blanks RPC)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import base58
from solders.keypair import Keypair

from bot_manager import bot_manager
from config import Config, _write_env_keys
from solana_client import SolanaClient


def _ephemeral_base58():
    kp = Keypair()
    return base58.b58encode(bytes(kp)).decode("ascii"), str(kp.pubkey())


def test_rpc_update_persists_env_and_hot_endpoint():
    prev = Config.SOLANA_RPC_URL
    prev_env = os.environ.get("SOLANA_RPC_URL")
    custom = "https://rpc.example.invalid/"
    with tempfile.TemporaryDirectory() as tmp:
        env_path = Path(tmp) / ".env"
        with patch("config.PROJECT_ROOT", Path(tmp)):
            result = Config.update_runtime(SOLANA_RPC_URL=custom)
            assert "SOLANA_RPC_URL" in result["applied"]
            assert Config.get_rpc_endpoint() == custom
            assert env_path.exists()
            custom2 = "https://rpc.example2.invalid/"
            mgr = bot_manager.update_config({"solana_rpc_url": custom2})
            assert mgr.get("rpc_persisted") is True
            assert Config.SOLANA_RPC_URL == custom2
            assert f"SOLANA_RPC_URL={custom2}" in env_path.read_text(encoding="utf-8")
    Config.SOLANA_RPC_URL = prev
    if prev_env is None:
        os.environ.pop("SOLANA_RPC_URL", None)
    else:
        os.environ["SOLANA_RPC_URL"] = prev_env
    print("PASS: SOLANA_RPC_URL persists to .env")


def test_rpc_hot_swap_on_solana_client():
    client = SolanaClient(private_key=None, dry_run=True)
    old = client.rpc_endpoint
    assert client.apply_rpc_endpoint("https://rpc.hot-swap.test/") == "https://rpc.hot-swap.test/"
    client.apply_rpc_endpoint(old)
    print("PASS: hot-swap")


def test_stop_clears_session_wallet_keeps_env_rpc():
    key, pubkey = _ephemeral_base58()
    prev_key, prev_pub = bot_manager._private_key, bot_manager._public_key
    prev_status, prev_bot, prev_thread = bot_manager._status, bot_manager._bot, bot_manager._thread
    prev_rpc = Config.SOLANA_RPC_URL
    prev_env = os.environ.get("SOLANA_RPC_URL")
    custom = "https://stop-clear-rpc.example.invalid/"
    try:
        bot_manager._status = "stopped"
        bot_manager._bot = None
        bot_manager._thread = None
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            with patch("config.PROJECT_ROOT", Path(tmp)):
                bot_manager.set_wallet(key)
                assert bot_manager.get_status().get("session_public_key") == pubkey
                mgr = bot_manager.update_config({"solana_rpc_url": custom})
                assert mgr.get("rpc_persisted") is True
                assert Config.SOLANA_RPC_URL == custom
                assert f"SOLANA_RPC_URL={custom}" in env_path.read_text(encoding="utf-8")

                bot_manager.stop()
                assert bot_manager._private_key is None
                assert bot_manager.get_session_public_key() is None
                assert bot_manager.get_status().get("has_session_wallet") is False
                # Stop must not wipe .env RPC — UI blanks the field separately.
                assert Config.SOLANA_RPC_URL == custom
                assert f"SOLANA_RPC_URL={custom}" in env_path.read_text(encoding="utf-8")

                bot_manager.set_wallet(key)
                forced = bot_manager.force_reset()
                assert forced["status"] == "stopped"
                assert bot_manager._private_key is None
                assert Config.SOLANA_RPC_URL == custom
    finally:
        bot_manager._private_key = prev_key
        bot_manager._public_key = prev_pub
        bot_manager._status = prev_status
        bot_manager._bot = prev_bot
        bot_manager._thread = prev_thread
        Config.SOLANA_RPC_URL = prev_rpc
        if prev_env is None:
            os.environ.pop("SOLANA_RPC_URL", None)
        else:
            os.environ["SOLANA_RPC_URL"] = prev_env
    print("PASS: Stop/Force Reset clear session wallet; .env RPC kept")


def test_to_dict_includes_rpc_endpoint():
    d = Config.to_dict()
    assert d["rpc_endpoint"] == Config.get_rpc_endpoint()
    print("PASS: rpc_endpoint")


def test_write_env_keys_rpc_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        with patch("config.PROJECT_ROOT", Path(tmp)):
            _write_env_keys({"solana_rpc_url": "https://persist.test/"}, {"solana_rpc_url": "SOLANA_RPC_URL"})
            assert "SOLANA_RPC_URL=https://persist.test/" in (Path(tmp) / ".env").read_text(encoding="utf-8")
    print("PASS: write env")


if __name__ == "__main__":
    test_rpc_update_persists_env_and_hot_endpoint()
    test_rpc_hot_swap_on_solana_client()
    test_stop_clears_session_wallet_keeps_env_rpc()
    test_to_dict_includes_rpc_endpoint()
    test_write_env_keys_rpc_roundtrip()
    print("All RPC/wallet persist checks passed.")
