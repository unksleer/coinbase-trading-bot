#!/usr/bin/env python3
"""
sim_report.py — Sim Trading Performance Report
Reads all per-coin completed_trades_*_dry.csv files and produces
a rich summary with graduation readiness scoring.

Usage:
  python3 sim_report.py             # All coins
  python3 sim_report.py --coin SUI  # Single coin
"""
import os
import csv
import sys
import glob
import statistics
from datetime import datetime

# ── Graduation Criteria ────────────────────────────────────────────────────────
# Defined once in utils/graduation.py so this report, the learner's promotion
# gate and the dashboard cannot show different answers for the same coin.
from utils.graduation import (
    MIN_TRADES, MIN_WIN_RATE, MIN_NET_PNL, MIN_SHARPE, MAX_SCORE,
    WORST_TRADE_FLOOR as MAX_SINGLE_LOSS,
    evaluate as evaluate_graduation,
    per_trade_sharpe,
)

# ── Compounding Stake Table ────────────────────────────────────────────────────
STAKE_TABLE = [(0, 20, 10), (20, 50, 15), (50, 100, 20), (100, 200, 30), (200, float('inf'), 50)]

def get_stake(profit):
    for low, high, stake in STAKE_TABLE:
        if low <= profit < high:
            return stake
    return 50

def load_trades(csv_path):
    trades = []
    if not os.path.exists(csv_path):
        return trades
    try:
        with open(csv_path, 'r') as f:
            for row in csv.DictReader(f):
                try:
                    trades.append({
                        "pair":        row.get("pair", "?"),
                        "exit_reason": row.get("exit_reason", ""),
                        "pnl_usd":     float(row.get("pnl_usd", 0)),
                        "fees_usd":    float(row.get("fees_usd", 0)),
                        "hold_hours":  float(row.get("holding_time_hours", 0)),
                        "gross_pct":   float(row.get("gross_pct", 0)),
                        "net_pct":     float(row.get("net_pct", 0)),
                    })
                except (ValueError, KeyError):
                    continue
    except Exception as e:
        print(f"  Warning: {e}")
    return trades

def compute_sharpe(pnls):
    """Per-trade risk-adjusted return. See utils.graduation.per_trade_sharpe."""
    return per_trade_sharpe(pnls)

def graduation(trades):
    result = evaluate_graduation([t["pnl_usd"] for t in trades])
    return result["score"], result["blockers"], result["ready"]

W = 66          # email clients wrap monospace safely under ~72 columns
RULE = "─" * W


def bar(fraction, width=10, fill="█", empty="░"):
    """Fixed-width progress bar. Monospace so columns stay aligned."""
    filled = max(0, min(width, round(fraction * width)))
    return fill * filled + empty * (width - filled)


def money(value, width=0):
    return f"{'$' + format(abs(value), '.2f') if value >= 0 else '-$' + format(abs(value), '.2f'):>{width}}"


def signed(value, width=0):
    return f"{('+$' if value >= 0 else '-$') + format(abs(value), '.2f'):>{width}}"


def summarise(trades):
    """Per-coin figures used by both the leaderboard and the detail block."""
    n = len(trades)
    pnls = [t["pnl_usd"] for t in trades]
    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    fees = sum(t["fees_usd"] for t in trades)
    net = sum(pnls)
    win_sum = sum(t["pnl_usd"] for t in wins)
    loss_sum = sum(t["pnl_usd"] for t in losses)
    return {
        "n": n,
        "win_rate": 100.0 * len(wins) / n if n else 0.0,
        "wins": len(wins), "losses": len(losses),
        "net": net, "fees": fees, "gross": net + fees,
        "avg_win": win_sum / len(wins) if wins else 0.0,
        "avg_loss": loss_sum / len(losses) if losses else 0.0,
        "profit_factor": abs(win_sum / loss_sum) if loss_sum else float("inf"),
        "avg_hold": sum(t["hold_hours"] for t in trades) / n if n else 0.0,
        "best": max(trades, key=lambda t: t["pnl_usd"]) if trades else None,
        "worst": min(trades, key=lambda t: t["pnl_usd"]) if trades else None,
        "grad": evaluate_graduation(pnls),
    }


def print_portfolio(results):
    """Headline figures. Gross vs fees leads, because that is the decision."""
    trades = [t for group in results.values() for t in group]
    n = len(trades)
    net = sum(t["pnl_usd"] for t in trades)
    fees = sum(t["fees_usd"] for t in trades)
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    wr = 100.0 * wins / n if n else 0.0

    print(f"\n{RULE}")
    print(f"  SIM TRADING REPORT{datetime.now().strftime('%Y-%m-%d %H:%M'):>{W - 20}}")
    print(RULE)
    plural = "coin" if len(results) == 1 else "coins"
    print(f"  {n} trades · {len(results)} {plural} · {wr:.1f}% win rate")
    print()
    print(f"    gross {signed(net + fees, 9)}      "
          f"fees {money(-fees, 9)}      net {signed(net, 9)}")

    # The one line that says which problem this is.
    if n:
        if net + fees > 0 and net < 0:
            gross = net + fees
            print(f"    → direction is right: {signed(gross)} gross. "
                  f"Fees of {money(fees)} erase it.")
        elif net + fees <= 0:
            print(f"    → negative before fees; the entry signal is the problem")
        else:
            print(f"    → profitable after costs")


def print_leaderboard(results):
    """Every coin on one screen, closest to graduating first."""
    rows = sorted(
        ((label, summarise(t)) for label, t in results.items() if t),
        key=lambda kv: (-kv[1]["grad"]["score"], -kv[1]["n"]),
    )
    if not rows:
        return
    print(f"\n{RULE}")
    print(f"  {'COIN':<14}{'TRADES':>7}{'WIN%':>7}{'NET':>10}   READINESS")
    print(RULE)
    for label, s in rows:
        g = s["grad"]
        mark = "*" if g["ready"] else " "
        print(f" {mark}{label.upper():<14}{s['n']:>6}{s['win_rate']:>7.0f}%"
              f"{signed(s['net'], 10)}   {bar(g['score'] / MAX_SCORE)} {g['score']:>3}/{MAX_SCORE}")


def print_report(label, trades):
    """Per-coin detail. Only the failing criteria are listed — passing ones
    are visible in the leaderboard and would just be noise here."""
    n = len(trades)
    if n == 0:
        return
    s = summarise(trades)
    g = s["grad"]

    header = f"{label.upper()}  ·  {g['score']}/{MAX_SCORE} pts  ·  {n} trades"
    print(f"\n{RULE}")
    print(f"  {header}")
    print(RULE)
    pf = "inf" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}x"
    print(f"  win rate  {s['win_rate']:>7.1f}%  ({s['wins']}W/{s['losses']}L)"
          f"{'profit factor':>18}  {pf:>8}")
    print(f"  net P&L   {signed(s['net'], 8)}"
          f"{'gross':>14}  {signed(s['gross'], 8)}"
          f"{'fees':>10}  {money(-s['fees'], 8)}")
    print(f"  avg win   {signed(s['avg_win'], 8)}"
          f"{'avg loss':>14}  {signed(s['avg_loss'], 8)}"
          f"{'hold':>10}  {s['avg_hold']:>7.0f}h")
    if s["best"] and s["worst"]:
        print(f"  best      {signed(s['best']['pnl_usd'], 8)}  {s['best']['exit_reason']}")
        print(f"  worst     {signed(s['worst']['pnl_usd'], 8)}  {s['worst']['exit_reason']}")

    reasons = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    print("\n  exits")
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        share = count / n
        print(f"    {reason:<28} {bar(share, 8)} {count:>3}  {share*100:>3.0f}%")

    if g["ready"]:
        stake = get_stake(max(s["net"], 0))
        print(f"\n  ✅ READY FOR LIVE  →  ${stake}/trade next stake")
    else:
        print("\n  still needed")
        for blocker in g["blockers"]:
            print(f"    ✗ {blocker}")


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    coin_filter = None
    for i, a in enumerate(sys.argv):
        if a == "--coin" and i + 1 < len(sys.argv):
            coin_filter = sys.argv[i + 1].upper()

    files = sorted(glob.glob(os.path.join(root, "completed_trades_*_dry.csv")))
    # The legacy shared completed_trades_dry.csv is deliberately not included:
    # its name does not carry a coin, so it rendered as a phantom "DRY.CSV"
    # coin at the top of every report.

    results = {}
    for fpath in files:
        label = os.path.basename(fpath).replace("completed_trades_", "").replace("_dry.csv", "")
        if coin_filter and coin_filter not in label.upper():
            continue
        trades = load_trades(fpath)
        if trades:
            results[label] = trades

    if not results:
        print(f"\n{RULE}")
        if coin_filter:
            print(f"  No completed sim trades for {coin_filter} yet.")
        else:
            print("  No completed sim trades yet.")
            print(f"  Files appear here once bots close their first position.")
        print(RULE)
        return

    print_portfolio(results)
    print_leaderboard(results)

    ready = [l for l, t in results.items() if summarise(t)["grad"]["ready"]]
    for label, trades in sorted(results.items(),
                                key=lambda kv: -summarise(kv[1])["grad"]["score"]):
        print_report(label, trades)

    print(f"\n{RULE}")
    if ready:
        print(f"  🚀 READY FOR LIVE: {', '.join(l.upper() for l in ready)}")
    else:
        closest = max(results.items(), key=lambda kv: summarise(kv[1])["grad"]["score"])
        score = summarise(closest[1])["grad"]["score"]
        print(f"  ⏳ No coins ready for live yet — closest is "
              f"{closest[0].upper()} at {score}/{MAX_SCORE}")
    print(RULE + "\n")


if __name__ == "__main__":
    main()
