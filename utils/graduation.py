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


MIN_TRADES = 20
MIN_WIN_RATE = 55.0          # percent
MIN_NET_PNL = 0.0            # dollars, must be strictly greater
WORST_TRADE_FLOOR = -2.50    # dollars, no single trade worse than this

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
    "win_rate": 25,
    "net_pnl": 25,
    "worst_trade": 15,
    "sharpe": 10,
}
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


def evaluate(pnls):
    """
    Score a coin's completed-trade P&Ls against the graduation criteria.

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
                      "worst_trade": 0.0, "sharpe": 0.0},
        }

    wins = sum(1 for p in pnls if p > 0)
    win_rate = 100.0 * wins / count
    net_pnl = sum(pnls)
    worst = min(pnls)
    sharpe = per_trade_sharpe(pnls)

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
            "key": "worst_trade", "label": f"worst trade ≥ ${WORST_TRADE_FLOOR:.2f}",
            "passed": worst >= WORST_TRADE_FLOOR,
            "detail": f"${worst:+.2f}",
            "blocker": f"Worst trade ${worst:.2f} exceeds ${WORST_TRADE_FLOOR:.2f} limit",
        },
        {
            "key": "sharpe", "label": f"Sharpe ≥ {MIN_SHARPE:.2f}",
            "passed": sharpe >= MIN_SHARPE,
            "detail": f"{sharpe:.2f}",
            # Kept short: the report wraps at 66 columns.
            "blocker": f"Sharpe {sharpe:.2f} < {MIN_SHARPE} — returns too uneven",
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
        },
    }
