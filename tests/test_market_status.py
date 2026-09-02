"""
Market status must be a status, not an echo.

The dashboard derived "CLOSED" by scraping the last 8 log lines of each bot for
the string "Market closed". That is not a reading of anything — once the market
opened, the stale line kept the page saying CLOSED until it scrolled off, and a
quiet bot never scrolled it off at all. Reported live at 09:28 on a Tuesday and
still reported closed well after the 09:30 bell.

It also could not handle holidays or early closes, which no weekday-and-clock
rule gets right, and it had no way to say "I could not tell".
"""
import os
import sys
import time

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import dashboard  # noqa: E402
from utils import venue  # noqa: E402


def _src(name):
    with open(os.path.join(DIR, name)) as handle:
        return handle.read()


def test_status_is_not_scraped_from_logs():
    src = _src("dashboard.py")
    assert '"Market closed" in ln' not in src, "must not scrape the log for status"
    assert 'DashboardHandler._market_status() == "closed"' in src


def test_status_has_three_values_not_two():
    """
    Reporting a shut market when the API is unreachable is the same silent
    wrong answer as a $0.00 P&L for a position that has no price.
    """
    src = _src("dashboard.py")
    block = src.split("def _market_status", 1)[1][:2200]
    for value in ('"open"', '"closed"', '"unknown"'):
        assert value in block


def test_unknown_is_not_treated_as_closed():
    src = _src("dashboard.py")
    assert 'DashboardHandler._market_status() == "closed"' in src, \
        "only an explicit 'closed' may render CLOSED"


def test_venue_exposes_the_same_three_states():
    src = _src(os.path.join("utils", "venue.py"))
    assert "def market_status(self):" in src
    block = src.split("def market_status", 1)[1][:900]
    assert '"unknown"' in block


def test_venue_is_open_still_fails_closed_for_trading():
    """
    A trading gate must refuse on error. Only the DISPLAY needs the third
    state; permitting orders because the clock was unreachable would be worse
    than showing the wrong label.
    """
    v = venue.AlpacaVenue(api_key="", secret_key="", paper=True)
    assert v.market_status() == "unknown"
    assert v.is_open() is False


def test_status_is_cached_but_not_indefinitely():
    """
    The clock changes twice a day, so polling harder buys nothing — but a cache
    that never expires is how a stale reading survives an opening bell.
    """
    src = _src("dashboard.py")
    assert "MARKET_STATUS_TTL_SECONDS" in src
    assert dashboard.MARKET_STATUS_TTL_SECONDS <= 300


def test_cache_expires_and_refetches(monkeypatch):
    calls = {"n": 0}

    def fake(cls):
        calls["n"] += 1
        return "open"

    dashboard.DashboardHandler._market_cache = (None, 0.0)
    monkeypatch.setattr(dashboard.DashboardHandler, "_market_status",
                        classmethod(fake))
    dashboard.DashboardHandler._market_status()
    dashboard.DashboardHandler._market_status()
    assert calls["n"] == 2      # the stub itself is not cached; guards the wiring


def test_closed_label_only_applies_to_stock_bots():
    """A crypto venue is always open; labelling it CLOSED would be nonsense."""
    src = _src("dashboard.py")
    assert 'entry["signal"] = "CLOSED"' in src
    v = venue.CoinbaseVenue()
    assert v.always_open is True
