#!/usr/bin/env python3
"""
Crypto strategy classes, head to head, on stored candles only.

The SMA crossover is parked because at a 1.0% round trip (0.40% maker per
side plus 0.10% slippage) it cannot clear its own costs, and thousands of
parameter combinations never changed that. So the question is not "which
parameters" but "which class of rule": one whose typical trade moves far more
than 1%, which means holding for days to weeks, not hours.

Every class below is long-only (Coinbase spot), pays the same costs, and is
scored on the same nine coins over the same window. Buy-and-hold is the bar:
equal capital, held for the same window, paying the same two fees. A rule
that makes money but less than holding is a worse way to own the coin.

Two views are reported, and the second matters more than the first.

  full window     one number per class, easily flattered by a bull run
  walk-forward    five chronological folds, each scored on its own bars, with
                  the count of folds where the class BEAT HOLDING in that fold.
                  A class that beats holding in the down folds and lags in the
                  up folds is the profile worth having; one that only wins in
                  the aggregate is riding the market.

Six-hour candles are aggregated to UTC days for the daily rules. All signals
act on closed bars and fill at the next bar's open, plus slippage.

    .venv/bin/python backtest_crypto_classes.py
    .venv/bin/python backtest_crypto_classes.py --position 100 --folds 5
"""
import argparse
import os
import statistics as st
import sys
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

from market_data.candle_store import CandleStore          # noqa: E402

PAIRS = ["BTC-USDC", "ETH-USDC", "SOL-USDC", "ADA-USDC", "LINK-USDC",
         "NEAR-USDC", "SUI-USDC", "XRP-USDC", "RENDER-USDC"]
FEE = 0.004            # maker, per side
SLIP = 0.001           # per side


# ── data ────────────────────────────────────────────────────────────────────

def load_six_hour():
    out = {}
    with CandleStore() as store:
        for pair in PAIRS:
            rows = store.get("coinbase", pair, "SIX_HOUR")
            if len(rows) > 300:
                out[pair] = [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3],
                              "close": r[4], "volume": r[5]} for r in rows]
    return out


def to_daily(bars):
    """UTC days from six-hour bars. A day with fewer than four bars is dropped."""
    days = defaultdict(list)
    for b in bars:
        days[b["ts"] // 86400].append(b)
    out = []
    for day in sorted(days):
        chunk = days[day]
        if len(chunk) < 4:
            continue
        out.append({"ts": day * 86400, "open": chunk[0]["open"],
                    "high": max(c["high"] for c in chunk),
                    "low": min(c["low"] for c in chunk),
                    "close": chunk[-1]["close"],
                    "volume": sum(c["volume"] for c in chunk)})
    return out


# ── indicators (plain lists; nothing here needs pandas) ────────────────────

def sma(values, n):
    out = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def rsi(values, n=14):
    out = [None] * len(values)
    gain = loss = 0.0
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            gain += g / n
            loss += l / n
            if i < n:
                continue
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l) / n
        out[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)
    return out


def rolling_max(values, n):
    """Highest of the PRIOR n values, excluding the current one."""
    out = [None] * len(values)
    for i in range(n, len(values)):
        out[i] = max(values[i - n:i])
    return out


def rolling_min(values, n):
    out = [None] * len(values)
    for i in range(n, len(values)):
        out[i] = min(values[i - n:i])
    return out


# ── strategy classes: each returns (enter(i), leave(i)) over closed bars ───

def sma_cross(bars, short_n, long_n):
    c = [b["close"] for b in bars]
    s, l = sma(c, short_n), sma(c, long_n)

    def enter(i):
        return (s[i - 1] is not None and l[i - 1] is not None
                and s[i - 1] <= l[i - 1] and s[i] > l[i])

    def leave(i):
        return s[i - 1] is not None and s[i - 1] >= l[i - 1] and s[i] < l[i]
    return enter, leave


def rsi_reversion(bars, low, high):
    r = rsi([b["close"] for b in bars])

    def enter(i):
        return r[i - 1] is not None and r[i - 1] < low <= r[i]

    def leave(i):
        return r[i - 1] is not None and r[i - 1] > high >= r[i]
    return enter, leave


def donchian(bars, entry_n, exit_n):
    c = [b["close"] for b in bars]
    hi, lo = rolling_max(c, entry_n), rolling_min(c, exit_n)

    def enter(i):
        return hi[i] is not None and c[i] > hi[i]

    def leave(i):
        return lo[i] is not None and c[i] < lo[i]
    return enter, leave


def trend_filter(bars, n, band_pct):
    """Own the coin while it closes above its n-bar average, with a band
    around the average so a flat market does not flip the rule every bar."""
    c = [b["close"] for b in bars]
    m = sma(c, n)
    band = band_pct / 100.0

    def enter(i):
        return m[i] is not None and c[i] > m[i] * (1.0 + band)

    def leave(i):
        return m[i] is not None and c[i] < m[i] * (1.0 - band)
    return enter, leave


# ── execution: one cost model for every class ──────────────────────────────

def simulate(bars, enter, leave, position_usd, stop_pct=None):
    """
    Signals on bar i's close; fills at bar i+1's open. Optional hard stop
    checked against the bar's low, filled at the stop level.
    """
    in_pos = False
    entry = qty = 0.0
    trades = []
    entry_i = None
    for i in range(1, len(bars) - 1):
        nxt = bars[i + 1]
        if in_pos:
            stopped = stop_pct is not None and bars[i]["low"] <= entry * (1.0 - stop_pct / 100.0)
            if stopped or leave(i):
                price = entry * (1.0 - stop_pct / 100.0) if stopped else nxt["open"]
                fill = price * (1.0 - SLIP)
                proceeds = qty * fill * (1.0 - FEE)
                trades.append({"net": proceeds - position_usd, "bars": i - entry_i,
                               "entry_ts": bars[entry_i]["ts"], "exit_ts": nxt["ts"],
                               "reason": "stop" if stopped else "rule"})
                in_pos = False
        elif enter(i):
            fill = nxt["open"] * (1.0 + SLIP)
            qty = position_usd * (1.0 - FEE) / fill
            entry, entry_i, in_pos = fill, i + 1, True
    if in_pos:
        fill = bars[-1]["close"] * (1.0 - SLIP)
        proceeds = qty * fill * (1.0 - FEE)
        trades.append({"net": proceeds - position_usd, "bars": len(bars) - 1 - entry_i,
                       "entry_ts": bars[entry_i]["ts"], "exit_ts": bars[-1]["ts"],
                       "reason": "end"})
    return trades


def hold(bars, position_usd):
    """Same capital, same two fees, held across the window."""
    buy = bars[0]["open"] * (1.0 + SLIP)
    sell = bars[-1]["close"] * (1.0 - SLIP)
    qty = position_usd * (1.0 - FEE) / buy
    return qty * sell * (1.0 - FEE) - position_usd


def drawdown(trades):
    equity = peak = dd = 0.0
    for t in sorted(trades, key=lambda t: t["exit_ts"]):
        equity += t["net"]
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return dd


# ── the candidates ──────────────────────────────────────────────────────────

CLASSES = [
    # name, timeframe, builder, args, hard stop %
    ("SMA 10/40 six-hour (parked live rule)",   "6h", sma_cross,     (10, 40),     5.0),
    ("SMA 5/30 six-hour (SIM profile)",         "6h", sma_cross,     (5, 30),      5.0),
    ("RSI 30/70 six-hour",                      "6h", rsi_reversion, (30.0, 70.0), 5.0),
    ("RSI 30/55 daily",                         "1d", rsi_reversion, (30.0, 55.0), 8.0),
    ("Donchian 20/10 daily",                    "1d", donchian,      (20, 10),     None),
    ("Donchian 40/20 daily",                    "1d", donchian,      (40, 20),     None),
    ("Donchian 55/20 daily (turtle)",           "1d", donchian,      (55, 20),     None),
    ("Trend: above 50-day SMA, 2% band",        "1d", trend_filter,  (50, 2.0),    None),
    ("Trend: above 100-day SMA, 2% band",       "1d", trend_filter,  (100, 2.0),   None),
    ("Trend: above 20-day SMA, 3% band",        "1d", trend_filter,  (20, 3.0),    None),
]


def score(series, cls, position_usd):
    name, tf, builder, args, stop = cls
    all_trades, per_coin = [], {}
    for pair, bars in series[tf].items():
        enter, leave = builder(bars, *args)
        t = simulate(bars, enter, leave, position_usd, stop)
        all_trades += t
        per_coin[pair] = (sum(x["net"] for x in t), hold(bars, position_usd), len(t))
    return all_trades, per_coin


def folds_of(bars, k):
    size = len(bars) // k
    return [bars[i * size:(i + 1) * size if i < k - 1 else len(bars)] for i in range(k)]


def walk_forward(series, cls, position_usd, k):
    """Signals are computed once over the full series, and each trade is
    attributed to the fold its EXIT falls in. Holding is measured over the
    same fold window. Recomputing indicators inside each fold would blind a
    50-day rule for half of a 103-day fold, which punishes slow rules for
    being slow rather than for being wrong."""
    name, tf, builder, args, stop = cls
    out = []
    bounds = {pair: [(f[0]["ts"], f[-1]["ts"]) for f in folds_of(bars, k)]
              for pair, bars in series[tf].items()}
    per_fold = [[0.0, 0.0, 0] for _ in range(k)]
    for pair, bars in series[tf].items():
        enter, leave = builder(bars, *args)
        trades = simulate(bars, enter, leave, position_usd, stop)
        for f, (lo, hi) in enumerate(bounds[pair]):
            per_fold[f][1] += hold(folds_of(bars, k)[f], position_usd)
            for t in trades:
                if lo <= t["exit_ts"] <= hi + 1:
                    per_fold[f][0] += t["net"]
                    per_fold[f][2] += 1
    return [tuple(x) for x in per_fold]


def rotation(daily, lookback, top_k, capital, absolute=False, every=7):
    """
    Cross-sectional momentum. Every `every` days, rank coins by trailing
    `lookback`-day return and hold the top_k in equal weight. With
    `absolute`, a coin also needs a positive trailing return, else that slot
    sits in cash. Costs are paid only on the slots that change hands.

    Returns (trades, fold_nets) where each trade is one held slot from entry
    to exit, so it is comparable with the per-coin classes.
    """
    pairs = sorted(daily)
    n = min(len(daily[p]) for p in pairs)
    closes = {p: [b["close"] for b in daily[p][-n:]] for p in pairs}
    opens = {p: [b["open"] for b in daily[p][-n:]] for p in pairs}
    ts = [b["ts"] for b in daily[pairs[0]][-n:]]
    slot_usd = capital / top_k
    held = {}                       # pair -> (entry_fill, qty, entry_ts)
    trades = []
    for i in range(lookback, n - 1, every):
        ranked = sorted(pairs, key=lambda p: closes[p][i] / closes[p][i - lookback], reverse=True)
        want = [p for p in ranked[:top_k]
                if not absolute or closes[p][i] > closes[p][i - lookback]]
        for p in list(held):
            if p not in want:
                fill = opens[p][i + 1] * (1.0 - SLIP)
                entry, qty, ets = held.pop(p)
                trades.append({"net": qty * fill * (1.0 - FEE) - slot_usd,
                               "bars": (ts[i + 1] - ets) // 86400,
                               "entry_ts": ets, "exit_ts": ts[i + 1], "reason": "rotate"})
        for p in want:
            if p not in held:
                fill = opens[p][i + 1] * (1.0 + SLIP)
                held[p] = (fill, slot_usd * (1.0 - FEE) / fill, ts[i + 1])
    for p, (entry, qty, ets) in held.items():
        fill = closes[p][-1] * (1.0 - SLIP)
        trades.append({"net": qty * fill * (1.0 - FEE) - slot_usd, "bars": (ts[-1] - ets) // 86400,
                       "entry_ts": ets, "exit_ts": ts[-1], "reason": "end"})
    return trades


ROTATIONS = [
    ("Rotation: top 3 by 30-day return, weekly",            dict(lookback=30, top_k=3, absolute=False)),
    ("Rotation: top 3 by 30-day, only if positive",         dict(lookback=30, top_k=3, absolute=True)),
    ("Rotation: top 3 by 60-day, only if positive",         dict(lookback=60, top_k=3, absolute=True)),
    ("Rotation: top 1 by 30-day, only if positive",         dict(lookback=30, top_k=1, absolute=True)),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--position", type=float, default=100.0)
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    six = load_six_hour()
    if not six:
        print("  no stored six-hour candles")
        return 1
    series = {"6h": six, "1d": {p: to_daily(b) for p, b in six.items()}}
    any_pair = next(iter(six))
    from datetime import datetime, timezone
    start = datetime.fromtimestamp(six[any_pair][0]["ts"], timezone.utc).date()
    end = datetime.fromtimestamp(six[any_pair][-1]["ts"], timezone.utc).date()
    print(f"  {len(six)} coins · {start} .. {end} · {len(six[any_pair])} six-hour bars, "
          f"{len(series['1d'][any_pair])} daily · fee {FEE*100:.2f}%/side + slippage "
          f"{SLIP*100:.2f}%/side · ${args.position:.0f} per coin")

    total_hold = sum(hold(b, args.position) for b in six.values())
    print(f"  buy-and-hold, all coins, full window: ${total_hold:+.2f}")
    fold_holds = [sum(hold(folds_of(b, args.folds)[f], args.position) for b in six.values())
                  for f in range(args.folds)]
    print("  buy-and-hold by fold: " + "  ".join(f"${h:+.0f}" for h in fold_holds))

    print(f"\n  {'class':40} {'trades':>6} {'win%':>5} {'avg hold':>9} {'net':>9} "
          f"{'vs hold':>9} {'max DD':>8} {'coins>hold':>10} {'folds>hold':>10} {'folds>0':>8}")
    print("  " + "-" * 128)
    for cls in CLASSES:
        trades, per_coin = score(series, cls, args.position)
        if not trades:
            print(f"  {cls[0]:40} no trades")
            continue
        wins = sum(1 for t in trades if t["net"] > 0)
        net = sum(t["net"] for t in trades)
        bar_hours = 6 if cls[1] == "6h" else 24
        avg_hold_days = st.mean(t["bars"] for t in trades) * bar_hours / 24.0
        beat = sum(1 for s, h, _ in per_coin.values() if s > h)
        wf = walk_forward(series, cls, args.position, args.folds)
        f_beat = sum(1 for s, h, _ in wf if s > h)
        f_pos = sum(1 for s, _, _ in wf if s > 0)
        print(f"  {cls[0]:40} {len(trades):>6} {100*wins/len(trades):>5.0f} "
              f"{avg_hold_days:>7.1f}d {net:>+9.2f} {net-total_hold:>+9.2f} "
              f"{drawdown(trades):>8.2f} {beat:>7}/{len(per_coin)} "
              f"{f_beat:>7}/{args.folds} {f_pos:>5}/{args.folds}")
        print("  " + " " * 40 + " folds: " + "  ".join(
            f"${s:+.0f} (hold ${h:+.0f}, {n}t)" for s, h, n in wf))

    # Rotation deploys the same total capital (9 x position) across its slots.
    capital = args.position * len(six)
    daily = series["1d"]
    any_daily = daily[any_pair]
    bounds = [(f[0]["ts"], f[-1]["ts"]) for f in folds_of(any_daily, args.folds)]
    print(f"\n  cross-sectional, ${capital:.0f} total capital, same fees on every slot change:")
    for name, kw in ROTATIONS:
        trades = rotation(daily, capital=capital, **kw)
        if not trades:
            print(f"  {name:40} no trades")
            continue
        wins = sum(1 for t in trades if t["net"] > 0)
        net = sum(t["net"] for t in trades)
        wf = []
        for f, (lo, hi) in enumerate(bounds):
            s_ = sum(t["net"] for t in trades if lo <= t["exit_ts"] <= hi + 1)
            wf.append((s_, fold_holds[f], sum(1 for t in trades if lo <= t["exit_ts"] <= hi + 1)))
        f_beat = sum(1 for s_, h, _ in wf if s_ > h)
        f_pos = sum(1 for s_, _, _ in wf if s_ > 0)
        print(f"  {name:40} {len(trades):>6} {100*wins/len(trades):>5.0f} "
              f"{st.mean(t['bars'] for t in trades):>7.1f}d {net:>+9.2f} {net-total_hold:>+9.2f} "
              f"{drawdown(trades):>8.2f} {'':>10} {f_beat:>7}/{args.folds} {f_pos:>5}/{args.folds}")
        print("  " + " " * 40 + " folds: " + "  ".join(
            f"${s_:+.0f} (hold ${h:+.0f}, {n}t)" for s_, h, n in wf))
    return 0


if __name__ == "__main__":
    sys.exit(main())
