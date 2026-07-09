"""Request firewall middleware for the Flask GUI — localhost-only, allowlist, rate limit."""

import logging
import re
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Set, Tuple

from flask import Flask, jsonify, request

from config import Config

logger = logging.getLogger(__name__)

LOCALHOST_ADDRS = frozenset({"127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1"})

# POST body fields that must never appear on control-plane routes (swap / key export injection).
FORBIDDEN_BODY_FIELDS = frozenset({
    "transaction",
    "swaptransaction",
    "raw_tx",
    "tx_bytes",
    "signed_transaction",
    "swap_now",
    "execute_swap",
    "input_mint",
    "output_mint",
    "in_amount",
    "out_amount",
    "quote_response",
    "export_private_key",
    "private_key_export",
})

SWAP_ACTION_PATTERN = re.compile(r"\bswap\b", re.IGNORECASE)

ALLOWED_ROUTES: Set[Tuple[str, str]] = {
    ("GET", "/"),
    ("GET", "/favicon.ico"),
    ("GET", "/manifest.json"),
    ("GET", "/service-worker.js"),
    ("GET", "/api/branding"),
    ("GET", "/api/bot/status"),
    ("GET", "/api/movers"),
    ("GET", "/api/positions"),
    ("GET", "/api/trades"),
    ("GET", "/api/logs"),
    ("GET", "/api/config"),
    ("GET", "/api/pnl"),
    ("GET", "/api/wallet/balance"),
    ("POST", "/api/wallet"),
    ("POST", "/api/bot/start"),
    ("POST", "/api/bot/stop"),
    ("POST", "/api/bot/force-reset"),
    ("POST", "/api/mint/unblock"),
    ("GET", "/api/actions/pending"),
    ("POST", "/api/actions/decide"),
    ("POST", "/api/config"),
    ("POST", "/api/config/save-bookmark"),
    ("POST", "/api/config/apply-best-win-strategy"),
    ("POST", "/api/config/apply-balanced-win-strategy"),
    ("POST", "/api/config/apply-steady-trade-strategy"),
    ("POST", "/api/config/restore-bookmark"),
    ("POST", "/api/wallet/save-env"),
}

TAX_ROUTE_PREFIX = "/api/tax/"
PAPER_ROUTE_PREFIX = "/api/paper/"
LIVE_ROUTE_PREFIX = "/api/live/"


class RateLimiter:
    """Simple in-memory per-IP sliding window rate limiter."""

    def __init__(self, max_requests: int, window_sec: float = 60.0):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = __import__("threading").Lock()

    def is_allowed(self, client_ip: str, max_requests: Optional[int] = None) -> bool:
        limit = self.max_requests if max_requests is None else max_requests
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[client_ip]
            while bucket and now - bucket[0] > self.window_sec:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_rate_limiter: Optional[RateLimiter] = None
_blocked_count = 0


def get_firewall_stats() -> dict:
    return {
        "active": True,
        "localhost_only": True,
        "rate_limit_per_min": Config.FIREWALL_RATE_LIMIT,
        "blocked_requests": _blocked_count,
        "trust_x_forwarded_for": Config.TRUST_X_FORWARDED_FOR,
    }


def _increment_blocked() -> None:
    global _blocked_count
    _blocked_count += 1


def _reject(reason: str, status: int = 403):
    _increment_blocked()
    logger.warning(
        "FIREWALL BLOCK [%s] %s %s from %s — %s",
        request.method,
        request.path,
        request.query_string.decode("utf-8", errors="replace"),
        request.remote_addr,
        reason,
    )
    return jsonify({"ok": False, "error": "Forbidden", "reason": reason}), status


def _normalize_path(path: str) -> str:
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def _is_static_path(path: str) -> bool:
    return path.startswith("/static/")


def _is_tax_path(path: str) -> bool:
    return path.startswith(TAX_ROUTE_PREFIX)


def _is_paper_path(path: str) -> bool:
    return path.startswith(PAPER_ROUTE_PREFIX)


def _is_live_path(path: str) -> bool:
    return path.startswith(LIVE_ROUTE_PREFIX)


def _route_allowed(method: str, path: str) -> bool:
    path = _normalize_path(path)
    if _is_static_path(path):
        return method == "GET"
    if _is_tax_path(path):
        return method == "GET"
    if _is_paper_path(path):
        if path in ("/api/paper/balance", "/api/paper/balance/reset"):
            return method == "POST"
        return method == "GET"
    if _is_live_path(path):
        if path == "/api/live/tradeable-balance":
            return method in ("GET", "POST")
        return method == "GET"
    return (method, path) in ALLOWED_ROUTES


def _resolve_client_ip() -> str:
    remote = (request.remote_addr or "").strip().lower()
    if Config.TRUST_X_FORWARDED_FOR:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip().lower()
    return remote


def _is_localhost(ip: str) -> bool:
    if not ip:
        return False
    if ip in LOCALHOST_ADDRS:
        return True
    if ip.startswith("127."):
        return True
    return False


def _body_has_forbidden_fields() -> Optional[str]:
    if request.method not in ("POST", "PUT", "PATCH"):
        return None

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None

    path = _normalize_path(request.path)

    for key, value in data.items():
        key_lower = key.lower()
        if key_lower in FORBIDDEN_BODY_FIELDS:
            return f"forbidden field: {key}"

        if key_lower == "mint" and path not in ("/api/wallet", "/api/mint/unblock"):
            if isinstance(value, str) and value:
                return "arbitrary mint injection blocked"
            if isinstance(value, dict) and value.get("swap"):
                return "arbitrary mint swap injection blocked"

        if key_lower == "action" and isinstance(value, str) and SWAP_ACTION_PATTERN.search(value):
            if path not in ("/api/wallet",):
                return "swap action injection blocked"

    if path == "/api/bot/start":
        swap_keys = {"mint", "input_mint", "output_mint", "amount", "swap", "transaction"}
        for key in data:
            if key.lower() in swap_keys:
                return f"bot control must not include swap param: {key}"

    return None


def init_firewall(app: Flask) -> None:
    global _rate_limiter
    _rate_limiter = RateLimiter(Config.FIREWALL_RATE_LIMIT, window_sec=60.0)

    @app.before_request
    def _firewall_before_request():
        client_ip = _resolve_client_ip()

        if not _is_localhost(client_ip):
            return _reject(f"non-localhost client: {client_ip}")

        method = request.method.upper()
        path = _normalize_path(request.path)

        # Tax/paper preview routes are polled by the dashboard; exempt from rate limit.
        if _rate_limiter and not _is_tax_path(path) and not _is_paper_path(path):
            per_min = Config.FIREWALL_RATE_LIMIT
            if _is_localhost(client_ip) and per_min >= 60:
                per_min = max(per_min, 300)
            if not _rate_limiter.is_allowed(client_ip, max_requests=per_min):
                return _reject("rate limit exceeded", status=429)

        if not _route_allowed(method, path):
            return _reject(f"route not allowlisted: {method} {path}")

        forbidden = _body_has_forbidden_fields()
        if forbidden:
            return _reject(forbidden)

        return None
