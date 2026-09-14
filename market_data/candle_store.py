"""
Append-only local store of closed candles.

Every analysis in this project re-fetches from the exchange, and the exchange
caps a response at ~2000 candles. That cap is not neutral: measured on the same
strategy, six-hour gross edge read 1.71% over 83 days, 1.48% over 100 and 1.22%
over 500, against a 1.206% round trip. The shorter the history, the better
everything looks. A store that appends each closed candle accumulates history
the API will not serve, and the benefit grows without further work.

Three invariants, each guarding a way this kind of store quietly corrupts the
research built on it.

**Only closed candles.** A candle is closed when start + granularity <= now.
Storing one still forming records a partial high, low and close that are later
revised, so a backtest reads prices no trader could have acted on — and it
errs optimistically every time, because the strategy effectively sees a few
minutes into the candle it is deciding on. This is the single most dangerous
defect available here, so it is enforced on write rather than checked later.

**No duplicates.** A primary key on (venue, symbol, granularity, ts). Re-runs
and overlapping backfills are therefore safe and idempotent.

**No silent revision.** A candle already stored is never overwritten. If the
venue later returns different values for the same timestamp, that is a fact
worth surfacing, not absorbing — `conflicts()` reports them.

Gaps are not repaired automatically. `gaps()` reports them and leaves the
decision alone, because a gap caused by an outage and one caused by a venue
halt need different responses.
"""
import os
import sqlite3
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(DIR, "data", "market_data.sqlite")

# Seconds per candle. Mirrors auto_learner.GRANULARITY_MAP; kept here so the
# store has no import dependency on the learner.
GRANULARITY_SECONDS = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 300,
    "FIFTEEN_MINUTE": 900,
    "ONE_HOUR": 3600,
    "SIX_HOUR": 21600,
    "ONE_DAY": 86400,
    # Equities, named as the venue names them.
    "Min15": 900,
    "Hour": 3600,
    "Day": 86400,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    venue       TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    granularity TEXT    NOT NULL,
    ts          INTEGER NOT NULL,      -- candle START, epoch seconds, UTC
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    fetched_at  INTEGER NOT NULL,
    PRIMARY KEY (venue, symbol, granularity, ts)
);
CREATE INDEX IF NOT EXISTS candles_lookup
    ON candles (venue, symbol, granularity, ts);

-- A venue returning different values for a timestamp already stored is a data
-- integrity event. Recorded rather than applied.
CREATE TABLE IF NOT EXISTS conflicts (
    venue       TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    granularity TEXT    NOT NULL,
    ts          INTEGER NOT NULL,
    field       TEXT    NOT NULL,
    stored      REAL    NOT NULL,
    offered     REAL    NOT NULL,
    seen_at     INTEGER NOT NULL
);
"""

PRICE_FIELDS = ("open", "high", "low", "close", "volume")


class UnfinishedCandleError(ValueError):
    """Raised when a caller tries to store a candle that has not closed."""


class ImplausibleTimestampError(ValueError):
    """Raised for a timestamp outside the range any real candle can occupy."""


# Bitcoin's genesis block, and a generous ceiling. A collector that reads the
# wrong field produces small integers — 0, 1, 2 — and every one of them passes
# a "has this candle closed" test trivially, so the closed check alone lets a
# whole dataset of 1970 candles through. That is exactly what happened on the
# first run.
EARLIEST_PLAUSIBLE_TS = 1231006505      # 2009-01-03
FUTURE_TOLERANCE_SECONDS = 86400


def seconds_for(granularity):
    try:
        return GRANULARITY_SECONDS[granularity]
    except KeyError:
        raise ValueError(
            f"Unknown granularity {granularity!r}. "
            f"Known: {sorted(GRANULARITY_SECONDS)}") from None


def is_closed(ts, granularity, now=None):
    """
    Whether the candle starting at ts has finished.

    Strict: a candle whose end is exactly now is closed, one ending a second
    later is not.
    """
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    return int(ts) + seconds_for(granularity) <= now


class CandleStore:
    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── writing ─────────────────────────────────────────────────────────────

    def add(self, venue, symbol, granularity, rows, now=None):
        """
        Store closed candles. Returns (inserted, skipped_open, duplicates).

        rows is an iterable of (ts, open, high, low, close, volume).

        Refuses any candle that has not closed. That refusal is the point of
        the method: a partial candle looks exactly like a real one a few
        minutes later, and nothing downstream can tell them apart.
        """
        seconds_for(granularity)          # validate before touching the db
        now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
        fetched = now

        inserted = skipped_open = duplicates = 0
        for row in rows:
            ts, o, h, l, c, v = (int(row[0]), float(row[1]), float(row[2]),
                                 float(row[3]), float(row[4]), float(row[5]))
            if ts < EARLIEST_PLAUSIBLE_TS or ts > now + FUTURE_TOLERANCE_SECONDS:
                raise ImplausibleTimestampError(
                    f"{venue} {symbol} {granularity}: timestamp {ts} is outside "
                    f"any plausible range. A collector reading the wrong field "
                    f"produces values like this, and they pass the closed check.")
            if not is_closed(ts, granularity, now):
                skipped_open += 1
                continue

            existing = self._conn.execute(
                "SELECT open, high, low, close, volume FROM candles "
                "WHERE venue=? AND symbol=? AND granularity=? AND ts=?",
                (venue, symbol, granularity, ts)).fetchone()

            if existing is not None:
                duplicates += 1
                offered = dict(zip(PRICE_FIELDS, (o, h, l, c, v)))
                for field in PRICE_FIELDS:
                    if abs(existing[field] - offered[field]) > 1e-9:
                        self._conn.execute(
                            "INSERT INTO conflicts (venue, symbol, granularity, "
                            "ts, field, stored, offered, seen_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (venue, symbol, granularity, ts, field,
                             existing[field], offered[field], now))
                continue

            self._conn.execute(
                "INSERT INTO candles (venue, symbol, granularity, ts, open, "
                "high, low, close, volume, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (venue, symbol, granularity, ts, o, h, l, c, v, fetched))
            inserted += 1

        self._conn.commit()
        return inserted, skipped_open, duplicates

    # ── reading ─────────────────────────────────────────────────────────────

    def get(self, venue, symbol, granularity, limit=None, since=None):
        """Closed candles, oldest first."""
        sql = ("SELECT ts, open, high, low, close, volume FROM candles "
               "WHERE venue=? AND symbol=? AND granularity=?")
        args = [venue, symbol, granularity]
        if since is not None:
            sql += " AND ts >= ?"
            args.append(int(since))
        sql += " ORDER BY ts ASC"
        rows = self._conn.execute(sql, args).fetchall()
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]          # most recent, still oldest-first
        return [tuple(r) for r in rows]

    def closes(self, venue, symbol, granularity, limit=None):
        return [r[4] for r in self.get(venue, symbol, granularity, limit)]

    def count(self, venue=None, symbol=None, granularity=None):
        sql = "SELECT COUNT(*) FROM candles WHERE 1=1"
        args = []
        for column, value in (("venue", venue), ("symbol", symbol),
                              ("granularity", granularity)):
            if value is not None:
                sql += f" AND {column}=?"
                args.append(value)
        return self._conn.execute(sql, args).fetchone()[0]

    def span(self, venue, symbol, granularity):
        """(first_ts, last_ts) or (None, None) when empty."""
        row = self._conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM candles "
            "WHERE venue=? AND symbol=? AND granularity=?",
            (venue, symbol, granularity)).fetchone()
        return (row[0], row[1])

    def symbols(self):
        return [(r[0], r[1], r[2]) for r in self._conn.execute(
            "SELECT DISTINCT venue, symbol, granularity FROM candles "
            "ORDER BY venue, symbol, granularity")]

    # ── integrity ───────────────────────────────────────────────────────────

    def gaps(self, venue, symbol, granularity, session_of=None):
        """
        Missing intervals as (gap_start_ts, gap_end_ts, missing_count).

        Reported, not repaired: a gap from a collector outage and one from a
        venue halt want different responses, and guessing between them is how
        invented data enters a dataset.

        session_of(ts) -> a hashable session key. When given, only consecutive
        candles in the SAME session are compared. A 24/7 venue needs nothing;
        for equities a session is the trading day, and the interval between
        15:45 and the next 09:30 is the market being closed, not a gap. Without
        this the SPY report counted 502 gap runs across 501 sessions — one per
        night — which made a clean dataset look broken and would have hidden
        a real gap inside the noise.
        """
        step = seconds_for(granularity)
        rows = [r[0] for r in self._conn.execute(
            "SELECT ts FROM candles WHERE venue=? AND symbol=? AND granularity=? "
            "ORDER BY ts ASC", (venue, symbol, granularity))]
        out = []
        for previous, current in zip(rows, rows[1:]):
            if session_of is not None and session_of(previous) != session_of(current):
                continue
            missing = (current - previous) // step - 1
            if missing > 0:
                out.append((previous + step, current - step, int(missing)))
        return out

    def conflicts(self, limit=50):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM conflicts ORDER BY seen_at DESC LIMIT ?", (limit,))]

    def integrity_report(self, session_of_by_venue=None):
        """
        One dict per series: counts, span, gaps. For a health check.

        session_of_by_venue maps a venue name to a session_of callable; see
        gaps(). For such venues `expected` is summed within sessions rather
        than across the whole span, for the same reason.
        """
        session_of_by_venue = session_of_by_venue or {}
        out = []
        for venue, symbol, granularity in self.symbols():
            first, last = self.span(venue, symbol, granularity)
            session_of = session_of_by_venue.get(venue)
            found = self.gaps(venue, symbol, granularity, session_of)
            step = seconds_for(granularity)
            if first is None:
                expected = 0
            elif session_of is None:
                expected = (last - first) // step + 1
            else:
                stamps = [r[0] for r in self._conn.execute(
                    "SELECT ts FROM candles WHERE venue=? AND symbol=? AND granularity=? "
                    "ORDER BY ts ASC", (venue, symbol, granularity))]
                bounds = {}
                for ts in stamps:
                    key = session_of(ts)
                    lo, hi = bounds.get(key, (ts, ts))
                    bounds[key] = (min(lo, ts), max(hi, ts))
                expected = sum((hi - lo) // step + 1 for lo, hi in bounds.values())
            out.append({
                "venue": venue, "symbol": symbol, "granularity": granularity,
                "stored": self.count(venue, symbol, granularity),
                "expected": expected,
                "gaps": len(found),
                "missing": sum(g[2] for g in found),
                "first": first, "last": last,
                "days": round((last - first) / 86400.0, 1) if first is not None else 0.0,
            })
        return out
