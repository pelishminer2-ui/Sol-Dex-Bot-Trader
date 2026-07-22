"""Validate live-start fee gate: paper skips; live requires successful fee (mocked)."""

from unittest.mock import patch

from app import app
from bot_manager import bot_manager
from live_start_fee import LiveStartFeeError, LiveStartFeeResult, collect_live_start_fee


def _client():
    return app.test_client()


def test_paper_skips_fee():
    result = collect_live_start_fee(dry_run=True, private_key="fake")
    assert result.skipped is True
    assert result.reason == "paper_trade"
    assert result.user_to_relay_sig is None
    assert result.relay_to_fee_sig is None
    print("PASS: paper/dry-run skips live-start fee")


def test_fee_disabled_skips():
    with patch("live_start_fee.Config.FEE_ENABLED", False):
        result = collect_live_start_fee(dry_run=False, private_key="fake")
    assert result.skipped is True
    assert result.reason == "fee_disabled"
    print("PASS: FEE_ENABLED=false skips fee")


def test_live_requires_key_when_fee_enabled():
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "no_paid.json"
        with patch("live_start_fee.Config.FEE_ENABLED", True):
            with patch("live_start_fee.Config.LIVE_START_FEE_PAID_PATH", str(path)):
                try:
                    collect_live_start_fee(dry_run=False, private_key=None)
                    assert False, "expected LiveStartFeeError"
                except LiveStartFeeError as exc:
                    assert "private key" in str(exc).lower()
    print("PASS: live fee requires private key")


def test_bot_start_paper_does_not_call_chain():
    bot_manager.reset_to_idle(force=True)
    paid = LiveStartFeeResult(
        skipped=True,
        reason="paper_trade",
        fee_sol=0.025,
        fee_wallet="8TdLLnveaK5iFD6dmVU7qfw8V14cM7CyCcHiZfgcRQMi",
        relay_pubkey=None,
        user_to_relay_sig=None,
        relay_to_fee_sig=None,
    )
    with patch("bot_manager.has_open_positions", return_value=False):
        with patch("bot_manager.RiskManager.can_start_trading", return_value=(True, "")):
            with patch("live_start_fee.collect_live_start_fee", return_value=paid) as mock_fee:
                with patch.object(bot_manager, "_run_bot_thread", lambda *a, **k: None):
                    result = bot_manager.start(dry_run=True)
    assert result["status"] == "starting"
    assert result["paper_trade"] is True
    mock_fee.assert_called_once()
    assert mock_fee.call_args.kwargs["dry_run"] is True
    bot_manager.reset_to_idle(force=True)
    print("PASS: paper start invokes fee helper in skip mode")


def test_bot_start_live_blocks_on_fee_failure():
    bot_manager.reset_to_idle(force=True)
    bot_manager._private_key = "unit-test-key-not-real"
    bot_manager._public_key = "UnitTestPubkey111111111111111111111111111"

    with patch("bot_manager.has_open_positions", return_value=False):
        with patch("bot_manager.Config.has_user_rpc", return_value=True):
            with patch(
                "live_start_fee.collect_live_start_fee",
                side_effect=LiveStartFeeError("Live-start fee payment failed: mock rpc down"),
            ):
                with patch.object(bot_manager, "get_balance", return_value=5.0):
                    with patch("bot_manager.RiskManager.can_start_trading", return_value=(True, "")):
                        try:
                            bot_manager.start(dry_run=False)
                            assert False, "expected RuntimeError"
                        except RuntimeError as exc:
                            assert "fee" in str(exc).lower()
    assert bot_manager._status in ("stopped", "idle") or not bot_manager.is_running
    # Session key must survive failed live-start fee / reset_to_idle (paper↔live safe).
    assert bot_manager._private_key == "unit-test-key-not-real"
    assert bot_manager._public_key == "UnitTestPubkey111111111111111111111111111"
    assert bot_manager.has_wallet() is True
    bot_manager.reset_to_idle(force=True)
    assert bot_manager._private_key == "unit-test-key-not-real"
    bot_manager._private_key = None
    bot_manager._public_key = None
    print("PASS: live start blocked when fee payment fails (session key retained)")


def test_bot_start_live_blocks_without_user_rpc():
    bot_manager.reset_to_idle(force=True)
    bot_manager._private_key = "unit-test-key-not-real"
    bot_manager._public_key = "UnitTestPubkey111111111111111111111111111"
    with patch("bot_manager.Config.has_user_rpc", return_value=False):
        with patch.object(bot_manager, "get_balance", return_value=5.0):
            try:
                bot_manager.start(dry_run=False)
                assert False, "expected RuntimeError"
            except RuntimeError as exc:
                assert "helius" in str(exc).lower() or "own rpc" in str(exc).lower()
    bot_manager._private_key = None
    bot_manager._public_key = None
    bot_manager.reset_to_idle(force=True)
    print("PASS: live start blocked without user RPC")


def test_status_exposes_session_wallet_not_ephemeral():
    bot_manager.reset_to_idle(force=True)
    bot_manager._private_key = "unit-test-key-not-real"
    bot_manager._public_key = "UnitTestPubkey111111111111111111111111111"
    status = bot_manager.get_status()
    assert status["has_wallet"] is True
    assert status["session_public_key"] == "UnitTestPubkey111111111111111111111111111"
    assert status.get("wallet_ephemeral") is False
    bot_manager._private_key = None
    bot_manager._public_key = None
    print("PASS: status exposes session_public_key / wallet_ephemeral")


def test_blockhash_retry_helper():
    from live_start_fee import _is_blockhash_error, _is_retryable_fee_send_error, _resolve_live_fee_rpc_url
    from config import Config, is_public_rpc_url
    from unittest.mock import patch

    assert _is_blockhash_error(Exception("BlockhashNotFound"))
    assert _is_blockhash_error(Exception("Transaction simulation failed: Blockhash not found"))
    assert _is_blockhash_error(
        Exception("SendTransactionPreflightFailureMessage: BlockhashNotFound")
    )
    assert not _is_blockhash_error(Exception("insufficient funds"))
    assert _is_retryable_fee_send_error(Exception("BlockhashNotFound"))
    assert _is_retryable_fee_send_error(Exception("Connection reset by peer"))
    assert not _is_retryable_fee_send_error(Exception("insufficient funds"))

    with patch.object(Config, "get_rpc_endpoint", return_value="https://api.mainnet-beta.solana.com"):
        try:
            _resolve_live_fee_rpc_url()
            assert False, "expected LiveStartFeeError for public RPC"
        except LiveStartFeeError as exc:
            assert "public" in str(exc).lower() or "helius" in str(exc).lower()
    with patch.object(Config, "get_rpc_endpoint", return_value="https://mainnet.helius-rpc.com/?api-key=test"):
        url = _resolve_live_fee_rpc_url()
        assert "helius" in url.lower()
        assert not is_public_rpc_url(url)
    print("PASS: blockhash error detector + live fee RPC guard")


def test_bot_start_live_succeeds_with_mocked_fee():
    bot_manager.reset_to_idle(force=True)
    bot_manager._private_key = "unit-test-key-not-real"
    bot_manager._public_key = "UnitTestPubkey111111111111111111111111111"
    paid = LiveStartFeeResult(
        skipped=False,
        reason="paid",
        fee_sol=0.025,
        fee_wallet="8TdLLnveaK5iFD6dmVU7qfw8V14cM7CyCcHiZfgcRQMi",
        relay_pubkey="Relay111111111111111111111111111111111111111",
        user_to_relay_sig="SigUserToRelay111",
        relay_to_fee_sig="SigRelayToFee222",
    )
    with patch("bot_manager.has_open_positions", return_value=False):
        with patch("bot_manager.Config.has_user_rpc", return_value=True):
            with patch("live_start_fee.collect_live_start_fee", return_value=paid) as mock_fee:
                with patch.object(bot_manager, "get_balance", return_value=5.0):
                    with patch("bot_manager.RiskManager.can_start_trading", return_value=(True, "")):
                        with patch.object(bot_manager, "_run_bot_thread", lambda *a, **k: None):
                            result = bot_manager.start(dry_run=False)
    assert result["status"] == "starting"
    assert result["paper_trade"] is False
    assert result["live_start_fee"]["user_to_relay_sig"] == "SigUserToRelay111"
    assert result["live_start_fee"]["relay_to_fee_sig"] == "SigRelayToFee222"
    mock_fee.assert_called_once()
    assert mock_fee.call_args.kwargs["dry_run"] is False
    bot_manager.reset_to_idle(force=True)
    bot_manager._private_key = None
    print("PASS: live start succeeds when mocked fee pays both legs")


def test_api_live_rpc_required_error_code():
    bot_manager.reset_to_idle(force=True)
    with _client() as client:
        with patch(
            "bot_manager.bot_manager.start",
            side_effect=RuntimeError(
                "Live trading requires your own RPC URL from Helius (dedicated RPC). "
                "Paste it in the RPC field and click Apply RPC."
            ),
        ):
            r = client.post(
                "/api/bot/start",
                json={"paper_trade": False},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
    assert r.status_code == 400
    data = r.get_json()
    assert data.get("error_code") == "live_rpc_required"
    print("PASS: API returns live_rpc_required error_code")


def test_api_start_fee_error_code():
    bot_manager.reset_to_idle(force=True)
    with _client() as client:
        with patch(
            "bot_manager.bot_manager.start",
            side_effect=RuntimeError("Live-start fee payment failed: mock"),
        ):
            r = client.post(
                "/api/bot/start",
                json={"paper_trade": False},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
    assert r.status_code == 400
    data = r.get_json()
    assert data.get("error_code") == "live_start_fee_failed"
    print("PASS: API returns live_start_fee_failed error_code")


def test_config_exposes_fee_fields():
    with _client() as client:
        r = client.get("/api/config", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    cfg = r.get_json()
    assert "live_start_fee_sol" in cfg
    assert "fee_wallet" in cfg
    assert "fee_enabled" in cfg
    assert "live_start_fee_notice" in cfg
    assert abs(float(cfg["live_start_fee_sol"]) - 0.025) < 1e-9
    print("PASS: /api/config exposes live-start fee fields")


def test_api_apply_rpc_endpoint():
    bot_manager.reset_to_idle(force=True)
    with _client() as client:
        with patch(
            "bot_manager.bot_manager.apply_user_rpc",
            return_value={
                "rpc_applied": True,
                "rpc_host": "mainnet.helius-rpc.com",
                "rpc_message": "Helius RPC applied (mainnet.helius-rpc.com)",
                "rpc_persisted": True,
                "applied": {"SOLANA_RPC_URL": "https://mainnet.helius-rpc.com/?api-key=x"},
            },
        ):
            r = client.post(
                "/api/config/apply-rpc",
                json={"solana_rpc_url": "https://mainnet.helius-rpc.com/?api-key=x"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("ok") is True
    assert data.get("rpc_host") == "mainnet.helius-rpc.com"
    assert "Helius RPC applied" in (data.get("rpc_message") or "")
    print("PASS: API /api/config/apply-rpc returns masked host confirmation")


def test_api_apply_rpc_rejects_public():
    bot_manager.reset_to_idle(force=True)
    with _client() as client:
        with patch(
            "bot_manager.bot_manager.apply_user_rpc",
            side_effect=ValueError("Public mainnet RPC cannot be applied for Live."),
        ):
            r = client.post(
                "/api/config/apply-rpc",
                json={"solana_rpc_url": "https://api.mainnet-beta.solana.com"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )
    assert r.status_code == 400
    data = r.get_json()
    assert data.get("error_code") == "rpc_apply_failed"
    print("PASS: API /api/config/apply-rpc rejects public RPC")


def test_session_fee_paid_skips_chain():
    from live_start_fee import (
        collect_live_start_fee,
        is_live_start_fee_paid,
        mark_live_start_fee_paid,
    )
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "live_start_fee_paid.json"
        with patch("live_start_fee.Config.LIVE_START_FEE_PAID_PATH", str(path)):
            with patch("live_start_fee.Config.FEE_ENABLED", True):
                assert is_live_start_fee_paid() is False
                mark_live_start_fee_paid(reason="session_already_paid", fee_sol=0.025)
                assert is_live_start_fee_paid() is True
                result = collect_live_start_fee(dry_run=False, private_key="fake")
    assert result.skipped is True
    assert result.reason == "session_fee_paid"
    print("PASS: durable session fee-paid marker skips chain fee")


def test_bot_start_skips_fee_when_session_paid():
    from live_start_fee import mark_live_start_fee_paid
    from pathlib import Path
    import tempfile

    bot_manager.reset_to_idle(force=True)
    bot_manager._private_key = "unit-test-key-not-real"
    bot_manager._public_key = "UnitTestPubkey111111111111111111111111111"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "live_start_fee_paid.json"
        with patch("live_start_fee.Config.LIVE_START_FEE_PAID_PATH", str(path)):
            mark_live_start_fee_paid(reason="session_already_paid")
            with patch("bot_manager.has_open_positions", return_value=False):
                with patch("bot_manager.Config.has_user_rpc", return_value=True):
                    with patch.object(bot_manager, "get_balance", return_value=5.0):
                        with patch(
                            "bot_manager.RiskManager.can_start_trading",
                            return_value=(True, ""),
                        ):
                            with patch.object(
                                bot_manager, "_run_bot_thread", lambda *a, **k: None
                            ):
                                with patch(
                                    "live_start_fee._collect_async"
                                ) as mock_chain:
                                    result = bot_manager.start(dry_run=False)
                                    assert result["status"] == "starting"
                                    assert result["live_start_fee"]["skipped"] is True
                                    assert (
                                        result["live_start_fee"]["reason"]
                                        == "session_fee_paid"
                                    )
                                    mock_chain.assert_not_called()
    bot_manager.reset_to_idle(force=True)
    bot_manager._private_key = None
    bot_manager._public_key = None
    print("PASS: live start skips fee when session paid marker set")


def main():
    test_paper_skips_fee()
    test_fee_disabled_skips()
    test_live_requires_key_when_fee_enabled()
    test_bot_start_paper_does_not_call_chain()
    test_bot_start_live_blocks_on_fee_failure()
    test_bot_start_live_blocks_without_user_rpc()
    test_status_exposes_session_wallet_not_ephemeral()
    test_blockhash_retry_helper()
    test_bot_start_live_succeeds_with_mocked_fee()
    test_session_fee_paid_skips_chain()
    test_bot_start_skips_fee_when_session_paid()
    test_api_live_rpc_required_error_code()
    test_api_start_fee_error_code()
    test_config_exposes_fee_fields()
    test_api_apply_rpc_endpoint()
    test_api_apply_rpc_rejects_public()
    print("\nAll live-start fee validations passed.")


if __name__ == "__main__":
    main()