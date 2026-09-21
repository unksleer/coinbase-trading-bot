"""
Donchian breakout — strategy logic.

The failures worth guarding: a channel that includes the bar being judged
(no breakout can ever print), a channel built from the wrong number of bars,
a signal on a touch rather than a close beyond the channel, and an
insufficient-data path that returns something other than HOLD.
"""
import os
import sys

import pandas as pd
import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from strategies import DonchianBreakoutStrategy          # noqa: E402
from strategies.donchian_breakout import DonchianBreakoutStrategy as Direct  # noqa: E402


def frame(closes):
    return pd.DataFrame({"start": range(len(closes)), "close": closes,
                         "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                         "volume": [1.0] * len(closes)})


def test_exported_from_package():
    assert DonchianBreakoutStrategy is Direct


def test_required_candles_covers_the_longer_channel_plus_spare():
    assert DonchianBreakoutStrategy(80, 40).get_required_candles_count() == 82
    assert DonchianBreakoutStrategy(10, 20).get_required_candles_count() == 22


def test_too_few_candles_holds_and_says_why():
    s = DonchianBreakoutStrategy(5, 3)
    assert s.evaluate(frame([1] * 6)) == "HOLD"
    assert s.last_decision["blocked_by"] == "INSUFFICIENT_DATA"


def test_close_above_prior_highs_is_a_buy():
    s = DonchianBreakoutStrategy(5, 3)
    closes = [10, 11, 12, 11, 10, 11, 12.5]      # prior five max 12; latest 12.5
    assert s.evaluate(frame(closes)) == "BUY"
    assert s.last_channel_high == 12


def test_equal_to_prior_high_is_not_a_breakout():
    s = DonchianBreakoutStrategy(5, 3)
    assert s.evaluate(frame([10, 11, 12, 11, 10, 11, 12])) == "HOLD"


def test_latest_bar_is_excluded_from_its_own_channel():
    # If the latest close were in the channel, the max would be 15 and no
    # BUY could ever fire. The window is the five bars before it: max 12.
    s = DonchianBreakoutStrategy(5, 3)
    assert s.evaluate(frame([9, 10, 11, 12, 11, 10, 15])) == "BUY"


def test_close_below_prior_lows_is_a_sell():
    s = DonchianBreakoutStrategy(5, 3)
    closes = [10, 11, 12, 11, 10.5, 10.2, 9.9]   # prior three min 10.2; latest 9.9
    assert s.evaluate(frame(closes)) == "SELL"
    assert s.last_channel_low == 10.2


def test_inside_the_channel_holds():
    s = DonchianBreakoutStrategy(5, 3)
    assert s.evaluate(frame([10, 11, 12, 11, 10, 11, 11.5])) == "HOLD"


def test_channel_uses_exactly_entry_bars():
    # A high of 20 sits six bars back; entry window is five, so it is outside
    # the channel and a close of 13 IS a breakout. Widen to six and it is not.
    closes = [20, 10, 11, 12, 11, 10, 13]
    assert DonchianBreakoutStrategy(5, 3).evaluate(frame(closes)) == "BUY"
    assert DonchianBreakoutStrategy(6, 3).evaluate(frame(closes)) == "HOLD"


def test_decision_record_carries_the_levels():
    s = DonchianBreakoutStrategy(5, 3)
    s.evaluate(frame([10, 11, 12, 11, 10, 11, 12.5]))
    d = s.last_decision
    assert d["raw_signal"] == d["final_signal"] == "BUY"
    assert d["blocked_by"] == ""
    assert (d["channel_high"], d["channel_low"], d["close"]) == (12, 10, 12.5)


def test_rejects_degenerate_windows():
    with pytest.raises(ValueError):
        DonchianBreakoutStrategy(1, 3)
