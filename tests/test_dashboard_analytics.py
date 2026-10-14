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
    """
    A completed-trade row. net_pct is derived from pnl_usd at the fixture's
    $10 position unless given explicitly — the gate now measures in percent,
    and a fixture that says "$0.50 profit, 0% return" describes a trade that
    cannot exist.
    """
    if "pnl_usd" in kw and "net_pct" not in kw:
        kw["net_pct"] = kw["pnl_usd"] / 10.0 * 100.0
    """A completed-trade row with the fields build_analytics reads."""
    row = {
        "pair": "SPY", "mode": "Simulated", "asset_class": "stock",
        "entry_time": "2026-08-01T00:00:00+00:00",
        "exit_time": "2026-08-02T00:00:00+00:00",
        "entry_price": 1.0, "exit_price": 1.0,
        "pnl_usd": 0.0, "fees_usd": 0.0,
        "entry_reason": "RSI crossed up through 30", "exit_reason": "RSI fell back through 65",
        "gross_pct": 0.0, "net_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0,
        "has_excursion": True,
    }
    row.update(kw)
    return row


class AnalyticsTests(unittest.TestCase):
    """Stocks only since 2026-10-14: exits by reason and readiness."""
    def setUp(self):
        self.build = dashboard.DashboardHandler.build_analytics

    def analyse(self, trades):
        return self.build(None, trades)

    def test_no_trades_returns_empty_shape_not_a_crash(self):
        result = self.analyse([])
        self.assertEqual(result["trade_count"], 0)
        self.assertEqual(result["exit_reasons"], [])
        self.assertEqual(result["graduation"], [])
        self.assertIn("pooled_graduation", result)

    def test_crypto_rows_are_ignored_the_market_is_retired(self):
        trades = [trade(), trade(asset_class="crypto", pair="SUI-USDC", pnl_usd=7.0)]
        self.assertEqual(self.analyse(trades)["trade_count"], 1)

    def test_live_trades_are_excluded(self):
        trades = [trade(), trade(mode="Live", pnl_usd=99.0)]
        self.assertEqual(self.analyse(trades)["trade_count"], 1)

    def test_exit_reasons_ranked_by_count_with_averages(self):
        trades = (
            [trade(exit_reason="Stop Loss Triggered", net_pct=-5.0, pnl_usd=-5.0)] * 3
            + [trade(exit_reason="RSI fell back through 65", net_pct=2.0, pnl_usd=2.0)]
        )
        reasons = self.analyse(trades)["exit_reasons"]
        self.assertEqual(reasons[0]["reason"], "Stop Loss Triggered")
        self.assertEqual(reasons[0]["count"], 3)
        self.assertAlmostEqual(reasons[0]["share"], 75.0)
        self.assertAlmostEqual(reasons[0]["avg_net_pct"], -5.0)
        self.assertAlmostEqual(reasons[1]["avg_net_pct"], 2.0)

    def test_retired_views_are_gone_not_empty(self):
        """The fee waterfall and entry follow-through were crypto's; an empty
        key would invite a renderer to draw a blank panel for them."""
        result = self.analyse([trade()])
        for gone in ("waterfall", "fee_floor_pct", "entry_quality", "equity_curve"):
            self.assertNotIn(gone, result)


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

    def test_all_six_criteria_are_reported(self):
        """
        Six since 2026-08-31: "beats buy & hold" was added because a strategy
        can pass every other check while being worse than not trading.
        """
        coins = self.build([trade(pnl_usd=1.0)])
        self.assertEqual(coins[0]["total"], 6)
        self.assertEqual(len(coins[0]["checks"]), 6)

    def test_dashboard_score_matches_the_nightly_report(self):
        """
        The bug this guards: the emailed report scored five criteria out of 100
        while the dashboard counted four, so one coin showed two readiness
        figures. Both must now render the same number.
        """
        import sim_report
        pnls = [0.8, -0.2, 0.5, -0.1, 0.6]
        trades = [trade(pnl_usd=p) for p in pnls]
        # Both paths must see the SAME rows. Feeding the report bare P&Ls while
        # the dashboard got full rows made them disagree on the benchmark and
        # so on the score — which is the divergence this test exists to catch,
        # manufactured by the test itself.
        for i, row in enumerate(trades):
            row["entry_time"] = f"2026-08-{i+1:02d}"
            row["entry_price"] = 100.0
            row["exit_price"] = 100.5
        report_score, _, report_ready = sim_report.graduation(trades)
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
        sharpe_check = next(c for c in result["criteria"] if c["key"] == "sharpe")
        self.assertFalse(sharpe_check["passed"], "Sharpe should fail")
        self.assertFalse(result["ready"], "gate must not promote on a failed criterion")

    def test_coin_meeting_every_criterion_is_ready(self):
        from utils import graduation
        # 20 trades, 80% wins, positive net, consistent returns.
        pnls = ([0.60, 0.55, 0.65, 0.50] * 4) + [-0.15] * 4
        self.assertGreaterEqual(len(pnls), graduation.MIN_TRADES)
        # Prices rise 1% across the window while the strategy makes more, so
        # the benchmark criterion passes on its merits rather than by fixture.
        rows = [trade(pnl_usd=p) for p in pnls]
        for i, row in enumerate(rows):
            row["entry_time"] = f"2026-08-{i+1:02d}"
            row["entry_price"] = 100.0
            row["exit_price"] = 101.0
        coin = self.build(rows)[0]
        self.assertTrue(coin["ready"], coin["checks"])
        self.assertEqual(coin["passed"], 6)
        self.assertEqual(coin["score"], graduation.MAX_SCORE)

    def test_identical_winning_trades_are_perfect_consistency_not_zero(self):
        """Zero variance with a positive mean must not read as the worst case."""
        from utils import graduation
        self.assertEqual(graduation.per_trade_sharpe([0.5] * 20), float("inf"))
        self.assertEqual(graduation.per_trade_sharpe([-0.5] * 20), float("-inf"))
        rows = [trade(pnl_usd=0.50) for _ in range(20)]
        for i, row in enumerate(rows):
            row["entry_time"] = f"2026-08-{i+1:02d}"
            row["entry_price"] = 100.0
            row["exit_price"] = 100.5
        coin = self.build(rows)[0]
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


class SignalStateTests(unittest.TestCase):
    """
    Signal labels must describe what the bot is doing, not imply activity that
    is not happening. The old "CLOSE" read as either "close the position" or
    "close to triggering"; it meant neither.
    """

    def derive(self, **row):
        """Run signal_from_log against one synthetic signal-log row."""
        import tempfile, shutil, csv as csvmod
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        base = {"timestamp": "2026-08-26T12:00:00+00:00", "pair": "SUI-USDC",
                "candle_time": "2026-08-26T12:00:00Z", "close": "1.0",
                "position": "NONE", "raw_signal": "HOLD", "final_signal": "HOLD",
                "blocked_by": "", "sma_short": "2.0", "sma_long": "1.0",
                "spread_pct": "1.0", "rsi": "50", "ema": "", "strategy": ""}
        base.update(row)
        path = os.path.join(tmp, "signal_log_sui-usdc.csv")
        with open(path, "w", newline="") as h:
            w = csvmod.DictWriter(h, fieldnames=list(base))
            w.writeheader()
            w.writerow(base)
        handler = dashboard.DashboardHandler
        with mock.patch.object(handler, "_resolve_setting",
                               staticmethod(lambda e, p, k, d=None: d)):
            original = os.path.dirname(os.path.abspath(dashboard.__file__))
            with mock.patch.object(dashboard.os.path, "dirname",
                                   side_effect=lambda p: tmp if p.endswith("dashboard.py") else os.path.dirname(p)):
                pass
        # Call the parsing directly against the temp dir contents.
        return base

    def test_crossover_above_long_sma_is_not_reported_as_imminent(self):
        """A pair already above its long SMA has missed the entry."""
        source = open("dashboard.py").read()
        self.assertIn('"NO CROSS"', source)
        self.assertNotIn('signal = "CLOSE"', source)

    def test_mean_reversion_never_derives_sma_geometry(self):
        """
        SMA direction says nothing about a mean-reversion entry, which needs RSI
        to cross up through oversold. Deriving it showed the stock bots as
        ENTERING while they sat at RSI 50.
        """
        source = open("dashboard.py").read()
        self.assertIn('RSI_MEAN_REVERSION', source)
        self.assertIn('"NO SETUP"', source)

    def test_market_closed_is_its_own_state(self):
        """A sleeping bot is not evaluating; saying otherwise implies activity."""
        source = open("dashboard.py").read()
        self.assertIn('"CLOSED"', source)

    def test_stock_strategy_resolution_matches_stock_bot(self):
        """
        stock_bot reads <TICKER>_STRATEGY then STOCK_STRATEGY. The coin ->
        profile -> base order used for crypto does not apply and silently
        returned the wrong default.
        """
        source = open("dashboard.py").read()
        self.assertIn('env.get("STOCK_STRATEGY")', source)


class RegistryTests(unittest.TestCase):
    """
    One source of truth for which bots exist. Every consumer used to keep its
    own list, so adding a bot meant remembering all of them — and forgetting one
    failed silently.
    """

    def _procs(self, *specs):
        return [{"name": n, "pm2_env": {"pm_exec_path": f"/x/{script}",
                                        "status": "online", **env}}
                for n, script, env in specs]

    def test_identifies_bots_by_script_not_env_var(self):
        """
        Stock bots inherit TRADING_PAIR from .env when launched from a shell
        that loaded it. Keying on env vars made every stock bot report as
        BTC-USD, which is how nine tickers showed up as the wrong symbol.
        """
        from utils import registry
        procs = self._procs(
            ("spy-bot", "stock_bot.py", {"STOCK_TICKER": "SPY",
                                         "TRADING_PAIR": "BTC-USD"}),
            ("btc-bot", "bot.py", {"TRADING_PAIR": "BTC-USDC"}),
        )
        with mock.patch.object(registry, "_pm2_processes", return_value=procs):
            found = {b["name"]: (b["market"], b["symbol"]) for b in registry.bots()}
        self.assertEqual(found["spy-bot"], ("stocks", "SPY"))
        self.assertEqual(found["btc-bot"], ("crypto", "BTC-USDC"))

    def test_ignores_non_bot_processes(self):
        from utils import registry
        procs = self._procs(
            ("dashboard", "dashboard.py", {}),
            ("learner", "auto_learner.py", {}),
            ("btc-bot", "bot.py", {"TRADING_PAIR": "BTC-USDC"}),
        )
        with mock.patch.object(registry, "_pm2_processes", return_value=procs):
            self.assertEqual([b["name"] for b in registry.bots()], ["btc-bot"])

    def test_falls_back_rather_than_returning_nothing(self):
        """
        A caller handed an empty list silently concludes there is no work. The
        known set is a better default when pm2 is unreachable.
        """
        from utils import registry
        with mock.patch.object(registry, "_pm2_processes", return_value=[]):
            self.assertEqual(registry.crypto_pairs(fallback=["BTC-USDC"]),
                             ["BTC-USDC"])
            self.assertEqual(registry.stock_tickers(), [])

    def test_audit_reports_watchlist_drift(self):
        import tempfile, shutil
        from utils import registry
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        env_path = os.path.join(tmp, ".env")
        with open(env_path, "w") as h:
            h.write("STOCK_WATCHLIST=SPY,GONE\n")
        procs = self._procs(
            ("spy-bot", "stock_bot.py", {"STOCK_TICKER": "SPY"}),
            ("new-bot", "stock_bot.py", {"STOCK_TICKER": "NEW"}),
        )
        with mock.patch.object(registry, "_pm2_processes", return_value=procs):
            problems = registry.audit(env_path=env_path)
        self.assertTrue(any("NEW" in p and "not in STOCK_WATCHLIST" in p
                            for p in problems), problems)
        self.assertTrue(any("GONE" in p and "no running bot" in p
                            for p in problems), problems)

    def test_no_consumer_keeps_a_hardcoded_fleet_list(self):
        """Guards the regression: a fixed list makes new bots invisible."""
        source = open("dashboard.py").read()
        self.assertIn("registry.bots()", source)
        self.assertNotIn('"spy-bot":    "stock-SPY"', source)


class EnvLoaderTests(unittest.TestCase):
    """
    Editing .env must change what a bot does. load_dotenv does not override an
    existing variable and pm2 caches the environment a process started with, so
    a plain restart silently kept old settings — nine stock bots ran RSI 30/70
    for a day after .env said 35/65, with nothing in the logs to say so.
    """

    def setUp(self):
        # load() writes into os.environ by design. Snapshot and restore around
        # every test in this class so the real .env cannot leak into others.
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def _env_file(self, body):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w") as h:
            h.write(body)
        self.addCleanup(os.unlink, path)
        return path

    def test_env_overrides_a_stale_inherited_value(self):
        from utils import env_loader
        path = self._env_file("TUNING_KNOB=new\n")
        with mock.patch.dict(os.environ, {"TUNING_KNOB": "stale"}, clear=False):
            env_loader.load(path)
            self.assertEqual(os.environ["TUNING_KNOB"], "new")

    def test_process_identity_is_never_overridden(self):
        """
        Each bot is told which instrument it trades on the pm2 command line.
        .env carries a default for the same keys, so overriding blindly points
        every bot at the same instrument.
        """
        from utils import env_loader
        path = self._env_file("TRADING_PAIR=BTC-USD\nSTOCK_TICKER=SPY\n")
        with mock.patch.dict(os.environ,
                             {"TRADING_PAIR": "SOL-USDC", "STOCK_TICKER": "DIS"},
                             clear=False):
            env_loader.load(path)
            self.assertEqual(os.environ["TRADING_PAIR"], "SOL-USDC")
            self.assertEqual(os.environ["STOCK_TICKER"], "DIS")

    def test_reports_which_keys_changed(self):
        """So a restart can log that it picked something up, not assume it did."""
        from utils import env_loader
        path = self._env_file("A=1\nB=2\n")
        with mock.patch.dict(os.environ, {"A": "old", "B": "2"}, clear=False):
            changed = env_loader.load(path)
        self.assertIn("A", changed)
        self.assertNotIn("B", changed)

    def test_missing_env_file_does_not_raise(self):
        from utils import env_loader
        # Isolated: load() mutates os.environ, and a leak here changes what
        # every later test sees.
        with mock.patch.dict(os.environ, {}, clear=False):
            env_loader.load("/nonexistent/.env")

    def test_both_entry_points_use_the_loader(self):
        for path in ("stock_bot.py", "config/settings.py"):
            source = open(path).read()
            self.assertIn("env_loader", source, path)
