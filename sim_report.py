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
MIN_TRADES      = 20      # Minimum completed round-trips
MIN_WIN_RATE    = 55.0    # Win rate %
MIN_NET_PNL     = 0.0     # Must be net profitable
MAX_SINGLE_LOSS = -2.50   # No single trade worse than -$2.50
MIN_SHARPE      = 0.5     # Sharpe ratio (rough estimate)

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
    if len(pnls) < 2:
        return 0.0
    mean = statistics.mean(pnls)
    std  = statistics.stdev(pnls)
    return (mean / std) * (252 ** 0.5) if std > 0 else 0.0

def graduation(trades):
    n = len(trades)
    if n == 0:
        return 0, ["No trades yet"], False
    wins      = [t for t in trades if t["pnl_usd"] > 0]
    net_pnl   = sum(t["pnl_usd"] for t in trades)
    win_rate  = len(wins) / n * 100
    worst     = min(t["pnl_usd"] for t in trades)
    sharpe    = compute_sharpe([t["pnl_usd"] for t in trades])
    score, reasons = 0, []
    if n >= MIN_TRADES:              score += 25
    else: reasons.append(f"Need {MIN_TRADES - n} more trades (have {n})")
    if win_rate >= MIN_WIN_RATE:     score += 25
    else: reasons.append(f"Win rate {win_rate:.1f}% < {MIN_WIN_RATE}% required")
    if net_pnl > MIN_NET_PNL:        score += 25
    else: reasons.append(f"Net P&L ${net_pnl:+.2f} not positive yet")
    if worst >= MAX_SINGLE_LOSS:     score += 15
    else: reasons.append(f"Worst trade ${worst:.2f} exceeds -$2.50 limit")
    if sharpe >= MIN_SHARPE:         score += 10
    else: reasons.append(f"Sharpe {sharpe:.2f} < {MIN_SHARPE} required")
    return score, reasons, score >= 100

def print_report(label, trades):
    n = len(trades)
    sep = "═" * 60
    print(f"\n{sep}")
    print(f"  {label.upper()}  ({n} completed sim trades)")
    print(sep)
    if n == 0:
        print("  No completed sim trades yet — waiting for first crossover signal.")
        return
    wins        = [t for t in trades if t["pnl_usd"] > 0]
    losses      = [t for t in trades if t["pnl_usd"] <= 0]
    net_pnl     = sum(t["pnl_usd"] for t in trades)
    total_fees  = sum(t["fees_usd"] for t in trades)
    win_rate    = len(wins) / n * 100
    avg_hold    = sum(t["hold_hours"] for t in trades) / n
    avg_win     = sum(t["pnl_usd"] for t in wins) / len(wins) if wins else 0
    avg_loss    = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0
    best        = max(trades, key=lambda t: t["pnl_usd"])
    worst       = min(trades, key=lambda t: t["pnl_usd"])
    loss_sum    = sum(t["pnl_usd"] for t in losses)
    win_sum     = sum(t["pnl_usd"] for t in wins)
    pf          = abs(win_sum / loss_sum) if loss_sum != 0 else float('inf')

    print(f"  Win Rate:       {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Net P&L:        ${net_pnl:+.4f}   (Fees: ${total_fees:.4f})")
    print(f"  Avg Win / Loss: ${avg_win:+.4f} / ${avg_loss:+.4f}")
    print(f"  Profit Factor:  {pf:.2f}x")
    print(f"  Avg Hold Time:  {avg_hold:.1f}h")
    print(f"  Best Trade:     ${best['pnl_usd']:+.4f}  ({best['exit_reason']})")
    print(f"  Worst Trade:    ${worst['pnl_usd']:+.4f}  ({worst['exit_reason']})")

    reasons_map = {}
    for t in trades:
        reasons_map[t["exit_reason"]] = reasons_map.get(t["exit_reason"], 0) + 1
    print(f"\n  Exit Breakdown:")
    for r, cnt in sorted(reasons_map.items(), key=lambda x: -x[1]):
        print(f"    {r:<38} {cnt:>3}x ({cnt/n*100:.0f}%)")

    score, blockers, ready = graduation(trades)
    print(f"\n  ─── Graduation: ", end="")
    if ready:
        stake = get_stake(max(net_pnl, 0))
        print(f"✅ READY FOR LIVE  →  ${stake}/trade next stake")
    else:
        print(f"⏳ {score}/100 pts")
        for b in blockers:
            print(f"    ✗ {b}")

def main():
    root        = os.path.dirname(os.path.abspath(__file__))
    coin_filter = None
    for i, a in enumerate(sys.argv):
        if a == "--coin" and i + 1 < len(sys.argv):
            coin_filter = sys.argv[i + 1].upper()

    print("\n" + "█" * 60)
    print("    COINBASE SIM TRADING — PERFORMANCE REPORT")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("█" * 60)

    files = sorted(glob.glob(os.path.join(root, "completed_trades_*_dry.csv")))
    # Also include old shared file if it exists
    shared = os.path.join(root, "completed_trades_dry.csv")
    if os.path.exists(shared) and shared not in files:
        files.insert(0, shared)

    if not files:
        print("\n  No sim trade files found yet.")
        print("  Files appear here once bots start completing trades.")
        print(f"  Looking for: {root}/completed_trades_*_dry.csv")
        return

    all_trades, results = [], {}
    for fpath in files:
        label = os.path.basename(fpath).replace("completed_trades_", "").replace("_dry.csv", "")
        if coin_filter and coin_filter not in label.upper():
            continue
        trades = load_trades(fpath)
        results[label] = trades
        all_trades.extend(trades)
        print_report(label, trades)

    if not results:
        print(f"\n  No data for --coin {coin_filter}")
        return

    # Portfolio total
    n      = len(all_trades)
    pnl    = sum(t["pnl_usd"] for t in all_trades)
    fees   = sum(t["fees_usd"] for t in all_trades)
    wr     = sum(1 for t in all_trades if t["pnl_usd"] > 0) / n * 100 if n else 0
    ready  = [l for l, t in results.items() if graduation(t)[2]]

    print(f"\n{'█'*60}")
    print("    PORTFOLIO OVERVIEW")
    print(f"{'█'*60}")
    print(f"  Coins Active:   {len(results)}")
    print(f"  Total Trades:   {n}")
    print(f"  Overall Win %:  {wr:.1f}%")
    print(f"  Total Net P&L:  ${pnl:+.4f}  (after ${fees:.4f} fees)")
    if ready:
        print(f"\n  🚀 READY FOR LIVE: {', '.join(l.upper() for l in ready)}")
    else:
        print(f"\n  ⏳ Building track record — no coins ready for live yet.")
    print(f"{'█'*60}\n")

if __name__ == "__main__":
    main()
