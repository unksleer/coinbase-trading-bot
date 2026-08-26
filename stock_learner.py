#!/usr/bin/env python3
"""
stock_learner.py — nightly evaluation for the stock bots.

Deliberately not a copy of auto_learner.py. The crypto learner searches SMA
parameters within one strategy on one timeframe, and months of that produced no
passing candidate because the answer was outside the box it searched. Here the
strategy class is part of the search from the start, since on commission-free
equities the class matters more than the parameters: measured across 15 symbols,
mean reversion beat crossover on both timeframes at every threshold tested.

Advisory only. It never edits .env and never restarts a bot. Graduation is
evaluated by utils.graduation, the same code the crypto report and dashboard
use, so a ticker cannot show one readiness figure here and another elsewhere.

Usage:
    .venv/bin/python stock_learner.py
    .venv/bin/python stock_learner.py --ticker NVDA
"""
import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.abspath(__file__))
RECOMMENDATIONS_PATH = os.path.join(DIR, "logs", "stock_recommendations.json")

# A candidate must clear all of these before it is worth reporting. Same shape
# as the crypto gates and for the same reason: an in-sample best is the maximum
# of many noisy draws, and reporting it as a finding is how a search produces
# recommendations that then fail.
MIN_OOS_TRADES = 5
MIN_NEIGHBOUR_AGREEMENT = 0.60


def load_env():
    values = {}
    try:
        with open(os.path.join(DIR, ".env")) as handle:
            for line in handle:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except OSError:
        pass
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def watchlist(values):
    raw = values.get("STOCK_WATCHLIST", "")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    return tickers or ["SPY", "QQQ", "LPL"]


def completed_trades(ticker):
    """Realised paper trades for one ticker."""
    path = os.path.join(DIR, f"completed_trades_stock_{ticker.lower()}_paper.csv")
    pnls = []
    try:
        with open(path) as handle:
            for row in csv.DictReader(handle):
                try:
                    pnls.append(float(row.get("pnl_usd") or 0.0))
                except (TypeError, ValueError):
                    continue
    except OSError:
        pass
    return pnls


def candidates():
    """
    Strategy variants to evaluate.

    Both classes are represented so the comparison is between classes as much as
    between parameters — the distinction the crypto learner could not make.
    """
    import compare_strategies as cs
    return [
        ("SMA 5/30", cs.sma_crossover, (5, 30)),
        ("SMA 10/30", cs.sma_crossover, (10, 30)),
        ("SMA 10/40", cs.sma_crossover, (10, 40)),
        ("SMA 20/60", cs.sma_crossover, (20, 60)),
        ("RSI mean-rev 25/75", cs.rsi_mean_reversion, (14, 25.0, 75.0)),
        ("RSI mean-rev 30/70", cs.rsi_mean_reversion, (14, 30.0, 70.0)),
        ("RSI mean-rev 35/65", cs.rsi_mean_reversion, (14, 35.0, 65.0)),
        ("RSI mean-rev 20/80", cs.rsi_mean_reversion, (14, 20.0, 80.0)),
    ]


def neighbours(label):
    """
    Variants adjacent to this one in parameter space.

    A result that only holds at one setting and collapses either side of it is
    a lucky cell. Requiring the neighbours to agree is what separates a plateau
    from noise, and it is the check the crypto grid search kept failing.
    """
    groups = [
        ["SMA 5/30", "SMA 10/30", "SMA 10/40", "SMA 20/60"],
        ["RSI mean-rev 20/80", "RSI mean-rev 25/75",
         "RSI mean-rev 30/70", "RSI mean-rev 35/65"],
    ]
    for group in groups:
        if label in group:
            i = group.index(label)
            return [g for g in group[max(0, i - 1):i + 2] if g != label]
    return []


def evaluate_ticker(ticker, timeframe, lookback_days):
    import compare_strategies as cs
    from utils import stock_data

    cs.FEE = cs.STOCK_FEE          # commission-free; the whole reason stocks differ
    closes = stock_data.closes(ticker, timeframe, lookback_days)
    if len(closes) < 200:
        return None

    split = int(len(closes) * 0.6)
    hold = cs.al.buy_and_hold_backtest(closes[split:])["net_profit"]

    results = {}
    for label, builder, params in candidates():
        outcome = cs.evaluate(closes, builder, params)
        results[label] = outcome["out_sample"]

    ranked = sorted(results.items(), key=lambda kv: -kv[1]["net"])
    best_label, best = ranked[0]

    agreement = [n for n in neighbours(best_label) if results.get(n, {}).get("net", 0) > 0]
    expected = neighbours(best_label)
    stability = len(agreement) / len(expected) if expected else 0.0

    blockers = []
    if best["net"] <= 0:
        blockers.append("best variant is not profitable out of sample")
    if best["trades"] < MIN_OOS_TRADES:
        blockers.append(f"only {best['trades']} out-of-sample trades "
                        f"(need {MIN_OOS_TRADES})")
    if best["net"] <= hold:
        blockers.append(f"does not beat buy-and-hold (${hold:+.2f})")
    if stability < MIN_NEIGHBOUR_AGREEMENT:
        blockers.append(f"neighbouring settings agree {stability:.0%} "
                        f"(need {MIN_NEIGHBOUR_AGREEMENT:.0%}) — likely a lucky cell")

    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "bars": len(closes),
        "hold": hold,
        "results": results,
        "best": best_label,
        "best_net": best["net"],
        "best_trades": best["trades"],
        "best_win_rate": best["win_rate"],
        "stability": stability,
        "blockers": blockers,
        "eligible": not blockers,
    }


def print_report(reports, values):
    from utils.graduation import evaluate as grad_evaluate, MAX_SCORE

    W = 66
    rule = "─" * W
    print(f"\n{rule}")
    print(f"  STOCK LEARNER{datetime.now().strftime('%Y-%m-%d %H:%M'):>{W - 15}}")
    print(rule)
    print("  Commission-free: round-trip cost is slippage only, ~0.20%.")
    print("  Crypto findings do not transfer — that fee difference decides")
    print("  which strategies are viable.\n")

    if not reports:
        print("  No tickers with enough history yet.\n")
        return

    print(f"  {'TICKER':9}{'BEST VARIANT':22}{'NET':>9}{'TRADES':>8}{'WIN%':>7}{'HOLD':>9}")
    print(rule)
    for r in sorted(reports, key=lambda x: -x["best_net"]):
        mark = "*" if r["eligible"] else " "
        print(f" {mark}{r['ticker']:8}{r['best']:22}{r['best_net']:>+9.2f}"
              f"{r['best_trades']:>8}{r['best_win_rate']:>6.0f}%{r['hold']:>+9.2f}")

    for r in sorted(reports, key=lambda x: -x["best_net"]):
        print(f"\n{rule}")
        print(f"  {r['ticker']}  ·  {r['bars']} {r['timeframe'].lower()} bars")
        print(rule)
        for label, res in sorted(r["results"].items(), key=lambda kv: -kv[1]["net"]):
            flag = " <" if label == r["best"] else ""
            print(f"    {label:22}{res['net']:>+9.2f}{res['trades']:>7} trades"
                  f"{res['win_rate']:>6.0f}%{flag}")

        pnls = completed_trades(r["ticker"])
        grad = grad_evaluate(pnls)
        print(f"\n    live paper record: {len(pnls)} trades · "
              f"graduation {grad['score']}/{MAX_SCORE}")

        if r["eligible"]:
            print(f"    ✓ {r['best']} clears every gate — candidate for "
                  f"STOCK_STRATEGY")
        else:
            print("    still needed")
            for b in r["blockers"]:
                print(f"      ✗ {b}")
    print(f"\n{rule}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", help="evaluate one ticker only")
    parser.add_argument("--timeframe", default="Hour", choices=("Hour", "Day"))
    parser.add_argument("--lookback-days", type=int, default=400)
    args = parser.parse_args()

    values = load_env()
    tickers = [args.ticker.upper()] if args.ticker else watchlist(values)

    reports = []
    for ticker in tickers:
        report = evaluate_ticker(ticker, args.timeframe, args.lookback_days)
        if report:
            reports.append(report)
        else:
            print(f"  {ticker}: not enough history, skipped")

    print_report(reports, values)

    payload = {
        "mode": "ADVISORY",
        "timeframe": args.timeframe,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recommendations": [
            {k: r[k] for k in ("ticker", "best", "best_net", "best_trades",
                               "best_win_rate", "hold", "stability", "eligible",
                               "blockers")}
            for r in reports
        ],
    }
    try:
        os.makedirs(os.path.dirname(RECOMMENDATIONS_PATH), exist_ok=True)
        with open(RECOMMENDATIONS_PATH, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        print(f"  Written to {os.path.relpath(RECOMMENDATIONS_PATH, DIR)}\n")
    except OSError as e:
        print(f"  Could not write recommendations: {e}\n")


if __name__ == "__main__":
    main()
