"""Validate session wallet set while running + base58 auto-sign hot-apply."""

from __future__ import annotations

import base58
from solders.keypair import Keypair

from bot import TradingBot
from bot_manager import bot_manager
from solana_client import SolanaClient


def _ephemeral_base58() -> tuple[str, str]:
    kp = Keypair()
    secret = bytes(kp)
    return base58.b58encode(secret).decode("ascii"), str(kp.pubkey())


def test_set_wallet_while_running_stores_session_key():
    key, pubkey = _ephemeral_base58()
    prev_status = bot_manager._status
    prev_key = bot_manager._private_key
    prev_pub = bot_manager._public_key
    prev_bot = bot_manager._bot
    try:
        bot_manager._status = "running"
        bot_manager._bot = None
        result = bot_manager.set_wallet(key)
        assert result["public_key"] == pubkey
        assert result.get("auto_sign") is True
        assert bot_manager._private_key == key
        assert bot_manager._public_key == pubkey
        assert bot_manager._resolve_private_key() == key
    finally:
        bot_manager._status = prev_status
        bot_manager._private_key = prev_key
        bot_manager._public_key = prev_pub
        bot_manager._bot = prev_bot
    print("PASS: set_wallet allowed while running (session memory)")


def test_apply_session_key_hot_swaps_signer():
    key, pubkey = _ephemeral_base58()
    bot = TradingBot(dry_run=False, private_key=None)
    bot.solana = SolanaClient(private_key=None, dry_run=False)
    bot.jupiter = type("J", (), {"public_key": "old"})()
    bot.apply_session_key(key)
    assert bot._private_key == key
    assert str(bot.solana.public_key) == pubkey
    assert bot.jupiter.public_key == pubkey
    # Sign path uses keypair — round-trip load matches
    loaded = SolanaClient(private_key=key, dry_run=True)
    assert str(loaded.public_key) == pubkey
    print("PASS: apply_session_key hot-swaps SolanaClient/Jupiter for auto-sign")


def test_keypair_load_does_not_echo_secret_in_error():
    try:
        SolanaClient(private_key="!!!not-valid-base58!!!", dry_run=True)
        raise AssertionError("expected load failure")
    except Exception as exc:
        msg = str(exc)
        assert "!!!not-valid-base58!!!" not in msg
    print("PASS: invalid key load does not echo secret material")


if __name__ == "__main__":
    test_set_wallet_while_running_stores_session_key()
    test_apply_session_key_hot_swaps_signer()
    test_keypair_load_does_not_echo_secret_in_error()
    print("All wallet session key checks passed.")
