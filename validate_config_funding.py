"""Validate spread config updates and minimum live funding enforcement."""



import json
import time

from unittest.mock import patch



from app import app

from config import (

    Config,

    DEFAULT_ENTRY_MOMENTUM_PCT,

    DEFAULT_TAKE_PROFIT_LEVELS,

    DEFAULT_TAKE_PROFIT_PORTIONS,

    DEFAULT_STOP_LOSS_PCT,

    DEFAULT_TRADE_SIZE_SOL,

    normalize_stop_loss_pct,

    normalize_entry_momentum_pct,

    normalize_trade_size,

)

from paper_session import PaperSession, PaperSessionManager, paper_session_manager
from bot_manager import bot_manager
from risk import RiskManager
from trade_activity import TradeActivityTracker, trade_activity





def test_spread_config_update():

    client = app.test_client()

    payload = {

        "entry_momentum_pct": 0.005,

        "take_profit_levels": [0.02, 0.04, 0.06, 0.08],

        "stop_loss_pct": 0.03,

    }

    res = client.post("/api/config", data=json.dumps(payload), content_type="application/json")

    assert res.status_code == 200, res.get_data(as_text=True)

    data = res.get_json()

    assert data["ok"] is True

    assert Config.ENTRY_MOMENTUM_PCT == 0.005

    assert Config.TAKE_PROFIT_LEVELS == [0.02, 0.04, 0.06, 0.08]

    assert Config.STOP_LOSS_PCT == 0.03

    assert data["config"]["entry_momentum_pct"] == 0.005



    # Restore defaults

    client.post(

        "/api/config",

        data=json.dumps(

            {

                "entry_momentum_pct": DEFAULT_ENTRY_MOMENTUM_PCT,

                "take_profit_levels": DEFAULT_TAKE_PROFIT_LEVELS,

                "take_profit_portions": DEFAULT_TAKE_PROFIT_PORTIONS,

                "stop_loss_pct": DEFAULT_STOP_LOSS_PCT,

            }

        ),

        content_type="application/json",

    )

    print("PASS: spread config update via POST /api/config")





def test_live_start_blocked_low_balance():

    with patch.object(RiskManager, "min_fund_waived", return_value=False):
        with patch.object(bot_manager, "_resolve_private_key", return_value="fake-key"):

            with patch.object(bot_manager, "get_balance", return_value=0.5):

                with patch.object(bot_manager, "_status", "stopped"):

                    try:

                        bot_manager.start(dry_run=False)

                        raise AssertionError("Expected RuntimeError for low balance")

                    except RuntimeError as exc:

                        assert "0.75" in str(exc) or "minimum" in str(exc).lower()

    print("PASS: live start blocked when balance < MIN_FUND_SOL")





def test_paper_trade_start_api():

    client = app.test_client()

    with patch.object(bot_manager, "_status", "stopped"):

        with patch.object(bot_manager, "_run_bot_thread"):

            res = client.post(

                "/api/bot/start",

                data=json.dumps({"paper_trade": True}),

                content_type="application/json",

            )

            assert res.status_code == 200, res.get_json()

            data = res.get_json()

            assert data["ok"] is True

            assert data["paper_trade"] is True

            assert data["dry_run"] is True

            bot_manager.stop()



    status = client.get("/api/bot/status").get_json()

    assert "paper_trade" in status

    assert status["paper_trade"] is True



    cfg = client.get("/api/config").get_json()

    assert "paper_trade" in cfg

    print("PASS: paper_trade API wiring")





def test_wallet_balance_api_paper():

    client = app.test_client()

    res = client.get("/api/wallet/balance?paper_trade=true")

    assert res.status_code == 200, res.get_json()

    data = res.get_json()

    assert data["simulated"] is True

    assert abs(data["balance"] - Config.PAPER_SIMULATED_BALANCE_SOL) < 1e-9

    print("PASS: /api/wallet/balance returns simulated paper balance")





def test_paper_can_open_with_zero_wallet():

    risk = RiskManager()

    ok, reason = risk.can_open_position(0, 0.0, dry_run=True)

    assert ok is True, reason

    size = risk.compute_trade_size(0.0, dry_run=True)

    assert size > 0

    print("PASS: paper mode can open position with zero real wallet")





def test_dry_run_allowed_low_balance():

    with patch.object(bot_manager, "get_balance", return_value=0.1):

        with patch.object(bot_manager, "_status", "stopped"):

            with patch.object(bot_manager, "_run_bot_thread"):

                result = bot_manager.start(dry_run=True)

                assert result["dry_run"] is True

                bot_manager.stop()

    print("PASS: dry-run start allowed without MIN_FUND_SOL")





def test_check_minimum_funding_unit():

    risk = RiskManager()

    with patch.object(RiskManager, "min_fund_waived", return_value=False):
        ok, _ = risk.check_minimum_funding(0.74, dry_run=False)

        assert ok is False

        ok, _ = risk.check_minimum_funding(0.75, dry_run=False)

        assert ok is True

        ok, _ = risk.check_minimum_funding(0.0, dry_run=True)

        assert ok is True

        with patch.object(paper_session_manager, "get_simulated_balance", return_value=0.5):

            ok, _ = risk.check_minimum_funding(0.0, dry_run=True)

            assert ok is False

        ok, _ = risk.can_start_trading(0.74, dry_run=False)
        assert ok is False
        ok, _ = risk.can_start_trading(0.75, dry_run=False)
        assert ok is True

    print("PASS: check_minimum_funding unit checks")





def _reset_trade_activity() -> None:
    with trade_activity._lock:
        trade_activity._session_trade_count = 0
        trade_activity._session_active = False
        trade_activity._last_trade_at = None


def _reset_paper_session(target_balance: float = 0.75) -> None:
    with paper_session_manager._lock:
        paper_session_manager._target_balance_sol = target_balance
        Config.PAPER_SIMULATED_BALANCE_SOL = target_balance
        paper_session_manager._session = PaperSession()
        paper_session_manager._last_session = PaperSession()


def test_entry_allowed_below_min_fund_during_session():
    """After a session trade, start gate is waived when balance < MIN_FUND_SOL."""
    risk = RiskManager()
    _reset_trade_activity()
    _reset_paper_session(0.75)

    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
            with patch.object(Config, "TRADE_SIZE_SOL", 0.05):
                paper_session_manager.start_session()
                paper_session_manager.record_buy(0.0024)
                trade_activity.start_session()
                trade_activity.record_trade(
                    {"action": "buy", "timestamp": time.time(), "dry_run": True}
                )
                balance = paper_session_manager.get_simulated_balance()
                assert balance < Config.MIN_FUND_SOL
                assert abs(balance - 0.7476) < 1e-9

                ok_start, _ = risk.can_start_trading(None, dry_run=True)
                assert ok_start is True

                ok_entry, reason = risk.can_open_position(0, 0.0, dry_run=True)
                assert ok_entry is True, reason

    paper_session_manager.end_session()
    _reset_trade_activity()
    print("PASS: entry allowed below MIN_FUND_SOL during active session")





def test_paper_start_blocked_low_simulated_balance():
    risk = RiskManager()
    _reset_trade_activity()
    with patch.object(RiskManager, "min_fund_waived", return_value=False):
        with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.5):
            ok, reason = risk.can_start_trading(None, dry_run=True)
            assert ok is False
            assert "0.75" in reason or "minimum" in reason.lower()

    with patch.object(bot_manager, "_status", "stopped"):
        with patch.object(bot_manager, "_run_bot_thread"):
            with patch.object(
                RiskManager,
                "can_start_trading",
                return_value=(False, "paper simulated balance 0.5000 SOL is below minimum 0.75 SOL"),
            ):
                try:
                    bot_manager.start(dry_run=True)
                    raise AssertionError("Expected RuntimeError for low paper balance")
                except RuntimeError as exc:
                    assert "0.75" in str(exc) or "minimum" in str(exc).lower()

    _reset_paper_session(0.75)

    print("PASS: paper start blocked when simulated balance < MIN_FUND_SOL")


def test_config_to_dict_includes_spread_defaults():

    cfg = Config.to_dict()

    assert "spread_defaults" in cfg

    assert cfg["min_fund_sol"] == Config.MIN_FUND_SOL

    assert cfg["max_wallet_trade_pct"] == Config.MAX_WALLET_TRADE_PCT

    assert cfg["spread_defaults"]["entry_momentum_pct"] == DEFAULT_ENTRY_MOMENTUM_PCT

    assert cfg["spread_defaults"]["take_profit_levels"] == DEFAULT_TAKE_PROFIT_LEVELS

    assert cfg["spread_defaults"]["take_profit_portions"] == DEFAULT_TAKE_PROFIT_PORTIONS

    assert cfg["spread_defaults"]["stop_loss_pct"] == DEFAULT_STOP_LOSS_PCT

    assert cfg["spread_defaults"]["trade_size_sol"] == DEFAULT_TRADE_SIZE_SOL

    assert "take_profit_levels" in cfg

    print("PASS: Config.to_dict includes spread_defaults and ladder fields")





def test_compute_trade_size_wallet_cap():

    risk = RiskManager()

    with patch.object(Config, "TRADE_SIZE_SOL", 1.0):

        with patch.object(Config, "MAX_POSITION_SOL", 1.0):

            with patch.object(Config, "MIN_SOL_RESERVE", 0.02):

                with patch.object(Config, "MAX_WALLET_TRADE_PCT", 0.15):

                    with patch.object(Config, "LIVE_TRADEABLE_BALANCE_SOL", 10.0):

                        size = risk.compute_trade_size(1.0)

                        assert abs(size - 0.147) < 0.001, f"expected ~0.147, got {size}"

    print("PASS: 1.0 SOL wallet caps trade at ~15% of available")





def test_compute_trade_size_min_fund_wallet():

    risk = RiskManager()

    with patch.object(Config, "TRADE_SIZE_SOL", 1.0):

        with patch.object(Config, "MAX_POSITION_SOL", 1.0):

            with patch.object(Config, "MIN_SOL_RESERVE", 0.02):

                with patch.object(Config, "MAX_WALLET_TRADE_PCT", 0.15):

                    size = risk.compute_trade_size(0.75)

                    expected = (0.75 - 0.02) * 0.15

                    assert abs(size - expected) < 0.001, f"expected {expected}, got {size}"

    print("PASS: 0.75 SOL min-fund wallet cap")





def test_paper_trade_compute_trade_size():

    risk = RiskManager()

    size = risk.compute_trade_size(0, dry_run=True)

    assert size > 0

    assert size <= Config.TRADE_SIZE_SOL

    with patch.object(Config, "TRADE_SIZE_SOL", 1.0):

        with patch.object(Config, "MAX_POSITION_SOL", 1.0):

            with patch.object(Config, "MIN_SOL_RESERVE", 0.02):

                with patch.object(Config, "MAX_WALLET_TRADE_PCT", 0.15):

                    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):

                        size = risk.compute_trade_size(0, dry_run=True)

                        expected = (0.75 - 0.02) * 0.15

                        assert abs(size - expected) < 0.001, f"expected {expected}, got {size}"

    print("PASS: paper trade compute_trade_size uses simulated 0.75 SOL wallet cap")





def test_start_applies_stop_loss_from_payload():
    client = app.test_client()
    from config import Config

    Config.STOP_LOSS_PCT = DEFAULT_STOP_LOSS_PCT
    with patch.object(bot_manager, "_status", "stopped"):
        with patch.object(bot_manager, "_run_bot_thread"):
            res = client.post(
                "/api/bot/start",
                data=json.dumps({"paper_trade": True, "stop_loss_pct": 0.015}),
                content_type="application/json",
            )
            assert res.status_code == 200, res.get_json()
            bot_manager.stop()

    cfg_after = client.get("/api/config").get_json()
    assert cfg_after["stop_loss_pct"] == 0.015
    client.post(
        "/api/config",
        data=json.dumps({"stop_loss_pct": DEFAULT_STOP_LOSS_PCT}),
        content_type="application/json",
    )
    print("PASS: start applies stop_loss_pct from payload")


def test_apply_5pct_stop_loss_config():
    """POST 5% stop loss via /api/config and verify Config + strategy exit threshold."""
    import time
    from fee_estimator import compute_take_profit_levels, get_fee_budget
    from strategy import MomentumStrategy, Position, SignalType

    client = app.test_client()
    prev = Config.STOP_LOSS_PCT
    try:
        res = client.post(
            "/api/config",
            data=json.dumps({"stop_loss_pct": 0.05}),
            content_type="application/json",
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        assert Config.STOP_LOSS_PCT == 0.05

        token_raw = 10000
        pos = Position(
            mint="testmint",
            symbol="TEST",
            entry_price=1.0,
            entry_time=time.time(),
            size_sol=0.05,
            token_amount_raw=token_raw,
            initial_token_amount_raw=token_raw,
            remaining_token_amount_raw=token_raw,
            tp_levels=compute_take_profit_levels(0.05),
            tp_portions=list(Config.TAKE_PROFIT_PORTIONS),
            target_net_profit_sol=Config.TARGET_NET_PROFIT_SOL,
            fee_budget_sol=get_fee_budget(0.05),
        )
        strategy = MomentumStrategy()
        signal = strategy.evaluate_exit(pos, current_price=0.95)
        assert signal is not None
        assert signal.signal_type == SignalType.SELL_SL

        no_signal = strategy.evaluate_exit(pos, current_price=0.96)
        assert no_signal is None
    finally:
        client.post(
            "/api/config",
            data=json.dumps({"stop_loss_pct": prev}),
            content_type="application/json",
        )
    print("PASS: 5% stop loss applied via config and strategy exits at -5%")


def test_start_applies_5pct_stop_loss_from_payload():
    client = app.test_client()

    Config.STOP_LOSS_PCT = DEFAULT_STOP_LOSS_PCT
    with patch.object(bot_manager, "_status", "stopped"):
        with patch.object(bot_manager, "_run_bot_thread"):
            res = client.post(
                "/api/bot/start",
                data=json.dumps({"paper_trade": True, "stop_loss_pct": 0.05}),
                content_type="application/json",
            )
            assert res.status_code == 200, res.get_json()
            bot_manager.stop()

    cfg_after = client.get("/api/config").get_json()
    assert cfg_after["stop_loss_pct"] == 0.05
    assert Config.STOP_LOSS_PCT == 0.05

    client.post(
        "/api/config",
        data=json.dumps({"stop_loss_pct": DEFAULT_STOP_LOSS_PCT}),
        content_type="application/json",
    )
    print("PASS: start applies 5% stop_loss_pct from payload")


def test_normalize_stop_loss_pct():
    assert normalize_stop_loss_pct(0.015) == 0.015
    assert normalize_stop_loss_pct(0.02) == 0.02
    assert normalize_stop_loss_pct(0.03) == 0.03
    assert normalize_stop_loss_pct(0.05) == 0.05
    try:
        normalize_stop_loss_pct(0.04)
        raise AssertionError("Expected ValueError for disallowed stop loss")
    except ValueError as exc:
        assert "0.05 (5.0%)" in str(exc)
    print("PASS: normalize_stop_loss_pct accepts 1.5%, 3%, and 5%")


def test_normalize_trade_size_extended():
    assert normalize_trade_size(0.20) == 0.20
    assert normalize_trade_size(1.0) == 1.0
    try:
        normalize_trade_size(0.15)
        raise AssertionError("Expected ValueError for disallowed trade size")
    except ValueError:
        pass
    print("PASS: normalize_trade_size accepts 0.20 and 1.0")


def test_normalize_entry_momentum_pct():
    assert normalize_entry_momentum_pct(0.0025) == 0.0025
    assert normalize_entry_momentum_pct(0.005) == 0.005
    assert normalize_entry_momentum_pct(0.0075) == 0.0075
    try:
        normalize_entry_momentum_pct(0.003)
        raise AssertionError("Expected ValueError for disallowed entry momentum")
    except ValueError as exc:
        assert "0.75%" in str(exc) or "0.25%" in str(exc)
    print("PASS: normalize_entry_momentum_pct accepts 0.25%, 0.50%, and 0.75%")


def test_apply_025_entry_momentum_config():
    client = app.test_client()
    prev = Config.ENTRY_MOMENTUM_PCT

    res = client.post(
        "/api/config",
        data=json.dumps({"entry_momentum_pct": 0.0025}),
        content_type="application/json",
    )
    assert res.status_code == 200, res.get_json()
    assert Config.ENTRY_MOMENTUM_PCT == 0.0025
    assert res.get_json()["config"]["entry_momentum_pct"] == 0.0025

    client.post(
        "/api/config",
        data=json.dumps({"entry_momentum_pct": prev}),
        content_type="application/json",
    )
    print("PASS: apply config sets entry_momentum_pct to 0.25%")


def test_start_applies_entry_momentum_from_payload():
    client = app.test_client()
    prev = Config.ENTRY_MOMENTUM_PCT

    with patch.object(bot_manager, "_status", "stopped"):
        with patch.object(bot_manager, "_run_bot_thread"):
            res = client.post(
                "/api/bot/start",
                data=json.dumps({"paper_trade": True, "entry_momentum_pct": 0.0025}),
                content_type="application/json",
            )
            assert res.status_code == 200, res.get_json()
            bot_manager.stop()

    assert Config.ENTRY_MOMENTUM_PCT == 0.0025
    cfg_after = client.get("/api/config").get_json()
    assert cfg_after["entry_momentum_pct"] == 0.0025

    client.post(
        "/api/config",
        data=json.dumps({"entry_momentum_pct": prev}),
        content_type="application/json",
    )
    print("PASS: start applies entry_momentum_pct from payload")


def test_paper_simulated_balance_bounds():
    from config import (
        MAX_PAPER_SIMULATED_BALANCE_SOL,
        MIN_PAPER_SIMULATED_BALANCE_SOL,
        normalize_paper_balance_sol,
    )

    assert normalize_paper_balance_sol(0.75) == 0.75
    assert normalize_paper_balance_sol(MIN_PAPER_SIMULATED_BALANCE_SOL) == MIN_PAPER_SIMULATED_BALANCE_SOL
    try:
        normalize_paper_balance_sol(0.05)
        raise AssertionError("expected ValueError for balance below minimum")
    except ValueError:
        pass
    try:
        normalize_paper_balance_sol(MAX_PAPER_SIMULATED_BALANCE_SOL + 1)
        raise AssertionError("expected ValueError for balance above maximum")
    except ValueError:
        pass
    print("PASS: paper balance normalize enforces min/max bounds")


def test_live_tradeable_balance_bounds():
    from config import (
        MAX_LIVE_TRADEABLE_BALANCE_SOL,
        MIN_LIVE_TRADEABLE_BALANCE_SOL,
        normalize_live_tradeable_balance_sol,
    )

    assert normalize_live_tradeable_balance_sol(0.75) == 0.75
    assert (
        normalize_live_tradeable_balance_sol(MIN_LIVE_TRADEABLE_BALANCE_SOL)
        == MIN_LIVE_TRADEABLE_BALANCE_SOL
    )
    try:
        normalize_live_tradeable_balance_sol(0.5)
        raise AssertionError("expected ValueError for tradeable below minimum")
    except ValueError:
        pass
    try:
        normalize_live_tradeable_balance_sol(MAX_LIVE_TRADEABLE_BALANCE_SOL + 1)
        raise AssertionError("expected ValueError for tradeable above maximum")
    except ValueError:
        pass
    print("PASS: live tradeable balance normalize enforces min/max bounds")


def test_live_tradeable_balance_caps_sizing():
    risk = RiskManager()
    with patch.object(Config, "TRADE_SIZE_SOL", 2.0):
        with patch.object(Config, "MAX_POSITION_SOL", 2.0):
            with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
                with patch.object(Config, "MAX_WALLET_TRADE_PCT", 0.75):
                    with patch.object(Config, "LIVE_TRADEABLE_BALANCE_SOL", 2.0):
                        size = risk.compute_trade_size(10.0)
                        expected = (2.0 - 0.02) * 0.75
                        assert abs(size - expected) < 0.001, f"expected {expected}, got {size}"
    print("PASS: live tradeable balance caps trade sizing")


def test_api_set_live_tradeable_balance():
    from live_tradeable_balance import live_tradeable_balance_manager

    client = app.test_client()
    prev = live_tradeable_balance_manager.get_balance()
    try:
        resp = client.post("/api/live/tradeable-balance", json={"amount": 3.5})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert abs(data["live_tradeable_balance_sol"] - 3.5) < 1e-9
        assert abs(live_tradeable_balance_manager.get_balance() - 3.5) < 1e-9
    finally:
        live_tradeable_balance_manager.set_balance(prev)
    print("PASS: POST /api/live/tradeable-balance sets configured cap")


def test_api_get_live_tradeable_balance():
    client = app.test_client()
    resp = client.get("/api/live/tradeable-balance")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "live_tradeable_balance_sol" in data
    assert "min_live_tradeable_balance_sol" in data
    assert "max_live_tradeable_balance_sol" in data
    print("PASS: GET /api/live/tradeable-balance returns configured cap")


def test_apply_trade_size_config():
    client = app.test_client()
    prev = Config.TRADE_SIZE_SOL

    res = client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": 0.07}),
        content_type="application/json",
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["config"]["trade_size_sol"] == 0.07
    assert Config.TRADE_SIZE_SOL == 0.07

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": prev}),
        content_type="application/json",
    )
    print("PASS: apply config sets TRADE_SIZE_SOL to 0.07")


def test_apply_trade_size_020_and_100():
    client = app.test_client()
    prev = Config.TRADE_SIZE_SOL

    res = client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": 0.20}),
        content_type="application/json",
    )
    assert res.status_code == 200, res.get_json()
    assert Config.TRADE_SIZE_SOL == 0.20
    assert res.get_json()["config"]["trade_size_sol"] == 0.20

    res = client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": 1.0}),
        content_type="application/json",
    )
    assert res.status_code == 200, res.get_json()
    assert Config.TRADE_SIZE_SOL == 1.0
    assert res.get_json()["config"]["trade_size_sol"] == 1.0

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": prev}),
        content_type="application/json",
    )
    print("PASS: apply config sets TRADE_SIZE_SOL to 0.20 and 1.0")


def test_compute_trade_size_50pct_cap():
    risk = RiskManager()

    with patch.object(Config, "TRADE_SIZE_SOL", 1.0):
        with patch.object(Config, "MAX_POSITION_SOL", 1.0):
            with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
                with patch.object(Config, "MAX_WALLET_TRADE_PCT", 0.50):
                    size = risk.compute_trade_size(0.75)
                    expected = (0.75 - 0.02) * 0.50
                    assert abs(size - expected) < 0.001, f"expected {expected}, got {size}"

    print("PASS: 50% max trade cap on 0.75 SOL wallet")


def test_paper_trade_020_on_paper_wallet():
    client = app.test_client()
    prev = Config.TRADE_SIZE_SOL

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": 0.20}),
        content_type="application/json",
    )
    risk = RiskManager()
    size = risk.compute_trade_size(0.0, dry_run=True)
    assert size == 0.20, f"expected 0.20 SOL on 0.75 paper wallet with 75% cap, got {size}"

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": prev}),
        content_type="application/json",
    )
    print("PASS: 0.20 SOL selection applies on 0.75 SOL paper wallet")


def test_trade_size_100_with_sufficient_wallet():
    client = app.test_client()
    prev = Config.TRADE_SIZE_SOL

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": 1.0}),
        content_type="application/json",
    )
    risk = RiskManager()
    with patch.object(Config, "MAX_POSITION_SOL", 1.0):
        with patch.object(Config, "LIVE_TRADEABLE_BALANCE_SOL", 10.0):
            size = risk.compute_trade_size(10.0)
            assert size == 1.0, f"expected 1.0 SOL with sufficient wallet, got {size}"

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": prev}),
        content_type="application/json",
    )
    print("PASS: 1.0 SOL applies when wallet has sufficient balance")


def test_paper_trade_uses_selected_trade_size():
    client = app.test_client()
    prev = Config.TRADE_SIZE_SOL

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": 0.07}),
        content_type="application/json",
    )
    risk = RiskManager()
    size = risk.compute_trade_size(0.0, dry_run=True)
    assert size == 0.07, f"expected 0.07 SOL, got {size}"

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": prev}),
        content_type="application/json",
    )
    print("PASS: paper trade uses selected 0.07 SOL size with 0.75 simulated balance")


def test_start_applies_trade_size_from_payload():
    client = app.test_client()
    prev = Config.TRADE_SIZE_SOL

    with patch.object(bot_manager, "_status", "stopped"):
        with patch.object(bot_manager, "_run_bot_thread"):
            res = client.post(
                "/api/bot/start",
                data=json.dumps({"paper_trade": True, "trade_size_sol": 0.10}),
                content_type="application/json",
            )
            assert res.status_code == 200, res.get_json()
            bot_manager.stop()

    assert Config.TRADE_SIZE_SOL == 0.10
    cfg_after = client.get("/api/config").get_json()
    assert cfg_after["trade_size_sol"] == 0.10

    client.post(
        "/api/config",
        data=json.dumps({"trade_size_sol": prev}),
        content_type="application/json",
    )
    print("PASS: start applies trade_size_sol from payload")


def test_min_fund_waiver_after_recent_journal_trade():
    """Start allowed below MIN_FUND_SOL when journal has a trade within waiver window."""
    import tempfile
    from pathlib import Path

    _reset_trade_activity()
    now = time.time()
    journal = Path(tempfile.mktemp(suffix=".jsonl"))
    journal.write_text(
        json.dumps(
            {
                "action": "buy",
                "mint": "TestMint",
                "symbol": "TEST",
                "timestamp": now - 300,
                "dry_run": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    risk = RiskManager()
    with patch.object(Config, "TRADE_JOURNAL_PATH", str(journal)):
        with patch.object(trade_activity, "_journal_path", journal):
            with patch.object(trade_activity, "_last_trade_at", now - 300):
                with patch.object(trade_activity, "_clock", lambda: now):
                    assert trade_activity.min_fund_waived() is True
                    ok, reason = risk.can_start_trading(0.50, dry_run=False)
                    assert ok is True, reason

    journal.unlink(missing_ok=True)
    _reset_trade_activity()
    print("PASS: min fund waived after recent journal trade")


def test_min_fund_waiver_active_session():
    """Waiver applies when current session has recorded trades."""
    _reset_trade_activity()
    trade_activity.start_session()
    trade_activity.record_trade(
        {"action": "buy", "timestamp": time.time(), "dry_run": True}
    )
    assert trade_activity.min_fund_waived() is True
    trade_activity.end_session()
    _reset_trade_activity()
    print("PASS: min fund waived during active session with trades")


def test_min_fund_waiver_still_requires_affordable_trade():
    """Waiver skips MIN_FUND_SOL but not reserve + trade size affordability."""
    _reset_trade_activity()
    trade_activity.start_session()
    trade_activity.record_trade(
        {"action": "buy", "timestamp": time.time(), "dry_run": True}
    )
    risk = RiskManager()
    with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
        with patch.object(Config, "TRADE_SIZE_SOL", 0.05):
            ok, reason = risk.can_open_position(0, 0.02, dry_run=False)
            assert ok is False, reason
            assert "insufficient" in reason.lower()
    trade_activity.end_session()
    _reset_trade_activity()
    print("PASS: waiver still requires affordable trade size")


def test_min_fund_waiver_status_fields():
    _reset_trade_activity()
    _reset_paper_session(0.75)
    paper_session_manager.start_session()
    paper_session_manager.record_buy(0.05)
    fields = trade_activity.status_fields()
    assert fields["session_has_trades"] is True
    assert fields["min_fund_waived"] is True
    assert fields["last_trade_at"] is not None
    paper_session_manager.end_session()
    _reset_trade_activity()
    print("PASS: min fund waiver status fields")


def test_live_start_allowed_with_min_fund_waiver():
    """Live bot_manager.start succeeds below MIN_FUND_SOL when journal has recent trade."""
    import tempfile
    from pathlib import Path

    _reset_trade_activity()
    now = time.time()
    journal = Path(tempfile.mktemp(suffix=".jsonl"))
    journal.write_text(
        json.dumps(
            {
                "action": "sell",
                "mint": "LiveMint",
                "timestamp": now - 120,
                "dry_run": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with patch.object(Config, "TRADE_JOURNAL_PATH", str(journal)):
        with patch.object(trade_activity, "_journal_path", journal):
            with patch.object(trade_activity, "_clock", lambda: now):
                trade_activity.refresh_from_journal()
                with patch.object(bot_manager, "_resolve_private_key", return_value="fake-key"):
                    with patch.object(bot_manager, "get_balance", return_value=0.60):
                        with patch.object(bot_manager, "_status", "stopped"):
                            with patch.object(bot_manager, "_run_bot_thread"):
                                result = bot_manager.start(dry_run=False)
                                assert result["dry_run"] is False
                                bot_manager.stop()

    journal.unlink(missing_ok=True)
    _reset_trade_activity()
    print("PASS: live start allowed with min fund waiver")


def test_paper_start_allowed_with_min_fund_waiver():
    """Paper bot_manager.start succeeds below MIN_FUND_SOL when session has trades."""
    _reset_trade_activity()
    _reset_paper_session(0.75)

    with patch.object(Config, "PAPER_SIMULATED_BALANCE_SOL", 0.75):
        with patch.object(Config, "MIN_SOL_RESERVE", 0.02):
            with patch.object(Config, "TRADE_SIZE_SOL", 0.05):
                paper_session_manager.start_session()
                paper_session_manager.record_buy(0.05)
                assert paper_session_manager.get_simulated_balance() < Config.MIN_FUND_SOL
                paper_session_manager.end_session()
                assert trade_activity.min_fund_waived() is True

                with patch.object(bot_manager, "_status", "stopped"):
                    with patch.object(bot_manager, "_run_bot_thread"):
                        result = bot_manager.start(dry_run=True)
                        assert result["dry_run"] is True
                        bot_manager.stop()

    _reset_trade_activity()
    print("PASS: paper start allowed with min fund waiver")


def test_journal_tracks_live_and_paper_trades():
    """Journal buy/sell/sell_partial from live or paper both enable waiver."""
    import tempfile
    from pathlib import Path

    _reset_trade_activity()
    now = time.time()
    risk = RiskManager()

    for action, dry_run in (("buy", False), ("sell", True), ("sell_partial", False)):
        journal = Path(tempfile.mktemp(suffix=".jsonl"))
        journal.write_text(
            json.dumps(
                {"action": action, "timestamp": now - 60, "dry_run": dry_run}
            )
            + "\n",
            encoding="utf-8",
        )
        with patch.object(trade_activity, "_journal_path", journal):
            with patch.object(trade_activity, "_clock", lambda: now):
                trade_activity.refresh_from_journal()
                assert trade_activity.has_trades_in_last_hour() is True
                ok_live, _ = risk.can_start_trading(0.55, dry_run=False)
                assert ok_live is True, f"live start blocked for journal {action}"
                ok_paper, _ = risk.can_start_trading(None, dry_run=True)
                assert ok_paper is True, f"paper start blocked for journal {action}"
        journal.unlink(missing_ok=True)

    _reset_trade_activity()
    print("PASS: journal tracks live and paper trades for waiver")


def main():

    test_check_minimum_funding_unit()

    test_entry_allowed_below_min_fund_during_session()

    test_paper_start_blocked_low_simulated_balance()

    test_config_to_dict_includes_spread_defaults()

    test_compute_trade_size_wallet_cap()

    test_compute_trade_size_min_fund_wallet()

    test_paper_trade_compute_trade_size()

    test_wallet_balance_api_paper()

    test_paper_can_open_with_zero_wallet()

    test_spread_config_update()

    test_live_start_blocked_low_balance()

    test_paper_trade_start_api()

    test_start_applies_stop_loss_from_payload()

    test_apply_5pct_stop_loss_config()

    test_start_applies_5pct_stop_loss_from_payload()

    test_apply_trade_size_config()

    test_normalize_stop_loss_pct()

    test_normalize_trade_size_extended()

    test_normalize_entry_momentum_pct()

    test_apply_025_entry_momentum_config()

    test_start_applies_entry_momentum_from_payload()

    test_min_fund_waiver_after_recent_journal_trade()

    test_min_fund_waiver_active_session()

    test_min_fund_waiver_still_requires_affordable_trade()

    test_min_fund_waiver_status_fields()

    test_live_start_allowed_with_min_fund_waiver()

    test_paper_start_allowed_with_min_fund_waiver()

    test_journal_tracks_live_and_paper_trades()

    test_paper_simulated_balance_bounds()

    test_live_tradeable_balance_bounds()

    test_live_tradeable_balance_caps_sizing()

    test_api_set_live_tradeable_balance()

    test_api_get_live_tradeable_balance()

    test_apply_trade_size_020_and_100()

    test_compute_trade_size_50pct_cap()

    test_paper_trade_020_on_paper_wallet()

    test_trade_size_100_with_sufficient_wallet()

    test_paper_trade_uses_selected_trade_size()

    test_start_applies_trade_size_from_payload()

    test_dry_run_allowed_low_balance()

    print("\nAll validation tests passed.")





if __name__ == "__main__":

    main()

