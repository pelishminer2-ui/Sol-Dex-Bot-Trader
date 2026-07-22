"""One-time authorization tokens for outbound transactions — Jupiter swaps only."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional, Set

from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.token import ID as TOKEN_PROGRAM_ID
from solders.transaction import VersionedTransaction

from config import Config, SOL_MINT
from trading_lock import trading_lock

logger = logging.getLogger(__name__)

JUPITER_V6_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
JUPITER_V4_PROGRAM = "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
# Official Associated Token Account program (SPL).
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"

_DEFAULT_ALLOWED_SWAP_PROGRAMS: FrozenSet[str] = frozenset({
    JUPITER_V6_PROGRAM,
    JUPITER_V4_PROGRAM,
    str(TOKEN_PROGRAM_ID),
    TOKEN_2022_PROGRAM,
    ASSOCIATED_TOKEN_PROGRAM,
    COMPUTE_BUDGET_PROGRAM,
    str(SYSTEM_PROGRAM_ID),
})

# Backward-compatible alias; prefer get_allowed_swap_programs() which also
# merges data/allowed_programs.json on every authorize/inspect call.
ALLOWED_SWAP_PROGRAMS: FrozenSet[str] = _DEFAULT_ALLOWED_SWAP_PROGRAMS

_ALLOWED_PROGRAMS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "allowed_programs.json"
)
_runtime_extra_programs: Set[str] = set()
_programs_lock = threading.Lock()

SYSTEM_TRANSFER_DISCRIMINATOR = 2
SYSTEM_TRANSFER_WITH_SEED_DISCRIMINATOR = 11
SPL_TRANSFER_INSTRUCTION = 3


def _load_allowlist_file() -> Set[str]:
    """Read optional JSON allowlist; failures fall back to empty (builtins still apply)."""
    path = _ALLOWED_PROGRAMS_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return set()
    except Exception as exc:
        logger.warning("transfer guard: failed reading %s: %s", path, exc)
        return set()
    programs = raw.get("programs") if isinstance(raw, dict) else raw
    if not isinstance(programs, list):
        return set()
    out: Set[str] = set()
    for item in programs:
        if isinstance(item, str) and item.strip():
            out.add(item.strip())
    return out


def get_allowed_swap_programs() -> FrozenSet[str]:
    """Builtin + JSON file (re-read each call) + any runtime extras."""
    with _programs_lock:
        extras = set(_runtime_extra_programs)
    return frozenset(_DEFAULT_ALLOWED_SWAP_PROGRAMS | _load_allowlist_file() | extras)


def add_allowed_programs(program_ids: List[str]) -> FrozenSet[str]:
    """Hot-add program ids for the current process (also persists into JSON when possible)."""
    cleaned = [p.strip() for p in program_ids if isinstance(p, str) and p.strip()]
    if not cleaned:
        return get_allowed_swap_programs()
    with _programs_lock:
        _runtime_extra_programs.update(cleaned)
    try:
        existing = sorted(_load_allowlist_file() | set(cleaned) | set(_DEFAULT_ALLOWED_SWAP_PROGRAMS))
        os.makedirs(os.path.dirname(_ALLOWED_PROGRAMS_PATH), exist_ok=True)
        with open(_ALLOWED_PROGRAMS_PATH, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "programs": existing,
                    "notes": "Official Associated Token Account program allowlisted for Jupiter ATA create/use. Re-read on every transfer-guard inspect.",
                },
                fh,
                indent=2,
            )
            fh.write("\n")
    except Exception as exc:
        logger.warning("transfer guard: failed persisting allowlist: %s", exc)
    return get_allowed_swap_programs()


class UnauthorizedTransferError(Exception):
    """Raised when a transaction is not authorized for signing/sending."""


@dataclass
class AuthorizedTradeContext:
    mint: str
    side: str  # "buy" | "sell"
    amount_sol: float
    trade_id: str
    authorized_at: float = field(default_factory=time.time)


@dataclass
class _PendingAuthorization:
    token: str
    context: AuthorizedTradeContext
    created_at: float


class TxAuthorizer:
    """Registers one-time tokens for bot-initiated Jupiter swaps; verifies before sign."""

    TOKEN_TTL_SEC = 120.0

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: Dict[str, _PendingAuthorization] = {}
        self._blocked_count = 0
        self._last_sent: Optional[AuthorizedTradeContext] = None

    def authorize(
        self,
        context: AuthorizedTradeContext,
        mint_allowed: Callable[[str], bool],
        is_running: Callable[[], bool],
    ) -> str:
        if not Config.ENFORCE_TRANSFER_GUARD:
            return ""

        if not trading_lock.is_authorized(is_running):
            self._record_block("trading lock not held during authorize")
            raise UnauthorizedTransferError("Trading lock not held")

        if context.side not in ("buy", "sell"):
            self._record_block(f"invalid side: {context.side}")
            raise UnauthorizedTransferError(f"Invalid trade side: {context.side}")

        if not mint_allowed(context.mint):
            self._record_block(f"mint not on watchlist or in positions: {context.mint}")
            raise UnauthorizedTransferError(
                f"Mint {context.mint} is not on watchlist or in open positions"
            )

        token = str(uuid.uuid4())
        with self._lock:
            self._purge_expired()
            self._pending[token] = _PendingAuthorization(
                token=token,
                context=context,
                created_at=time.time(),
            )
        logger.debug(
            "Authorized %s %s for %.6f SOL (trade_id=%s)",
            context.side,
            context.mint,
            context.amount_sol,
            context.trade_id,
        )
        return token

    def verify_and_consume(
        self,
        tx_bytes: bytes,
        auth_token: Optional[str],
        wallet_pubkey: str,
        is_running: Callable[[], bool],
    ) -> AuthorizedTradeContext:
        if not Config.ENFORCE_TRANSFER_GUARD:
            return AuthorizedTradeContext(
                mint="",
                side="buy",
                amount_sol=0.0,
                trade_id="guard-disabled",
            )

        if not trading_lock.is_authorized(is_running):
            self._record_block("trading lock not held during send")
            raise UnauthorizedTransferError("Trading lock not held for send")

        if not auth_token:
            self._record_block("missing authorization token")
            raise UnauthorizedTransferError("Missing authorization token")

        with self._lock:
            self._purge_expired()
            pending = self._pending.pop(auth_token, None)

        if pending is None:
            self._record_block("invalid or expired authorization token")
            raise UnauthorizedTransferError("Invalid or expired authorization token")

        self._inspect_transaction(tx_bytes, wallet_pubkey)
        with self._lock:
            self._last_sent = pending.context
        return pending.context

    def verify_journal_match(self, context: AuthorizedTradeContext, signature: str) -> None:
        """Defensive post-send check — log if context looks inconsistent."""
        if not Config.ENFORCE_TRANSFER_GUARD or not signature or signature == "dry-run-signature":
            return
        if context.amount_sol <= 0:
            logger.warning(
                "SECURITY: post-send journal check — zero amount for %s sig=%s",
                context.mint,
                signature,
            )

    def _record_block(self, reason: str) -> None:
        with self._lock:
            self._blocked_count += 1
        logger.error("SECURITY: blocked unauthorized transfer attempt — %s", reason)

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [
            tok
            for tok, entry in self._pending.items()
            if now - entry.created_at > self.TOKEN_TTL_SEC
        ]
        for tok in expired:
            del self._pending[tok]

    def _inspect_transaction(self, tx_bytes: bytes, wallet_pubkey: str) -> None:
        try:
            tx = VersionedTransaction.from_bytes(tx_bytes)
        except Exception as exc:
            self._record_block(f"failed to deserialize transaction: {exc}")
            raise UnauthorizedTransferError(f"Invalid transaction bytes: {exc}") from exc

        message = tx.message
        account_keys = self._resolve_account_keys(message)
        if not account_keys:
            self._record_block("no account keys in transaction")
            raise UnauthorizedTransferError("Transaction has no account keys")

        wallet = Pubkey.from_string(wallet_pubkey)
        swap_flow_accounts = self._collect_swap_flow_accounts(message, account_keys)
        allowed_sol_destinations = self._allowed_sol_destinations(wallet, swap_flow_accounts)

        has_jupiter = any(
            ix.program_id_index < len(account_keys)
            and str(account_keys[ix.program_id_index]) in (JUPITER_V6_PROGRAM, JUPITER_V4_PROGRAM)
            for ix in message.instructions
        )

        for ix in message.instructions:
            if ix.program_id_index >= len(account_keys):
                continue
            program_id = str(account_keys[ix.program_id_index])

            if program_id in (JUPITER_V6_PROGRAM, JUPITER_V4_PROGRAM):
                continue

            if program_id == str(SYSTEM_PROGRAM_ID):
                if self._is_system_transfer(ix):
                    dest = self._system_transfer_destination(ix, account_keys)
                    if dest is not None and dest not in allowed_sol_destinations:
                        self._record_block(
                            f"bare SOL transfer to third party: {dest}"
                        )
                        raise UnauthorizedTransferError(
                            "Bare SOL transfer to unauthorized destination"
                        )
                continue

            if program_id in (str(TOKEN_PROGRAM_ID), TOKEN_2022_PROGRAM):
                if self._is_spl_transfer(ix) and not has_jupiter:
                    self._record_block("SPL transfer without Jupiter swap program")
                    raise UnauthorizedTransferError(
                        "SPL token transfer outside Jupiter swap flow"
                    )
                if self._is_spl_transfer(ix):
                    dest = self._spl_transfer_destination(ix, account_keys)
                    if dest is not None and dest not in swap_flow_accounts:
                        self._record_block(
                            f"SPL transfer to address outside swap flow: {dest}"
                        )
                        raise UnauthorizedTransferError(
                            "SPL token transfer to unauthorized destination"
                        )

            if program_id not in get_allowed_swap_programs():
                self._record_block(f"unexpected program id: {program_id}")
                raise UnauthorizedTransferError(
                    f"Unexpected program in transaction: {program_id}"
                )

        if not has_jupiter:
            self._record_block("transaction missing Jupiter swap program")
            raise UnauthorizedTransferError(
                "Transaction must include Jupiter swap program"
            )

    @staticmethod
    def _resolve_account_keys(message) -> List[Pubkey]:
        if hasattr(message, "account_keys"):
            return list(message.account_keys)
        return []

    @staticmethod
    def _instruction_account_indices(ix) -> List[int]:
        acc = ix.accounts
        if isinstance(acc, bytes):
            return list(acc)
        return list(acc)

    @staticmethod
    def _collect_swap_flow_accounts(message, account_keys: List[Pubkey]) -> Set[str]:
        swap_programs = {
            JUPITER_V6_PROGRAM,
            JUPITER_V4_PROGRAM,
            str(TOKEN_PROGRAM_ID),
            TOKEN_2022_PROGRAM,
            ASSOCIATED_TOKEN_PROGRAM,
        }
        accounts: Set[str] = set()
        for ix in message.instructions:
            if ix.program_id_index >= len(account_keys):
                continue
            if str(account_keys[ix.program_id_index]) not in swap_programs:
                continue
            for idx in TxAuthorizer._instruction_account_indices(ix):
                if idx < len(account_keys):
                    accounts.add(str(account_keys[idx]))
        return accounts

    @staticmethod
    def _allowed_sol_destinations(wallet: Pubkey, swap_accounts: Set[str]) -> Set[str]:
        allowed = {str(wallet)} | swap_accounts
        try:
            from solders.associated_token import get_associated_token_address

            sol_mint = Pubkey.from_string(SOL_MINT)
            wsol_ata = get_associated_token_address(wallet, sol_mint)
            allowed.add(str(wsol_ata))
        except Exception:
            pass
        return allowed

    @staticmethod
    def _is_system_transfer(ix) -> bool:
        data = bytes(ix.data)
        if len(data) < 4:
            return False
        disc = int.from_bytes(data[:4], "little")
        return disc in (SYSTEM_TRANSFER_DISCRIMINATOR, SYSTEM_TRANSFER_WITH_SEED_DISCRIMINATOR)

    @staticmethod
    def _system_transfer_destination(ix, account_keys: List[Pubkey]) -> Optional[str]:
        indices = TxAuthorizer._instruction_account_indices(ix)
        if len(indices) < 2:
            return None
        to_idx = indices[1]
        if to_idx >= len(account_keys):
            return None
        return str(account_keys[to_idx])

    @staticmethod
    def _is_spl_transfer(ix) -> bool:
        data = bytes(ix.data)
        return len(data) >= 1 and data[0] == SPL_TRANSFER_INSTRUCTION

    @staticmethod
    def _spl_transfer_destination(ix, account_keys: List[Pubkey]) -> Optional[str]:
        # Transfer: [source, destination, authority, ...]
        indices = TxAuthorizer._instruction_account_indices(ix)
        if len(indices) < 2:
            return None
        dest_idx = indices[1]
        if dest_idx >= len(account_keys):
            return None
        return str(account_keys[dest_idx])

    def get_stats(self) -> dict:
        with self._lock:
            pending = len(self._pending)
            blocked = self._blocked_count
        allowed = sorted(get_allowed_swap_programs())
        return {
            "active": Config.ENFORCE_TRANSFER_GUARD,
            "enforced": Config.ENFORCE_TRANSFER_GUARD,
            "blocked_transfer_attempts": blocked,
            "pending_authorizations": pending,
            "allowed_programs_count": len(allowed),
            "associated_token_program_allowed": ASSOCIATED_TOKEN_PROGRAM in allowed,
            "description": "Only automated Jupiter swaps authorized",
        }


def context_from_quote(quote) -> AuthorizedTradeContext:
    """Build authorization context from a Jupiter SwapQuote."""
    from config import USDC_MINT, USDT_MINT

    stables = {USDC_MINT, USDT_MINT}
    # USDC/USDT → WSOL (So1111…112)
    if quote.input_mint in stables and quote.output_mint == SOL_MINT:
        side = "buy"
        mint = SOL_MINT
        amount_sol = quote.sol_out
    # WSOL → USDC/USDT
    elif quote.input_mint == SOL_MINT and quote.output_mint in stables:
        side = "sell"
        mint = SOL_MINT
        amount_sol = quote.sol_in
    elif quote.input_mint == SOL_MINT:
        side = "buy"
        mint = quote.output_mint
        amount_sol = quote.sol_in
    else:
        side = "sell"
        mint = quote.input_mint
        amount_sol = quote.sol_out
    return AuthorizedTradeContext(
        mint=mint,
        side=side,
        amount_sol=amount_sol,
        trade_id=str(uuid.uuid4()),
        authorized_at=time.time(),
    )


tx_authorizer = TxAuthorizer()


def get_transfer_guard_stats() -> dict:
    return tx_authorizer.get_stats()
