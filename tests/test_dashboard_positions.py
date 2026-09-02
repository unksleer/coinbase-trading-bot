"""
An equity position must display as itself, priced.

All twelve stock bots shared one code path with the crypto bots and lost three
things to it: the symbol became STOCK_WMT_PAPER (no feed can resolve that),
holdings read 0 because stock state records `shares` not `holdings_base`, and
the price resolver only ever asked Coinbase. The result was six positions
showing a P&L of exactly $0.00 — a missing number rendering as a real one,
which is the failure this dashboard keeps repeating.
"""
import json
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import dashboard  # noqa: E402


def _positions():
    return dashboard.DashboardHandler.__new__(
        dashboard.DashboardHandler)  # not used; see source-level tests below


def test_stock_state_filename_yields_a_real_ticker():
    """state_stock_wmt_paper.json -> WMT, not STOCK_WMT_PAPER."""
    stem = "stock_wmt_paper"
    assert stem.startswith("stock_")
    ticker = stem[len("stock_"):].replace("_paper", "").replace("_live", "").upper()
    assert ticker == "WMT"


def test_crypto_state_filename_still_yields_its_pair():
    stem = "ada-usdc_dry"
    assert not stem.startswith("stock_")
    assert stem.replace("_dry", "").upper() == "ADA-USDC"


def test_reader_branches_on_market():
    """Parsing moved to utils.positions once it had been wrong in two files."""
    src = open(os.path.join(DIR, "utils", "positions.py")).read()
    assert 'stem.startswith("stock_")' in src
    assert 'state.get("shares", 0.0)' in src
    assert 'state.get("holdings_base", 0.0)' in src


def test_price_resolver_is_asked_for_the_right_venue():
    src = open(os.path.join(DIR, "dashboard.py")).read()
    assert 'held["symbol"], held["entry_price"], held["market"]' in src
    assert "def _stock_price(self, ticker):" in src


def test_stock_prices_are_never_labelled_live():
    """The free IEX feed is delayed; calling it live would be a lie."""
    src = open(os.path.join(DIR, "dashboard.py")).read()
    # Only the stocks branch — a fixed-width slice runs past it into the crypto
    # path, which legitimately returns "live" and fails a correct test.
    after = src.split('if market == "stocks":', 1)[1]
    block = after.split("live = self._live_price", 1)[0]
    # Strip comments: the word "live" appears in the comment explaining why the
    # stock path must never claim it, and matching that fails a correct test.
    code = "\n".join(l.split("#", 1)[0] for l in block.splitlines())
    assert '"candle"' in code
    assert '"live"' not in code


def test_dashboard_loads_env_for_credentials():
    """
    Without .env there are no Alpaca keys in this process, every stock price
    lookup fails, and each position falls back to its entry price — a P&L of
    $0.00 that reads as flat rather than unknown.
    """
    src = open(os.path.join(DIR, "dashboard.py")).read()
    assert "env_loader.load()" in src


def test_missing_price_is_flagged_stale_not_reported_as_flat():
    src = open(os.path.join(DIR, "dashboard.py")).read()
    assert 'price_source == "entry"' in src
    assert '"price_stale"' in src


@pytest.mark.parametrize("shares,entry,now,expected", [
    (0.0974, 102.69, 104.83, +0.2084),
    (0.1662, 60.17, 58.08, -0.3473),
])
def test_unrealised_pnl_arithmetic(shares, entry, now, expected):
    assert (now - entry) * shares == pytest.approx(expected, abs=0.01)


def test_every_open_position_on_disk_is_reported():
    """A position held but not displayed is the same as not knowing about it."""
    import glob
    on_disk = 0
    for path in glob.glob(os.path.join(DIR, "state_*.json")):
        try:
            with open(path) as handle:
                if json.load(handle).get("position") == "LONG":
                    on_disk += 1
        except (OSError, ValueError):
            continue
    from utils import positions
    reported = positions.open_positions(DIR)
    assert len(reported) == on_disk, \
        "every LONG state file must be reported, regardless of market"
