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
    """
    Score against the gate, benchmark included.

    The benchmark is derived from the trade rows themselves so the emailed
    score cannot diverge from what the gate enforces — a coin reading "ready"
    in the report while the gate blocks it (or the reverse) is the exact defect
    utils/graduation.py was created to end.
    """
    from utils import benchmark
    pnls = [t["pnl_usd"] for t in trades]
    excess = benchmark.excess_from_trades(trades, pnls)
    result = evaluate_graduation(pnls, benchmark_excess=excess)
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




# ── HTML rendering ────────────────────────────────────────────────────────────
# The learner emails this report. Plain text stays the terminal path; --html
# produces the emailed version. Table layout and inline styles only — email
# clients strip <style> blocks and do not support flex or grid.

_BG      = "#0b0f19"
_CARD    = "#151b2b"
_LINE    = "#252d40"
_TEXT    = "#e6ebf5"
_MUTED   = "#8b96ad"
_UP      = "#34d399"
_DOWN    = "#f87171"
_ACCENT  = "#60a5fa"


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _money_html(value):
    colour = _UP if value >= 0 else _DOWN
    return f'<span style="color:{colour};font-weight:600">{signed(value)}</span>'


def _meter(fraction, colour=_ACCENT, width=110):
    """Bar as a nested table — the only construct every email client renders."""
    filled = max(0, min(width, int(round(fraction * width))))
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'style="display:inline-block;vertical-align:middle"><tr>'
        f'<td style="width:{filled}px;height:8px;background:{colour};'
        f'border-radius:4px 0 0 4px;font-size:0;line-height:0">&nbsp;</td>'
        f'<td style="width:{width - filled}px;height:8px;background:{_LINE};'
        f'border-radius:0 4px 4px 0;font-size:0;line-height:0">&nbsp;</td>'
        f'</tr></table>'
    )


def render_html(results):
    """Full HTML email body for the nightly report."""
    trades = [t for group in results.values() for t in group]
    n = len(trades)
    net = sum(t["pnl_usd"] for t in trades)
    fees = sum(t["fees_usd"] for t in trades)
    gross = net + fees
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    wr = 100.0 * wins / n if n else 0.0

    if gross > 0 and net < 0:
        verdict = (f"Direction is right — {signed(gross)} gross. "
                   f"Fees of {money(fees)} erase it. This is a cost problem.")
        verdict_colour = _DOWN
    elif gross <= 0:
        verdict = "Negative before fees. The entry signal is the problem."
        verdict_colour = _DOWN
    else:
        verdict = "Profitable after costs."
        verdict_colour = _UP

    rows = sorted(((label, summarise(t)) for label, t in results.items() if t),
                  key=lambda kv: (-kv[1]["grad"]["score"], -kv[1]["n"]))

    head = (
        f'<tr><td style="padding:0 0 6px"><div style="font-size:11px;'
        f'letter-spacing:.09em;text-transform:uppercase;color:{_MUTED}">'
        f'Sim Trading Report</div>'
        f'<div style="font-size:13px;color:{_MUTED};padding-top:2px">'
        f'{datetime.now().strftime("%Y-%m-%d %H:%M")} &middot; {n} trades &middot; '
        f'{len(results)} coin{"" if len(results) == 1 else "s"} &middot; '
        f'{wr:.1f}% win rate</div></td></tr>'
    )

    money_row = (
        f'<tr><td style="padding:14px 0 4px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        + "".join(
            f'<td style="width:33%;padding:10px 0">'
            f'<div style="font-size:11px;color:{_MUTED};text-transform:uppercase;'
            f'letter-spacing:.07em">{lbl}</div>'
            f'<div style="font-size:24px;font-weight:700;color:{col};'
            f'padding-top:3px;font-family:ui-monospace,Menlo,Consolas,monospace">'
            f'{val}</div></td>'
            for lbl, val, col in (
                ("gross", signed(gross), _UP if gross >= 0 else _DOWN),
                ("fees", money(-fees), _DOWN),
                ("net", signed(net), _UP if net >= 0 else _DOWN),
            ))
        + '</tr></table></td></tr>'
        f'<tr><td style="padding:2px 0 4px;font-size:13px;color:{verdict_colour}">'
        f'{_esc(verdict)}</td></tr>'
    )

    board = [
        f'<tr><td style="padding:20px 0 8px;font-size:11px;letter-spacing:.09em;'
        f'text-transform:uppercase;color:{_MUTED}">Readiness</td></tr>'
        f'<tr><td><table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="font-size:13px">'
    ]
    for label, s in rows:
        g = s["grad"]
        name = _esc(label.upper()) + (" ✅" if g["ready"] else "")
        board.append(
            f'<tr>'
            f'<td style="padding:7px 0;border-bottom:1px solid {_LINE};color:{_TEXT};'
            f'font-weight:600;white-space:nowrap">{name}</td>'
            f'<td style="padding:7px 8px;border-bottom:1px solid {_LINE};'
            f'color:{_MUTED};text-align:right;white-space:nowrap">{s["n"]} trades</td>'
            f'<td style="padding:7px 8px;border-bottom:1px solid {_LINE};'
            f'text-align:right;white-space:nowrap">{_money_html(s["net"])}</td>'
            f'<td style="padding:7px 0 7px 10px;border-bottom:1px solid {_LINE};'
            f'width:120px">{_meter(g["score"] / MAX_SCORE, _UP if g["ready"] else _ACCENT)}</td>'
            f'<td style="padding:7px 0 7px 8px;border-bottom:1px solid {_LINE};'
            f'color:{_MUTED};text-align:right;white-space:nowrap;'
            f'font-family:ui-monospace,Menlo,Consolas,monospace">'
            f'{g["score"]}/{MAX_SCORE}</td></tr>'
        )
    board.append('</table></td></tr>')

    detail = []
    for label, s in rows:
        g = s["grad"]
        pf = "∞" if s["profit_factor"] == float("inf") else f'{s["profit_factor"]:.2f}x'
        stats = "".join(
            f'<td style="padding:4px 12px 4px 0;white-space:nowrap">'
            f'<span style="color:{_MUTED}">{lbl}</span> {val}</td>'
            for lbl, val in (
                ("win", f'{s["win_rate"]:.0f}% ({s["wins"]}W/{s["losses"]}L)'),
                ("net", _money_html(s["net"])),
                ("fees", money(s["fees"])),
                ("PF", pf),
                ("hold", f'{s["avg_hold"]:.0f}h'),
            ))
        exits = {}
        for t in results[label]:
            exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
        exit_rows = "".join(
            f'<tr><td style="padding:3px 0;color:{_MUTED};white-space:nowrap">'
            f'{_esc(reason)}</td>'
            f'<td style="padding:3px 0 3px 10px;width:90px">'
            f'{_meter(count / s["n"], _MUTED, 80)}</td>'
            f'<td style="padding:3px 0 3px 8px;color:{_MUTED};text-align:right">'
            f'{count} &middot; {100 * count / s["n"]:.0f}%</td></tr>'
            for reason, count in sorted(exits.items(), key=lambda kv: -kv[1]))

        if g["ready"]:
            status = (f'<div style="color:{_UP};padding-top:8px;font-weight:600">'
                      f'✅ Ready for live → ${get_stake(max(s["net"], 0))}/trade next stake</div>')
        else:
            items = "".join(
                f'<div style="color:{_MUTED};padding-top:3px">✗ {_esc(b)}</div>'
                for b in g["blockers"])
            status = (f'<div style="padding-top:10px"><div style="font-size:11px;'
                      f'letter-spacing:.07em;text-transform:uppercase;color:{_MUTED}">'
                      f'Still needed</div>{items}</div>')

        detail.append(
            f'<tr><td style="padding:10px 0 0"><table role="presentation" width="100%" '
            f'cellpadding="0" cellspacing="0" style="background:{_CARD};'
            f'border:1px solid {_LINE};border-radius:10px;font-size:13px">'
            f'<tr><td style="padding:14px 16px">'
            f'<div style="font-size:15px;font-weight:700;color:{_TEXT}">'
            f'{_esc(label.upper())} '
            f'<span style="color:{_MUTED};font-weight:400;font-size:13px">'
            f'&middot; {g["score"]}/{MAX_SCORE} &middot; {s["n"]} trades</span></div>'
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'style="font-size:13px;color:{_TEXT};padding-top:8px"><tr>{stats}</tr></table>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="font-size:12px;padding-top:10px">{exit_rows}</table>'
            f'{status}</td></tr></table></td></tr>'
        )

    return (
        f'<!DOCTYPE html><html><body style="margin:0;padding:0;background:{_BG}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_BG};padding:22px 0">'
        f'<tr><td align="center">'
        f'<table role="presentation" width="620" cellpadding="0" cellspacing="0" '
        f'style="width:620px;max-width:94%;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;color:{_TEXT}">'
        + head + money_row + "".join(board) + "".join(detail) +
        f'<tr><td style="padding:18px 0 0;font-size:11px;color:{_MUTED}">'
        f'All five criteria must pass to graduate. Score is for display only.'
        f'</td></tr>'
        f'</table></td></tr></table></body></html>'
    )


def load_stock_trades():
    """
    Completed stock paper trades, per ticker.

    Kept separate from crypto throughout: equities are commission-free, so the
    fee floor that dominates every crypto figure does not apply and averaging
    the two together would understate crypto drag and overstate stock cost.
    """
    out = {}
    for path in sorted(glob.glob(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "completed_trades_stock_*_paper.csv"))):
        ticker = (os.path.basename(path)
                  .replace("completed_trades_stock_", "")
                  .replace("_paper.csv", "").upper())
        rows = []
        try:
            with open(path, newline="") as handle:
                for row in csv.DictReader(handle):
                    try:
                        rows.append({
                            "pnl_usd": float(row.get("pnl_usd") or 0.0),
                            "exit_reason": row.get("exit_reason", ""),
                            "gross_pct": float(row.get("gross_pct") or 0.0),
                        })
                    except (TypeError, ValueError):
                        continue
        except (OSError, csv.Error):
            continue
        if rows:
            out[ticker] = rows
    return out


def print_stocks(by_ticker):
    """Stock section of the report. Silent when there is nothing to say."""
    if not by_ticker:
        return
    trades = [t for rows in by_ticker.values() for t in rows]
    n = len(trades)
    net = sum(t["pnl_usd"] for t in trades)
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)

    print(f"\n{RULE}")
    print("  STOCKS (Alpaca paper · commission-free)")
    print(RULE)
    print(f"  {n} trades · {len(by_ticker)} tickers · "
          f"{100*wins/n if n else 0:.1f}% win rate · net {signed(net)}")
    print("  No fee floor here — round trip is slippage only, ~0.20%,")
    print("  against 1.207% on crypto. The two are not comparable.\n")

    for ticker, rows in sorted(by_ticker.items(),
                               key=lambda kv: -sum(t["pnl_usd"] for t in kv[1])):
        tn = len(rows)
        tnet = sum(t["pnl_usd"] for t in rows)
        tw = sum(1 for t in rows if t["pnl_usd"] > 0)
        g = evaluate_graduation([t["pnl_usd"] for t in rows])
        print(f"  {ticker:8}{tn:>4} trades{signed(tnet, 9)}"
              f"{100*tw/tn:>6.0f}% win   {bar(g['score']/MAX_SCORE)} "
              f"{g['score']:>3}/{MAX_SCORE}")


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    coin_filter = None
    as_html = "--html" in sys.argv
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
        stocks = load_stock_trades()
        if stocks and not as_html:
            print_stocks(stocks)
            return
        if as_html:
            print(f'<html><body style="background:{_BG};color:{_MUTED};'
                  f'font-family:sans-serif;padding:20px">'
                  f'No completed sim trades yet.</body></html>')
            return
        print(f"\n{RULE}")
        if coin_filter:
            print(f"  No completed sim trades for {coin_filter} yet.")
        else:
            print("  No completed sim trades yet.")
            print(f"  Files appear here once bots close their first position.")
        print(RULE)
        return

    if as_html:
        print(render_html(results))
        return

    print_portfolio(results)
    print_leaderboard(results)
    print_stocks(load_stock_trades())

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
