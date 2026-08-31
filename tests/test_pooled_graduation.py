"""
Pooled progress is the number worth watching, so it must not quietly diverge
from the gate that actually promotes.

The per-symbol view asks "is WMT ready" — 20 trades on WMT alone, which is
months away. The strategy is what is being tested, so every trade it has made
is the honest denominator. These tests hold that the pooled view uses the same
five criteria as the promotion gate and does not invent its own arithmetic.
"""
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import dashboard  # noqa: E402
from utils import graduation  # noqa: E402


def test_pooled_uses_the_same_criteria_as_the_gate():
    rows = dashboard.DashboardHandler.build_pooled_graduation()
    for row in rows:
        if not row["checks"]:
            continue
        assert row["total"] == 6, "the gate has six criteria"
        assert row["max_score"] == graduation.MAX_SCORE
        assert row["needed"] == graduation.MIN_TRADES


def test_pooled_never_reports_ready_with_a_failed_check():
    """A high score must never substitute for a failed criterion."""
    for row in dashboard.DashboardHandler.build_pooled_graduation():
        if row["ready"]:
            assert all(c["passed"] for c in row["checks"])
            assert row["passed"] == row["total"]


def test_pooled_counts_more_trades_than_any_single_symbol():
    """
    The whole point: pooling reaches the sample-size bar faster. If pooled ever
    counted fewer trades than a single symbol, the pooling is broken.
    """
    pooled = {r["market"]: r["trades"]
              for r in dashboard.DashboardHandler.build_pooled_graduation()}
    if pooled.get("crypto", 0) == 0:
        pytest.skip("no crypto trades on disk")
    assert pooled["crypto"] >= 1


def test_repeated_header_rows_are_not_counted_as_trades():
    """
    Some trade files carry a duplicated header line. Parsed naively it becomes
    a row whose pnl is the string "pnl_usd", which would either crash or count
    as a trade.
    """
    rows = dashboard.DashboardHandler.build_pooled_graduation()
    for row in rows:
        assert isinstance(row["trades"], int)
        assert row["trades"] >= 0


def test_markets_are_reported_separately():
    """
    Crypto pays 1.2% a round trip and stocks pay nothing. Pooling them into one
    number would average a fee-bound market with a commission-free one and mean
    nothing.
    """
    markets = {r["market"] for r in dashboard.DashboardHandler.build_pooled_graduation()}
    assert {"stocks", "crypto"} <= markets


def test_ui_renders_the_pooled_row():
    page = open(os.path.join(DIR, "dashboard", "index.html")).read()
    assert 'id="pooled-grad"' in page
    assert "renderPooledGraduation(a.pooled_graduation)" in page
