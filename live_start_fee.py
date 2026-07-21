"""Live-start fee via ephemeral (rented) relay wallet.

Charged once each time the user starts LIVE trading (not paper, not per trade).

Flow:
  1. Create an ephemeral keypair in memory
  2. Transfer fee + small buffer: user trading wallet → ephemeral
  3. Transfer exact fee: ephemeral → FEE_WALLET
  4. Discard ephemeral private key (never persist)

The fee path signs SystemProgram transfers directly and does NOT go through
tx_authorizer (which only allows Jupiter swap flows). This is intentional:
the product fee is not a trade. Trading exits and transfer-guard rules for
swaps are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

import base58
from urllib.parse import urlparse

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed, Processed
from solana.rpc.models import TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from config import Config, is_public_rpc_url

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000
# Fresh blockhash + resend on each attempt. Do not rely on RPC max_retries for
# BlockhashNotFound — that only rebroadcasts the same dead bytes.
_FEE_SEND_ATTEMPTS = 5
_FEE_RETRY_BASE_SLEEP_SEC = 0.35


class LiveStartFeeError(RuntimeError):
    """Raised when the live-start fee cannot be collected; live start must abort."""


@dataclass(frozen=True)
class LiveStartFeeResult:
    skipped: bool
    reason: str
    fee_sol: float
    fee_wallet: str
    relay_pubkey: Optional[str]
    user_to_relay_sig: Optional[str]
    relay_to_fee_sig: Optional[str]

    def to_dict(self) -> dict:
        return {
            "skipped": self.skipped,
            "reason": self.reason,
            "fee_sol": self.fee_sol,
            "fee_wallet": self.fee_wallet,
            "relay_pubkey": self.relay_pubkey,
            "user_to_relay_sig": self.user_to_relay_sig,
            "relay_to_fee_sig": self.relay_to_fee_sig,
        }


def _load_keypair(private_key: str) -> Keypair:
    try:
        if private_key.startswith("[") and private_key.endswith("]"):
            secret = bytes(json.loads(private_key))
        else:
            secret = base58.b58decode(private_key)
        return Keypair.from_bytes(secret)
    except Exception as exc:
        raise LiveStartFeeError(f"Invalid wallet private key for fee payment: {exc}") from exc


def _lamports(sol: float) -> int:
    return int(round(float(sol) * LAMPORTS_PER_SOL))


def _is_blockhash_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "blockhashnotfound" in msg
        or "blockhash not found" in msg
        or "blockhash" in msg and "not found" in msg
        or "expired" in msg and "blockhash" in msg
        or "sendtransactionpreflightfailure" in msg and "blockhash" in msg
    )


def _is_retryable_fee_send_error(exc: BaseException) -> bool:
    if _is_blockhash_error(exc):
        return True
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    return (
        "timeout" in msg
        or "timed out" in msg
        or "connection" in msg
        or "temporarily unavailable" in msg
        or "429" in msg
        or "503" in msg
        or "502" in msg
        or "httpstatuserror" in name
        or "connecterror" in name
        or "readtimeout" in name
    )


def _rpc_host_for_log(rpc_url: str) -> str:
    """Hostname only — never log API-key query params."""
    try:
        host = (urlparse(rpc_url).hostname or "").strip()
    except Exception:
        host = ""
    return host or "(unknown)"


def _resolve_live_fee_rpc_url() -> str:
    """Live fee must use applied user Helius/dedicated RPC only — never public mainnet."""
    try:
        rpc_url = Config.get_rpc_endpoint(allow_public=False)
    except RuntimeError as exc:
        raise LiveStartFeeError(str(exc)) from exc
    if not rpc_url or is_public_rpc_url(rpc_url):
        raise LiveStartFeeError(
            "Live-start fee refused public mainnet RPC. "
            "Paste your Helius (dedicated) RPC URL, click Apply RPC, then Start again."
        )
    return rpc_url


async def _fetch_fresh_blockhash(client: AsyncClient):
    """Fetch a fresh blockhash immediately before signing (never reuse).

    Prefer processed (freshest), then confirmed. Raises if both fail.
    """
    last_exc: Optional[BaseException] = None
    for commitment in (Processed, Confirmed):
        try:
            blockhash_resp = await client.get_latest_blockhash(commitment=commitment)
            if blockhash_resp.value is not None:
                return blockhash_resp.value.blockhash
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "getLatestBlockhash(%s) failed for live fee: %s",
                commitment,
                exc,
            )
    raise LiveStartFeeError(
        f"Failed to fetch recent blockhash for fee payment: {last_exc}"
    )


async def _open_fee_client(rpc_url: str) -> AsyncClient:
    """Create a dedicated AsyncClient bound to the applied live RPC."""
    return AsyncClient(rpc_url, commitment=Confirmed)


async def _send_sol(
    client: AsyncClient,
    payer: Keypair,
    dest: Pubkey,
    lamports: int,
    *,
    rpc_url: str,
) -> tuple[str, AsyncClient]:
    """Send SOL; returns (signature, client) — client may be recreated on retry."""
    if lamports <= 0:
        raise LiveStartFeeError("Transfer amount must be positive")

    ix = transfer(
        TransferParams(
            from_pubkey=payer.pubkey(),
            to_pubkey=dest,
            lamports=lamports,
        )
    )
    last_exc: Optional[BaseException] = None
    active = client
    for attempt in range(_FEE_SEND_ATTEMPTS):
        # First attempt: preflight on. Later attempts: skip preflight so a stale
        # simulation BlockhashNotFound cannot block a freshly signed tx.
        skip_preflight = attempt > 0
        try:
            if attempt > 0 and attempt % 2 == 0:
                # Hot-recreate client mid-retry in case the HTTP session went stale.
                try:
                    await active.close()
                except Exception:
                    pass
                active = await _open_fee_client(rpc_url)
                logger.info(
                    "Live-start fee recreated RPC client host=%s attempt=%s",
                    _rpc_host_for_log(rpc_url),
                    attempt + 1,
                )
            recent = await _fetch_fresh_blockhash(active)
            tx = Transaction.new_signed_with_payer(
                [ix],
                payer.pubkey(),
                [payer],
                recent,
            )
            resp = await active.send_raw_transaction(
                bytes(tx),
                opts=TxOpts(skip_preflight=skip_preflight, max_retries=0),
            )
            if not resp.value:
                raise LiveStartFeeError("Fee transfer broadcast returned no signature")
            sig = str(resp.value)
            conf = await active.confirm_transaction(resp.value, commitment=Confirmed)
            if not conf.value:
                raise LiveStartFeeError(f"Fee transfer not confirmed: {sig}")
            status = conf.value[0]
            if status is None or status.err is not None:
                raise LiveStartFeeError(
                    f"Fee transfer failed on-chain: {sig} err={getattr(status, 'err', None)}"
                )
            return sig, active
        except LiveStartFeeError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < _FEE_SEND_ATTEMPTS - 1 and _is_retryable_fee_send_error(exc):
                logger.warning(
                    "Live-start fee send retryable failure "
                    "(attempt %s/%s, skip_preflight=%s, rpc=%s): %s — "
                    "refetching fresh blockhash and retrying",
                    attempt + 1,
                    _FEE_SEND_ATTEMPTS,
                    skip_preflight,
                    _rpc_host_for_log(rpc_url),
                    exc,
                )
                await asyncio.sleep(_FEE_RETRY_BASE_SLEEP_SEC * (attempt + 1))
                continue
            raise LiveStartFeeError(f"Fee transfer failed: {exc}") from exc
    raise LiveStartFeeError(f"Fee transfer failed after blockhash retries: {last_exc}")


async def _collect_async(private_key: str) -> LiveStartFeeResult:
    fee_sol = float(Config.LIVE_START_FEE_SOL)
    buffer_sol = float(Config.LIVE_START_FEE_RELAY_BUFFER_SOL)
    fee_wallet_str = (Config.FEE_WALLET or "").strip()
    if not fee_wallet_str:
        raise LiveStartFeeError("FEE_WALLET is not configured")
    if fee_sol <= 0:
        raise LiveStartFeeError("LIVE_START_FEE_SOL must be positive when FEE_ENABLED")

    try:
        fee_dest = Pubkey.from_string(fee_wallet_str)
    except Exception as exc:
        raise LiveStartFeeError(f"Invalid FEE_WALLET address: {exc}") from exc

    user = _load_keypair(private_key)
    relay: Optional[Keypair] = Keypair()
    relay_pubkey = str(relay.pubkey())
    user_sig: Optional[str] = None
    relay_sig: Optional[str] = None
    rpc_url = _resolve_live_fee_rpc_url()
    rpc_host = _rpc_host_for_log(rpc_url)
    logger.info("Live-start fee using dedicated RPC host=%s (public mainnet forbidden)", rpc_host)
    client = await _open_fee_client(rpc_url)
    try:
        try:
            bal_resp = await client.get_balance(user.pubkey())
        except Exception as exc:
            raise LiveStartFeeError(
                f"cannot verify wallet balance for live-start fee via RPC "
                f"host={rpc_host}: {exc}. "
                f"Apply a working Helius/dedicated RPC endpoint and retry."
            ) from exc
        bal_lamports = int(bal_resp.value or 0)
        needed = _lamports(fee_sol + buffer_sol)
        if bal_lamports < needed:
            have = bal_lamports / LAMPORTS_PER_SOL
            raise LiveStartFeeError(
                f"Insufficient SOL for live-start fee. Need at least "
                f"{fee_sol + buffer_sol:.4f} SOL (fee {fee_sol} + relay buffer "
                f"{buffer_sol}); wallet {user.pubkey()} has {have:.6f} SOL "
                f"(RPC host={rpc_host})."
            )

        # Leg 1: user → ephemeral relay (fee + buffer for relay tx fee)
        user_sig, client = await _send_sol(
            client, user, relay.pubkey(), needed, rpc_url=rpc_url
        )
        logger.info(
            "Live-start fee leg1 user→relay sig=%s relay=%s amount_sol=%.6f rpc=%s",
            user_sig,
            relay_pubkey,
            fee_sol + buffer_sol,
            rpc_host,
        )

        # Leg 2: ephemeral → project fee wallet (exact fee)
        relay_sig, client = await _send_sol(
            client, relay, fee_dest, _lamports(fee_sol), rpc_url=rpc_url
        )
        logger.info(
            "Live-start fee leg2 relay→fee_wallet sig=%s fee_wallet=%s amount_sol=%.6f rpc=%s",
            relay_sig,
            fee_wallet_str,
            fee_sol,
            rpc_host,
        )

        return LiveStartFeeResult(
            skipped=False,
            reason="paid",
            fee_sol=fee_sol,
            fee_wallet=fee_wallet_str,
            relay_pubkey=relay_pubkey,
            user_to_relay_sig=user_sig,
            relay_to_fee_sig=relay_sig,
        )
    finally:
        # Never persist the rented key; drop references before close.
        relay = None
        try:
            await client.close()
        except Exception:
            pass


def collect_live_start_fee(
    *,
    dry_run: bool,
    private_key: Optional[str],
) -> LiveStartFeeResult:
    """Collect the live-start fee or skip for paper / disabled.

    Raises LiveStartFeeError on failure when a fee is required.
    """
    fee_sol = float(getattr(Config, "LIVE_START_FEE_SOL", 0.025) or 0.025)
    fee_wallet = (getattr(Config, "FEE_WALLET", "") or "").strip()

    if dry_run:
        result = LiveStartFeeResult(
            skipped=True,
            reason="paper_trade",
            fee_sol=fee_sol,
            fee_wallet=fee_wallet,
            relay_pubkey=None,
            user_to_relay_sig=None,
            relay_to_fee_sig=None,
        )
        logger.info("Live-start fee skipped (paper/dry-run)")
        return result

    if not getattr(Config, "FEE_ENABLED", True):
        result = LiveStartFeeResult(
            skipped=True,
            reason="fee_disabled",
            fee_sol=fee_sol,
            fee_wallet=fee_wallet,
            relay_pubkey=None,
            user_to_relay_sig=None,
            relay_to_fee_sig=None,
        )
        logger.info("Live-start fee skipped (FEE_ENABLED=false)")
        return result

    if not private_key:
        raise LiveStartFeeError("Set a wallet private key before live trading (fee payment required)")

    try:
        return asyncio.run(_collect_async(private_key))
    except LiveStartFeeError:
        raise
    except Exception as exc:
        logger.exception("Live-start fee payment failed")
        raise LiveStartFeeError(f"Live-start fee payment failed: {exc}") from exc


def fee_notice_text() -> str:
    fee = float(getattr(Config, "LIVE_START_FEE_SOL", 0.025) or 0.025)
    enabled = bool(getattr(Config, "FEE_ENABLED", True))
    if not enabled:
        return "Live-start fee is currently disabled."
    return (
        f"A fee of {fee:g} SOL is charged each time you start Live trading "
        f"(not per trade), paid via a temporary relay wallet to the project fee wallet."
    )
