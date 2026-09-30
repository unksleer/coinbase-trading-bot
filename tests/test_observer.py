"""
The observer must see correctly and must be unable to act.

Its value is checking the registrations' own abort rules every hour instead of
when someone asks. Its safety is structural: no import of anything that
restarts a bot or writes configuration, asserted by parsing this file.
"""
import ast
import json
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import observer  # noqa: E402

SRC = open(os.path.join(DIR, "observer.py")).read()


def row(net_pct, exit_reason="STRATEGY", symbol="ADA-USDC", exit_time="2026-10-01T00:00:00Z"):
    return {"_net_pct": net_pct, "_pnl": net_pct, "_symbol": symbol,
            "exit_reason": exit_reason, "exit_time": exit_time}


# ── cannot act ──────────────────────────────────────────────────────────────

def test_imports_nothing_that_can_change_the_fleet():
    tree = ast.parse(SRC)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    forbidden = {"restart_bots", "auto_learner", "subprocess", "dotenv"}
    assert not (found & forbidden), f"observer imports {found & forbidden}"


@pytest.mark.parametrize("token", ["pm2 restart", "pm2 stop", "pm2 delete", "restart_bot(",
                                   'open(".env"', "state_", "set_key(", "os.environ["])
def test_no_write_path_to_config_or_state(token):
    assert token not in SRC, f"observer contains {token!r}"


def test_only_writes_its_own_two_files():
    assert "observer_findings.json" in SRC and "observer.jsonl" in SRC
    writes = [l for l in SRC.splitlines() if 'open(' in l and '"w"' in l or '"a"' in l]
    for line in writes:
        assert "tmp" in line or "AUDIT_PATH" in line, f"unexpected write: {line.strip()}"


# ── sees correctly ──────────────────────────────────────────────────────────

def test_a_take_profit_exit_on_donchian_is_a_stop():
    out = observer.check_donchian([row(3.0, "Take Profit Triggered")], "r")
    assert any(f["level"] == observer.STOP for f in out)


def test_a_trailing_exit_on_donchian_is_a_stop():
    out = observer.check_donchian([row(1.0, "TRAILING_STOP (peak=...)")], "r")
    assert any(f["level"] == observer.STOP for f in out)


def test_a_stop_loss_exit_on_donchian_is_not():
    out = observer.check_donchian([row(-14.0, "Stop Loss Triggered")], "r")
    assert not any(f["level"] == observer.STOP for f in out)


def test_donchian_average_loss_beyond_ten_percent_warns():
    out = observer.check_donchian([row(-12.0, "Stop Loss"), row(-11.0, "Stop Loss"), row(20.0)], "r")
    assert any(f["level"] == observer.WARN and "10%" in f["title"] for f in out)


def test_stocks_warn_when_average_loss_exceeds_average_win():
    out = observer.check_stocks([row(1.0), row(1.0), row(-3.5)], "r")
    assert any(f["level"] == observer.WARN for f in out)


def test_stocks_do_not_warn_when_wins_dominate():
    out = observer.check_stocks([row(2.7), row(2.6), row(-0.5)], "r")
    assert not any(f["level"] == observer.WARN for f in out)


def test_stopping_rule_parsing():
    assert observer.parse_stop("Stop at 2027-01-15 OR 20 effective trades, whichever is later.") == ("2027-01-15", 20, "later")
    assert observer.parse_stop("Stop at 2026-10-31 or 20 logged sessions, whichever is LATER.") == ("2026-10-31", 20, "later")


def test_later_means_both_conditions_must_be_met(monkeypatch):
    reg = {"name": "r", "market": "stocks", "config": {"strategy": "RSI_MEAN_REVERSION"},
           "stopping_rule": "Stop at 2000-01-01 OR 20 effective trades, whichever is later."}
    monkeypatch.setattr(observer, "effective_factor", lambda m: 1.0)
    few = observer.check_registrations([reg], {"stocks": [row(1.0)] * 5}, {})
    assert few[0]["level"] == observer.INFO, "date passed but count not reached"
    many = observer.check_registrations([reg], {"stocks": [row(1.0)] * 25}, {})
    assert many[0]["level"] == observer.STOP


def test_effective_count_uses_the_correlation_discount(monkeypatch):
    reg = {"name": "r", "market": "stocks", "config": {"strategy": "RSI_MEAN_REVERSION"},
           "stopping_rule": "Stop at 2000-01-01 OR 20 effective trades, whichever is later."}
    monkeypatch.setattr(observer, "effective_factor", lambda m: 0.4)   # 4.9 of 12
    out = observer.check_registrations([reg], {"stocks": [row(1.0)] * 25}, {})
    assert out[0]["level"] == observer.INFO, "25 pooled is only 10 effective"
    assert "10 effective" in out[0]["detail"]


def test_fcr_drift_is_only_a_warning_before_twenty_sessions():
    sessions = {f"s{i}": {} for i in range(12)}
    reasons = observer.Counter({"rsi_outside_band": 12})     # 100% vs 4% baseline
    out = observer.check_fcr(sessions, reasons, "r")
    assert out[0]["level"] == observer.WARN


def test_fcr_drift_is_a_stop_at_twenty_sessions():
    sessions = {f"s{i}": {} for i in range(20)}
    reasons = observer.Counter({"rsi_outside_band": 20})
    out = observer.check_fcr(sessions, reasons, "r")
    assert out[0]["level"] == observer.STOP


def test_fcr_within_baseline_is_info():
    sessions = {f"s{i}": {} for i in range(50)}
    reasons = observer.Counter({"volume_below_threshold": 30, "no_breakout_close_above_range_high": 15,
                                "price_below_ema200": 2, "rsi_outside_band": 2, "accepted": 1})
    out = observer.check_fcr(sessions, reasons, "r")
    assert out[0]["level"] == observer.INFO


def test_stop_emails_are_deduplicated(tmp_path, monkeypatch):
    audit = tmp_path / "observer.jsonl"
    audit.write_text(json.dumps({"t": observer.time.time(), "emailed": ["r|x"]}) + "\n")
    monkeypatch.setattr(observer, "AUDIT_PATH", str(audit))
    assert observer.recently_emailed("r|x") is True
    assert observer.recently_emailed("r|y") is False


def test_every_run_writes_an_audit_row_even_with_nothing_to_report():
    assert 'with open(AUDIT_PATH, "a")' in SRC
    assert '"n_findings": len(findings)' in SRC


def test_dashboard_publishes_findings_with_age():
    src = open(os.path.join(DIR, "dashboard.py")).read()
    assert '"observer": DashboardHandler._observer_findings()' in src
    assert 'payload["age_seconds"]' in src
    page = open(os.path.join(DIR, "dashboard", "index.html")).read()
    assert 'id="observer-panel"' in page and "renderObserver(data.observer)" in page
    assert "OBSERVE ONLY" in page



# ── scoping ─────────────────────────────────────────────────────────────────

def test_trades_before_the_registration_started_do_not_count():
    """
    The first dry run graded the v3 stock registration on 20 trades when only
    13 had happened since it began; the rest belonged to the voided v1 and v2.
    """
    reg = {"market": "stocks", "started": "2026-09-04", "symbols": ["SPY", "WMT"]}
    all_trades = {"stocks": [
        row(2.0, symbol="SPY", exit_time="2026-09-01T00:00:00Z"),   # v2 era
        row(2.0, symbol="WMT", exit_time="2026-09-10T00:00:00Z"),
        row(-5.0, symbol="SPY", exit_time="2026-09-20T00:00:00Z"),
    ]}
    mine = observer.trades_for(reg, all_trades)
    assert [r["_symbol"] for r in mine] == ["WMT", "SPY"]


def test_symbols_outside_the_registration_do_not_count():
    reg = {"market": "stocks", "started": "2026-09-04", "symbols": ["SPY"]}
    all_trades = {"stocks": [row(1.0, symbol="SPY", exit_time="2026-09-10T00:00:00Z"),
                             row(1.0, symbol="TSLA", exit_time="2026-09-10T00:00:00Z")]}
    assert len(observer.trades_for(reg, all_trades)) == 1


def test_an_empty_symbol_list_means_the_whole_market():
    reg = {"market": "crypto", "started": "2026-09-21", "symbols": []}
    all_trades = {"crypto": [row(1.0, symbol="ADA-USDC", exit_time="2026-09-25T00:00:00Z")]}
    assert len(observer.trades_for(reg, all_trades)) == 1
