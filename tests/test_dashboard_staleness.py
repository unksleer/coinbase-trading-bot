"""
A frozen panel must look frozen.

The page polled every 30 s and, on any failed fetch, returned silently. The
table kept its last render and its "Updated:" stamp — set only on success —
froze with it. Browsers throttle timers in background tabs. SOL had stopped
out and a tab left open still said IN TRADE under a timestamp nobody reads.
"""
import os
import re

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = open(os.path.join(DIR, "dashboard", "index.html")).read()


def _fn(name):
    start = PAGE.index(f"async function {name}(")
    return PAGE[start:start + 900]


def test_a_non_ok_signals_response_is_not_a_silent_return():
    body = _fn("fetchBotSignals")
    assert "if (!res.ok) return;" not in body
    assert "throw new Error" in body


def test_both_panels_report_success_and_failure():
    for name, panel in (("fetchMetrics", "metrics"), ("fetchBotSignals", "signals")):
        body = _fn(name)
        assert f"markOk('{panel}')" in body, f"{name} must mark success"
        assert f"markFail('{panel}'" in body, f"{name} must mark failure"


def test_a_failed_fetch_shows_when_the_data_is_from():
    body = PAGE[PAGE.index("function markFail("):][:700]
    assert "UPDATE FAILED" in body
    assert "showing data from" in body
    assert "stamp-stale" in body


def test_a_watchdog_flags_missed_polls():
    assert "setInterval(checkStale, 5000)" in PAGE
    body = PAGE[PAGE.index("function checkStale("):][:800]
    assert "3 * POLL_MS[panel]" in body, "threshold is three missed polls"
    assert "STALE — last update" in body


def test_watchdog_threshold_is_a_multiple_of_the_real_poll_intervals():
    """The thresholds must track the timers, or a change to one breaks the other."""
    assert "setInterval(fetchMetrics, 10000)" in PAGE and "metrics: 10000" in PAGE
    assert "setInterval(fetchBotSignals, 30000)" in PAGE and "signals: 30000" in PAGE


def test_a_tab_regaining_focus_refetches_immediately():
    body = PAGE[PAGE.index("visibilitychange"):][:300]
    assert "fetchMetrics()" in body and "fetchBotSignals()" in body


def test_stale_style_is_defined_and_visible():
    assert re.search(r"\.stamp-stale\s*\{[^}]*var\(--accent\)", PAGE)


def test_a_recovered_fetch_clears_the_warning():
    body = PAGE[PAGE.index("function markOk("):][:400]
    assert "classList.remove('stamp-stale')" in body
    assert "lastFail[panel] = null" in body
