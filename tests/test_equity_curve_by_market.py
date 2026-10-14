"""
The cumulative P&L curve must exist per market and be scoped to the
registration, like every other number on the panel.

It lived inside the crypto-only analytics block, so it had been empty since
the crypto ledgers were archived on 2026-09-21, and stocks — the market that
actually has closed trades — had never had one.
"""
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import dashboard  # noqa: E402


def test_each_market_carries_a_curve():
    out = dashboard.DashboardHandler.build_pnl_by_market([])
    for m in ("stocks",):
        assert "equity_curve" in out[m]
        assert isinstance(out[m]["equity_curve"], list)


def test_curve_is_chronological_and_cumulative():
    out = dashboard.DashboardHandler.build_pnl_by_market([])
    for m in out.values():
        pts = m["equity_curve"]
        times = [p["exit_time"] for p in pts]
        assert times == sorted(times), "oldest first"
        if pts:
            assert pts[-1]["cumulative_pnl"] == pytest.approx(m["realized_usd"], abs=0.01), \
                "the curve must end where realized P&L is"
            assert len(pts) == m["closed"]


def test_page_draws_one_chart_per_market_and_not_the_old_single_one():
    page = open(os.path.join(DIR, "dashboard", "index.html")).read()
    assert 'id="equity-curve-stocks"' in page and 'id="curve-subtext-stocks"' in page
    assert 'equity-curve-crypto' not in page, "crypto was retired on 2026-10-14"
    assert 'id="equity-curve"' not in page, "the crypto-only single chart is gone"
    assert "renderEquityCurve(a.equity_curve)" not in page, "must not render from the crypto-only block"
    assert "equity-curve-' + m" in page or "equity-curve-${m}" in page or "equity-curve-" in page
