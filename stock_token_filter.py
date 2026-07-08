"""Filter stock-backed / tokenized equity tokens on Solana."""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional, Sequence

from config import Config

logger = logging.getLogger(__name__)

_logged_skipped_mints: set[str] = set()

# Backed Finance xStocks mint authority on Solana.
BACKED_XSTOCKS_MINT_AUTHORITY = "S7vYFFWH6BjJyEsdrPQpqpYTqLTrPRK6KW3VwsJuRaS"

# Verified Backed xStock mints and known stock-spoof CAs (Solana mainnet).
KNOWN_STOCK_MINTS: frozenset[str] = frozenset({
    "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB",  # TSLAx
    "XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp",  # AAPLx
    "XsCPL9dNWBMvFtTmwcCA5v3xWPSMEBCszbQdiLLq6aN",  # GOOGLx
    "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh",  # NVDAx
    "Xs3eBt7uRfJX8QUs4suhyU8p2M6DoUDrJyWBa8LLZsg",  # AMZNx
    "FSiC43YFG6cJswvTVJQowNDPXHu6LTJDdNT7HCwipump",  # SPCX (SpaceXAI spoof)
})

# Major US equity / ETF tickers (tokenized on Solana as TICKER or TICKERx).
STOCK_TICKERS: frozenset[str] = frozenset({
    "AAPL", "AMZN", "AMD", "ARM", "ARKK", "AVGO", "BA", "BRK", "BRKB", "COIN",
    "CRM", "DIA", "DIS", "GME", "GOOG", "GOOGL", "HOOD", "INTC", "IWM", "JPM",
    "MA", "META", "MSFT", "MSTR", "NFLX", "NVDA", "ORCL", "PLTR", "PYPL", "QQQ",
    "SPCX", "SPY", "TSLA", "UBER", "V", "VOO", "VTI", "WMT", "XOM",
})

# Crypto symbols that must never be blocked by ticker heuristics.
CRYPTO_ALLOWLIST_SYMBOLS: frozenset[str] = frozenset({
    "BTC", "WBTC", "WETH", "ETH", "SOL", "USDC", "USDT", "WSOL", "JUP", "RAY",
    "BONK", "WIF", "JTO", "PYTH", "RENDER", "HNT", "MSOL", "STSOL",
})

_NAME_KEYWORD_RE = re.compile(
    r"\b(?:xstock|xstocks|tokenized\s+(?:stock|equity|equities)|"
    r"stock[\s-]?backed|equity\s+token|backed\s+finance|"
    r"tokenized\s+us\s+stock|real\s+world\s+asset|spacex(?:ai)?)\b",
    re.IGNORECASE,
)

_LABEL_KEYWORD_RE = re.compile(
    r"\b(?:xstock|xstocks|stock|equity|equities|etf|rwa|backed)\b",
    re.IGNORECASE,
)


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper().replace(" ", "").replace(".", "")


def _env_mint_set(attr: str) -> frozenset[str]:
    return frozenset(getattr(Config, attr, ()) or ())


def stock_blocklist_mints() -> frozenset[str]:
    return KNOWN_STOCK_MINTS | _env_mint_set("STOCK_TOKEN_BLOCKLIST_MINTS")


def stock_allowlist_mints() -> frozenset[str]:
    return _env_mint_set("STOCK_TOKEN_ALLOWLIST_MINTS")


def _symbol_is_stock_ticker(symbol: str) -> bool:
    sym = _normalize_symbol(symbol)
    if not sym or sym in CRYPTO_ALLOWLIST_SYMBOLS:
        return False
    if sym in STOCK_TICKERS:
        return True
    if sym.endswith("X") and len(sym) > 1:
        base = sym[:-1]
        if base in STOCK_TICKERS:
            return True
    return False


def _text_has_stock_keyword(*parts: str) -> bool:
    combined = " ".join(p for p in parts if p).strip()
    if not combined:
        return False
    return bool(_NAME_KEYWORD_RE.search(combined))


def _labels_indicate_stock(labels: Optional[Sequence[str]]) -> bool:
    if not labels:
        return False
    for label in labels:
        text = str(label or "").strip()
        if not text:
            continue
        if _LABEL_KEYWORD_RE.search(text):
            return True
    return False


def _mint_is_backed_xstock(mint: str) -> bool:
    """Backed xStocks on Solana use mints starting with Xs."""
    if not mint or not mint.startswith("Xs"):
        return False
    return len(mint) >= 32


def is_stock_related_token(
    *,
    mint: str = "",
    symbol: str = "",
    name: str = "",
    dex_labels: Optional[Sequence[str]] = None,
) -> bool:
    """Return True when a token looks stock-backed / tokenized equity."""
    if not Config.BLOCK_STOCK_RELATED_TOKENS:
        return False

    mint = (mint or "").strip()
    if mint and mint in stock_allowlist_mints():
        return False

    if mint and mint in stock_blocklist_mints():
        return True

    sym = _normalize_symbol(symbol)
    if sym and sym not in CRYPTO_ALLOWLIST_SYMBOLS and _symbol_is_stock_ticker(sym):
        return True

    if _text_has_stock_keyword(symbol, name):
        return True

    if _labels_indicate_stock(dex_labels):
        return True

    if mint and _mint_is_backed_xstock(mint) and sym and sym.endswith("X"):
        return True

    return False


def log_skipped_stock_token(mint: str, symbol: str) -> None:
    """Log once per mint when a stock-related token is skipped."""
    if mint in _logged_skipped_mints:
        return
    _logged_skipped_mints.add(mint)
    label = symbol or mint[:8]
    logger.info("skipped stock-related token: %s", label)


def filter_stock_candidates(candidates: Iterable) -> list:
    """Drop stock-related MoverCandidate rows; log each skipped mint once."""
    kept = []
    for candidate in candidates:
        if is_stock_related_token(
            mint=getattr(candidate, "mint", ""),
            symbol=getattr(candidate, "symbol", ""),
            name=getattr(candidate, "name", ""),
        ):
            log_skipped_stock_token(
                getattr(candidate, "mint", ""),
                getattr(candidate, "symbol", "") or getattr(candidate, "mint", "")[:8],
            )
            continue
        kept.append(candidate)
    return kept


def add_stock_allowlist_mint(mint: str) -> None:
    """Allow a mint through the stock filter at runtime."""
    mint = (mint or "").strip()
    if not mint:
        return
    current = set(Config.STOCK_TOKEN_ALLOWLIST_MINTS)
    current.add(mint)
    Config.STOCK_TOKEN_ALLOWLIST_MINTS = frozenset(current)
    _logged_skipped_mints.discard(mint)
    logger.info("Stock allowlist added: %s", mint[:8])


def reset_logged_skips() -> None:
    """Clear per-mint skip log dedupe (for tests)."""
    _logged_skipped_mints.clear()
