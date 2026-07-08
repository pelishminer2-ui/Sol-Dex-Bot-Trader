import base64
import logging
from dataclasses import dataclass
from typing import Optional

from config import Config, SOL_MINT
from jupiter_client import get_jupiter_client

logger = logging.getLogger(__name__)


@dataclass
class SwapQuote:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    price_impact_pct: float
    raw: dict
    output_decimals: Optional[int] = None

    @property
    def sol_in(self) -> float:
        if self.input_mint == SOL_MINT:
            return self.in_amount / 1e9
        return 0.0

    @property
    def sol_out(self) -> float:
        if self.output_mint == SOL_MINT:
            return self.out_amount / 1e9
        return 0.0

    def swap_fees_sol(self) -> float:
        """Estimated chain-route DEX/platform fees for this quote leg."""
        from fee_estimator import estimate_quote_swap_fees_sol

        notional = self.sol_in or self.sol_out
        return estimate_quote_swap_fees_sol(self.raw, sol_notional=notional or None)


def _quote_from_data(
    data: dict,
    input_mint: str,
    output_mint: str,
    amount: int,
) -> SwapQuote:
    output_decimals = data.get("outputDecimals")
    if output_decimals is None and data.get("routePlan"):
        for step in data["routePlan"]:
            swap = (step or {}).get("swapInfo") or {}
            if swap.get("outputMint") == output_mint:
                output_decimals = swap.get("outputDecimals")
                if output_decimals is not None:
                    break
    return SwapQuote(
        input_mint=input_mint,
        output_mint=output_mint,
        in_amount=int(data.get("inAmount", amount)),
        out_amount=int(data.get("outAmount", 0)),
        price_impact_pct=float(data.get("priceImpactPct") or 0),
        raw=data,
        output_decimals=int(output_decimals) if output_decimals is not None else None,
    )


class JupiterExecutor:
    def __init__(self, public_key: str, dry_run: bool = True):
        self.public_key = public_key
        self.dry_run = dry_run
        self._client = get_jupiter_client()

    def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: Optional[int] = None,
        *,
        use_cache: bool = True,
    ) -> Optional[SwapQuote]:
        slippage = slippage_bps or Config.DEFAULT_SLIPPAGE_BPS
        data = self._client.get_quote(
            input_mint,
            output_mint,
            amount,
            slippage,
            use_cache=use_cache,
        )
        if not data:
            return None
        if "error" in data:
            logger.warning("Jupiter quote error: %s", data["error"])
            return None
        return _quote_from_data(data, input_mint, output_mint, amount)

    def build_swap_transaction(self, quote: SwapQuote) -> Optional[bytes]:
        payload = {
            "quoteResponse": quote.raw,
            "userPublicKey": self.public_key,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": Config.PRIORITY_FEE_LAMPORTS,
        }
        data = self._client.post_swap(payload)
        if not data:
            return None
        swap_tx = data.get("swapTransaction")
        if not swap_tx:
            logger.error("Jupiter swap response missing transaction")
            return None
        return base64.b64decode(swap_tx)

    def validate_quote(
        self,
        quote: SwapQuote,
        *,
        max_impact_pct: Optional[float] = None,
    ) -> bool:
        if quote.out_amount <= 0:
            logger.warning("Quote has zero output amount")
            return False
        limit = (
            float(max_impact_pct)
            if max_impact_pct is not None
            else Config.MAX_ABSOLUTE_PRICE_IMPACT_PCT
        )
        if abs(quote.price_impact_pct) > limit:
            logger.warning(
                "Price impact %.2f%% exceeds max %.2f%%",
                quote.price_impact_pct,
                limit,
            )
            return False
        return True

    def buy_token(
        self,
        token_mint: str,
        sol_amount: float,
        *,
        use_cache: bool = True,
    ) -> Optional[SwapQuote]:
        lamports = int(sol_amount * 1e9)
        quote = self.get_quote(SOL_MINT, token_mint, lamports, use_cache=use_cache)
        if not quote or not self.validate_quote(
            quote, max_impact_pct=Config.effective_max_entry_price_impact_pct()
        ):
            return None
        if self.dry_run:
            logger.info(
                "[PAPER] BUY %s for %.4f SOL -> %d tokens raw (impact %.3f%%)",
                token_mint,
                sol_amount,
                quote.out_amount,
                quote.price_impact_pct,
            )
            return quote
        return quote

    def sell_token(
        self,
        token_mint: str,
        token_amount_raw: int,
        *,
        use_cache: bool = False,
        allow_high_impact: bool = False,
    ) -> Optional[SwapQuote]:
        if token_amount_raw <= 0:
            logger.warning("Cannot sell zero token amount")
            return None
        quote = self.get_quote(
            token_mint, SOL_MINT, token_amount_raw, use_cache=use_cache
        )
        if not quote:
            return None
        if quote.out_amount <= 0:
            logger.warning("Quote has zero output amount")
            return None
        if not allow_high_impact and not self.validate_quote(quote):
            return None
        if allow_high_impact and abs(quote.price_impact_pct) > Config.MAX_ABSOLUTE_PRICE_IMPACT_PCT:
            logger.warning(
                "High-impact sell quote accepted: impact %.2f%% (forced exit path)",
                quote.price_impact_pct,
            )
        if self.dry_run:
            logger.info(
                "[PAPER] SELL %s amount=%d raw -> %.6f SOL (impact %.3f%%)",
                token_mint,
                token_amount_raw,
                quote.sol_out,
                quote.price_impact_pct,
            )
            return quote
        return quote

    async def execute_quote(
        self, quote: SwapQuote, solana_client, *, forced_exit: bool = False
    ) -> Optional[str]:
        from bot_manager import bot_manager
        from trading_lock import trading_lock
        from tx_authorizer import (
            UnauthorizedTransferError,
            context_from_quote,
            tx_authorizer,
        )

        if self.dry_run:
            return "dry-run-signature"

        fresh = self.get_quote(
            quote.input_mint,
            quote.output_mint,
            quote.in_amount,
            use_cache=False,
        )
        if fresh:
            if forced_exit:
                quote = fresh
            elif self.validate_quote(fresh):
                quote = fresh
        elif not forced_exit and not self.validate_quote(quote):
            return None

        if not trading_lock.is_authorized(
            bot_manager.is_running, dry_run=self.dry_run
        ):
            logger.error("SECURITY: execute_quote blocked by trading lock")
            return None
        tx_bytes = self.build_swap_transaction(quote)
        if not tx_bytes:
            return None

        trade_context = context_from_quote(quote)
        auth_token = ""
        try:
            auth_token = tx_authorizer.authorize(
                trade_context,
                mint_allowed=bot_manager.is_mint_trade_allowed,
                is_running=bot_manager.is_running,
            )
        except UnauthorizedTransferError as exc:
            logger.error("SECURITY: execute_quote authorization failed: %s", exc)
            return None

        try:
            signature = await solana_client.send_versioned_transaction(
                tx_bytes, auth_token=auth_token
            )
        except UnauthorizedTransferError as exc:
            logger.error("SECURITY: execute_quote send blocked: %s", exc)
            return None

        if not signature:
            return None

        tx_authorizer.verify_journal_match(trade_context, signature)
        confirmed = await solana_client.confirm_transaction(signature)
        if not confirmed:
            logger.error("Transaction not confirmed: %s", signature)
            return None
        logger.info("Swap confirmed: %s", signature)
        return signature
