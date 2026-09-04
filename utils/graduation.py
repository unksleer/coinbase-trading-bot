"""
Single definition of sim -> live graduation.

This existed in three places that disagreed: auto_learner.check_graduation
gated actual promotion on four criteria, sim_report.py displayed a weighted
five-criteria score in the nightly email, and the dashboard showed a fourth
count. A coin could read 90/100 in the email while the gate that actually
promotes it said pass, because the gate did not check the fifth criterion at
all — the displayed score was stricter than the control it appeared to
describe.

Everything now evaluates here, so the number shown and the number enforced
cannot drift apart.
"""
import statistics


# ⚠ STATISTICAL POWER — read before trusting a pass.
#
# 20 trades cannot detect the effect sizes this system actually produces.
# Measured on the pooled stock backtest: mean +$0.033/trade with a per-trade
# standard deviation of $0.449 — the noise is 13x the effect.
#
#     n=20    95% band  ±$0.197   effect $0.033   invisible
#     n=40    95% band  ±$0.139   effect $0.033   invisible
#     n=1000  95% band  ±$0.028   effect $0.033   detectable
#
# A 20-trade win rate carries a ±22 point interval, so "55%" is statistically
# indistinguishable from anything between 33% and 77%.
#
# This gate therefore does NOT establish an edge. It is a floor that filters
# out the obviously broken — a strategy losing badly, or one with a catastrophic
# single trade — and nothing more. Passing it means "not visibly broken", not
# "profitable". Treat a pass as permission to risk a small amount of real money
# in order to check that live execution matches simulation, not as evidence the
# strategy works.
#
# Raising MIN_TRADES to a statistically honest number would put graduation
# years away at current rates. The threshold is kept low deliberately, with the
# limitation documented here rather than hidden behind a passing score.

MIN_TRADES = 20
MIN_WIN_RATE = 55.0          # percent
MIN_NET_PNL = 0.0            # dollars, must be strictly greater
# Percent of position size. Was -$2.50 in DOLLARS, which meant a -25% trade at
# $10 and a -2.5% trade at $100 — the same rule, ten times stricter, depending
# on a number that has nothing to do with the strategy. A floor on a single
# trade is a statement about risk control, and risk control is a percentage.
WORST_TRADE_FLOOR_PCT = -2.5
# Retained for callers that still pass dollars; derived, not independent.
WORST_TRADE_FLOOR = -2.50

# Per-trade risk-adjusted return: mean P&L divided by its standard deviation.
#
# NOT annualised. The previous implementation multiplied by sqrt(252), the
# factor for daily returns, but these are per-trade results on positions held
# ~20 hours and opened every few days. That inflated every value 15.87x, so the
# check passed whenever mean P&L was positive — making it a duplicate of the
# net-P&L criterion rather than an independent one.
#
# Threshold derived from the observed win/loss distributions: simulating
# 20-trade coins that just satisfy the other four criteria gives a median
# Sharpe of 0.515 and a 10th percentile of 0.270 at a 55% win rate. 0.35 sits
# near the 20th percentile, so it rejects the lumpiest fifth — a coin carried
# by two large wins — without blocking the median qualifying coin. Re-derive
# with analyze_entries.py if the trade distribution shifts materially.
MIN_SHARPE = 0.35

# Weights are for display only; `ready` requires every criterion to pass, so a
# high score never substitutes for a failed check.
_WEIGHTS = {
    "trades": 25,
    "win_rate": 20,
    "net_pnl": 15,
    "worst_trade": 10,
    "sharpe": 10,
    "beats_hold": 20,
}

# The alternative to running a bot is not cash — it is holding the same
# instrument at no per-trade cost. A strategy can pass every other criterion
# here while being strictly worse than doing nothing: on crypto the strategy
# lost to buy-and-hold on 5 of 7 coins, by 44 points on LINK and 35 on SOL,
# while SOL's +1.44% would have read as a success against a zero bar.
#
# So this is a criterion, not a footnote. It is evaluated only when a benchmark
# excess is supplied; without one it FAILS rather than passing, because "not
# measured" must never score the same as "beat the alternative".
MAX_SCORE = sum(_WEIGHTS.values())


def per_trade_sharpe(pnls):
    """
    Mean P&L over its standard deviation. Deliberately unannualised — see
    MIN_SHARPE.

    Zero variance is the degenerate case and the sign of the mean decides it:
    identical winning trades are perfect consistency, not undefined, so they
    must not be scored as if they were the worst possible outcome. Identical
    losses are the reverse.
    """
    if len(pnls) < 2:
        return 0.0
    mean = statistics.mean(pnls)
    spread = statistics.stdev(pnls)
    if spread == 0:
        if mean > 0:
            return float("inf")
        return 0.0 if mean == 0 else float("-inf")
    return mean / spread


def evaluate(pnls, benchmark_excess=None, position_usd=None):
    """
    Score a coin's completed-trade P&Ls against the graduation criteria.

    benchmark_excess is the strategy's return minus buy-and-hold's over the
    same window, as a fraction. Pass None when no price series is available;
    the criterion then fails, since an unmeasured benchmark is not a passed
    one. utils.benchmark.compare produces this value.

    Returns a dict with:
      criteria  list of {key, label, passed, detail, weight}, in display order
      score     weighted total out of MAX_SCORE
      passed    how many criteria passed
      total     how many criteria there are
      ready     True only when every criterion passes
      blockers  human-readable reasons for each failure
      stats     the underlying measurements
    """
    count = len(pnls)
    if count == 0:
        return {
            "criteria": [], "score": 0, "passed": 0,
            "total": len(_WEIGHTS), "ready": False,
            "blockers": ["No trades yet"],
            "stats": {"trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
                      "worst_trade": 0.0, "sharpe": 0.0,
                      "benchmark_excess": benchmark_excess},
        }

    wins = sum(1 for p in pnls if p > 0)
    win_rate = 100.0 * wins / count
    net_pnl = sum(pnls)
    worst = min(pnls)
    # The floor scales with position size. Without position_usd there is no
    # way to know what -$2.50 means, so the historical $10 default is assumed
    # and the dollar figure reads as it always did.
    floor_usd = (position_usd if position_usd else 10.0) * WORST_TRADE_FLOOR_PCT / 100.0
    sharpe = per_trade_sharpe(pnls)

    if benchmark_excess is None:
        hold_detail, hold_pass = "not measured", False
        hold_blocker = "No buy-and-hold benchmark for this window"
    else:
        hold_detail = f"{benchmark_excess*100:+.2f}% vs holding"
        hold_pass = benchmark_excess > 0
        hold_blocker = (f"Loses to buy-and-hold by "
                        f"{abs(benchmark_excess)*100:.2f}%")

    criteria = [
        {
            "key": "trades", "label": f"{MIN_TRADES} trades",
            "passed": count >= MIN_TRADES,
            "detail": f"{count} of {MIN_TRADES}",
            "blocker": f"Need {MIN_TRADES - count} more trades (have {count})",
        },
        {
            "key": "win_rate", "label": f"win rate ≥ {MIN_WIN_RATE:.0f}%",
            "passed": win_rate >= MIN_WIN_RATE,
            "detail": f"{win_rate:.1f}%",
            "blocker": f"Win rate {win_rate:.1f}% < {MIN_WIN_RATE}% required",
        },
        {
            "key": "net_pnl", "label": "net P&L positive",
            "passed": net_pnl > MIN_NET_PNL,
            "detail": f"${net_pnl:+.2f}",
            "blocker": f"Net P&L ${net_pnl:+.2f} not positive yet",
        },
        {
            "key": "worst_trade", "label": f"worst trade ≥ {WORST_TRADE_FLOOR_PCT:.1f}%",
            "passed": worst >= floor_usd,
            "detail": f"${worst:+.2f} (floor ${floor_usd:.2f})",
            "blocker": f"Worst trade ${worst:.2f} exceeds ${floor_usd:.2f} limit",
        },
        {
            "key": "sharpe", "label": f"Sharpe ≥ {MIN_SHARPE:.2f}",
            "passed": sharpe >= MIN_SHARPE,
            "detail": f"{sharpe:.2f}",
            # Kept short: the report wraps at 66 columns.
            "blocker": f"Sharpe {sharpe:.2f} < {MIN_SHARPE} — returns too uneven",
        },
        {
            "key": "beats_hold", "label": "beats buy & hold",
            "passed": hold_pass,
            "detail": hold_detail,
            "blocker": hold_blocker,
        },
    ]
    for item in criteria:
        item["weight"] = _WEIGHTS[item["key"]]

    return {
        "criteria": criteria,
        "score": sum(c["weight"] for c in criteria if c["passed"]),
        "passed": sum(1 for c in criteria if c["passed"]),
        "total": len(criteria),
        "ready": all(c["passed"] for c in criteria),
        "blockers": [c["blocker"] for c in criteria if not c["passed"]],
        "stats": {
            "trades": count, "win_rate": win_rate, "net_pnl": net_pnl,
            "worst_trade": worst, "sharpe": sharpe,
            "benchmark_excess": benchmark_excess,
        },
    }
