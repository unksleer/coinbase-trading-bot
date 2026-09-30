"""
P&L must be shown per market, scoped to the active registration.

The headline card sums crypto and stock ledgers — different venues, different
costs — into one number that describes neither. And the pooled-graduation
panel, the one the operator was told to watch, was attached after a
crypto-only early return and vanished for nine days when the crypto ledgers
were archived. Both are pinned here.
"""
import csv
import glob
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import dashboard  # noqa: E402


def _stock_realized_since(started):
    total = 0.0
    for f in glob.glob(os.path.join(DIR, "completed_trades_stock_*_paper.csv")):
        for r in csv.DictReader(open(f)):
            if r.get("exit_time", "") >= started and r.get("exit_time") != "exit_time":
                total += float(r["pnl_usd"])
    return total


def test_pooled_graduation_is_served_even_with_no_crypto_trades():
    """The regression: it must not hide behind the crypto-only early return."""
    h = dashboard.DashboardHandler.__new__(dashboard.DashboardHandler)
    analytics = h.build_analytics([])          # no trades at all
    assert "pooled_graduation" in analytics
    assert isinstance(analytics["pooled_graduation"], list)


def test_both_markets_reported():
    out = dashboard.DashboardHandler.build_pnl_by_market([])
    assert set(out) == {"stocks", "crypto"}


def test_stock_realized_matches_the_ledger_since_registration_start():
    out = dashboard.DashboardHandler.build_pnl_by_market([])
    st = out["stocks"]
    if not st["since"]:
        pytest.skip("no active stock registration")
    assert st["realized_usd"] == pytest.approx(_stock_realized_since(st["since"]), abs=0.01)


def test_lifetime_is_at_least_the_registration_window():
    out = dashboard.DashboardHandler.build_pnl_by_market([])
    for m in out.values():
        assert m["lifetime_closed"] >= m["closed"]


def test_unrealized_is_summed_per_market_from_priced_positions():
    positions = [{"market": "stocks", "unrealized_pnl": -1.0, "unrealized_known": True},
                 {"market": "stocks", "unrealized_pnl": -2.0, "unrealized_known": True},
                 {"market": "crypto", "unrealized_pnl": 5.0, "unrealized_known": True},
                 {"market": "stocks", "unrealized_pnl": 99.0, "unrealized_known": False}]
    out = dashboard.DashboardHandler.build_pnl_by_market(positions)
    assert out["stocks"]["unrealized_usd"] == pytest.approx(-3.0), "unpriced positions must not count"
    assert out["crypto"]["unrealized_usd"] == pytest.approx(5.0)
    assert out["stocks"]["open"] == 3


def test_total_is_realized_plus_unrealized():
    out = dashboard.DashboardHandler.build_pnl_by_market(
        [{"market": "stocks", "unrealized_pnl": -2.5, "unrealized_known": True}])
    st = out["stocks"]
    assert st["total_usd"] == pytest.approx(st["realized_usd"] + st["unrealized_usd"])


def test_page_carries_the_panel_and_honest_card_labels():
    page = open(os.path.join(DIR, "dashboard", "index.html")).read()
    assert 'id="pnl-market-panel"' in page
    assert "renderMarketPnl(data.pnl_by_market)" in page
    assert "all markets, lifetime" in page, "the mixed card must say it is mixed"
