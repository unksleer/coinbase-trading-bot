"""
School Run — strategy logic.

The failures worth guarding are the ones that would make the rule look better
than it is: filling before price reached the level, taking the target when the
same bar hit the stop, moving a stop on the strength of the bar it then acts
on, or resolving a both-sides bar in the trade's favour.
"""
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from strategies import school_run as sr   # noqa: E402

BAR = 900


def candle(i, o, h, l, c):
    return {"ts": i * BAR, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def config(**over):
    base = dict(ref_index=1, entry_offset=0.0, stop_offset=0.0,
                target_r=None, breakeven_r=None, trail_r=None,
                position_usd=100.0, fee_per_side=0.0, slippage_per_side=0.0)
    base.update(over)
    return sr.Config(**base)


# Opening bar, then a 100..110 reference bar.
OPEN = candle(0, 100, 120, 90, 100)
REF = candle(1, 100, 110, 100, 105)


# ── reference candle and levels ─────────────────────────────────────────────

def test_reference_is_the_second_bar_by_default():
    d = sr.evaluate_session([OPEN, REF, candle(2, 105, 106, 104, 105)],
                            config(), "s", "SPY")
    assert (d.ref_high, d.ref_low) == (110, 100)
    assert d.ref_ts == REF["ts"]


def test_ref_index_zero_uses_the_opening_bar():
    d = sr.evaluate_session([OPEN, REF], config(ref_index=0), "s", "SPY")
    assert (d.ref_high, d.ref_low) == (120, 90)


def test_offsets_push_entries_outside_and_stops_beyond_the_range():
    lv = sr.levels(REF, config(entry_offset=0.01, stop_offset=0.02))
    assert lv["buy"] == pytest.approx(111.1)
    assert lv["sell"] == pytest.approx(99.0)
    assert lv["long_stop"] == pytest.approx(98.0)
    assert lv["short_stop"] == pytest.approx(112.2)


def test_session_shorter_than_reference_is_rejected():
    d = sr.evaluate_session([OPEN], config(), "s", "SPY")
    assert not d.accepted and d.reason == sr.SESSION_TOO_SHORT


def test_reference_on_last_bar_has_nothing_to_enter_on():
    d = sr.evaluate_session([OPEN, REF], config(), "s", "SPY")
    assert not d.accepted and d.reason == sr.NO_BAR_AFTER_REFERENCE


# ── triggering ──────────────────────────────────────────────────────────────

def test_no_trigger_when_price_stays_inside():
    inside = [candle(i, 104, 109, 101, 105) for i in range(2, 6)]
    d = sr.evaluate_session([OPEN, REF] + inside, config(), "s", "SPY")
    assert not d.accepted and d.reason == sr.NO_TRIGGER


def test_wick_through_the_high_fills_the_buy_stop_at_the_level():
    bars = [OPEN, REF, candle(2, 105, 112, 104, 104)]   # wick up, closes back inside
    d = sr.evaluate_session(bars, config(), "s", "SPY")
    assert d.accepted and d.direction == sr.LONG
    assert d.entry_price == 110                          # the level, not the close
    assert d.stop_price == 100
    assert d.entry_index == 2


def test_break_of_the_low_goes_short_with_stop_at_the_high():
    bars = [OPEN, REF, candle(2, 104, 105, 98, 99)]
    d = sr.evaluate_session(bars, config(), "s", "SPY")
    assert d.accepted and d.direction == sr.SHORT
    assert d.entry_price == 100 and d.stop_price == 110


def test_first_touch_wins_and_later_bars_are_ignored():
    bars = [OPEN, REF, candle(2, 104, 105, 103, 104), candle(3, 104, 111, 103, 108),
            candle(4, 108, 108, 95, 96)]
    d = sr.evaluate_session(bars, config(), "s", "SPY")
    assert d.direction == sr.LONG and d.entry_index == 3


def test_slippage_is_paid_against_the_trade_on_entry():
    bars = [OPEN, REF, candle(2, 105, 112, 104, 104)]
    d = sr.evaluate_session(bars, config(slippage_per_side=0.01), "s", "SPY")
    assert d.entry_price == pytest.approx(111.1)
    bars = [OPEN, REF, candle(2, 104, 105, 98, 99)]
    d = sr.evaluate_session(bars, config(slippage_per_side=0.01), "s", "SPY")
    assert d.entry_price == pytest.approx(99.0)


def test_both_sides_in_one_bar_is_flagged_and_stopped_in_that_bar():
    bars = [OPEN, REF, candle(2, 106, 115, 95, 100)]
    cfg = config()
    d = sr.evaluate_session(bars, cfg, "s", "SPY")
    assert d.accepted and d.both_sides_same_bar
    assert d.direction == sr.LONG                         # opened above the midpoint
    _, price, reason, *_ = sr.simulate_exit(d, bars, cfg)
    assert reason == sr.STOP and price == d.stop_price


def test_range_filters_reject_and_are_counted():
    bars = [OPEN, REF, candle(2, 105, 112, 104, 104)]
    d = sr.evaluate_session(bars, config(max_range_pct=5.0), "s", "SPY")
    assert not d.accepted and d.reason == sr.RANGE_TOO_WIDE
    d = sr.evaluate_session(bars, config(min_range_pct=20.0), "s", "SPY")
    assert not d.accepted and d.reason == sr.RANGE_TOO_NARROW


# ── exits ───────────────────────────────────────────────────────────────────

def long_entry(cfg, *later):
    bars = [OPEN, REF, candle(2, 105, 112, 104, 111)] + list(later)
    return sr.evaluate_session(bars, cfg, "s", "SPY"), bars


def test_stop_wins_when_one_bar_spans_stop_and_target():
    cfg = config(target_r=1.0)                            # target 120, stop 100
    d, bars = long_entry(cfg, candle(3, 111, 125, 99, 110))
    _, price, reason, *_ = sr.simulate_exit(d, bars, cfg)
    assert reason == sr.STOP and price == 100


def test_target_fills_at_the_target_level():
    cfg = config(target_r=1.0)
    d, bars = long_entry(cfg, candle(3, 111, 121, 110, 118))
    _, price, reason, *_ = sr.simulate_exit(d, bars, cfg)
    assert reason == sr.TARGET and price == 120


def test_time_exit_at_the_last_bar_close():
    cfg = config()
    d, bars = long_entry(cfg, candle(3, 111, 113, 109, 112), candle(4, 112, 114, 110, 113))
    ts, price, reason, *_ = sr.simulate_exit(d, bars, cfg)
    assert reason == sr.TIME and price == 113 and ts == bars[-1]["ts"]


def test_breakeven_moves_only_after_the_bar_that_earned_it():
    cfg = config(breakeven_r=1.0)                         # 1R = 10 points
    # Bar 3 reaches +1R then falls back to entry inside the same bar. A stop
    # moved intrabar would fire here; ours must not.
    d, bars = long_entry(cfg, candle(3, 111, 121, 110, 115), candle(4, 115, 116, 110, 112))
    _, price, reason, _, _, moved = sr.simulate_exit(d, bars, cfg)
    assert moved and reason == sr.STOP and price == 110   # breakeven hit on bar 4


def test_breakeven_not_moved_before_the_threshold():
    cfg = config(breakeven_r=1.0)
    d, bars = long_entry(cfg, candle(3, 111, 119, 110, 115), candle(4, 115, 116, 109, 112))
    _, price, reason, _, _, moved = sr.simulate_exit(d, bars, cfg)
    assert not moved and reason == sr.TIME


def test_trailing_stop_follows_the_extreme_and_never_retreats():
    cfg = config(breakeven_r=1.0, trail_r=1.0)
    d, bars = long_entry(cfg, candle(3, 111, 130, 110, 128),   # best 130, trail -> 120
                         candle(4, 128, 126, 122, 124),         # lower high; stop stays 120
                         candle(5, 124, 125, 119, 121))         # 119 <= 120: stopped
    _, price, reason, *_ = sr.simulate_exit(d, bars, cfg)
    assert reason == sr.STOP and price == 120


def test_short_exits_mirror_long():
    cfg = config(target_r=1.0)                            # short 100, stop 110, target 90
    bars = [OPEN, REF, candle(2, 104, 105, 98, 99), candle(3, 99, 101, 89, 92)]
    d = sr.evaluate_session(bars, cfg, "s", "SPY")
    _, price, reason, *_ = sr.simulate_exit(d, bars, cfg)
    assert d.direction == sr.SHORT and reason == sr.TARGET and price == 90
    bars[3] = candle(3, 99, 111, 89, 92)                  # spans both: stop first
    _, price, reason, *_ = sr.simulate_exit(d, bars, cfg)
    assert reason == sr.STOP and price == 110


# ── accounting ──────────────────────────────────────────────────────────────

def test_short_profit_is_positive_when_price_falls():
    cfg = config()
    d = sr.evaluate_session([OPEN, REF, candle(2, 104, 105, 98, 99)], cfg, "s", "SPY")
    gross, fees, net, filled = sr.pnl(d, 90.0, cfg)
    assert gross == pytest.approx(10.0) and net == pytest.approx(10.0)


def test_exit_slippage_and_fees_reduce_net_on_both_sides():
    cfg = config(slippage_per_side=0.01, fee_per_side=0.001)
    d = sr.evaluate_session([OPEN, REF, candle(2, 105, 112, 104, 111)], cfg, "s", "SPY")
    gross, fees, net, filled = sr.pnl(d, 120.0, cfg)
    assert filled == pytest.approx(118.8)                 # long sells lower
    assert fees == pytest.approx(0.2)
    assert net == pytest.approx(gross - 0.2)
    d = sr.evaluate_session([OPEN, REF, candle(2, 104, 105, 98, 99)], cfg, "s", "SPY")
    _, _, _, filled = sr.pnl(d, 90.0, cfg)
    assert filled == pytest.approx(90.9)                  # short covers higher
