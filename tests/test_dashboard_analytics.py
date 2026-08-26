"""
The dashboard reports money figures, so wrong numbers there are worse than no
dashboard. These cover the two places it can silently mislead: pricing an open
position from a stale source, and aggregating an edge that isn't there.
"""
import csv
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import dashboard


def trade(**kw):
    """A completed-trade row with the fields build_analytics reads."""
    row = {
        "pair": "SUI-USDC", "mode": "Simulated", "asset_class": "crypto",
        "entry_time": "2026-08-01T00:00:00+00:00",
        "exit_time": "2026-08-02T00:00:00+00:00",
        "entry_price": 1.0, "exit_price": 1.0,
        "pnl_usd": 0.0, "fees_usd": 0.12,
        "entry_reason": "SMA Bullish Cross", "exit_reason": "SMA Bearish Cross",
        "gross_pct": 0.0, "net_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0,
        "has_excursion": True,
    }
    row.update(kw)
    return row


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.build = dashboard.DashboardHandler.build_analytics

    def analyse(self, trades):
        return self.build(None, trades)

    def test_no_trades_returns_empty_shape_not_a_crash(self):
        result = self.analyse([])
        self.assertEqual(result["trade_count"], 0)
        self.assertIsNone(result["waterfall"])
        self.assertEqual(result["exit_reasons"], [])
        self.assertEqual(result["equity_curve"], [])

    def test_waterfall_reconstructs_gross_from_net_and_fees(self):
        trades = [
            trade(pnl_usd=-0.30, fees_usd=0.12),
            trade(pnl_usd=0.90, fees_usd=0.12),
        ]
        w = self.analyse(trades)["waterfall"]
        self.assertAlmostEqual(w["net"], 0.60)
        self.assertAlmostEqual(w["fees"], 0.24)
        self.assertAlmostEqual(w["gross"], 0.84)
        self.assertAlmostEqual(w["win_rate"], 50.0)

    def test_positive_gross_with_negative_net_is_representable(self):
        """The whole point of the panel: an edge that fees turn into a loss."""
        trades = [trade(pnl_usd=-0.10, fees_usd=0.12) for _ in range(10)]
        w = self.analyse(trades)["waterfall"]
        self.assertLess(w["net"], 0)
        self.assertGreater(w["gross"], 0)

    def test_stock_trades_are_excluded_from_crypto_fee_analysis(self):
        """Commission-free stock fills would understate crypto fee drag."""
        trades = [
            trade(pnl_usd=-0.30, fees_usd=0.12),
            trade(asset_class="stock", pair="SPY", pnl_usd=0.01, fees_usd=0.0,
                  gross_pct=0.15, net_pct=0.15, has_excursion=False),
        ]
        result = self.analyse(trades)
        self.assertEqual(result["trade_count"], 1)
        self.assertAlmostEqual(result["waterfall"]["fees"], 0.12)

    def test_live_trades_are_excluded(self):
        trades = [trade(), trade(mode="Live", pnl_usd=99.0)]
        self.assertEqual(self.analyse(trades)["trade_count"], 1)

    def test_fee_floor_is_measured_not_assumed(self):
        """Derived from realised gross-to-net, so it tracks what was charged."""
        trades = [
            trade(gross_pct=2.0, net_pct=0.8),
            trade(gross_pct=1.0, net_pct=-0.2),
        ]
        self.assertAlmostEqual(self.analyse(trades)["fee_floor_pct"], 1.2)

    def test_fee_floor_ignores_rows_without_excursion_data(self):
        trades = [
            trade(gross_pct=2.0, net_pct=0.8),
            trade(gross_pct=0.0, net_pct=0.0, has_excursion=False),
        ]
        self.assertAlmostEqual(self.analyse(trades)["fee_floor_pct"], 1.2)

    def test_exit_reasons_ranked_by_count_with_averages(self):
        trades = (
            [trade(exit_reason="SMA Bearish Cross", net_pct=-1.5, pnl_usd=-0.15)] * 3
            + [trade(exit_reason="Take Profit", net_pct=9.0, pnl_usd=0.90)]
        )
        reasons = self.analyse(trades)["exit_reasons"]
        self.assertEqual(reasons[0]["reason"], "SMA Bearish Cross")
        self.assertEqual(reasons[0]["count"], 3)
        self.assertAlmostEqual(reasons[0]["share"], 75.0)
        self.assertAlmostEqual(reasons[0]["avg_net_pct"], -1.5)
        self.assertAlmostEqual(reasons[1]["avg_net_pct"], 9.0)

    def test_entry_quality_counts_entries_clearing_the_fee_floor(self):
        trades = [
            trade(gross_pct=2.0, net_pct=0.8, mfe_pct=3.0),   # clears 1.2
            trade(gross_pct=2.0, net_pct=0.8, mfe_pct=0.5),   # does not
        ]
        q = self.analyse(trades)["entry_quality"]
        self.assertEqual(q["measured"], 2)
        self.assertEqual(q["cleared_fee_floor"], 1)
        self.assertAlmostEqual(q["cleared_pct"], 50.0)

    def test_equity_curve_is_chronological_and_cumulative(self):
        trades = [
            trade(exit_time="2026-08-03T00:00:00+00:00", pnl_usd=1.0),
            trade(exit_time="2026-08-01T00:00:00+00:00", pnl_usd=-0.5),
            trade(exit_time="2026-08-02T00:00:00+00:00", pnl_usd=0.25),
        ]
        curve = self.analyse(trades)["equity_curve"]
        self.assertEqual([round(p["cumulative_pnl"], 2) for p in curve],
                         [-0.5, -0.25, 0.75])

    def test_equity_curve_skips_trades_with_no_exit_time(self):
        trades = [trade(pnl_usd=1.0), trade(exit_time="", pnl_usd=5.0)]
        self.assertEqual(len(self.analyse(trades)["equity_curve"]), 1)


class GraduationTests(unittest.TestCase):
    """
    Progress toward live trading. Thresholds come from auto_learner, so a
    change to the gate must move this display with it.
    """

    def build(self, trades):
        return dashboard.DashboardHandler.build_graduation(trades)

    def test_thresholds_come_from_the_shared_module(self):
        from utils import graduation
        coins = self.build([trade(pnl_usd=1.0)])
        labels = " ".join(c["label"] for c in coins[0]["checks"])
        self.assertIn(str(graduation.MIN_TRADES), labels)
        self.assertIn(f"{graduation.MIN_WIN_RATE:.0f}", labels)
        self.assertIn(f"{graduation.MIN_SHARPE:.2f}", labels)

    def test_all_five_criteria_are_reported(self):
        coins = self.build([trade(pnl_usd=1.0)])
        self.assertEqual(coins[0]["total"], 5)
        self.assertEqual(len(coins[0]["checks"]), 5)

    def test_dashboard_score_matches_the_nightly_report(self):
        """
        The bug this guards: the emailed report scored five criteria out of 100
        while the dashboard counted four, so one coin showed two readiness
        figures. Both must now render the same number.
        """
        import sim_report
        pnls = [0.8, -0.2, 0.5, -0.1, 0.6]
        trades = [trade(pnl_usd=p) for p in pnls]
        report_score, _, report_ready = sim_report.graduation(
            [{"pnl_usd": p} for p in pnls])
        coin = self.build(trades)[0]
        self.assertEqual(coin["score"], report_score)
        self.assertEqual(coin["ready"], report_ready)

    def test_promotion_gate_agrees_with_the_displayed_score(self):
        """
        check_graduation previously ignored Sharpe, so it could promote a coin
        the emailed score showed as not ready.
        """
        from utils.graduation import evaluate
        # Meets the four original criteria but returns are lumpy.
        pnls = [2.40] * 12 + [-2.40] * 8
        result = evaluate(pnls)
        four_only = (len(pnls) >= 20
                     and 100 * sum(1 for p in pnls if p > 0) / len(pnls) >= 55
                     and sum(pnls) > 0 and min(pnls) >= -2.50)
        self.assertTrue(four_only, "fixture should pass the four original checks")
        self.assertFalse(result["criteria"][4]["passed"], "Sharpe should fail")
        self.assertFalse(result["ready"], "gate must not promote on a failed criterion")

    def test_coin_meeting_every_criterion_is_ready(self):
        from utils import graduation
        # 20 trades, 80% wins, positive net, consistent returns.
        pnls = ([0.60, 0.55, 0.65, 0.50] * 4) + [-0.15] * 4
        self.assertGreaterEqual(len(pnls), graduation.MIN_TRADES)
        coin = self.build([trade(pnl_usd=p) for p in pnls])[0]
        self.assertTrue(coin["ready"], coin["checks"])
        self.assertEqual(coin["passed"], 5)
        self.assertEqual(coin["score"], graduation.MAX_SCORE)

    def test_identical_winning_trades_are_perfect_consistency_not_zero(self):
        """Zero variance with a positive mean must not read as the worst case."""
        from utils import graduation
        self.assertEqual(graduation.per_trade_sharpe([0.5] * 20), float("inf"))
        self.assertEqual(graduation.per_trade_sharpe([-0.5] * 20), float("-inf"))
        coin = self.build([trade(pnl_usd=0.50) for _ in range(20)])[0]
        self.assertTrue(coin["ready"])

    def test_too_few_trades_blocks_an_otherwise_perfect_coin(self):
        coin = self.build([trade(pnl_usd=0.50) for _ in range(3)])[0]
        self.assertFalse(coin["ready"])
        self.assertFalse(coin["checks"][0]["passed"])
        self.assertTrue(coin["checks"][1]["passed"])

    def test_a_single_large_loss_blocks_graduation(self):
        import auto_learner
        trades = [trade(pnl_usd=1.0) for _ in range(auto_learner.GRADUATION_MIN_TRADES)]
        trades.append(trade(pnl_usd=auto_learner.GRADUATION_WORST_TRADE_FLOOR - 1.0))
        coin = self.build(trades)[0]
        self.assertFalse(coin["ready"])
        self.assertFalse(coin["checks"][3]["passed"])

    def test_coins_are_ranked_closest_to_eligible_first(self):
        trades = (
            [trade(pair="LOSER-USDC", pnl_usd=-1.0)]
            + [trade(pair="CLOSE-USDC", pnl_usd=1.0) for _ in range(5)]
        )
        coins = self.build(trades)
        self.assertEqual(coins[0]["pair"], "CLOSE-USDC")

    def test_no_trades_yields_no_coins(self):
        self.assertEqual(self.build([]), [])


class SignalFunnelTests(unittest.TestCase):
    """
    Crossovers seen vs. taken. This exists to make an over-tight filter visible:
    in the trade ledger, a filter blocking everything is indistinguishable from
    a quiet market, because both simply show no new trades.
    """

    NOW = 1_000_000.0

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def write_log(self, pair, rows):
        path = os.path.join(self.tmp, f"signal_log_{pair}.csv")
        headers = ["timestamp", "pair", "candle_time", "close", "position",
                   "raw_signal", "final_signal", "blocked_by",
                   "sma_short", "sma_long", "spread_pct", "rsi", "ema"]
        with open(path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})

    def row(self, offset_seconds=0, raw="HOLD", final="HOLD", blocked=""):
        stamp = datetime.fromtimestamp(self.NOW - offset_seconds, timezone.utc)
        return {"timestamp": stamp.isoformat(), "raw_signal": raw,
                "final_signal": final, "blocked_by": blocked}

    def funnel(self, **kw):
        handler = dashboard.DashboardHandler
        return handler.build_signal_funnel(
            None, log_dir=self.tmp, now=self.NOW, **kw)

    def test_no_logs_reports_nothing_rather_than_failing(self):
        f = self.funnel()
        self.assertEqual(f["candles"], 0)
        self.assertEqual(f["pairs"], 0)
        self.assertEqual(f["blocked"], [])

    def test_counts_candles_crossovers_and_entries(self):
        self.write_log("sui-usdc", [
            self.row(), self.row(),
            self.row(raw="BUY", final="BUY"),
            self.row(raw="BUY", final="HOLD", blocked="RSI"),
        ])
        f = self.funnel()
        self.assertEqual(f["candles"], 4)
        self.assertEqual(f["crossovers"], 2)
        self.assertEqual(f["taken"], 1)
        self.assertEqual(f["blocked_total"], 1)

    def test_blocks_are_grouped_by_reason_and_ranked(self):
        self.write_log("sui-usdc", [
            self.row(raw="BUY", blocked="RSI"),
            self.row(raw="BUY", blocked="RSI"),
            self.row(raw="BUY", blocked="EMA"),
        ])
        blocked = self.funnel()["blocked"]
        self.assertEqual(blocked[0], {"reason": "RSI", "count": 2})
        self.assertEqual(blocked[1], {"reason": "EMA", "count": 1})

    def test_every_crossover_blocked_is_representable(self):
        """The failure mode the panel exists to surface."""
        self.write_log("sui-usdc", [self.row(raw="BUY", blocked="RSI") for _ in range(5)])
        f = self.funnel()
        self.assertEqual(f["crossovers"], 5)
        self.assertEqual(f["taken"], 0)
        self.assertEqual(f["blocked_total"], f["crossovers"])

    def test_rows_outside_the_window_are_excluded(self):
        self.write_log("sui-usdc", [
            self.row(offset_seconds=2 * 86400, raw="BUY", final="BUY"),
            self.row(offset_seconds=30 * 86400, raw="BUY", final="BUY"),
        ])
        self.assertEqual(self.funnel(window_days=7)["crossovers"], 1)
        self.assertEqual(self.funnel(window_days=60)["crossovers"], 2)

    def test_aggregates_across_pairs_and_counts_them(self):
        self.write_log("sui-usdc", [self.row(raw="BUY", final="BUY")])
        self.write_log("link-usdc", [self.row(raw="BUY", blocked="EMA")])
        f = self.funnel()
        self.assertEqual(f["pairs"], 2)
        self.assertEqual(f["crossovers"], 2)
        self.assertEqual(f["taken"], 1)

    def test_reports_age_of_last_crossover_and_entry(self):
        self.write_log("sui-usdc", [
            self.row(offset_seconds=7200, raw="BUY", final="BUY"),
            self.row(offset_seconds=3600, raw="BUY", blocked="RSI"),
        ])
        f = self.funnel()
        self.assertAlmostEqual(f["last_crossover_age"], 3600, delta=2)
        self.assertAlmostEqual(f["last_entry_age"], 7200, delta=2)

    def test_ages_are_none_when_nothing_has_happened(self):
        self.write_log("sui-usdc", [self.row(), self.row()])
        f = self.funnel()
        self.assertIsNone(f["last_crossover_age"])
        self.assertIsNone(f["last_entry_age"])

    def test_a_blocked_crossover_is_not_counted_as_taken(self):
        self.write_log("sui-usdc", [self.row(raw="BUY", final="BUY", blocked="RSI")])
        self.assertEqual(self.funnel()["taken"], 0)

    def test_unreadable_log_does_not_abort_the_other_pairs(self):
        self.write_log("sui-usdc", [self.row(raw="BUY", final="BUY")])
        os.mkdir(os.path.join(self.tmp, "signal_log_broken.csv"))
        self.assertEqual(self.funnel()["crossovers"], 1)


class PriceResolverTests(unittest.TestCase):
    """
    Position P&L was previously computed from a scanner file that could be
    weeks old, with no way to tell. Every price now carries its origin.
    """

    def setUp(self):
        self.resolver = dashboard.PriceResolver("/nonexistent")

    def test_usdc_pairs_fall_back_to_the_usd_product(self):
        self.assertEqual(
            dashboard.PriceResolver._candidate_products("LINK-USDC"),
            ["LINK-USDC", "LINK-USD"])

    def test_non_usdc_pair_is_tried_alone(self):
        self.assertEqual(
            dashboard.PriceResolver._candidate_products("BTC-USD"), ["BTC-USD"])

    def test_live_price_is_used_and_reported_as_live(self):
        with mock.patch.object(self.resolver, "_live_price", return_value=11.5):
            price, source, age = self.resolver.get("LINK-USDC", 9.0)
        self.assertEqual((price, source, age), (11.5, "live", 0))

    def test_falls_back_to_last_candle_when_live_unavailable(self):
        with mock.patch.object(self.resolver, "_live_price", return_value=None), \
             mock.patch.object(self.resolver, "_last_candle_price",
                               return_value=(11.2, 1800.0)):
            price, source, age = self.resolver.get("LINK-USDC", 9.0)
        self.assertEqual((price, source, age), (11.2, "candle", 1800.0))

    def test_entry_price_is_the_last_resort_and_is_labelled(self):
        """Falling back to entry makes P&L read as zero — it must not look live."""
        with mock.patch.object(self.resolver, "_live_price", return_value=None), \
             mock.patch.object(self.resolver, "_last_candle_price", return_value=None):
            price, source, age = self.resolver.get("LINK-USDC", 9.0)
        self.assertEqual((price, source, age), (9.0, "entry", None))

    def test_live_price_is_cached_rather_than_refetched_each_poll(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"price": "11.5"}
        with mock.patch("dashboard.requests.get", return_value=response) as get:
            self.resolver.get("LINK-USDC", 9.0)
            self.resolver.get("LINK-USDC", 9.0)
        self.assertEqual(get.call_count, 1)

    def test_network_failure_does_not_raise(self):
        with mock.patch("dashboard.requests.get",
                        side_effect=dashboard.requests.RequestException("down")):
            price, source, _ = self.resolver.get("LINK-USDC", 9.0)
        self.assertEqual((price, source), (9.0, "entry"))

    def test_failed_pair_is_backed_off_instead_of_retried_every_poll(self):
        response = mock.Mock(status_code=404)
        with mock.patch("dashboard.requests.get", return_value=response) as get:
            self.resolver.get("FAKE-USDC", 1.0)
            first = get.call_count
            self.resolver.get("FAKE-USDC", 1.0)
        self.assertEqual(get.call_count, first)


if __name__ == "__main__":
    unittest.main()
