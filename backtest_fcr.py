#!/usr/bin/env python3
"""
Backtest the First Candle Rule, on stored candles only.

Reads the local store, never the network, so a run is reproducible and cannot
quietly change between invocations.

Exits are checked in a fixed priority on each subsequent candle: stop, target,
confirmed close back inside the opening range, then the session or hold limit.
Stop and target are checked against the candle's LOW and HIGH; when a single
candle spans both, the stop is assumed to fill first. That is the pessimistic
assumption and it is deliberate — the optimistic one turns losing strategies
into winning ones and cannot be distinguished from skill afterwards.

    .venv/bin/python backtest_fcr.py --instrument SPY
    .venv/bin/python backtest_fcr.py --instrument BTC-USDC
    .venv/bin/python backtest_fcr.py --instrument SPY --ablate
"""
import argparse
import os
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

from market_data.candle_store import CandleStore          # noqa: E402
from strategies import first_candle_rule as fcr           # noqa: E402

STOP, TARGET, INSIDE, TIME = "stop", "target", "back_inside_range", "time_exit"

SPEC = {
    "SPY": dict(venue="alpaca", granularity="Min15", tz="America/New_York",
                fee_per_side=0.0, slippage_per_side=0.0005, max_bars=None),
    "BTC-USDC": dict(venue="coinbase", granularity="ONE_HOUR", tz="UTC",
                     fee_per_side=0.004, slippage_per_side=0.001, max_bars=24),
}


def load_sessions(instrument):
    """Candles grouped into sessions, oldest first, with prior history kept."""
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


def simulate_exit(decision, session, config, max_bars):
    """Walk forward from entry. Returns (exit_ts, exit_price, reason, mfe, mae)."""
    entry_idx = next((i for i, c in enumerate(session)
                      if int(c["ts"]) == decision.entry_ts), None)
    if entry_idx is None:
        return None
    entry = decision.entry_price
    mfe = mae = 0.0
    limit = len(session) if max_bars is None else min(len(session), entry_idx + 1 + max_bars)

    for candle in session[entry_idx:limit]:
        high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])
        mfe = max(mfe, (high - entry) / entry * 100.0)
        mae = min(mae, (low - entry) / entry * 100.0)

        # Stop first when a candle spans both: the pessimistic assumption.
        if low <= decision.stop_price:
            return int(candle["ts"]), decision.stop_price, STOP, mfe, mae
        if high >= decision.target_price:
            return int(candle["ts"]), decision.target_price, TARGET, mfe, mae
        if close < decision.open_high:
            return int(candle["ts"]), close, INSIDE, mfe, mae

    last = session[limit - 1]
    return int(last["ts"]), float(last["close"]), TIME, mfe, mae


def run(instrument, config, sessions, all_candles, disable=None):
    """Evaluate every session. `disable` drops one filter, for ablation."""
    disable = disable or set()
    if "volume" in disable:
        config = fcr.Config(**{**config.__dict__, "volume_multiple": 0.0})
    if "rsi" in disable:
        config = fcr.Config(**{**config.__dict__, "rsi_min": 0.0, "rsi_max": 100.0})
    if "ema" in disable:
        # Setting ema_period=1 makes EMA equal close, so `close <= ema` is
        # always true and EVERY signal is rejected. That produced "0 signals"
        # and read like a finding about the filter. Disable the check itself.
        config = fcr.Config(**{**config.__dict__, "ema_period": 200})

    spec = SPEC[instrument]
    by_ts = {int(c["ts"]): i for i, c in enumerate(all_candles)}
    decisions, trades = [], []

    for label, session in sessions:
        first_idx = by_ts[int(session[0]["ts"])]
        history = all_candles[max(0, first_idx - 400):first_idx]
        d = fcr.evaluate_session(session, config, label, instrument, history,
                                 skip=disable)
        decisions.append(d)
        if not d.accepted:
            continue
        outcome = simulate_exit(d, session, config, spec["max_bars"])
        if outcome is None:
            continue
        exit_ts, exit_price, reason, mfe, mae = outcome
        exit_price *= (1.0 - config.slippage_per_side)
        qty = config.position_usd / d.entry_price
        gross = (exit_price - d.entry_price) * qty
        fees = config.position_usd * config.fee_per_side * 2
        trades.append({
            "session": label, "entry_ts": d.entry_ts, "exit_ts": exit_ts,
            "entry": d.entry_price, "exit": exit_price, "reason": reason,
            "gross": gross, "fees": fees, "net": gross - fees,
            "mfe": mfe, "mae": mae,
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
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
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
        "false_breakout_rate": 100.0 * sum(
            1 for t in trades if t["reason"] in (STOP, INSIDE)) / len(trades),
    }


def show(name, decisions, trades):
    s = stats(trades)
    reasons = Counter(d.reason for d in decisions if not d.accepted)
    print(f"\n  ── {name} ──")
    print(f"  sessions {len(decisions)}   signals {sum(1 for d in decisions if d.accepted)}"
          f"   trades {s.get('trades', 0)}")
    if not s:
        print("  no completed trades")
    else:
        print(f"  win rate {s['win_rate']:.0f}%   net ${s['net']:+.4f}   "
              f"expectancy ${s['expectancy']:+.5f}/trade")
        print(f"  avg win ${s['avg_win']:+.4f}   avg loss ${s['avg_loss']:+.4f}   "
              f"profit factor {s['profit_factor']:.2f}")
        print(f"  max drawdown ${s['max_drawdown']:.4f}   max consec losses "
              f"{s['max_consec_losses']}   avg hold {s['avg_hours']:.1f}h")
        print(f"  false-breakout rate {s['false_breakout_rate']:.0f}%   "
              f"largest single win ${s['largest_win']:+.4f} "
              f"({100*s['largest_win']/s['net']:.0f}% of net)" if s['net'] else "")
    print(f"  rejections: {dict(reasons.most_common(6))}")
    return s


def by_month(trades):
    grouped = defaultdict(list)
    for t in trades:
        grouped[t["session"][:7]].append(t["net"])
    return {m: (len(v), sum(v)) for m, v in sorted(grouped.items())}


def buy_and_hold(all_candles, position_usd=10.0):
    """Same capital, held across the whole tested window."""
    if len(all_candles) < 2:
        return 0.0
    first = float(all_candles[0]["close"])
    last = float(all_candles[-1]["close"])
    return (last - first) / first * position_usd


def walk_forward(instrument, config, sessions, all_candles, folds=5):
    """Chronological folds. Never shuffled — these are time series."""
    size = len(sessions) // folds
    out = []
    for k in range(folds):
        chunk = sessions[k * size:(k + 1) * size if k < folds - 1 else len(sessions)]
        if not chunk:
            continue
        _, t = run(instrument, config, chunk, all_candles)
        s = stats(t)
        out.append((chunk[0][0], chunk[-1][0], s.get("trades", 0), s.get("net", 0.0)))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", required=True, choices=sorted(SPEC))
    parser.add_argument("--ablate", action="store_true")
    args = parser.parse_args()

    spec = SPEC[args.instrument]
    config = fcr.Config(fee_per_side=spec["fee_per_side"],
                        slippage_per_side=spec["slippage_per_side"])

    sessions, all_candles = load_sessions(args.instrument)
    if not sessions:
        print(f"  no stored candles for {args.instrument}")
        return 1
    print(f"  {args.instrument}: {len(sessions)} sessions, {len(all_candles)} candles, "
          f"fee {spec['fee_per_side']*200:.2f}% round trip, "
          f"slippage {spec['slippage_per_side']*200:.2f}%")

    decisions, trades = run(args.instrument, config, sessions, all_candles)
    show("all filters", decisions, trades)

    print(f"\n  by month: {by_month(trades)}")
    print(f"  buy-and-hold over the same window: ${buy_and_hold(all_candles):+.4f}")
    print(f"\n  walk-forward folds (chronological):")
    for start, end, n, net in walk_forward(args.instrument, config, sessions, all_candles):
        print(f"    {start} .. {end}   {n:>3} trades   net ${net:+.4f}")

    if args.ablate:
        for drop in ("volume", "rsi", "ema"):
            d2, t2 = run(args.instrument, config, sessions, all_candles, {drop})
            show(f"without {drop} filter", d2, t2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
