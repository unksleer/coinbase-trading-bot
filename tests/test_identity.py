"""
The failure this guards against produced no error of any kind.

Eight crypto bots went to TRADING_PAIR=BTC-USDC in one second on 2026-08-27.
Each started cleanly, logged healthy, polled a real market and wrote a state
file — the same one. The only symptom was that per-coin trade files stopped
growing, which on six-hour candles looks exactly like a quiet market.

So these tests care most about the two ways the guard could fail quietly: not
firing when identity is wrong, and firing when it is fine.
"""
import os
import subprocess
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from utils import identity  # noqa: E402


@pytest.mark.parametrize("name,expected", [
    ("ada-bot", "ADA"),
    ("near-bot", "NEAR"),
    ("wmt-bot", "WMT"),
    ("tsla-bot", "TSLA"),
    ("ada_bot", "ADA"),
    ("ADA-BOT", "ADA"),
])
def test_prefix_read_from_conventional_names(name, expected):
    assert identity.expected_prefix(name) == expected


@pytest.mark.parametrize("name", ["worker", "dashboard", "pm2-logrotate",
                                  "learner", "", None, "-bot", "bot"])
def test_unconventional_names_carry_no_instrument(name):
    """
    A process not named "<symbol>-bot" must not have a symbol guessed out of
    it, or every sidecar process becomes a false alarm and the check gets
    turned off.
    """
    assert identity.expected_prefix(name) is None


def test_mismatch_is_reported():
    problem = identity.check("BTC-USDC", "ada-bot")
    assert problem is not None
    assert "ADA" in problem and "BTC-USDC" in problem


def test_the_actual_incident():
    """Every crypto bot pointed at BTC-USDC. Only btc-bot should pass."""
    fleet = ["ada-bot", "btc-bot", "eth-bot", "link-bot", "near-bot",
             "render-bot", "sol-bot", "sui-bot", "xrp-bot"]
    caught = [n for n in fleet if identity.check("BTC-USDC", n) is not None]
    assert caught == [n for n in fleet if n != "btc-bot"]


@pytest.mark.parametrize("symbol,name", [
    ("ADA-USDC", "ada-bot"),
    ("NEAR-USDC", "near-bot"),
    ("WMT", "wmt-bot"),
    ("BTC-USD", "btc-bot"),        # quote currency is not part of identity
])
def test_agreement_is_silent(symbol, name):
    assert identity.check(symbol, name) is None


def test_no_process_name_is_not_a_mismatch():
    """Tests and manual runs have no pm2 name and must not be blocked."""
    assert identity.check("ADA-USDC", "") is None
    assert identity.check("ADA-USDC", None) is None


def test_missing_symbol_is_not_a_mismatch():
    assert identity.check("", "ada-bot") is None


def test_enforce_exits_on_mismatch():
    with pytest.raises(SystemExit) as excinfo:
        identity.enforce("BTC-USDC", process_name="ada-bot")
    assert excinfo.value.code == 1


def test_enforce_returns_on_agreement():
    assert identity.enforce("ADA-USDC", process_name="ada-bot") is None


def test_check_reads_the_pm2_name_from_the_environment(monkeypatch):
    """pm2 sets `name`; that is the whole reason this check can work."""
    monkeypatch.setenv("name", "ada-bot")
    assert identity.check("BTC-USDC") is not None
    assert identity.check("ADA-USDC") is None


def _run(script, env):
    full = dict(os.environ)
    full.pop("name", None)
    full.update(env)
    return subprocess.run([sys.executable, os.path.join(DIR, script)],
                          env=full, cwd=DIR, capture_output=True,
                          text=True, timeout=90)


def test_crypto_bot_refuses_to_start_when_misidentified():
    """
    End to end: the unit test above can pass while the guard sits after the
    bot has already opened its state file. This asserts the process dies.
    """
    result = _run("bot.py", {"name": "ada-bot", "TRADING_PAIR": "BTC-USDC",
                             "DRY_RUN": "True"})
    assert result.returncode == 1
    assert "Identity mismatch" in (result.stdout + result.stderr)


def test_stock_bot_refuses_to_start_when_misidentified():
    result = _run("stock_bot.py", {"name": "wmt-bot", "STOCK_TICKER": "SPY",
                                   "ALPACA_PAPER": "True"})
    assert result.returncode == 1
    assert "Identity mismatch" in (result.stdout + result.stderr)


def test_stock_bot_does_not_touch_the_wrong_state_file(tmp_path):
    """
    The damaging part of the incident was not the wrong ticker, it was nine
    bots writing one book. A misidentified bot must die before it opens the
    state file belonging to the symbol it wrongly believes it is.
    """
    victim = os.path.join(DIR, "state_stock_spy_paper.json")
    before = os.path.getmtime(victim) if os.path.exists(victim) else None
    existed = os.path.exists(victim)

    result = _run("stock_bot.py", {"name": "wmt-bot", "STOCK_TICKER": "SPY",
                                   "ALPACA_PAPER": "True"})
    assert result.returncode == 1

    if existed:
        assert os.path.getmtime(victim) == before, \
            "misidentified bot wrote SPY's state file"
    else:
        assert not os.path.exists(victim), \
            "misidentified bot created SPY's state file"


# ── registry.audit must surface the incident, not just the guard ─────────────
# The bot-level guard only fires at startup. Bots already running when identity
# drifts keep going, so the audit that feeds the dashboard and the daily report
# has to show it too — otherwise the fleet looks like nine bots on nine coins.

INCIDENT = [
    {"name": f"{c}-bot", "market": "crypto", "symbol": "BTC-USDC",
     "status": "online", "script": "bot.py"}
    for c in ("ada", "btc", "eth", "link", "near", "render", "sol", "sui", "xrp")
]

HEALTHY = [
    {"name": f"{c}-bot", "market": "crypto", "symbol": f"{c.upper()}-USDC",
     "status": "online", "script": "bot.py"}
    for c in ("ada", "btc", "eth", "link", "near", "render", "sol", "sui", "xrp")
]


def _audit_with(monkeypatch, fleet, tmp_path):
    from utils import registry
    monkeypatch.setattr(registry, "bots", lambda: fleet)
    env = tmp_path / ".env"
    env.write_text("STOCK_WATCHLIST=\n")
    return registry.audit(env_path=str(env))


def test_audit_catches_the_incident(monkeypatch, tmp_path):
    problems = _audit_with(monkeypatch, INCIDENT, tmp_path)
    text = "\n".join(problems)
    for coin in ("ada", "eth", "link", "near", "render", "sol", "sui", "xrp"):
        assert f"{coin}-bot is trading BTC-USDC" in text
    assert "btc-bot is trading" not in text


def test_audit_reports_the_shared_state_file(monkeypatch, tmp_path):
    """The corruption risk is nine processes writing one book."""
    problems = _audit_with(monkeypatch, INCIDENT, tmp_path)
    assert any("share BTC-USDC and one state file" in p for p in problems)


def test_audit_is_quiet_on_a_healthy_fleet(monkeypatch, tmp_path):
    assert _audit_with(monkeypatch, HEALTHY, tmp_path) == []
