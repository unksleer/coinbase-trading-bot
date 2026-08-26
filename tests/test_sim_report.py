"""
The nightly report is delivered as a plain-text email, so its layout is load
bearing: anything wider than the client's wrap point breaks every aligned
column below it.
"""
import io
import unittest
from contextlib import redirect_stdout

import sim_report


def trades(pnls, fees=0.12, reason="SMA Bearish Cross", hold=20.0):
    return [{"pair": "SUI-USDC", "exit_reason": reason, "pnl_usd": p,
             "fees_usd": fees, "hold_hours": hold,
             "gross_pct": 0.0, "net_pct": 0.0} for p in pnls]


def render(fn, *args):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        fn(*args)
    return buffer.getvalue()


class LayoutTests(unittest.TestCase):
    def test_nothing_exceeds_the_wrap_width(self):
        """A single long line wrapping destroys the alignment of the table."""
        out = render(sim_report.print_report, "sui-usdc", trades([0.5, -0.2, 0.9, -0.1]))
        out += render(sim_report.print_leaderboard, {"sui-usdc": trades([0.5, -0.2])})
        out += render(sim_report.print_portfolio, {"sui-usdc": trades([0.5, -0.2])})
        for line in out.splitlines():
            self.assertLessEqual(len(line), sim_report.W,
                                 f"line exceeds {sim_report.W} cols: {line!r}")

    def test_blocker_lines_fit_when_every_criterion_fails(self):
        """Blockers are the longest strings in the report."""
        out = render(sim_report.print_report, "sui-usdc", trades([-3.0, -0.2, -0.9]))
        self.assertIn("still needed", out)
        for line in out.splitlines():
            self.assertLessEqual(len(line), sim_report.W, repr(line))

    def test_bar_is_fixed_width_regardless_of_value(self):
        """Variable-width bars would misalign the score column."""
        widths = {len(sim_report.bar(v / 20, 10)) for v in range(21)}
        self.assertEqual(widths, {10})

    def test_bar_clamps_out_of_range_values(self):
        self.assertEqual(len(sim_report.bar(5.0, 10)), 10)
        self.assertEqual(len(sim_report.bar(-2.0, 10)), 10)


class ContentTests(unittest.TestCase):
    def test_portfolio_leads_with_gross_fees_net(self):
        out = render(sim_report.print_portfolio, {"sui-usdc": trades([0.5, -0.2])})
        for token in ("gross", "fees", "net"):
            self.assertIn(token, out)

    def test_diagnoses_a_cost_problem_distinctly_from_a_signal_problem(self):
        # pnl_usd is already net of fees, so a cost problem is a small net
        # loss whose fees exceed it: gross = net + fees > 0.
        cost = render(sim_report.print_portfolio,
                      {"a": trades([-0.10, -0.10, -0.10], fees=0.20)})
        self.assertIn("direction is right", cost)

        # Negative before fees -> the signal itself.
        signal = render(sim_report.print_portfolio,
                        {"a": trades([-0.90, -0.80], fees=0.01)})
        self.assertIn("entry signal is the problem", signal)

        # Positive after fees.
        good = render(sim_report.print_portfolio,
                      {"a": trades([1.00, 1.00], fees=0.05)})
        self.assertIn("profitable after costs", good)

    def test_coin_is_singular_for_one_coin(self):
        out = render(sim_report.print_portfolio, {"sui-usdc": trades([0.5])})
        self.assertIn("1 coin ", out)
        self.assertNotIn("1 coins", out)

    def test_leaderboard_ranks_closest_to_graduating_first(self):
        strong = trades([0.6, 0.5, 0.55, 0.6, -0.1])
        weak = trades([-0.5, -0.4, -0.6])
        out = render(sim_report.print_leaderboard, {"weak": weak, "strong": strong})
        self.assertLess(out.index("STRONG"), out.index("WEAK"))

    def test_empty_coin_renders_nothing_rather_than_a_hollow_block(self):
        self.assertEqual(render(sim_report.print_report, "sui-usdc", []), "")

    def test_infinite_profit_factor_does_not_break_formatting(self):
        """A coin with no losses divides by zero on profit factor."""
        out = render(sim_report.print_report, "sui-usdc", trades([0.5, 0.6, 0.7]))
        self.assertIn("inf", out)
        for line in out.splitlines():
            self.assertLessEqual(len(line), sim_report.W, repr(line))

    def test_score_matches_the_shared_graduation_module(self):
        from utils.graduation import evaluate, MAX_SCORE
        pnls = [0.5, -0.2, 0.9, -0.1]
        out = render(sim_report.print_report, "sui-usdc", trades(pnls))
        self.assertIn(f"{evaluate(pnls)['score']}/{MAX_SCORE} pts", out)


class LegacyFileTests(unittest.TestCase):
    def test_shared_dry_csv_is_not_rendered_as_a_coin(self):
        """
        completed_trades_dry.csv carries no coin in its name, so it rendered as
        a phantom 'DRY.CSV' coin with 0 trades at the top of every report.
        """
        source = open("sim_report.py").read()
        main_body = source[source.index("def main("):]
        self.assertNotIn('files.insert(0, shared)', main_body)
        self.assertNotIn('completed_trades_dry.csv"', main_body.replace(
            "# its name does not carry a coin", ""))


if __name__ == "__main__":
    unittest.main()


class HtmlTests(unittest.TestCase):
    """
    The HTML body is emailed. Clients strip <style> blocks and do not support
    flex or grid, so the layout must be tables with inline styles only.
    """

    def html(self, results=None):
        if results is None:
            results = {"sui-usdc": trades([0.5, -0.2, 0.9]),
                       "link-usdc": trades([-0.3, -0.1])}
        return sim_report.render_html(results)

    def test_markup_is_well_formed(self):
        from html.parser import HTMLParser

        class Check(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack, self.bad = [], []

            def handle_starttag(self, tag, attrs):
                if tag not in ("br", "img", "meta", "hr", "input"):
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if self.stack and self.stack[-1] == tag:
                    self.stack.pop()
                else:
                    self.bad.append(tag)

        checker = Check()
        checker.feed(self.html())
        self.assertEqual(checker.stack, [], "unclosed tags")
        self.assertEqual(checker.bad, [], "mismatched closing tags")

    def test_avoids_constructs_email_clients_break_on(self):
        out = self.html()
        self.assertNotIn("<style", out.lower(), "clients strip <style> blocks")
        self.assertNotIn("display:flex", out)
        self.assertNotIn("display:grid", out)

    def test_loads_no_external_resources(self):
        """Remote assets are blocked by default and would render as gaps."""
        out = self.html()
        self.assertNotIn("http://", out)
        self.assertNotIn("https://", out)

    def test_meter_is_fixed_total_width(self):
        for fraction in (0.0, 0.15, 0.5, 1.0, 2.0, -1.0):
            bar = sim_report._meter(fraction, width=100)
            widths = [int(w) for w in
                      __import__("re").findall(r"width:(\d+)px", bar)]
            self.assertEqual(sum(widths), 100, f"fraction {fraction}")

    def test_coin_names_are_escaped(self):
        """Labels come from filenames; markup in one must not reach the email."""
        out = sim_report.render_html({"<script>x</script>": trades([0.5, -0.2])})
        self.assertNotIn("<script>", out.lower())
        self.assertIn("&lt;script&gt;", out.lower())

    def test_states_the_same_verdict_as_the_text_report(self):
        cost = {"a": trades([-0.10, -0.10, -0.10], fees=0.20)}
        self.assertIn("cost problem", self.html(cost))
        signal = {"a": trades([-0.90, -0.80], fees=0.01)}
        self.assertIn("entry signal is the problem", self.html(signal))

    def test_scores_match_the_text_report(self):
        from utils.graduation import evaluate, MAX_SCORE
        pnls = [0.5, -0.2, 0.9]
        out = self.html({"sui-usdc": trades(pnls)})
        self.assertIn(f'{evaluate(pnls)["score"]}/{MAX_SCORE}', out)

    def test_infinite_profit_factor_renders(self):
        out = self.html({"sui-usdc": trades([0.5, 0.6])})
        self.assertIn("∞", out)
