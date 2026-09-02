#!/usr/bin/env python3
"""
Entry-quality analysis.

The bot's problem is not that it exits badly, it is that it enters trades with
no follow-through. Every round trip costs a fixed percentage in fees, so an
entry whose price never travels further than that fee is a guaranteed loss no
matter how the exit is tuned. This script measures, per entry condition, how
far price actually travelled afterwards (MFE) and what the trade netted.

Two data sources, because they answer different questions:

  completed_trades_*_dry.csv  what happened to entries the bot TOOK
  logs/signal_log_*.csv       what the filters BLOCKED (invisible above)

The second one only becomes useful once it has accumulated candles; a fresh
restart leaves one row per pair. Both sections report their sample size so a
result computed from twenty trades is not mistaken for a validated edge.

Usage:
    .venv/bin/python analyze_entries.py
    .venv/bin/python analyze_entries.py --fee 1.2
"""
import argparse
import csv
import glob
import os
import statistics
from collections import defaultdict

# Fee drag per round trip as a percentage of notional. An entry must clear
# this before it can net anything, which makes it the natural pass/fail line
# for "did this entry go anywhere".
DEFAULT_FEE_PCT = 0.80


def load_completed_trades(pattern="completed_trades_*_dry.csv"):
    """Load completed sim trades with their entry indicator snapshot."""
    trades = []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, newline="") as handle:
                for row in csv.DictReader(handle):
                    trade = _parse_trade(row)
                    if trade:
                        trades.append(trade)
        except (OSError, csv.Error) as e:
            print(f"  ! could not read {path}: {e}")
    return trades


def _parse_trade(row):
    """Convert a CSV row, returning None if the entry snapshot is unusable."""
    try:
        sma_short = float(row["entry_sma_short"])
        sma_long = float(row["entry_sma_long"])
        if sma_long == 0:
            return None
        return {
            "pair": row["pair"],
            "exit_reason": row["exit_reason"],
            "net_pct": float(row["net_pct"]),
            "pnl_usd": float(row["pnl_usd"]),
            "mfe_pct": float(row["mfe_pct"]),
            "mae_pct": float(row["mae_pct"]),
            "rsi": float(row["entry_rsi"]),
            # Crossover strength: how far the fast SMA had pulled above the slow
            # one at entry. Note entry_spread in the CSV is this same quantity
            # (strategy.last_spread_percent), not a bid/ask spread — recomputed
            # here from the SMAs so the source is unambiguous.
            "sma_sep": (sma_short - sma_long) / sma_long * 100.0,
        }
    except (KeyError, TypeError, ValueError):
        return None


def summarize(label, trades, fee):
    """One line describing a group of trades."""
    if not trades:
        print(f"  {label:38} n=0")
        return
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    reached = sum(1 for t in trades if t["mfe_pct"] >= fee)
    print(
        f"  {label:38} n={len(trades):3}  "
        f"win={100 * wins / len(trades):3.0f}%  "
        f"net={statistics.mean(t['net_pct'] for t in trades):+6.2f}%  "
        f"total=${sum(t['pnl_usd'] for t in trades):+6.2f}  "
        f"cleared_fee={100 * reached / len(trades):3.0f}%"
    )


def bucket_report(trades, key, edges, label, fee):
    """Bucket trades by an entry condition and show follow-through per bucket."""
    print(f"\n  {label}")
    print(f"  {'bucket':>16} {'n':>4} {'avg MFE':>9} {'avg NET':>9} {'cleared fee':>12}")
    print(f"  {'-' * 54}")
    for low, high in zip(edges, edges[1:]):
        selected = [t for t in trades if low <= t[key] < high]
        if not selected:
            continue
        cleared = sum(1 for t in selected if t["mfe_pct"] >= fee)
        print(
            f"  {low:6.2f}..{high:<8.2f} {len(selected):4} "
            f"{statistics.mean(t['mfe_pct'] for t in selected):+9.2f} "
            f"{statistics.mean(t['net_pct'] for t in selected):+9.2f} "
            f"{100 * cleared / len(selected):11.0f}%"
        )


def per_pair_breakdown(trades, label):
    """
    Show a candidate filter's result per pair.

    A filter that looks profitable overall but is carried by one lucky coin is
    not a filter, so this is the check that matters before trusting one.
    """
    by_pair = defaultdict(list)
    for trade in trades:
        by_pair[trade["pair"]].append(trade)
    print(f"\n  {label} — per pair:")
    if not by_pair:
        print("    (no trades match)")
        return
    positive = 0
    for pair, group in sorted(by_pair.items()):
        total = sum(t["pnl_usd"] for t in group)
        positive += total > 0
        print(f"    {pair:12} n={len(group):3}  total=${total:+6.2f}")
    print(f"    -> positive on {positive}/{len(by_pair)} pairs")


def analyse_taken_entries(trades, fee):
    """Section 1: entries the bot took, bucketed by condition at entry."""
    print("=" * 72)
    print(f"ENTRIES TAKEN  ({len(trades)} completed sim trades)")
    print("=" * 72)
    if not trades:
        print("  No completed trades with entry snapshots yet.")
        return

    summarize("ALL TRADES (current behaviour)", trades, fee)

    bucket_report(trades, "rsi", [0, 40, 50, 55, 60, 65, 100],
                  "RSI at entry", fee)
    bucket_report(trades, "sma_sep", [0, 0.05, 0.10, 0.25, 0.50, 99],
                  "SMA separation at entry (%) — crossover strength", fee)


def analyse_candidate_filter(trades, fee, rsi_low, rsi_high, max_sma_sep):
    """Section 2: what a proposed entry filter would have kept and dropped."""
    print()
    print("=" * 72)
    print(f"CANDIDATE FILTER  RSI in [{rsi_low}, {rsi_high})  AND  SMA sep < {max_sma_sep}%")
    print("=" * 72)
    if not trades:
        print("  No completed trades to evaluate against.")
        return

    def passes(t):
        return rsi_low <= t["rsi"] < rsi_high and t["sma_sep"] < max_sma_sep

    kept = [t for t in trades if passes(t)]
    dropped = [t for t in trades if not passes(t)]

    summarize("BASELINE (no filter)", trades, fee)
    summarize("KEPT by filter", kept, fee)
    summarize("DROPPED by filter", dropped, fee)
    per_pair_breakdown(kept, "KEPT")

    if kept:
        baseline_total = sum(t["pnl_usd"] for t in trades)
        kept_total = sum(t["pnl_usd"] for t in kept)
        print(f"\n  Result: ${baseline_total:+.2f} -> ${kept_total:+.2f} "
              f"(improvement of ${kept_total - baseline_total:+.2f}, "
              f"{len(dropped)} entries avoided)")
    if 0 < len(kept) < 30:
        print(f"  ! Sample is {len(kept)} trades. Treat as a hypothesis to test "
              f"in sim, not a validated edge.")


def analyse_blocked_signals(log_dir="logs"):
    """
    Section 3: what the filters rejected.

    completed_trades only records entries that were taken, so a filter that is
    blocking good setups is invisible there. This reads the per-candle log to
    show what never became a trade.
    """
    print()
    print("=" * 72)
    print("SIGNALS BLOCKED BY FILTERS")
    print("=" * 72)

    rows = []
    for path in sorted(glob.glob(os.path.join(log_dir, "signal_log_*.csv"))):
        try:
            with open(path, newline="") as handle:
                rows.extend(csv.DictReader(handle))
        except (OSError, csv.Error) as e:
            print(f"  ! could not read {path}: {e}")

    if not rows:
        print("  No signal log rows found.")
        return

    buys = [r for r in rows if r.get("raw_signal") == "BUY"]
    blocked = [r for r in buys if r.get("blocked_by")]
    print(f"  {len(rows)} candle decisions logged, {len(buys)} raw BUY signals, "
          f"{len(blocked)} blocked")

    if len(rows) < 200:
        print(f"  ! Only {len(rows)} rows. The log resets nothing on restart but "
              f"fills one row per candle per pair,")
        print(f"    so at ONE_HOUR granularity expect ~24/pair/day. Let this run "
              f"several days before reading it.")

    if blocked:
        reasons = defaultdict(int)
        for row in blocked:
            reasons[row["blocked_by"]] += 1
        print("\n  Block reasons:")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:40} {count:4}x")


def main():
    parser = argparse.ArgumentParser(
        description="Analyse which entry conditions actually produce follow-through.")
    parser.add_argument("--fee", type=float, default=DEFAULT_FEE_PCT,
                        help=f"round-trip fee drag %%%% of notional (default {DEFAULT_FEE_PCT})")
    parser.add_argument("--rsi-low", type=float, default=50.0)
    parser.add_argument("--rsi-high", type=float, default=55.0)
    parser.add_argument("--max-sma-sep", type=float, default=0.10,
                        help="maximum SMA separation %% at entry (crossover strength cap)")
    parser.add_argument("--log-dir", default="logs")
    args = parser.parse_args()

    print(f"\nFee floor: {args.fee}% per round trip — an entry must clear this "
          f"to net anything.\n")

    trades = load_completed_trades()
    analyse_taken_entries(trades, args.fee)
    analyse_candidate_filter(trades, args.fee,
                             args.rsi_low, args.rsi_high, args.max_sma_sep)
    analyse_blocked_signals(args.log_dir)
    print()


if __name__ == "__main__":
    main()
