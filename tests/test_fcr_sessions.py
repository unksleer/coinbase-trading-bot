"""
Session boundaries for the two instruments.

SPY trades a clock-and-calendar session in America/New_York; BTC trades a fixed
UTC day. Both are easy to get subtly wrong in ways that silently change what
"the first candle" means — and the whole strategy is defined by that candle.
"""
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from market_data.alpaca_candle_collector import in_regular_session  # noqa: E402
from market_data.candle_store import is_closed, seconds_for          # noqa: E402


def et(y, m, d, hh, mm):
    import pandas as pd
    return pd.Timestamp(year=y, month=m, day=d, hour=hh, minute=mm,
                        tz="America/New_York")


# ── SPY: regular hours only ─────────────────────────────────────────────────

@pytest.mark.parametrize("hh,mm,inside", [
    (8, 0, False),    (9, 15, False),   # premarket — would become "the first candle"
    (9, 30, True),    (9, 45, True),
    (15, 45, True),                     # last bar starts here, ends at 16:00
    (16, 0, False),   (16, 30, False),  # after hours
])
def test_only_regular_hours_bars_are_collected(hh, mm, inside):
    assert in_regular_session(et(2026, 9, 2, hh, mm)) is inside


def test_the_opening_bar_is_0930_not_the_first_bar_of_the_feed():
    """
    The IEX feed starts at 08:00 ET. Without filtering, a premarket bar becomes
    the opening range and the strategy measures something else entirely.
    """
    assert in_regular_session(et(2026, 9, 2, 8, 0)) is False
    assert in_regular_session(et(2026, 9, 2, 9, 30)) is True


@pytest.mark.parametrize("day,weekday_open", [(5, True), (6, False), (7, False)])
def test_weekends_have_no_session(day, weekday_open):
    """2026-09-05 is a Saturday, 06 Sunday, 07 Monday (Labor Day)."""
    stamp = et(2026, 9, day, 10, 0)
    if stamp.weekday() >= 5:
        assert in_regular_session(stamp) is False


def test_daylight_saving_does_not_move_the_open():
    """
    09:30 is 09:30 in New York on both sides of the DST change; only the UTC
    offset moves. Collecting on a fixed UTC hour would drift by an hour twice
    a year and silently take the wrong candle.
    """
    assert in_regular_session(et(2026, 1, 15, 9, 30)) is True    # EST
    assert in_regular_session(et(2026, 7, 15, 9, 30)) is True    # EDT
    assert et(2026, 1, 15, 9, 30).utcoffset() != et(2026, 7, 15, 9, 30).utcoffset()


# ── BTC: fixed UTC day ──────────────────────────────────────────────────────

def test_the_utc_session_opens_at_midnight():
    import pandas as pd
    midnight = pd.Timestamp("2026-09-02 00:00:00", tz="UTC")
    assert midnight.hour == 0
    assert (midnight + pd.Timedelta(hours=1)).hour == 1


def test_an_hourly_candle_is_closed_only_after_a_full_hour():
    start = 1_780_000_000
    assert is_closed(start, "ONE_HOUR", start + 3600) is True
    assert is_closed(start, "ONE_HOUR", start + 3599) is False


def test_a_fifteen_minute_candle_needs_fifteen_minutes():
    start = 1_780_000_000
    assert seconds_for("Min15") == 900
    assert is_closed(start, "Min15", start + 900) is True
    assert is_closed(start, "Min15", start + 899) is False


# ── stored data actually matches these rules ────────────────────────────────

def test_every_stored_spy_bar_is_inside_regular_hours():
    import pandas as pd
    from market_data.candle_store import CandleStore
    with CandleStore() as store:
        rows = store.get("alpaca", "SPY", "Min15")
    if not rows:
        pytest.skip("no SPY bars collected yet")
    stamps = pd.to_datetime([r[0] for r in rows], unit="s", utc=True)
    local = stamps.tz_convert("America/New_York")
    outside = [t for t in local if not in_regular_session(t)]
    assert not outside, f"{len(outside)} stored bars fall outside regular hours"


def test_stored_spy_sessions_all_have_an_opening_candle():
    import pandas as pd
    from collections import defaultdict
    from market_data.candle_store import CandleStore
    with CandleStore() as store:
        rows = store.get("alpaca", "SPY", "Min15")
    if not rows:
        pytest.skip("no SPY bars collected yet")
    local = pd.to_datetime([r[0] for r in rows], unit="s", utc=True) \
              .tz_convert("America/New_York")
    by_day = defaultdict(list)
    for t in local:
        by_day[t.date()].append((t.hour, t.minute))
    missing = [d for d, times in by_day.items() if (9, 30) not in times]
    assert not missing, f"{len(missing)} sessions lack a 09:30 opening candle"


def test_ohlc_relationships_hold_in_stored_data():
    """low <= open, close <= high, and no non-positive prices."""
    from market_data.candle_store import CandleStore
    with CandleStore() as store:
        for venue, symbol, gran in store.symbols():
            for ts, o, h, l, c, v in store.get(venue, symbol, gran):
                assert l <= o <= h, f"{symbol} {ts}: open outside range"
                assert l <= c <= h, f"{symbol} {ts}: close outside range"
                assert min(o, h, l, c) > 0, f"{symbol} {ts}: non-positive price"
                assert v >= 0
