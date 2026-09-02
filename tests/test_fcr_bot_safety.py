"""
The experimental bot must be incapable of trading real money.

Not "gated from" — incapable. This file asserts there is no order-submission
code path in fcr_bot.py at all, which is a stronger guarantee than a flag
protecting one, because a flag can be flipped and a path that does not exist
cannot be.

It also asserts the experiment cannot disturb production: separate state,
separate trade file, separate signal log, and no import in either direction.
"""
import ast
import os
import subprocess
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

SOURCE = open(os.path.join(DIR, "fcr_bot.py")).read()


def _run(env_extra, timeout=40):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run([sys.executable, os.path.join(DIR, "fcr_bot.py")],
                          env=env, cwd=DIR, capture_output=True,
                          text=True, timeout=timeout)


# ── incapable of live trading ───────────────────────────────────────────────

@pytest.mark.parametrize("value", ["", "False", "false", "no", "0", "TRUE_ish", "off"])
def test_refuses_to_start_unless_paper_is_explicitly_true(value):
    """Absent and malformed must fail closed, not only an explicit false."""
    result = _run({"ALPACA_PAPER": value})
    assert result.returncode != 0
    assert "REFUSING TO START" in (result.stdout + result.stderr)


def test_an_explicit_live_override_is_refused():
    result = _run({"ALPACA_PAPER": "True", "FCR_ALLOW_LIVE": "1"})
    assert result.returncode != 0
    assert "no live path" in (result.stdout + result.stderr)


@pytest.mark.parametrize("forbidden", [
    "submit_order", "MarketOrderRequest", "LimitOrderRequest",
    "limit_order_gtc_buy", "market_order_sell", "submit_buy", "submit_sell",
])
def test_no_order_submission_code_exists(forbidden):
    """
    The guarantee is the absence of the path, not a flag in front of it.
    """
    assert forbidden not in SOURCE, f"fcr_bot.py contains {forbidden}"


def test_paper_is_hardcoded_where_a_client_is_constructed():
    """The only client built is for reading the market clock."""
    assert "paper=True" in SOURCE
    assert "paper=False" not in SOURCE


# ── isolation from production ───────────────────────────────────────────────

def _imports(path):
    with open(path) as handle:
        tree = ast.parse(handle.read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_experiment_does_not_import_the_production_bots():
    found = _imports(os.path.join(DIR, "fcr_bot.py"))
    assert not {m for m in found if m in ("bot", "stock_bot")}


def test_the_production_bots_do_not_import_the_experiment():
    for name in ("bot.py", "stock_bot.py"):
        found = _imports(os.path.join(DIR, name))
        assert not any("fcr" in m or "first_candle" in m for m in found), name


def test_state_and_trade_files_are_separate():
    """
    Writing to a production path would corrupt a pre-registered experiment.
    """
    assert 'state_fcr_' in SOURCE
    assert '_fcr_paper.csv' in SOURCE
    assert 'fcr_signals_' in SOURCE
    for production in ("state_stock_", "completed_trades_stock_"):
        assert production not in SOURCE, f"writes a production path: {production}"


# ── kill switch ─────────────────────────────────────────────────────────────

def test_the_kill_switch_blocks_entries_but_not_exits():
    """
    A kill switch that also blocks exits would trap an open position, which is
    worse than the risk it removes.
    """
    entry_block = SOURCE.split("elif entries_blocked():", 1)
    assert len(entry_block) == 2, "kill switch must gate the ENTRY branch"
    # The exit check must sit before it in the same chain.
    assert SOURCE.index('if state.get("position") == "LONG":\n                check_exit') \
        < SOURCE.index("elif entries_blocked():")


def test_kill_switch_reads_as_off_unless_explicitly_on():
    from importlib import reload
    for value, expected in (("", False), ("true", True), ("1", True),
                            ("no", False), ("maybe", False)):
        os.environ["FCR_KILL_SWITCH"] = value
        os.environ.setdefault("ALPACA_PAPER", "True")
        import fcr_bot
        reload(fcr_bot)
        assert fcr_bot.entries_blocked() is expected, value
    os.environ.pop("FCR_KILL_SWITCH", None)


# ── one entry per session ───────────────────────────────────────────────────

def test_a_session_already_attempted_is_not_retried():
    assert 'entered_sessions' in SOURCE
    assert 'session in state.get("entered_sessions", [])' in SOURCE


def test_no_averaging_down_or_pyramiding():
    """Entry is only considered when flat — there is no add-to-position path."""
    assert 'elif session in state.get("entered_sessions"' in SOURCE
    assert "consider_entry" in SOURCE
    # consider_entry is reachable only from the else of a LONG check.
    before = SOURCE.split("consider_entry(state,", 1)[0]
    assert 'if state.get("position") == "LONG":' in before


# ── restart recovery ────────────────────────────────────────────────────────

def test_state_is_written_atomically():
    """A crash mid-write must not leave a half-written position."""
    assert "os.replace(tmp, STATE_PATH)" in SOURCE


def test_a_missing_or_corrupt_state_file_starts_flat():
    from importlib import reload
    os.environ.setdefault("ALPACA_PAPER", "True")
    import fcr_bot
    reload(fcr_bot)
    original = fcr_bot.STATE_PATH
    try:
        fcr_bot.STATE_PATH = os.path.join(DIR, "does_not_exist_fcr.json")
        assert fcr_bot.load_state()["position"] == "NONE"
    finally:
        fcr_bot.STATE_PATH = original
