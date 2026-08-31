#!/usr/bin/env python3
"""
walkforward.py — validate a strategy the way it would actually be run.

Every result in this project came from one 60/40 split. One split is one draw.
It answers "did this work on that particular later window", which is a coin
flip dressed as evidence — and it is how a timeframe got chosen, reversed, and
chosen again inside a week.

Walk-forward asks the question you actually care about: if you had re-fit the
parameters periodically and traded the next window with them, what would have
happened? Each test window is genuinely unseen at the time its parameters are
chosen, and there are many of them instead of one.

What it reports is deliberately not a single number:

  fold results     each window separately, so consistency is visible
  hit rate         fraction of windows that were profitable
  vs buy-and-hold  the honest null — holding the same instrument, no per-trade
                   cost — because beating zero is not the bar
  parameter churn  how much the re-fit parameters moved between folds. High
                   churn means the fit is tracking noise, and it is the single
                   most useful warning here: a strategy whose best parameters
                   change every window does not have a stable edge, however
                   good its aggregate looks.

    .venv/bin/python walkforward.py --market stocks
    .venv/bin/python walkforward.py --market crypto --folds 6
"""
import argparse
import statistics
import sys

import compare_strategies as cs
from utils import benchmark


def folds(series, n_folds, train_frac=0.6):
    """
    Rolling (train, test) index pairs.

    Anchored: training always starts at the beginning and grows, which is what
    re-fitting on all history available at the time actually looks like.
    """
    total = len(series)
    if total < 100 or n_folds < 1:
        return []
    first_train = int(total * train_frac)
    remaining = total - first_train
    if remaining < n_folds:
        return []
    step = remaining // n_folds
    out = []
    for i in range(n_folds):
        train_end = first_train + i * step
        test_end = train_end + step if i < n_folds - 1 else total
        if test_end - train_end < 20:
            continue
        out.append((slice(0, train_end), slice(train_end, test_end)))
    return out


def fit(closes, grid, builder, builder_args):
    """Best parameters on this training window. Returns (sl, tp) or None."""
    best = None
    for sl, tp in grid:
        enter, leave = builder(closes, *builder_args)
        result = cs.run(closes, enter, leave, sl, tp, 1.5, 1.0)
        if result["trades"] < 3:
            continue
        if best is None or result["net"] > best[0]:
            best = (result["net"], sl, tp)
    return (best[1], best[2]) if best else None


def walk(closes, builder, builder_args, grid, n_folds=5):
    """Fit on each training window, trade the next unseen one."""
    out = []
    for train, test in folds(closes, n_folds):
        params = fit(closes[train], grid, builder, builder_args)
        if params is None:
            continue
        sl, tp = params
        segment = closes[test]
        enter, leave = builder(segment, *builder_args)
        result = cs.run(segment, enter, leave, sl, tp, 1.5, 1.0)
        hold = benchmark.buy_and_hold_return(list(segment))
        out.append({
            "sl": sl, "tp": tp,
            "trades": result["trades"],
            "net": result["net"],
            "strategy_return": result["net"] / 100.0,
            "hold_return": hold,
            "excess": (result["net"] / 100.0 - hold) if hold is not None else None,
        })
    return out


def summarise(results):
    if not results:
        return {"folds": 0}
    nets = [r["net"] for r in results]
    excess = [r["excess"] for r in results if r["excess"] is not None]
    sls = [r["sl"] for r in results]
    tps = [r["tp"] for r in results]

    def churn(values):
        if len(values) < 2:
            return 0.0
        mean = statistics.mean(values)
        return statistics.stdev(values) / mean if mean else 0.0

    return {
        "folds": len(results),
        "total_net": sum(nets),
        "profitable_folds": sum(1 for n in nets if n > 0),
        "hit_rate": 100.0 * sum(1 for n in nets if n > 0) / len(nets),
        "beat_hold_folds": sum(1 for e in excess if e > 0),
        "mean_excess": statistics.mean(excess) if excess else None,
        "trades": sum(r["trades"] for r in results),
        "sl_churn": churn(sls),
        "tp_churn": churn(tps),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", choices=("stocks", "crypto"), default="stocks")
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    from utils import env_loader, registry
    env_loader.load()

    grid = [(sl, tp) for sl in (1.0, 2.0, 3.0, 5.0) for tp in (1.0, 2.0, 3.0, 5.0, 8.0)]

    if args.market == "stocks":
        from utils import stock_data
        cs.FEE = cs.STOCK_FEE
        symbols = registry.stock_tickers()
        loader = lambda s: stock_data.closes(s, "Hour", 400)
        builder, builder_args = cs.rsi_mean_reversion, (14, 35, 65)
    else:
        import auto_learner as al
        import pandas as pd
        cs.FEE = cs.CRYPTO_FEE
        symbols = registry.crypto_pairs()

        def loader(s):
            frame = al.fetch_candles(s, "SIX_HOUR", 400)
            return (pd.to_numeric(frame["close"]).to_numpy()
                    if not frame.empty else [])
        builder, builder_args = cs.sma_crossover, (10, 40, 100.0, 30.0)

    print(f"\n  Walk-forward — {args.market}, {args.folds} folds, "
          f"{cs.FEE*200:.2f}% round trip\n")
    print(f"  {'symbol':8}{'folds':>6}{'net':>9}{'hit%':>7}{'beat hold':>11}"
          f"{'SL churn':>10}{'TP churn':>10}")

    pooled = []
    for symbol in symbols:
        closes = loader(symbol)
        if len(closes) < 200:
            continue
        results = walk(list(closes), builder, builder_args, grid, args.folds)
        if not results:
            continue
        pooled.extend(results)
        s = summarise(results)
        print(f"  {symbol[:7]:8}{s['folds']:>6}{s['total_net']:>+9.2f}"
              f"{s['hit_rate']:>6.0f}%{s['beat_hold_folds']:>7}/{s['folds']:<3}"
              f"{s['sl_churn']:>10.2f}{s['tp_churn']:>10.2f}")

    if pooled:
        s = summarise(pooled)
        print(f"\n  POOLED  {s['folds']} folds, {s['trades']} trades, "
              f"net {s['total_net']:+.2f}")
        print(f"          profitable folds {s['profitable_folds']}/{s['folds']} "
              f"({s['hit_rate']:.0f}%)")
        print(f"          beat buy-and-hold {s['beat_hold_folds']}/{s['folds']}")
        if s["mean_excess"] is not None:
            print(f"          mean excess vs holding {s['mean_excess']*100:+.2f}%")
        print(f"          parameter churn — SL {s['sl_churn']:.2f}, TP {s['tp_churn']:.2f}"
              f"   {'(unstable: fit is tracking noise)' if max(s['sl_churn'], s['tp_churn']) > 0.5 else '(stable)'}")
    else:
        print("\n  No symbol produced a usable walk-forward.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
