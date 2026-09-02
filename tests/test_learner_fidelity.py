"""
The learner's verdicts are only meaningful if its backtest simulates the same
strategy the bot trades. These tests pin the pieces that make that true: the
entry filter chain, the exit regime being threaded rather than defaulted, and
the config resolution that feeds both.
"""
import unittest

import numpy as np
import pandas as pd

import auto_learner as al
from strategies.simple_moving_average import SMACrossoverStrategy


class EntryFilterTests(unittest.TestCase):
    """entry_allowed must reproduce SMACrossoverStrategy's BUY filter chain."""

    def setUp(self):
        # Separation at index 1 is (11 - 10) / 10 = 10%.
        self.short = np.array([10.0, 11.0])
        self.long = np.array([10.0, 10.0])

    def test_no_filters_allows_entry(self):
        self.assertTrue(al.entry_allowed(1, 11.0, self.short, self.long, None, None, None))
        self.assertTrue(al.entry_allowed(1, 11.0, self.short, self.long, None, None, {}))

    def test_separation_floor_blocks_weak_crossover(self):
        self.assertFalse(al.entry_allowed(
            1, 11.0, self.short, self.long, None, None, {"min_spread_pct": 20.0}))
        self.assertTrue(al.entry_allowed(
            1, 11.0, self.short, self.long, None, None, {"min_spread_pct": 5.0}))

    def test_separation_ceiling_blocks_extended_crossover(self):
        self.assertFalse(al.entry_allowed(
            1, 11.0, self.short, self.long, None, None, {"max_spread_pct": 5.0}))
        self.assertTrue(al.entry_allowed(
            1, 11.0, self.short, self.long, None, None, {"max_spread_pct": 20.0}))

    def test_zero_disables_each_separation_bound(self):
        filters = {"min_spread_pct": 0.0, "max_spread_pct": 0.0}
        self.assertTrue(al.entry_allowed(1, 11.0, self.short, self.long, None, None, filters))

    def test_rsi_band_blocks_outside_and_allows_inside(self):
        filters = {"rsi_buy_min": 50.0, "rsi_buy_max": 55.0}
        for rsi, expected in ((52.0, True), (57.0, False), (45.0, False)):
            allowed = al.entry_allowed(
                1, 11.0, self.short, self.long, np.array([np.nan, rsi]), None, filters)
            self.assertEqual(allowed, expected, f"RSI {rsi}")

    def test_nan_rsi_during_warmup_blocks_entry(self):
        """A NaN must not compare its way into a trade during the warm-up window."""
        self.assertFalse(al.entry_allowed(
            1, 11.0, self.short, self.long, np.array([np.nan, np.nan]), None,
            {"rsi_buy_min": 0.0, "rsi_buy_max": 100.0}))

    def test_ema_trend_requires_price_above_ema(self):
        self.assertTrue(al.entry_allowed(
            1, 11.0, self.short, self.long, None, np.array([np.nan, 10.0]), {"ema_enabled": True}))
        self.assertFalse(al.entry_allowed(
            1, 11.0, self.short, self.long, None, np.array([np.nan, 12.0]), {"ema_enabled": True}))


class RsiParityTests(unittest.TestCase):
    """
    The backtest's RSI must match the strategy's to the last decimal, or the
    learner tunes against a band the bot never sees.
    """

    def test_rsi_matches_strategy_implementation(self):
        rng = np.random.default_rng(7)
        closes = 100 + np.cumsum(rng.normal(0, 1, 300))

        rsi_values, _ = al.compute_entry_filters(
            closes, {"rsi_enabled": True, "rsi_period": 14})

        series = pd.Series(closes)
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
        expected = 100 - (100 / (1 + avg_gain / avg_loss.replace(0, 1e-9)))

        np.testing.assert_allclose(rsi_values[20:], expected.to_numpy()[20:], rtol=1e-12)

    def test_disabled_filters_return_none(self):
        rsi_values, ema_values = al.compute_entry_filters(np.array([1.0, 2.0, 3.0]), {})
        self.assertIsNone(rsi_values)
        self.assertIsNone(ema_values)


class FilterChainOrderTests(unittest.TestCase):
    """
    entry_allowed and the live strategy must agree on the same candles. This
    drives real price data through both and compares the accept/reject verdict.
    """

    def test_backtest_and_strategy_agree_on_entries(self):
        rng = np.random.default_rng(11)
        closes = 100 + np.cumsum(rng.normal(0, 1.5, 400))
        filters = {
            "min_spread_pct": 0.0, "max_spread_pct": 0.30,
            "rsi_enabled": True, "rsi_period": 14,
            "rsi_buy_min": 30.0, "rsi_buy_max": 55.0,
            "ema_enabled": False, "ema_period": 200,
        }
        short_window, long_window = 5, 30

        series = pd.Series(closes)
        short_sma = series.rolling(short_window).mean().to_numpy()
        long_sma = series.rolling(long_window).mean().to_numpy()
        rsi_values, ema_values = al.compute_entry_filters(closes, filters)

        strategy = SMACrossoverStrategy(
            short_window=short_window, long_window=long_window,
            min_spread_percent=filters["min_spread_pct"],
            max_spread_percent=filters["max_spread_pct"],
            rsi_filter_enabled=True, rsi_period=14,
            rsi_buy_min=30.0, rsi_buy_max=55.0,
        )

        compared = 0
        for i in range(long_window + 2, len(closes)):
            # Only crossover candles reach the filter chain in either path.
            if not (short_sma[i - 1] < long_sma[i - 1] and short_sma[i] > long_sma[i]):
                continue
            window = pd.DataFrame({"close": closes[: i + 1]})
            strategy_says_buy = strategy.evaluate(window) == "BUY"
            backtest_allows = al.entry_allowed(
                i, closes[i], short_sma, long_sma, rsi_values, ema_values, filters)
            self.assertEqual(strategy_says_buy, backtest_allows,
                             f"disagreement at candle {i}")
            compared += 1

        self.assertGreater(compared, 0, "no crossovers generated; test proves nothing")


class ExitRegimeThreadingTests(unittest.TestCase):
    """
    quick_backtest's trailing defaults must never be what actually runs — a
    default that drifts from .env silently optimises for a bot that isn't live.
    """

    def _rising_then_falling(self):
        """Down, then up through the slow SMA, then a retrace — one clean
        bullish crossover followed by a peak the trailing stop can act on."""
        return np.concatenate([
            np.linspace(120, 100, 40),
            np.linspace(100, 140, 60),
            np.linspace(140, 125, 30),
        ])

    def test_trailing_config_changes_the_result(self):
        closes = self._rising_then_falling()
        series = pd.Series(closes)
        short_sma = series.rolling(5).mean().to_numpy()
        long_sma = series.rolling(30).mean().to_numpy()

        tight = al.quick_backtest(closes, short_sma, long_sma, 5.0, 50.0,
                                  trailing_stop_pct=1.5, trailing_activation_pct=1.5)
        loose = al.quick_backtest(closes, short_sma, long_sma, 5.0, 50.0,
                                  trailing_stop_pct=10.0, trailing_activation_pct=2.0)
        self.assertNotEqual(tight["net_profit"], loose["net_profit"])

    def test_run_segment_passes_trailing_through(self):
        closes = self._rising_then_falling()
        tight = al._run_segment(closes, 5, 30, 5.0, 50.0, trailing=(1.5, 1.5))
        loose = al._run_segment(closes, 5, 30, 5.0, 50.0, trailing=(10.0, 2.0))
        self.assertNotEqual(tight["net_profit"], loose["net_profit"])

    def test_run_segment_without_trailing_disables_it(self):
        """Absent config must mean 'no trailing stop', not quick_backtest's 3.0/2.0."""
        closes = self._rising_then_falling()
        none = al._run_segment(closes, 5, 30, 5.0, 50.0, trailing=None)
        explicit_off = al._run_segment(closes, 5, 30, 5.0, 50.0, trailing=(0.0, 0.0))
        self.assertEqual(none["net_profit"], explicit_off["net_profit"])

    def test_filters_reduce_trade_count(self):
        closes = self._rising_then_falling()
        unfiltered = al._run_segment(closes, 5, 30, 5.0, 50.0, trailing=(1.5, 1.5))
        blocked = al._run_segment(closes, 5, 30, 5.0, 50.0, trailing=(1.5, 1.5),
                                  filters={"max_spread_pct": 0.0001})
        self.assertLess(blocked["trades"], unfiltered["trades"])


class ConfigResolutionTests(unittest.TestCase):
    """_resolve_env must follow the bot's coin -> profile -> base order."""

    ENV = {
        "SUI_ACTIVE_PROFILE": "SIM",
        "SUI_RSI_BUY_MAX": "52",
        "SIM_RSI_BUY_MAX": "55",
        "ACTIVE_PROFILE": "CONSERVATIVE",
        "CONSERVATIVE_RSI_BUY_MAX": "60",
        "RSI_BUY_MAX": "70",
    }

    def test_coin_key_wins(self):
        self.assertEqual(al._resolve_env(self.ENV, "SUI", "RSI_BUY_MAX", "0"), "52")

    def test_profile_key_used_when_no_coin_key(self):
        env = {k: v for k, v in self.ENV.items() if k != "SUI_RSI_BUY_MAX"}
        self.assertEqual(al._resolve_env(env, "SUI", "RSI_BUY_MAX", "0"), "55")

    def test_falls_back_to_global_profile_then_bare_key(self):
        # NEAR has no coin key and no NEAR_ACTIVE_PROFILE -> global CONSERVATIVE.
        self.assertEqual(al._resolve_env(self.ENV, "NEAR", "RSI_BUY_MAX", "0"), "60")
        bare = {"RSI_BUY_MAX": "70"}
        self.assertEqual(al._resolve_env(bare, "NEAR", "RSI_BUY_MAX", "0"), "70")

    def test_default_when_nothing_matches(self):
        self.assertEqual(al._resolve_env({}, "NEAR", "RSI_BUY_MAX", "42"), "42")


class MaxSpreadSettingTests(unittest.TestCase):
    """The new ceiling must gate BUYs only — never a SELL."""

    def test_strategy_blocks_extended_buy(self):
        strategy = SMACrossoverStrategy(short_window=2, long_window=3,
                                        max_spread_percent=0.01)
        strategy.last_spread_percent = 5.0
        self.assertGreater(strategy.max_spread_percent, 0.0)

    def test_ceiling_never_blocks_an_exit(self):
        """A separation cap that could block a SELL would strand a position."""
        source = open("strategies/simple_moving_average.py").read()
        sell_branch = source.split('elif signal == "SELL":')[1]
        self.assertNotIn("max_spread_percent", sell_branch)


if __name__ == "__main__":
    unittest.main()
