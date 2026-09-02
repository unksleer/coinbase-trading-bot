"""
The store exists to accumulate history the API will not serve, and the tests
exist because a corrupt candle store silently corrupts every result built on it.

Three failures are specifically guarded, all of which bias research in the same
optimistic direction if they slip through:

  unfinished candles  a candle still forming has a partial high, low and close
                      that are revised later, so a backtest reads prices no
                      trader could have acted on
  duplicates          a re-run or an overlapping backfill double-counting a
                      bar changes every average computed from it
  silent revision     a venue quietly returning different values for a stored
                      timestamp is a fact to surface, not absorb

The plausibility check has its own history: the first collector read the frame
index instead of the `start` column, producing timestamps of 0, 1, 2. All 6318
were accepted, because "has this candle closed" is trivially true for 1970. An
invariant that only checks the near edge of a range does not notice a value at
the far end.
"""
import os
import sys
import tempfile

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from market_data.candle_store import (  # noqa: E402
    CandleStore, ImplausibleTimestampError, is_closed, seconds_for)

HOUR = 3600
SIX = 21600
NOW = 1_780_000_000          # a plausible 2026 epoch


@pytest.fixture
def store():
    path = os.path.join(tempfile.mkdtemp(), "candles.sqlite")
    s = CandleStore(path)
    yield s
    s.close()


def bar(ts, close=100.0):
    return (ts, close, close + 1, close - 1, close, 10.0)


# ── unfinished candles ──────────────────────────────────────────────────────

def test_a_candle_is_closed_only_after_its_full_interval():
    assert is_closed(NOW - HOUR, "ONE_HOUR", NOW) is True
    assert is_closed(NOW - HOUR + 1, "ONE_HOUR", NOW) is False


def test_a_candle_ending_exactly_now_is_closed():
    """Strict boundary: end == now counts as finished, not forming."""
    assert is_closed(NOW - SIX, "SIX_HOUR", NOW) is True


def test_forming_candles_are_refused_not_stored(store):
    inserted, skipped, _ = store.add(
        "coinbase", "BTC-USDC", "ONE_HOUR",
        [bar(NOW - 2 * HOUR), bar(NOW - HOUR), bar(NOW - 60)], now=NOW)
    assert inserted == 2
    assert skipped == 1
    assert store.count() == 2


def test_a_forming_candle_is_stored_once_it_closes(store):
    ts = NOW - 60
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts)], now=NOW)
    assert store.count() == 0
    later = ts + HOUR
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts)], now=later)
    assert store.count() == 1


# ── duplicates ──────────────────────────────────────────────────────────────

def test_reinserting_the_same_candle_is_a_no_op(store):
    rows = [bar(NOW - 3 * HOUR), bar(NOW - 2 * HOUR)]
    first = store.add("coinbase", "BTC-USDC", "ONE_HOUR", rows, now=NOW)
    second = store.add("coinbase", "BTC-USDC", "ONE_HOUR", rows, now=NOW)
    assert first == (2, 0, 0)
    assert second == (0, 0, 2)
    assert store.count() == 2


def test_overlapping_backfills_are_idempotent(store):
    """Hourly runs overlap by design; that must never double-count."""
    a = [bar(NOW - i * HOUR) for i in range(2, 8)]
    b = [bar(NOW - i * HOUR) for i in range(5, 12)]
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", a, now=NOW)
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", b, now=NOW)
    stored = store.get("coinbase", "BTC-USDC", "ONE_HOUR")
    assert len(stored) == len({ts for ts, *_ in stored})


def test_the_same_timestamp_on_different_series_is_not_a_duplicate(store):
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(NOW - HOUR)], now=NOW)
    store.add("coinbase", "ETH-USDC", "ONE_HOUR", [bar(NOW - HOUR)], now=NOW)
    store.add("coinbase", "BTC-USDC", "SIX_HOUR", [bar(NOW - SIX)], now=NOW)
    assert store.count() == 3


# ── silent revision ─────────────────────────────────────────────────────────

def test_a_stored_candle_is_never_overwritten(store):
    ts = NOW - HOUR
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts, 100.0)], now=NOW)
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts, 999.0)], now=NOW)
    assert store.closes("coinbase", "BTC-USDC", "ONE_HOUR") == [100.0]


def test_a_changed_value_is_recorded_as_a_conflict(store):
    ts = NOW - HOUR
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts, 100.0)], now=NOW)
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts, 999.0)], now=NOW)
    found = store.conflicts()
    assert found, "a differing value must be surfaced, not absorbed"
    assert {c["field"] for c in found} >= {"close"}


def test_identical_values_do_not_raise_a_conflict(store):
    ts = NOW - HOUR
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts)], now=NOW)
    store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts)], now=NOW)
    assert store.conflicts() == []


# ── implausible timestamps ──────────────────────────────────────────────────

@pytest.mark.parametrize("ts", [0, 1, 2, 300, 1_000_000])
def test_epoch_zero_timestamps_are_refused(store, ts):
    """
    The exact defect that shipped: reading the frame index instead of the
    `start` column gave 0, 1, 2 — and every one passed the closed check.
    """
    with pytest.raises(ImplausibleTimestampError):
        store.add("coinbase", "BTC-USDC", "ONE_HOUR", [bar(ts)], now=NOW)
    assert store.count() == 0


def test_far_future_timestamps_are_refused(store):
    with pytest.raises(ImplausibleTimestampError):
        store.add("coinbase", "BTC-USDC", "ONE_HOUR",
                  [bar(NOW + 400 * 86400)], now=NOW)


# ── gaps ────────────────────────────────────────────────────────────────────

def test_a_missing_interval_is_reported(store):
    store.add("coinbase", "BTC-USDC", "ONE_HOUR",
              [bar(NOW - 5 * HOUR), bar(NOW - 4 * HOUR), bar(NOW - 2 * HOUR)],
              now=NOW)
    gaps = store.gaps("coinbase", "BTC-USDC", "ONE_HOUR")
    assert len(gaps) == 1
    assert gaps[0][2] == 1


def test_a_contiguous_series_reports_no_gaps(store):
    store.add("coinbase", "BTC-USDC", "ONE_HOUR",
              [bar(NOW - i * HOUR) for i in range(2, 10)], now=NOW)
    assert store.gaps("coinbase", "BTC-USDC", "ONE_HOUR") == []


def test_gaps_are_reported_not_repaired(store):
    """
    Inventing a bar to close a gap puts fabricated prices into research. An
    outage and a venue halt need different responses and the store cannot tell
    them apart.
    """
    store.add("coinbase", "BTC-USDC", "ONE_HOUR",
              [bar(NOW - 5 * HOUR), bar(NOW - 2 * HOUR)], now=NOW)
    assert store.count() == 2, "the store must not fill a gap"
    assert store.gaps("coinbase", "BTC-USDC", "ONE_HOUR")


# ── reading ─────────────────────────────────────────────────────────────────

def test_candles_come_back_oldest_first(store):
    store.add("coinbase", "BTC-USDC", "ONE_HOUR",
              [bar(NOW - 2 * HOUR, 2.0), bar(NOW - 4 * HOUR, 4.0),
               bar(NOW - 3 * HOUR, 3.0)], now=NOW)
    assert store.closes("coinbase", "BTC-USDC", "ONE_HOUR") == [4.0, 3.0, 2.0]


def test_limit_returns_the_most_recent_still_oldest_first(store):
    store.add("coinbase", "BTC-USDC", "ONE_HOUR",
              [bar(NOW - i * HOUR, float(i)) for i in range(2, 8)], now=NOW)
    assert store.closes("coinbase", "BTC-USDC", "ONE_HOUR", limit=2) == [3.0, 2.0]


def test_unknown_granularity_is_rejected_before_writing(store):
    with pytest.raises(ValueError):
        store.add("coinbase", "BTC-USDC", "TWO_HOUR", [bar(NOW - HOUR)], now=NOW)
    assert store.count() == 0


def test_granularity_seconds_match_their_names():
    assert seconds_for("ONE_HOUR") == 3600
    assert seconds_for("SIX_HOUR") == 21600
    assert seconds_for("Hour") == 3600


# ── the collector must not touch trading ────────────────────────────────────

def test_collector_shares_no_code_path_with_the_bots():
    """
    The store is research infrastructure. The moment a bot reads from it, a
    stale or gapped store becomes a trading fault rather than an inconvenience,
    and it would void the pre-registered stock run.
    """
    import ast
    for name in ("bot.py", "stock_bot.py"):
        with open(os.path.join(DIR, name)) as handle:
            tree = ast.parse(handle.read())
        # Parse imports rather than grep for the string: "market_data" also
        # appears inside check_market_data_freshness, and matching that fails a
        # correct file.
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offending = {m for m in imported
                     if m == "market_data" or m.startswith("market_data.")}
        assert not offending, f"{name} imports {offending}; it must not read the store"
