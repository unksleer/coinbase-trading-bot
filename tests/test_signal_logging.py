import csv
import os
import tempfile
import unittest

import pandas as pd

from pathlib import Path

from strategies.simple_moving_average import SMACrossoverStrategy
from utils.signal_log import SignalLogger
from utils.state import StateManager


# The strategy only signals when the cross happens between the final two candles,
# so each fixture is truncated to end exactly on its crossover candle.
_BULL_CROSS_INDEX = 85
_WEAK_BULL_CROSS_INDEX = 89
_BEAR_CROSS_INDEX = 85


def _closes_with_bullish_cross():
    """Falling then sharply rising series, ending on the bullish crossover candle."""
    closes = [100.0] * 40 + [100.0 - i * 0.5 for i in range(40)] + [80.0 + i * 3.0 for i in range(12)]
    return closes[:_BULL_CROSS_INDEX + 1]


def _closes_with_weak_bullish_cross():
    """
    A shallow recovery: the short SMA crosses up, but price is still under the
    EMA50, which is exactly the case the trend filter is meant to reject.
    """
    closes = [100.0] * 40 + [100.0 - i * 0.5 for i in range(40)] + [80.0 + i * 0.8 for i in range(14)]
    return closes[:_WEAK_BULL_CROSS_INDEX + 1]


def _closes_with_bearish_cross():
    """Rising then sharply falling series, ending on the bearish crossover candle."""
    closes = [100.0] * 40 + [100.0 + i * 0.5 for i in range(40)] + [120.0 - i * 3.0 for i in range(12)]
    return closes[:_BEAR_CROSS_INDEX + 1]


class SignalDecisionTests(unittest.TestCase):
    def test_unfiltered_bullish_cross_is_recorded_as_accepted(self):
        df = pd.DataFrame({"close": _closes_with_bullish_cross()})
        strategy = SMACrossoverStrategy(short_window=5, long_window=30)
        signal = strategy.evaluate(df)

        self.assertEqual(signal, "BUY")
        self.assertEqual(strategy.last_decision["raw_signal"], "BUY")
        self.assertEqual(strategy.last_decision["final_signal"], "BUY")
        self.assertEqual(strategy.last_decision["blocked_by"], "")

    def test_rsi_rejection_is_attributed_and_still_returns_hold(self):
        df = pd.DataFrame({"close": _closes_with_bullish_cross()})
        # An impossible RSI ceiling forces the buy filter to reject.
        strategy = SMACrossoverStrategy(short_window=5, long_window=30,
                                        rsi_filter_enabled=True, rsi_buy_max=1.0)
        signal = strategy.evaluate(df)

        self.assertEqual(signal, "HOLD")
        self.assertEqual(strategy.last_decision["raw_signal"], "BUY")
        self.assertEqual(strategy.last_decision["blocked_by"], "RSI")

    def test_ema_rejection_is_attributed(self):
        df = pd.DataFrame({"close": _closes_with_weak_bullish_cross()})
        # Price crosses up but is still below the EMA50, so the trend filter rejects.
        strategy = SMACrossoverStrategy(short_window=5, long_window=30,
                                        ema_filter_enabled=True, ema_period=50)
        strategy.evaluate(df)

        self.assertEqual(strategy.last_decision["raw_signal"], "BUY")
        self.assertEqual(strategy.last_decision["blocked_by"], "EMA")

    def test_spread_rejection_takes_precedence_over_later_filters(self):
        df = pd.DataFrame({"close": _closes_with_bullish_cross()})
        strategy = SMACrossoverStrategy(short_window=5, long_window=30,
                                        min_spread_percent=99.0,
                                        rsi_filter_enabled=True, rsi_buy_max=1.0)
        strategy.evaluate(df)

        self.assertEqual(strategy.last_decision["blocked_by"], "SPREAD")

    def test_no_crossover_records_hold_with_no_blocker(self):
        df = pd.DataFrame({"close": [100.0] * 90})
        strategy = SMACrossoverStrategy(short_window=5, long_window=30)
        strategy.evaluate(df)

        self.assertEqual(strategy.last_decision["raw_signal"], "HOLD")
        self.assertEqual(strategy.last_decision["blocked_by"], "")

    def test_insufficient_data_is_flagged_rather_than_left_stale(self):
        strategy = SMACrossoverStrategy(short_window=5, long_window=30)
        strategy.evaluate(pd.DataFrame({"close": [100.0] * 5}))

        self.assertTrue(strategy.last_decision["insufficient_data"])
        self.assertEqual(strategy.last_decision["blocked_by"], "INSUFFICIENT_DATA")

    def test_bearish_cross_rejection_is_attributed(self):
        df = pd.DataFrame({"close": _closes_with_bearish_cross()})
        strategy = SMACrossoverStrategy(short_window=5, long_window=30,
                                        rsi_filter_enabled=True, rsi_sell_min=99.0)
        signal = strategy.evaluate(df)

        self.assertEqual(signal, "HOLD")
        self.assertEqual(strategy.last_decision["raw_signal"], "SELL")
        self.assertEqual(strategy.last_decision["blocked_by"], "RSI")


class SignalLoggerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _rows(self, logger):
        with open(logger.path, newline="") as handle:
            return list(csv.DictReader(handle))

    def test_one_row_per_candle_despite_repeated_evaluations(self):
        logger = SignalLogger("LINK-USDC", log_dir=self.log_dir)
        decision = {"raw_signal": "HOLD", "final_signal": "HOLD", "blocked_by": ""}

        # The loop runs every 60s but the closed candle only advances hourly.
        for _ in range(5):
            logger.record("2026-08-24T12:00:00Z", 11.5, "NONE", decision)

        self.assertEqual(len(self._rows(logger)), 1)

    def test_new_candle_appends_a_new_row(self):
        logger = SignalLogger("LINK-USDC", log_dir=self.log_dir)
        decision = {"raw_signal": "BUY", "final_signal": "HOLD", "blocked_by": "EMA"}

        logger.record("2026-08-24T12:00:00Z", 11.5, "NONE", decision)
        logger.record("2026-08-24T13:00:00Z", 11.6, "NONE", decision)

        rows = self._rows(logger)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["blocked_by"], "EMA")
        self.assertEqual(rows[0]["raw_signal"], "BUY")
        self.assertEqual(rows[0]["final_signal"], "HOLD")

    def test_restart_does_not_duplicate_the_in_flight_candle(self):
        decision = {"raw_signal": "HOLD", "final_signal": "HOLD", "blocked_by": ""}
        first = SignalLogger("LINK-USDC", log_dir=self.log_dir)
        first.record("2026-08-24T12:00:00Z", 11.5, "NONE", decision)

        restarted = SignalLogger("LINK-USDC", log_dir=self.log_dir)
        restarted.record("2026-08-24T12:00:00Z", 11.5, "NONE", decision)

        self.assertEqual(len(self._rows(restarted)), 1)

    def test_logging_failure_never_propagates_to_the_trading_loop(self):
        logger = SignalLogger("LINK-USDC", log_dir=os.path.join(self.log_dir, "sub"))
        # Replace the target path with a directory so the append cannot succeed.
        os.makedirs(logger.path, exist_ok=True)

        self.assertFalse(
            logger.record("2026-08-24T12:00:00Z", 11.5, "NONE",
                          {"raw_signal": "BUY", "final_signal": "BUY", "blocked_by": ""})
        )

    def test_missing_indicator_values_serialize_as_blank_not_zero(self):
        logger = SignalLogger("LINK-USDC", log_dir=self.log_dir)
        logger.record("2026-08-24T12:00:00Z", 11.5, "NONE",
                      {"raw_signal": "HOLD", "final_signal": "HOLD", "blocked_by": "",
                       "rsi": None, "ema": None})

        row = self._rows(logger)[0]
        self.assertEqual(row["rsi"], "")
        self.assertEqual(row["ema"], "")


class ExitReasonMappingTests(unittest.TestCase):
    """
    Exit reasons are mapped to stable friendly labels before they reach
    completed_trades_*.csv. The learner groups trades by the dominant exit
    label, so any reason left unmapped carries its embedded prices and becomes
    a unique string that never groups with its siblings.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.csv_path = str(Path(self._tmp.name) / "completed.csv")
        self._prev = os.environ.get("COMPLETED_TRADES_FILE")
        os.environ["COMPLETED_TRADES_FILE"] = self.csv_path
        self.addCleanup(self._restore_env)
        self.manager = StateManager(
            state_file=str(Path(self._tmp.name) / "state_link-usdc_dry.json"),
            dry_run=True,
        )

    def _restore_env(self):
        if self._prev is None:
            os.environ.pop("COMPLETED_TRADES_FILE", None)
        else:
            os.environ["COMPLETED_TRADES_FILE"] = self._prev

    def _log(self, exit_reason):
        self.manager.log_completed_roundtrip(
            exit_time="2026-08-24T13:00:00Z",
            entry_time="2026-08-24T12:00:00Z",
            entry_price=10.0,
            exit_price=10.5,
            entry_reason="SMA Bullish Cross",
            exit_reason=exit_reason,
            amount_base=1.0,
            buy_fee=0.06,
            sell_fee=0.06,
            pnl_usd=0.38,
            highest_price=10.8,
            lowest_price=9.9,
            entry_indicators={},
        )
        with open(self.csv_path, newline="") as handle:
            return list(csv.DictReader(handle))[-1]["exit_reason"]

    def test_trailing_stop_maps_to_a_stable_label(self):
        reason = "TRAILING_STOP (Peak=$10.8000, trail=3.0%, trigger=$10.4760)"
        self.assertEqual(self._log(reason), "Trailing Stop Triggered")

    def test_two_trailing_exits_share_one_label_so_they_group(self):
        first = self._log("TRAILING_STOP (Peak=$10.8000, trail=3.0%, trigger=$10.4760)")
        second = self._log("TRAILING_STOP (Peak=$11.9000, trail=3.0%, trigger=$11.5430)")
        self.assertEqual(first, second)

    def test_existing_exit_labels_are_unchanged(self):
        self.assertEqual(self._log("STOP_LOSS (Price fell 5.10% below entry $10.00)"),
                         "Stop Loss Triggered")
        self.assertEqual(self._log("TAKE_PROFIT (Price rose 8.20% above entry $10.00)"),
                         "Take Profit Triggered")
        self.assertEqual(self._log("BREAK_EVEN (Price fell to break-even price $10.1260)"),
                         "Break-even Stop Triggered")


if __name__ == "__main__":
    unittest.main()
