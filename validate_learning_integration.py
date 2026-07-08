"""Static integration checks: setup learning wired on every full exit path."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
SETUP = (ROOT / "setup_learner.py").read_text(encoding="utf-8")


def _require(text: str, needle: str, label: str) -> None:
    assert needle in text, f"FAIL: {label} — missing {needle!r}"


def test_rank_movers_uses_setup_learner() -> None:
    _require(BOT, "def _rank_movers", "_rank_movers")
    _require(BOT, "self.setup_learner.learning_active", "learning_active gate")
    _require(BOT, "return self.setup_learner.rank(movers)", "setup_learner.rank")
    _require(BOT, "return self.similarity.rank(movers)", "similarity fallback")
    print("PASS: _rank_movers uses setup learner with similarity fallback")


def test_record_on_full_exit_paths() -> None:
    _require(BOT, "def _record_setup_learning", "_record_setup_learning")
    # Normal monitor full exit
    assert BOT.count("_record_setup_learning(") >= 4, (
        "expected _record_setup_learning on monitor, partial-close, force sell, session expiry"
    )
    _require(BOT, "async def force_sell_position", "force_sell_position")
    force_block = BOT.split("async def force_sell_position", 1)[1].split(
        "async def _monitor_all_open_positions", 1
    )[0]
    _require(force_block, "_record_setup_learning(", "force sell learning")
    session_block = BOT.split("async def _close_for_session_expiry", 1)[1].split(
        "async def _stop_for_paper_balance_depletion", 1
    )[0]
    _require(session_block, "_record_setup_learning(", "session expiry learning")
    shutdown_block = BOT.split("async def _shutdown", 1)[1]
    _require(shutdown_block, "_monitor_open_position", "shutdown uses monitor exit path")
    print("PASS: setup learning on all full exit paths")


def test_no_dry_run_skip_on_learning() -> None:
    record_fn = BOT.split("def _record_setup_learning", 1)[1].split(
        "def _record_completed_trade_outcome", 1
    )[0]
    assert "dry_run" not in record_fn, "learning must not skip in paper mode"
    _require(SETUP, "if not Config.SETUP_LEARNING_ENABLED:", "config gate only")
    print("PASS: paper and live share learning path (no dry_run skip)")


def test_persistence_and_bootstrap() -> None:
    _require(SETUP, 'resolve_data_path("data/setup_learning.json")', "store path")
    _require(SETUP, "def save(self)", "save()")
    _require(SETUP, "def _bootstrap_from_journal", "journal bootstrap")
    _require(SETUP, "def _condense(self)", "centroid condensation")
    print("PASS: persistence, bootstrap, and condensation present")


if __name__ == "__main__":
    test_rank_movers_uses_setup_learner()
    test_record_on_full_exit_paths()
    test_no_dry_run_skip_on_learning()
    test_persistence_and_bootstrap()
    print("\nAll learning integration checks passed.")
