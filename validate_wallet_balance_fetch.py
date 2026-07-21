"""Validate wallet balance fetch does not treat RPC failures as 0.0 SOL."""

from unittest.mock import AsyncMock, MagicMock, patch

from bot_manager import BotManager
from risk import RiskManager


def test_get_balance_returns_none_on_rpc_failure():
    mgr = BotManager()
    mgr._private_key = "session-key-placeholder"
    with patch.object(mgr, "get_session_public_key", return_value=None):
        with patch("bot_manager.SolanaClient") as MockClient:
            inst = MockClient.return_value
            inst.public_key = MagicMock()
            inst.public_key.__str__ = lambda self: "FakePubkey111"
            inst.get_balance = AsyncMock(side_effect=RuntimeError("429 Too Many Requests"))
            inst.close = AsyncMock()
            # Bypass real keypair load inside SolanaClient.__init__
            MockClient.side_effect = None
            bal = mgr.get_balance()
    assert bal is None, f"expected None on RPC failure, got {bal!r}"
    print("PASS: get_balance returns None on RPC failure (not 0.0)")


def test_rpc_failure_does_not_pass_as_zero_min_fund():
    risk = RiskManager()
    with patch.object(RiskManager, "min_fund_waived", return_value=False):
        ok, reason = risk.can_start_trading(None, dry_run=False)
    assert ok is False
    assert "cannot verify" in reason.lower()
    with patch.object(RiskManager, "min_fund_waived", return_value=True):
        ok2, reason2 = risk.can_start_trading(None, dry_run=False)
    assert ok2 is False
    assert "cannot verify" in reason2.lower()
    assert "0.0000" not in reason2
    print("PASS: None balance blocked with verify message (not zero-wallet)")


def test_status_exposes_wallet_while_paper_session():
    from app import app
    from bot_manager import bot_manager

    client = app.test_client()
    with patch.object(bot_manager, "has_wallet", return_value=True):
        with patch.object(bot_manager, "get_balance", return_value=1.25):
            with patch.object(bot_manager, "_dry_run", True):
                with patch.object(bot_manager, "_status", "stopped"):
                    status = client.get("/api/bot/status").get_json()
    assert status["wallet_balance_sol"] == 1.25
    assert status["balance_simulated"] is True or status["dry_run"] is True
    print("PASS: status exposes on-chain wallet_balance_sol even in paper mode")


def main():
    test_get_balance_returns_none_on_rpc_failure()
    test_rpc_failure_does_not_pass_as_zero_min_fund()
    test_status_exposes_wallet_while_paper_session()
    print("\nAll wallet balance fetch validations passed.")


if __name__ == "__main__":
    main()
