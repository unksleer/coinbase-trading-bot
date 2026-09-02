"""
First Candle Rule — strategy logic.

The failures worth guarding are the ones that would make a worthless rule look
profitable: entering on a wick, entering at the breakout level rather than the
next available price, letting an indicator read a candle that had not printed,
or letting a breakout bar inflate its own volume baseline.
"""
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from strategies import first_candle_rule as fcr   # noqa: E402
from utils import indicators                       # noqa: E402

HOUR = 3600


def candle(ts, o, h, l, c, v=1000.0):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def history(n=400, price=100.0, volume=1000.0):
    """Flat prior history: EMA200 sits at `price`, volume baseline at `volume`."""
    return [candle(i * HOUR, price, price, price, price, volume) for i in range(n)]


def config(**over):
    base = dict(volume_multiple=1.5, rsi_min=0.0, rsi_max=100.0,
                position_usd=10.0, max_risk_usd=5.0,
                fee_per_side=0.0, slippage_per_side=0.0)
    base.update(over)
    return fcr.Config(**base)


# ── breakout definition ─────────────────────────────────────────────────────

def test_a_wick_above_the_range_is_not_a_breakout():
    """
    A high through the opening range that closes back inside is exactly the
    false breakout the rule exists to avoid. Testing touches instead of closes
    would enter on a price that may never have been fillable.
    """
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 105, 99, 100.5, 5000),   # wick over 101
               candle(402 * HOUR, 100, 100, 99, 100)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert not d.accepted
    assert d.reason == fcr.NO_BREAKOUT


def test_a_close_above_the_range_high_is_a_breakout():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.accepted, d.reason


def test_a_close_exactly_at_the_range_high_is_not_a_breakout():
    """Strictly above. Equal is inside the range."""
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 101, 100, 101.0, 5000),
               candle(402 * HOUR, 101, 102, 100, 101)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.reason == fcr.NO_BREAKOUT


# ── entry pricing ───────────────────────────────────────────────────────────

def test_entry_is_the_next_candle_open_not_the_breakout_price():
    """
    Entering at the breakout close assumes a fill at a price already gone. That
    single assumption separates a strategy from a backtest artefact.
    """
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.9, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.entry_price == pytest.approx(101.9)
    assert d.entry_price != d.breakout_close


def test_slippage_is_paid_against_you_on_entry():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 100.0, 103, 99, 102)]
    d = fcr.evaluate_session(session, config(slippage_per_side=0.001),
                             "s", "TEST", history())
    assert d.entry_price == pytest.approx(100.0 * 1.001)


def test_a_breakout_on_the_last_candle_cannot_be_entered():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.reason == fcr.NO_NEXT_CANDLE


# ── confirmation filters ────────────────────────────────────────────────────

def test_volume_below_the_multiple_is_rejected():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 1200),   # 1.2x, needs 1.5x
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.reason == fcr.VOLUME_TOO_LOW


def test_the_breakout_bar_does_not_inflate_its_own_volume_baseline():
    """
    Including the current bar in its own average is the quiet look-ahead in a
    volume filter: a huge bar raises its own baseline and the ratio understates
    exactly the move being tested.
    """
    volumes = [100.0] * 20 + [1000.0]
    ratio = indicators.volume_ratio(volumes, 20).iloc[-1]
    assert ratio == pytest.approx(10.0)


def test_price_below_ema200_is_rejected():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(), "s", "TEST",
                             history(price=200.0))       # EMA far above
    assert d.reason == fcr.BELOW_EMA


def test_rsi_outside_the_band_is_rejected():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(rsi_min=95.0, rsi_max=99.0),
                             "s", "TEST", history())
    assert d.reason == fcr.RSI_OUT_OF_BAND


def test_skipping_a_filter_actually_skips_it():
    """
    Approximating "no EMA filter" by setting the period to 1 makes EMA equal
    close, so every signal is rejected and the ablation reads as though the
    filter were essential. It produced a false "0 signals" result.
    """
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    blocked = fcr.evaluate_session(session, config(), "s", "TEST",
                                   history(price=200.0))
    assert blocked.reason == fcr.BELOW_EMA
    allowed = fcr.evaluate_session(session, config(), "s", "TEST",
                                   history(price=200.0), skip={"ema"})
    assert allowed.accepted, allowed.reason


# ── stop and target ─────────────────────────────────────────────────────────

def test_the_stop_is_never_at_or_above_entry():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.stop_price < d.entry_price


def test_the_closer_valid_stop_is_chosen():
    """Smaller risk, still structurally valid."""
    session = [candle(400 * HOUR, 100, 101, 90, 100),      # midpoint 95.5, far
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.stop_price > d.open_mid, "ATR stop was nearer and should win"


def test_risk_above_the_limit_is_rejected():
    session = [candle(400 * HOUR, 100, 101, 50, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(max_risk_usd=0.001),
                             "s", "TEST", history())
    assert d.reason == fcr.RISK_EXCEEDS_LIMIT


def test_target_gives_at_least_the_configured_reward_to_risk():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(min_reward_risk=2.0),
                             "s", "TEST", history())
    assert d.target_distance == pytest.approx(d.stop_distance * 2.0)
    assert d.target_price > d.entry_price


def test_a_target_that_cannot_clear_costs_is_rejected():
    """The BTC requirement: gross reward must exceed the whole round trip."""
    session = [candle(400 * HOUR, 100, 100.02, 99.99, 100),
               candle(401 * HOUR, 100, 102, 100, 100.03, 5000),
               candle(402 * HOUR, 100.03, 103, 100, 102)]
    d = fcr.evaluate_session(session, config(fee_per_side=0.05),
                             "s", "TEST", history())
    assert d.reason in (fcr.TARGET_BELOW_COSTS, fcr.RISK_EXCEEDS_LIMIT)


# ── session discipline ──────────────────────────────────────────────────────

def test_only_the_first_breakout_of_a_session_is_considered():
    """One entry attempt per session, and a rejected first attempt ends it."""
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 1000),   # fails volume
               candle(402 * HOUR, 100, 103, 100, 102.5, 9000),   # would pass
               candle(403 * HOUR, 102.5, 104, 102, 103)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.reason == fcr.VOLUME_TOO_LOW
    assert not d.accepted


def test_an_empty_session_is_recorded_not_skipped():
    d = fcr.evaluate_session([], config(), "s", "TEST", history())
    assert d.reason == fcr.NO_OPENING_CANDLE


def test_insufficient_history_is_named_not_silently_passed():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history(n=10))
    assert d.reason == fcr.INSUFFICIENT_HISTORY


def test_the_opening_range_is_the_first_candle_only():
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 110, 90, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102)]
    d = fcr.evaluate_session(session, config(), "s", "TEST", history())
    assert d.open_high == 101 and d.open_low == 99
    assert d.open_mid == 100


# ── no look-ahead ───────────────────────────────────────────────────────────

def test_indicators_are_read_at_the_breakout_bar_not_the_session_end():
    """
    Reading the last value of a session-wide series would let the decision see
    candles that had not printed when it was made.
    """
    session = [candle(400 * HOUR, 100, 101, 99, 100),
               candle(401 * HOUR, 100, 102, 100, 101.5, 5000),
               candle(402 * HOUR, 101.5, 103, 101, 102),
               candle(403 * HOUR, 102, 500, 102, 480, 99999)]   # a later spike
    with_spike = fcr.evaluate_session(session, config(), "s", "TEST", history())
    without = fcr.evaluate_session(session[:3], config(), "s", "TEST", history())
    assert with_spike.rsi == pytest.approx(without.rsi)
    assert with_spike.ema200 == pytest.approx(without.ema200)
    assert with_spike.entry_price == pytest.approx(without.entry_price)


def test_indicator_maths_matches_the_running_bots():
    """
    Written fresh rather than extracted, so the two must be checked to agree —
    otherwise two strategies quietly disagree about what "RSI 40" means.
    """
    import pandas as pd
    from strategies.simple_moving_average import SMACrossoverStrategy
    closes = [100 + (i % 7) - 3 for i in range(300)]
    df = pd.DataFrame({"close": closes, "high": [c + 1 for c in closes],
                       "low": [c - 1 for c in closes], "open": closes,
                       "volume": [1000] * 300})
    s = SMACrossoverStrategy(short_window=10, long_window=40,
                             rsi_filter_enabled=True, rsi_period=14,
                             ema_filter_enabled=True, ema_period=200)
    s.evaluate(df)
    assert indicators.rsi(closes, 14).iloc[-1] == pytest.approx(s.last_rsi)
    assert indicators.ema(closes, 200).iloc[-1] == pytest.approx(s.last_ema)


# ── isolation from production ───────────────────────────────────────────────

def test_the_experiment_does_not_touch_existing_strategies():
    import ast
    for name in ("bot.py", "stock_bot.py", "strategies/simple_moving_average.py",
                 "strategies/rsi_mean_reversion.py"):
        with open(os.path.join(DIR, name)) as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any("first_candle" in m for m in imported), \
            f"{name} must not import the experiment"
