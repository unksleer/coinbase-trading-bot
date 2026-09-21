#!/usr/bin/env python3
"""
Backtest the School Run on stored SPY candles only.

Reads the local store, never the network, so a run is reproducible and cannot
quietly change between invocations. Sessions are America/New_York calendar
days of regular-hours fifteen-minute bars; the reference candle is the bar at
`--ref-candle` (default 1: the 09:45-10:00 ET bar, the SPY analogue of
Hougaard's 09:15-09:30 CET DAX candle).

Entries fill at the stop level plus slippage on the first bar that trades
through it. Exits are checked per bar with the stop before the target; when a
bar spans both, the stop is assumed to fill first. Anything the fifteen-minute
bar cannot resolve is counted, not guessed.

Result on 2026-09-21, 501 sessions (2024-09-03 .. 2026-09-02), reference bar
#1, $100 position: the range breaks in every session, so 501 trades. Gross
edge before costs is +0.021% per trade; the 0.10% round-trip slippage
assumption turns that into -0.08% per trade and net -$40 to -$44 across every
exit variant, against +$39.91 for holding. Breakeven is at about 1 basis point
of slippage per side. The rule is cost-dominated on SPY at this bar size, not
merely unlucky, and no exit variant changes the sign.

    .venv/bin/python backtest_school_run.py
    .venv/bin/python backtest_school_run.py --ref-candle 0
    .venv/bin/python backtest_school_run.py --variants
"""
import argparse
import os
import statistics as st
import sys
from collections import Counter, defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

from market_data.candle_store import CandleStore          # noqa: E402
from strategies import school_run as sr                   # noqa: E402

SPEC = {
    "SPY": dict(venue="alpaca", granularity="Min15", tz="America/New_York",
                fee_per_side=0.0, slippage_per_side=0.0005),
}

# Named exit variants. The first is the rule as described; the others show how
# much the answer depends on the part of the rule Hougaard leaves discretionary.
VARIANTS = {
    "breakeven at 1R, close at end of day": dict(target_r=None, breakeven_r=1.0, trail_r=None),
    "no management, close at end of day":   dict(target_r=None, breakeven_r=None, trail_r=None),
    "fixed 1R target, static stop":         dict(target_r=1.0, breakeven_r=None, trail_r=None),
    "fixed 2R target, static stop":         dict(target_r=2.0, breakeven_r=None, trail_r=None),
    "breakeven at 1R, trail 1R behind":     dict(target_r=None, breakeven_r=1.0, trail_r=1.0),
}


def load_sessions(instrument):
    """Candles grouped into sessions, oldest first."""
    import pandas as pd
    spec = SPEC[instrument]
    with CandleStore() as store:
        rows = store.get(spec["venue"], instrument, spec["granularity"])
    if not rows:
        return [], []
    stamps = pd.to_datetime([r[0] for r in rows], unit="s", utc=True)
    local = stamps.tz_convert(spec["tz"])
    candles = [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3],
                "close": r[4], "volume": r[5]} for r in rows]
    grouped = defaultdict(list)
    for i, candle in enumerate(candles):
        grouped[str(local[i].date())].append(candle)
    return sorted(grouped.items()), candles


def run(config, sessions, instrument="SPY"):
    decisions, trades = [], []
    for label, session in sessions:
        d = sr.evaluate_session(session, config, label, instrument)
        decisions.append(d)
        if not d.accepted:
            continue
        exit_ts, exit_price, reason, mfe, mae, moved = sr.simulate_exit(d, session, config)
        gross, fees, net, filled = sr.pnl(d, exit_price, config)
        trades.append({
            "session": label, "direction": d.direction,
            "entry_ts": d.entry_ts, "exit_ts": exit_ts,
            "entry": d.entry_price, "exit": filled, "reason": reason,
            "gross": gross, "fees": fees, "net": net,
            "mfe": mfe, "mae": mae, "range_pct": d.range_pct,
            "ambiguous": d.both_sides_same_bar, "stop_moved": moved,
            "hours": (exit_ts - d.entry_ts) / 3600.0,
        })
    return decisions, trades


def stats(trades):
    if not trades:
        return {}
    nets = [t["net"] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    equity, peak, dd = 0.0, 0.0, 0.0
    streak = worst_streak = 0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
        streak = streak + 1 if n <= 0 else 0
        worst_streak = max(worst_streak, streak)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "trades": len(trades),
        "win_rate": 100.0 * len(wins) / len(trades),
        "avg_win": st.mean(wins) if wins else 0.0,
        "avg_loss": st.mean(losses) if losses else 0.0,
        "net": sum(nets),
        "profit_factor": (gross_win / gross_loss) if gross_loss else float("inf"),
        "expectancy": st.mean(nets),
        "max_drawdown": dd,
        "max_consec_losses": worst_streak,
        "avg_hours": st.mean([t["hours"] for t in trades]),
        "largest_win": max(nets),
        "largest_loss": min(nets),
    }


def show(name, decisions, trades, position_usd):
    s = stats(trades)
    reasons = Counter(d.reason for d in decisions if not d.accepted)
    print(f"\n  ── {name} ──")
    print(f"  sessions {len(decisions)}   triggered {sum(1 for d in decisions if d.accepted)}"
          f"   trades {s.get('trades', 0)}")
    if not s:
        print("  no completed trades")
        print(f"  rejections: {dict(reasons.most_common(6))}")
        return s
    print(f"  win rate {s['win_rate']:.0f}%   net ${s['net']:+.2f} on ${position_usd:.0f}"
          f"   expectancy ${s['expectancy']:+.3f}/trade "
          f"({100 * s['expectancy'] / position_usd:+.3f}%)")
    print(f"  avg win ${s['avg_win']:+.3f}   avg loss ${s['avg_loss']:+.3f}   "
          f"profit factor {s['profit_factor']:.2f}")
    print(f"  max drawdown ${s['max_drawdown']:.2f}   max consec losses "
          f"{s['max_consec_losses']}   avg hold {s['avg_hours']:.1f}h")
    print(f"  largest win ${s['largest_win']:+.2f}   largest loss ${s['largest_loss']:+.2f}")
    print(f"  exits: {dict(Counter(t['reason'] for t in trades))}")
    print(f"  ambiguous both-sides bars: {sum(1 for t in trades if t['ambiguous'])}"
          f"   stop moved before exit: {sum(1 for t in trades if t['stop_moved'])}")
    for side in (sr.LONG, sr.SHORT):
        sub = [t for t in trades if t["direction"] == side]
        ss = stats(sub)
        if ss:
            print(f"  {side:<5} {ss['trades']:>3} trades   win {ss['win_rate']:.0f}%   "
                  f"net ${ss['net']:+.2f}   PF {ss['profit_factor']:.2f}")
    if reasons:
        print(f"  rejections: {dict(reasons.most_common(6))}")
    return s


def by_month(trades):
    grouped = defaultdict(list)
    for t in trades:
        grouped[t["session"][:7]].append(t["net"])
    return {m: (len(v), round(sum(v), 2)) for m, v in sorted(grouped.items())}


def by_range_tercile(trades):
    """Net by reference-candle width: does the rule care how wide the candle is?"""
    if len(trades) < 6:
        return []
    ordered = sorted(trades, key=lambda t: t["range_pct"])
    n = len(ordered) // 3
    out = []
    for k, chunk in enumerate((ordered[:n], ordered[n:2 * n], ordered[2 * n:])):
        s = stats(chunk)
        out.append((("narrow", "middle", "wide")[k],
                    chunk[0]["range_pct"], chunk[-1]["range_pct"],
                    s["trades"], s["win_rate"], s["net"]))
    return out


def buy_and_hold(all_candles, position_usd):
    if len(all_candles) < 2:
        return 0.0
    first, last = float(all_candles[0]["close"]), float(all_candles[-1]["close"])
    return (last - first) / first * position_usd


def walk_forward(config, sessions, folds=5):
    """Chronological folds. Never shuffled — these are time series."""
    size = len(sessions) // folds
    out = []
    for k in range(folds):
        chunk = sessions[k * size:(k + 1) * size if k < folds - 1 else len(sessions)]
        if not chunk:
            continue
        _, t = run(config, chunk)
        s = stats(t)
        out.append((chunk[0][0], chunk[-1][0], s.get("trades", 0), s.get("net", 0.0)))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="SPY", choices=sorted(SPEC))
    parser.add_argument("--ref-candle", type=int, default=1,
                        help="session bar used as the reference (0 = opening bar)")
    parser.add_argument("--position", type=float, default=100.0)
    parser.add_argument("--variants", action="store_true",
                        help="also run the alternative exit rules")
    args = parser.parse_args()

    spec = SPEC[args.instrument]
    base = dict(ref_index=args.ref_candle, position_usd=args.position,
                fee_per_side=spec["fee_per_side"],
                slippage_per_side=spec["slippage_per_side"])

    sessions, all_candles = load_sessions(args.instrument)
    if not sessions:
        print(f"  no stored candles for {args.instrument}")
        return 1
    print(f"  {args.instrument}: {len(sessions)} sessions, {len(all_candles)} candles, "
          f"reference bar #{args.ref_candle}, "
          f"slippage {spec['slippage_per_side'] * 200:.2f}% round trip, "
          f"position ${args.position:.0f}")

    names = list(VARIANTS) if args.variants else [next(iter(VARIANTS))]
    for name in names:
        config = sr.Config(**{**base, **VARIANTS[name]})
        decisions, trades = run(config, sessions, args.instrument)
        show(name, decisions, trades, args.position)
        if name == next(iter(VARIANTS)):
            print(f"\n  by month: {by_month(trades)}")
            print(f"  buy-and-hold over the same window: "
                  f"${buy_and_hold(all_candles, args.position):+.2f}")
            print("\n  by reference-candle width:")
            for label, lo, hi, n, wr, net in by_range_tercile(trades):
                print(f"    {label:<7} {lo:.2f}%..{hi:.2f}%   {n:>3} trades   "
                      f"win {wr:.0f}%   net ${net:+.2f}")
            print("\n  walk-forward folds (chronological):")
            for start, end, n, net in walk_forward(config, sessions):
                print(f"    {start} .. {end}   {n:>3} trades   net ${net:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
