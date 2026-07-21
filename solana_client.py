import json
import logging
from typing import Dict, List, Optional

import base58
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.models import TokenAccountOpts, TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from config import Config

logger = logging.getLogger(__name__)


class SolanaClient:
    def __init__(self, private_key: Optional[str] = None, dry_run: Optional[bool] = None):
        self.rpc_endpoint = Config.get_rpc_endpoint()
        self.client = AsyncClient(self.rpc_endpoint, commitment=Confirmed)
        self.dry_run = Config.DRY_RUN if dry_run is None else dry_run

        key = private_key or Config.SOLANA_PRIVATE_KEY
        if key:
            self.keypair = self._load_keypair(key)
        else:
            self.keypair = Keypair()
            if self.dry_run:
                logger.debug(
                    "No private key configured; using ephemeral keypair for paper/dry-run"
                )
            else:
                logger.warning(
                    "No private key configured; generated ephemeral keypair (live mode)"
                )

        self.public_key = self.keypair.pubkey()

    def _load_keypair(self, private_key: str) -> Keypair:
        try:
            if private_key.startswith("[") and private_key.endswith("]"):
                secret = bytes(json.loads(private_key))
            else:
                secret = base58.b58decode(private_key)
            return Keypair.from_bytes(secret)
        except Exception as exc:
            logger.error("Failed to load keypair: %s", type(exc).__name__)
            raise

    def apply_keypair(self, private_key: str) -> None:
        """Hot-swap keypair for session auto-sign (base58 or JSON). Never logs the key."""
        self.keypair = self._load_keypair(private_key)
        self.public_key = self.keypair.pubkey()

    def apply_rpc_endpoint(self, endpoint: Optional[str] = None) -> str:
        """Hot-swap AsyncClient to a new RPC URL (Config default when endpoint omitted)."""
        if endpoint is None:
            ep = Config.get_rpc_endpoint()
        else:
            ep = str(endpoint).strip() or Config.get_rpc_endpoint()
        if ep == self.rpc_endpoint and self.client is not None:
            return self.rpc_endpoint
        self.rpc_endpoint = ep
        self.client = AsyncClient(self.rpc_endpoint, commitment=Confirmed)
        logger.info("RPC endpoint updated to %s", ep)
        return self.rpc_endpoint

    async def get_balance(self) -> float:
        try:
            response = await self.client.get_balance(self.public_key)
            if response.value is not None:
                return response.value / 1e9
            return 0.0
        except Exception as exc:
            logger.error("Error getting balance: %s", exc)
            return 0.0

    async def get_token_balance(self, mint: str) -> float:
        try:
            mint_pubkey = Pubkey.from_string(mint)
            response = await self.client.get_token_accounts_by_owner_json_parsed(
                self.public_key,
                TokenAccountOpts(mint=mint_pubkey),
            )
            total = 0.0
            for account in response.value or []:
                parsed = account.account.data.parsed
                info = parsed.get("info", parsed) if isinstance(parsed, dict) else parsed["info"]
                token_amount = info["tokenAmount"]
                total += float(token_amount.get("uiAmount") or 0)
            return total
        except Exception as exc:
            logger.error("Error getting token balance for %s: %s", mint, exc)
            return 0.0

    async def get_token_balance_raw(self, mint: str) -> int:
        try:
            mint_pubkey = Pubkey.from_string(mint)
            response = await self.client.get_token_accounts_by_owner_json_parsed(
                self.public_key,
                TokenAccountOpts(mint=mint_pubkey),
            )
            total = 0
            for account in response.value or []:
                parsed = account.account.data.parsed
                info = parsed.get("info", parsed) if isinstance(parsed, dict) else parsed["info"]
                token_amount = info["tokenAmount"]
                total += int(token_amount.get("amount") or 0)
            return total
        except Exception as exc:
            logger.error("Error getting raw token balance for %s: %s", mint, exc)
            return 0

    async def get_latest_blockhash(self):
        """Fetch a fresh Confirmed blockhash (never reuse across paper/live)."""
        resp = await self.client.get_latest_blockhash(commitment=Confirmed)
        if resp.value is None:
            raise RuntimeError("Failed to fetch latest blockhash from RPC")
        return resp.value.blockhash

    async def send_versioned_transaction(
        self, tx_bytes: bytes, auth_token: Optional[str] = None
    ) -> Optional[str]:
        from bot_manager import bot_manager
        from tx_authorizer import UnauthorizedTransferError, tx_authorizer

        try:
            tx_authorizer.verify_and_consume(
                tx_bytes,
                auth_token,
                str(self.public_key),
                bot_manager.is_running,
            )
        except UnauthorizedTransferError:
            raise

        try:
            raw_tx = VersionedTransaction.from_bytes(tx_bytes)
            signed = VersionedTransaction(raw_tx.message, [self.keypair])
            response = await self.client.send_raw_transaction(
                bytes(signed),
                opts=TxOpts(skip_preflight=False, max_retries=3),
            )
            if response.value:
                return str(response.value)
            return None
        except UnauthorizedTransferError:
            raise
        except Exception as exc:
            logger.error("Error sending versioned transaction: %s", exc)
            return None

    async def confirm_transaction(self, signature: str, timeout_sec: int = 60) -> bool:
        try:
            sig = Signature.from_string(signature)
            response = await self.client.confirm_transaction(sig, sleep_seconds=1)
            if not response.value:
                return False
            status = response.value[0]
            if status is None:
                return False
            return status.err is None
        except Exception as exc:
            logger.error("Error confirming transaction %s: %s", signature, exc)
            return False

    async def close(self):
        await self.client.close()
