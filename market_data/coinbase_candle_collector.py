#!/usr/bin/env python3
"""
Collect closed Coinbase candles into the local store.

Read-only with respect to trading. It shares no code path with the bots —
bot.py fetches its own candles through fetch_candles_public — so this process
cannot influence a live decision. That separation is deliberate and worth
keeping: the moment a bot reads from the store, a stale or gapped store becomes
a trading fault rather than a research inconvenience.

Why a store at all: the exchange caps a response at ~2000 candles, and that cap
flatters results. On the same strategy, six-hour gross edge measured 1.71% over
83 days, 1.48% over 100 and 1.22% over 500, against a 1.206% round trip. The
shorter the history, the better everything looks. Appending each closed candle
accumulates history the API will never serve.

Run hourly, shortly after the hour. Overlap is free — the store is idempotent
and refuses anything still forming — so a missed run repairs itself on the next
one, up to the API's own limit.

    .venv/bin/python -m market_data.coinbase_candle_collector
    .venv/bin/python -m market_data.coinbase_candle_collector --report
    .venv/bin/python -m market_data.coinbase_candle_collector --granularity ONE_HOUR
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from market_data.candle_store import CandleStore, seconds_for  # noqa: E402

VENUE = "coinbase"
# Collected regardless of what is currently trading. History for a pair not yet
# running is exactly what makes adding it later a decision rather than a guess,
# and unlike a live bot it costs nothing but disk.
DEFAULT_GRANULARITIES = ("SIX_HOUR", "ONE_HOUR")


def pairs():
    """Pairs to collect: the running fleet, plus anything already in the store."""
    found = set()
    try:
        from utils import registry
        found.update(registry.crypto_pairs())
    except Exception as e:
        print(f"  ! registry unavailable ({e})")
    try:
        with CandleStore() as store:
            found.update(s for v, s, _ in store.symbols() if v == VENUE)
    except Exception:
        pass
    return sorted(found)


def collect(pair, granularity, store, candles=350):
    """
    Fetch and store. Returns (inserted, skipped_open, duplicates) or None.

    350 rather than the API maximum because this runs hourly and only needs to
    cover the overlap since the last run. Backfill is a separate concern —
    --backfill asks for the full window once.
    """
    import auto_learner as al
    frame = al.fetch_candles(pair, granularity, candles)
    if frame is None or frame.empty:
        return None

    # The epoch lives in a `start` COLUMN; the frame carries a plain
    # RangeIndex. Reading the index instead produced timestamps of 0, 1, 2 —
    # every candle dated 1970 — and the store accepted all 6318 of them,
    # because "is this candle closed" is trivially true for 1970. Hence the
    # plausibility guard in the store: an invariant that only checks the near
    # edge of a range will not notice a value at the far end.
    if "start" not in frame.columns:
        return None
    rows = []
    for row in frame.itertuples():
        try:
            rows.append((int(row.start), float(row.open), float(row.high),
                         float(row.low), float(row.close), float(row.volume)))
        except (AttributeError, TypeError, ValueError):
            continue
    if not rows:
        return None
    return store.add(VENUE, pair, granularity, rows)


def report(store):
    rows = store.integrity_report()
    if not rows:
        print("  store is empty")
        return 0
    print(f"  {'symbol':14}{'granularity':12}{'stored':>8}{'days':>8}"
          f"{'gaps':>7}{'missing':>9}  last candle (UTC)")
    total_missing = 0
    for r in rows:
        last = (datetime.fromtimestamp(r["last"], timezone.utc)
                .strftime("%Y-%m-%d %H:%M") if r["last"] else "-")
        total_missing += r["missing"]
        flag = "  <- gaps" if r["gaps"] else ""
        print(f"  {r['symbol']:14}{r['granularity']:12}{r['stored']:>8}"
              f"{r['days']:>8.1f}{r['gaps']:>7}{r['missing']:>9}  {last}{flag}")
    conflicts = store.conflicts(limit=5)
    if conflicts:
        print(f"\n  ! {len(conflicts)} value conflicts recorded "
              f"(venue returned different values for a stored timestamp)")
        for c in conflicts:
            print(f"    {c['symbol']} {c['granularity']} ts={c['ts']} "
                  f"{c['field']}: stored {c['stored']} offered {c['offered']}")
    print(f"\n  {len(rows)} series, {sum(r['stored'] for r in rows)} candles, "
          f"{total_missing} missing")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--granularity", action="append",
                        help="repeatable; default SIX_HOUR and ONE_HOUR")
    parser.add_argument("--pair", action="append", help="repeatable")
    parser.add_argument("--backfill", action="store_true",
                        help="request the full API window instead of the recent overlap")
    parser.add_argument("--report", action="store_true",
                        help="print an integrity report and exit")
    args = parser.parse_args()

    from utils import env_loader
    env_loader.load()

    with CandleStore() as store:
        if args.report:
            return report(store)

        targets = args.pair or pairs()
        grans = args.granularity or list(DEFAULT_GRANULARITIES)
        if not targets:
            print("  no pairs to collect")
            return 1

        count = 2000 if args.backfill else 350
        started = time.time()
        print(f"  {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  "
              f"{len(targets)} pairs x {len(grans)} granularities"
              f"{'  (backfill)' if args.backfill else ''}")

        added = opened = dup = 0
        for pair in targets:
            for granularity in grans:
                result = collect(pair, granularity, store, count)
                if result is None:
                    print(f"    {pair:14}{granularity:10} no data")
                    continue
                i, o, d = result
                added += i; opened += o; dup += d
                if i:
                    print(f"    {pair:14}{granularity:10} +{i} new")

        print(f"  {added} stored, {dup} already held, {opened} still forming "
              f"(refused), {time.time()-started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
