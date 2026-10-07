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


def _reg(rules=None, name="r"):
    return {"name": name, "started": "2026-09-04", "symbols": [], "market": "stocks",
            "success_criteria": ({"shape_rules": rules} if rules is not None else {})}


RULES = {"min_win_rate_pct": 70.0, "min_profit_factor": 1.0, "min_effective_trades": 20}


def _backtest_shape(n=30):
    """The Sep 21 backtest of the live exits: 72% wins of +2.52%, losses of -5.64%."""
    wins = int(round(n * 0.72))
    return [row(2.52)] * wins + [row(-5.64)] * (n - wins)


def test_the_designed_win_loss_asymmetry_is_not_a_warning(monkeypatch):
    """
    Regression for the misspecified rule. The backtest of this exact
    configuration has win/loss 0.45; the old rule fired on that. The new one
    must read the backtest's own shape as normal.
    """
    monkeypatch.setattr(observer, "effective_factor", lambda m: 1.0)
    out = observer.check_shape_rules(_backtest_shape(), _reg(RULES), "stocks")
    assert all(f["level"] == observer.INFO for f in out), out


def test_win_rate_below_the_sealed_floor_warns(monkeypatch):
    monkeypatch.setattr(observer, "effective_factor", lambda m: 1.0)
    rows = [row(2.52)] * 13 + [row(-5.64)] * 7            # 65%
    out = observer.check_shape_rules(rows, _reg(RULES), "stocks")
    assert any(f["level"] == observer.WARN and "win rate" in f["detail"] for f in out)


def test_profit_factor_below_the_sealed_floor_warns(monkeypatch):
    monkeypatch.setattr(observer, "effective_factor", lambda m: 1.0)
    rows = [row(1.0)] * 15 + [row(-5.0)] * 5              # 75% wins, PF 0.6
    out = observer.check_shape_rules(rows, _reg(RULES), "stocks")
    assert any(f["level"] == observer.WARN and "profit factor" in f["detail"] for f in out)


def test_rules_do_not_fire_before_the_effective_minimum(monkeypatch):
    """5 effective trades cannot breach a rule that needs 20."""
    monkeypatch.setattr(observer, "effective_factor", lambda m: 0.4)
    rows = [row(1.0)] * 9 + [row(-5.0)] * 4               # the live 13: eff = 5
    out = observer.check_shape_rules(rows, _reg(RULES), "stocks")
    assert all(f["level"] == observer.INFO for f in out)
    assert "rules apply at 20" in out[0]["detail"]


def test_a_registration_without_shape_rules_gets_info_only(monkeypatch):
    monkeypatch.setattr(observer, "effective_factor", lambda m: 1.0)
    out = observer.check_shape_rules([row(1.0)] * 5 + [row(-9.0)] * 15, _reg(), "stocks")
    assert all(f["level"] == observer.INFO for f in out)
    assert "no shape rules sealed" in out[0]["detail"]


def test_rules_come_from_the_registration_not_the_code():
    src = open(os.path.join(DIR, "observer.py")).read()
    assert 'reg.get("success_criteria") or {}).get("shape_rules")' in src
    assert "ratio < 1.0" not in src, "the hardcoded win/loss rule must be gone"


def test_progress_projects_when_the_count_will_be_reached(monkeypatch):
    monkeypatch.setattr(observer, "effective_factor", lambda m: 1.0)
    reg = {"name": "r", "market": "stocks", "started": "2026-09-04", "symbols": [],
           "config": {"strategy": "RSI_MEAN_REVERSION"},
           "stopping_rule": "Stop at 20 effective trades."}
    out = observer.check_registrations([reg], {"stocks": [row(1.0)] * 5}, {})
    assert "at this rate 20 effective trades" in out[0]["detail"]


def test_a_date_that_is_only_a_review_point_is_not_a_stop(monkeypatch):
    """v4's rule names a date as a review point; only the count may stop it."""
    monkeypatch.setattr(observer, "effective_factor", lambda m: 1.0)
    reg = {"name": "r", "market": "stocks", "started": "2026-09-04", "symbols": [],
           "config": {"strategy": "RSI_MEAN_REVERSION"},
           "stopping_rule": "Stop at 20 effective trades. 2000-01-01 is a REVIEW point, not a stop."}
    out = observer.check_registrations([reg], {"stocks": [row(1.0)] * 5}, {})
    assert out[0]["level"] == observer.INFO


def test_stopping_rule_parsing():
    """One case per live registration, plus the shape that caused the bug."""
    assert observer.parse_stop("Stop at 2027-01-15 OR 20 effective trades, whichever is later.") == ("2027-01-15", 20, "later")
    assert observer.parse_stop("Stop at 2026-10-31 or 20 logged sessions, whichever is LATER.") == ("2026-10-31", 20, "later")
    # v4: the date is a review point. It must NOT parse as a stop date.
    v4 = "Stop at 20 effective trades. 2026-10-04, and the 4th of each month after, are REVIEW points, not stops."
    assert observer.parse_stop(v4) == (None, 20, "earlier")


def test_the_live_registrations_parse_the_way_they_read():
    """Guards the parser against the actual sealed prose, not just fixtures."""
    from utils import experiment
    for reg in experiment.active():
        date, count, _ = observer.parse_stop(reg["stopping_rule"])
        assert count == 20, reg["name"]
        if "REVIEW point" in reg["stopping_rule"]:
            assert date is None, f"{reg['name']}: a review date parsed as a stop"
        else:
            assert date is not None, f"{reg['name']}: stop date missing"


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


# ── expected hold measured from the run ──────────────────────────────────────

def _trade(days):
    return {"entry_time": "2026-09-01T14:00:00+00:00",
            "exit_time": f"2026-09-{1 + days:02d}T14:00:00+00:00"}


def test_expected_hold_is_assumed_until_ten_closed_trades():
    days, basis = observer.expected_hold_days("RSI_MEAN_REVERSION", [_trade(d) for d in range(1, 10)])
    assert basis == "assumed" and days == observer.EXPECTED_HOLD_DAYS["RSI_MEAN_REVERSION"]


def test_expected_hold_is_the_runs_own_90th_percentile_once_it_can_be():
    rows = [_trade(d) for d in (2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20)]
    days, basis = observer.expected_hold_days("RSI_MEAN_REVERSION", rows)
    assert basis == "measured" and days == 15.0   # p90 of eleven holds, not the max


def test_unparseable_rows_are_skipped_not_fatal():
    rows = [{"entry_time": "garbage", "exit_time": None}] + [_trade(d) for d in range(1, 12)]
    days, basis = observer.expected_hold_days("RSI_MEAN_REVERSION", rows)
    assert basis == "measured"


def test_run_passes_the_trades_into_the_positions_check():
    import ast, inspect
    tree = ast.parse(inspect.getsource(observer.run))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "check_positions"]
    assert calls and calls[0].args, "check_positions() must receive the trades, or it falls back to the assumed constant"
