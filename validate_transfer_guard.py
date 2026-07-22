"""Validate transfer guard: authorized Jupiter swaps pass; unauthorized sends blocked."""

import asyncio
import json
from unittest.mock import MagicMock

from solders.hash import Hash
from solders.instruction import CompiledInstruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.transaction import VersionedTransaction

from app import app
from config import Config, SOL_MINT
from jupiter import JupiterExecutor, SwapQuote
from solana_client import SolanaClient
from trading_lock import trading_lock
from tx_authorizer import (
    JUPITER_V6_PROGRAM,
    AuthorizedTradeContext,
    UnauthorizedTransferError,
    tx_authorizer,
)


def _client():
    return app.test_client()


def _jupiter_tx_bytes(wallet: Pubkey) -> bytes:
    jupiter = Pubkey.from_string(JUPITER_V6_PROGRAM)
    ix = CompiledInstruction(program_id_index=1, data=bytes([9, 0, 0]), accounts=bytes([0]))
    message = Message.new_with_compiled_instructions(
        1,
        0,
        1,
        [wallet, jupiter],
        Hash.default(),
        [ix],
    )
    return bytes(VersionedTransaction.populate(message, []))


def _drain_tx_bytes(wallet: Pubkey, attacker: Pubkey) -> bytes:
    jupiter = Pubkey.from_string(JUPITER_V6_PROGRAM)
    transfer_data = (2).to_bytes(4, "little") + (1_000_000).to_bytes(8, "little")
    transfer_ix = CompiledInstruction(
        program_id_index=2, data=transfer_data, accounts=bytes([0, 1])
    )
    jupiter_ix = CompiledInstruction(program_id_index=3, data=bytes([1]), accounts=bytes([0]))
    message = Message.new_with_compiled_instructions(
        1,
        0,
        2,
        [wallet, attacker, SYSTEM_PROGRAM_ID, jupiter],
        Hash.default(),
        [transfer_ix, jupiter_ix],
    )
    return bytes(VersionedTransaction.populate(message, []))


def _running():
    return True


def test_authorized_jupiter_swap_passes():
    trading_lock.register_bot_thread()
    wallet = Pubkey.new_unique()
    context = AuthorizedTradeContext(
        mint="TokenMint1111111111111111111111111111111",
        side="buy",
        amount_sol=0.05,
        trade_id="test-trade-1",
    )
    token = tx_authorizer.authorize(context, mint_allowed=lambda m: True, is_running=_running)
    tx_bytes = _jupiter_tx_bytes(wallet)
    result = tx_authorizer.verify_and_consume(
        tx_bytes, token, str(wallet), is_running=_running
    )
    assert result.trade_id == "test-trade-1"
    print("PASS: authorized Jupiter swap path passes inspection")


def test_unauthorized_send_without_token_blocked():
    trading_lock.register_bot_thread()
    wallet = Pubkey.new_unique()
    tx_bytes = _jupiter_tx_bytes(wallet)
    try:
        tx_authorizer.verify_and_consume(tx_bytes, None, str(wallet), is_running=_running)
        raise AssertionError("expected UnauthorizedTransferError")
    except UnauthorizedTransferError:
        pass
    print("PASS: unauthorized send without token blocked")


def test_replay_token_blocked():
    trading_lock.register_bot_thread()
    wallet = Pubkey.new_unique()
    context = AuthorizedTradeContext(
        mint="TokenMint1111111111111111111111111111111",
        side="sell",
        amount_sol=0.1,
        trade_id="test-trade-2",
    )
    token = tx_authorizer.authorize(context, mint_allowed=lambda m: True, is_running=_running)
    tx_bytes = _jupiter_tx_bytes(wallet)
    tx_authorizer.verify_and_consume(tx_bytes, token, str(wallet), is_running=_running)
    try:
        tx_authorizer.verify_and_consume(tx_bytes, token, str(wallet), is_running=_running)
        raise AssertionError("expected replay to be blocked")
    except UnauthorizedTransferError:
        pass
    print("PASS: one-time token consumed — replay blocked")


def test_bare_sol_drain_blocked():
    trading_lock.register_bot_thread()
    wallet = Pubkey.new_unique()
    attacker = Pubkey.new_unique()
    context = AuthorizedTradeContext(
        mint="TokenMint1111111111111111111111111111111",
        side="buy",
        amount_sol=0.05,
        trade_id="test-trade-3",
    )
    token = tx_authorizer.authorize(context, mint_allowed=lambda m: True, is_running=_running)
    tx_bytes = _drain_tx_bytes(wallet, attacker)
    try:
        tx_authorizer.verify_and_consume(tx_bytes, token, str(wallet), is_running=_running)
        raise AssertionError("expected drain tx to be blocked")
    except UnauthorizedTransferError:
        pass
    print("PASS: bare SOL transfer to third party blocked")


def test_trading_lock_required_for_authorize():
    trading_lock.unregister_bot_thread()
    context = AuthorizedTradeContext(
        mint="TokenMint1111111111111111111111111111111",
        side="buy",
        amount_sol=0.05,
        trade_id="test-trade-4",
    )
    try:
        tx_authorizer.authorize(context, mint_allowed=lambda m: True, is_running=_running)
        raise AssertionError("expected authorize without trading lock to fail")
    except UnauthorizedTransferError:
        pass
    print("PASS: trading lock required for authorize")


def test_trading_lock_required_for_send():
    trading_lock.unregister_bot_thread()
    wallet = Pubkey.new_unique()
    tx_bytes = _jupiter_tx_bytes(wallet)
    try:
        tx_authorizer.verify_and_consume(
            tx_bytes, "fake-token", str(wallet), is_running=_running
        )
        raise AssertionError("expected send without trading lock to fail")
    except UnauthorizedTransferError:
        pass
    print("PASS: trading lock required for send")


def test_solana_client_blocks_unauthorized():
    trading_lock.unregister_bot_thread()
    client = SolanaClient()
    tx_bytes = _jupiter_tx_bytes(client.public_key)

    async def _run():
        try:
            await client.send_versioned_transaction(tx_bytes, auth_token=None)
            raise AssertionError("expected client to block unauthorized send")
        except UnauthorizedTransferError:
            pass
        finally:
            await client.close()

    asyncio.run(_run())
    print("PASS: solana_client.send_versioned_transaction blocks without auth")


def test_api_cannot_trigger_raw_send():
    with _client() as client:
        for path, body in [
            ("/api/bot/start", {"transaction": "base64", "paper_trade": True}),
            ("/api/config", {"raw_tx": "deadbeef"}),
            ("/api/swap/send", {"tx_bytes": "abc"}),
        ]:
            method = client.post if body is not None else client.get
            kwargs = {"environ_overrides": {"REMOTE_ADDR": "127.0.0.1"}}
            if body is not None:
                r = method(
                    path,
                    data=json.dumps(body),
                    content_type="application/json",
                    **kwargs,
                )
            else:
                r = method(path, **kwargs)
            assert r.status_code == 403, f"{path} should be blocked, got {r.status_code}"
    print("PASS: API cannot trigger raw send (firewall blocks swap/tx routes)")


def test_execute_quote_blocked_without_trading_lock():
    trading_lock.unregister_bot_thread()
    executor = JupiterExecutor("11111111111111111111111111111111", dry_run=False)
    quote = SwapQuote(
        input_mint=SOL_MINT,
        output_mint="TokenMint1111111111111111111111111111111",
        in_amount=1_000_000,
        out_amount=2_000_000,
        price_impact_pct=0.1,
        raw={},
    )
    mock_client = MagicMock()
    result = asyncio.run(executor.execute_quote(quote, mock_client))
    assert result is None
    mock_client.send_versioned_transaction.assert_not_called()
    print("PASS: execute_quote blocked without trading lock")


def test_status_includes_transfer_guard():
    with _client() as client:
        r = client.get("/api/bot/status", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    data = r.get_json()
    assert "transfer_guard" in data
    assert data["transfer_guard"]["active"] is True
    assert "blocked_transfer_attempts" in data["transfer_guard"]
    assert data["transfer_guard"].get("associated_token_program_allowed") is True
    print("PASS: bot status includes transfer_guard")


def test_official_ata_program_allowlisted():
    from tx_authorizer import ASSOCIATED_TOKEN_PROGRAM, get_allowed_swap_programs

    official = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
    assert ASSOCIATED_TOKEN_PROGRAM == official
    assert official in get_allowed_swap_programs()
    # Typo / wrong ATA id must not be the builtin constant
    assert ASSOCIATED_TOKEN_PROGRAM != "ATokenGPvbdGVxr1b2dvvtandZdxQ7WH9MoNcLd8f4x3"
    print("PASS: official Associated Token program allowlisted")


def _ata_jupiter_tx_bytes(wallet: Pubkey) -> bytes:
    from tx_authorizer import ASSOCIATED_TOKEN_PROGRAM

    jupiter = Pubkey.from_string(JUPITER_V6_PROGRAM)
    ata = Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM)
    ata_ix = CompiledInstruction(program_id_index=1, data=bytes([1]), accounts=bytes([0]))
    jupiter_ix = CompiledInstruction(program_id_index=2, data=bytes([9, 0, 0]), accounts=bytes([0]))
    message = Message.new_with_compiled_instructions(
        1,
        0,
        1,
        [wallet, ata, jupiter],
        Hash.default(),
        [ata_ix, jupiter_ix],
    )
    return bytes(VersionedTransaction.populate(message, []))


def test_jupiter_with_ata_program_passes():
    trading_lock.register_bot_thread()
    wallet = Pubkey.new_unique()
    context = AuthorizedTradeContext(
        mint="TokenMint1111111111111111111111111111111",
        side="buy",
        amount_sol=0.05,
        trade_id="test-trade-ata",
    )
    token = tx_authorizer.authorize(context, mint_allowed=lambda m: True, is_running=_running)
    tx_bytes = _ata_jupiter_tx_bytes(wallet)
    result = tx_authorizer.verify_and_consume(
        tx_bytes, token, str(wallet), is_running=_running
    )
    assert result.trade_id == "test-trade-ata"
    print("PASS: Jupiter swap with Associated Token program passes")


def test_unexpected_program_still_blocked():
    trading_lock.register_bot_thread()
    wallet = Pubkey.new_unique()
    evil = Pubkey.new_unique()
    jupiter = Pubkey.from_string(JUPITER_V6_PROGRAM)
    evil_ix = CompiledInstruction(program_id_index=1, data=bytes([1]), accounts=bytes([0]))
    jupiter_ix = CompiledInstruction(program_id_index=2, data=bytes([9]), accounts=bytes([0]))
    message = Message.new_with_compiled_instructions(
        1,
        0,
        1,
        [wallet, evil, jupiter],
        Hash.default(),
        [evil_ix, jupiter_ix],
    )
    tx_bytes = bytes(VersionedTransaction.populate(message, []))
    context = AuthorizedTradeContext(
        mint="TokenMint1111111111111111111111111111111",
        side="buy",
        amount_sol=0.05,
        trade_id="test-trade-evil",
    )
    token = tx_authorizer.authorize(context, mint_allowed=lambda m: True, is_running=_running)
    try:
        tx_authorizer.verify_and_consume(tx_bytes, token, str(wallet), is_running=_running)
        raise AssertionError("expected unexpected program to be blocked")
    except UnauthorizedTransferError:
        pass
    print("PASS: unexpected program id still blocked")


def test_mint_not_on_watchlist_blocked():
    trading_lock.register_bot_thread()
    context = AuthorizedTradeContext(
        mint="UnknownMint111111111111111111111111111111",
        side="buy",
        amount_sol=0.05,
        trade_id="test-trade-5",
    )
    try:
        tx_authorizer.authorize(
            context, mint_allowed=lambda m: m != context.mint, is_running=_running
        )
        raise AssertionError("expected unauthorized mint to fail")
    except UnauthorizedTransferError:
        pass
    print("PASS: mint not on watchlist/positions blocked at authorize")


def main():
    Config.ENFORCE_TRANSFER_GUARD = True
    test_official_ata_program_allowlisted()
    test_authorized_jupiter_swap_passes()
    test_jupiter_with_ata_program_passes()
    test_unexpected_program_still_blocked()
    test_unauthorized_send_without_token_blocked()
    test_replay_token_blocked()
    test_bare_sol_drain_blocked()
    test_trading_lock_required_for_authorize()
    test_trading_lock_required_for_send()
    test_solana_client_blocks_unauthorized()
    test_api_cannot_trigger_raw_send()
    test_execute_quote_blocked_without_trading_lock()
    test_status_includes_transfer_guard()
    test_mint_not_on_watchlist_blocked()
    trading_lock.unregister_bot_thread()
    print("\nAll transfer guard validations passed.")


if __name__ == "__main__":
    main()
