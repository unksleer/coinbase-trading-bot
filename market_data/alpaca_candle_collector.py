#!/usr/bin/env python3
"""
Collect closed 15-minute SPY bars into the local store.

Same contract as the Coinbase collector: writes only, shares no code path with
any bot, and refuses anything still forming.

Two things are specific to equities and both matter for an opening-range rule.

**Regular hours only.** The IEX feed returns bars from 08:00 ET and through
16:30 ET. A premarket bar at 09:15 would silently become "the first candle" and
the whole strategy would be measuring something other than the opening range.
Bars outside 09:30-16:00 ET are dropped at collection, so nothing downstream
has to remember to filter.

**Sessions, not days.** Weekends and market holidays simply produce no bars,
which is the correct representation — there is no session to trade.

    .venv/bin/python -m market_data.alpaca_candle_collector
    .venv/bin/python -m market_data.alpaca_candle_collector --backfill --days 730
    .venv/bin/python -m market_data.alpaca_candle_collector --report
"""
import argparse
import os
import sys
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from market_data.candle_store import CandleStore  # noqa: E402

VENUE = "alpaca"
GRANULARITY = "Min15"
DEFAULT_SYMBOLS = ("SPY",)

# Regular US equity session, America/New_York. The last 15-minute bar of the
# session STARTS at 15:45 and ends at 16:00, so the window is inclusive of
# 09:30 and exclusive of 16:00 as a start time.
SESSION_OPEN = (9, 30)
SESSION_LAST_BAR_START = (15, 45)


def in_regular_session(stamp_et):
    """Whether a bar START time falls inside regular trading hours."""
    if stamp_et.weekday() >= 5:
        return False
    hm = (stamp_et.hour, stamp_et.minute)
    return SESSION_OPEN <= hm <= SESSION_LAST_BAR_START


def collect(symbol, store, days=730):
    from utils import stock_data
    import pandas as pd

    frame = stock_data.fetch_bars(symbol, GRANULARITY, days)
    if frame is None or frame.empty:
        return None

    frame = frame.reset_index()
    stamps = pd.to_datetime(frame["timestamp"], utc=True)
    et = stamps.dt.tz_convert("America/New_York")

    rows, dropped = [], 0
    for i in range(len(frame)):
        if not in_regular_session(et.iloc[i]):
            dropped += 1
            continue
        try:
            rows.append((int(stamps.iloc[i].timestamp()),
                         float(frame["open"].iloc[i]), float(frame["high"].iloc[i]),
                         float(frame["low"].iloc[i]), float(frame["close"].iloc[i]),
                         float(frame["volume"].iloc[i])))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return None
    inserted, skipped, dupes = store.add(VENUE, symbol, GRANULARITY, rows)
    return inserted, skipped, dupes, dropped


def session_of(ts):
    """The ET trading date a bar belongs to. Nights and weekends are not gaps."""
    import pandas as pd
    return str(pd.Timestamp(ts, unit="s", tz="UTC").tz_convert("America/New_York").date())


def sessions(store, symbol):
    """Distinct trading dates held, and how many have a 09:30 opening bar."""
    from datetime import timedelta
    import pandas as pd
    rows = store.get(VENUE, symbol, GRANULARITY)
    if not rows:
        return 0, 0
    stamps = pd.to_datetime([r[0] for r in rows], unit="s", utc=True)
    et = stamps.tz_convert("America/New_York")
    dates = et.date
    opens = sum(1 for t in et if (t.hour, t.minute) == SESSION_OPEN)
    return len(set(dates)), opens


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    from utils import env_loader
    env_loader.load()

    symbols = args.symbol or list(DEFAULT_SYMBOLS)
    with CandleStore() as store:
        if args.report:
            for symbol in symbols:
                held, opens = sessions(store, symbol)
                gaps = store.gaps(VENUE, symbol, GRANULARITY, session_of)
                missing = sum(g[2] for g in gaps)
                print(f"  {symbol}: {store.count(VENUE, symbol, GRANULARITY)} bars, "
                      f"{held} sessions, {opens} with a 09:30 ET open, "
                      f"{len(gaps)} intra-session gap runs ({missing} bars missing)")
                # A whole missing weekday is either a market holiday or a day
                # the collector was down. Telling them apart needs a market
                # calendar this repo does not have, so it is reported as a
                # count rather than guessed at.
                import pandas as pd
                first, last = store.span(VENUE, symbol, GRANULARITY)
                if first and last:
                    et = lambda t: pd.Timestamp(t, unit="s", tz="UTC").tz_convert("America/New_York")
                    weekdays = len(pd.bdate_range(et(first).date(), et(last).date()))
                    print(f"      {weekdays - held} weekdays in span with no session "
                          f"(holidays or missed collection; not distinguishable without a calendar)")
            return 0

        days = args.days if args.backfill else 5
        print(f"  {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  "
              f"{len(symbols)} symbol(s), {days}d window")
        for symbol in symbols:
            result = collect(symbol, store, days)
            if result is None:
                print(f"    {symbol}: no data")
                continue
            i, o, d, outside = result
            held, opens = sessions(store, symbol)
            print(f"    {symbol}: +{i} new, {d} held, {o} still forming, "
                  f"{outside} outside regular hours")
            print(f"      -> {held} sessions, {opens} with a 09:30 ET open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
