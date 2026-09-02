#!/usr/bin/env python3
"""
screen_candidates.py — rank symbols by how well they suit the strategy.

Most screeners rank by recent return, which selects whatever went up lately and
tells you nothing about whether a strategy will work on it. This ranks on the
mechanism instead.

Mean reversion needs two things, and the second is the one that bites:

  1. the symbol must reach oversold, or there are no entries at all
  2. it must then RECOVER

A symbol that repeatedly hits oversold and does not bounce is not an
opportunity, it is a downtrend — and mean reversion will buy it the whole way
down. That profile scores well on entry frequency and is precisely the failure
mode, so frequency alone is never enough to qualify a symbol here.

Crypto and stocks are screened under their own cost model. Equities are
commission-free (~0.20% round trip, slippage only); Coinbase is ~0.80% maker. The
same symbol behaviour can qualify in one market and fail in the other purely on
that, so results never transfer between them.

Usage:
    .venv/bin/python screen_candidates.py --market stocks
    .venv/bin/python screen_candidates.py --market crypto
    .venv/bin/python screen_candidates.py --market stocks --universe AAPL,MSFT
    .venv/bin/python screen_candidates.py --market stocks --strategy sma
"""
import argparse
import json
import os
import sys
import time

import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))

# Liquid, widely held names across sectors and asset classes. A starting set to
# screen, not a recommendation to hold any of them.
STOCK_UNIVERSE = [
    "SPY", "QQQ", "DIA", "IWM", "EEM", "EFA",          # broad index
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLU",   # sectors
    "GLD", "SLV", "TLT", "HYG",                        # commodities, bonds
    "AAPL", "MSFT", "NVDA", "AMD", "INTC", "AMZN",     # large cap tech
    "GOOGL", "META", "TSLA", "NFLX",
    "JPM", "BAC", "WFC", "XOM", "CVX", "JNJ", "PFE",   # financials, energy, health
    "KO", "PG", "WMT", "DIS", "BA", "CAT", "LPL",
]

CRYPTO_UNIVERSE = [
    "BTC-USDC", "ETH-USDC", "SOL-USDC", "ADA-USDC", "LINK-USDC",
    "NEAR-USDC", "SUI-USDC", "XRP-USDC", "RENDER-USDC",
    "AVAX-USDC", "DOGE-USDC", "APT-USDC", "ICP-USDC", "DOT-USDC",
    "LTC-USDC", "ATOM-USDC", "FIL-USDC", "ARB-USDC",
]

# Neighbouring thresholds. A result that holds at one setting and collapses
# either side of it is a lucky cell, not an edge — the check the crypto grid
# search never had.
MR_THRESHOLDS = [(25.0, 75.0), (30.0, 70.0), (35.0, 65.0)]
SMA_WINDOWS = [(10, 30), (10, 40), (20, 60)]

MIN_ENTRIES = 4          # below this, win rate is not a number worth reading
MIN_BARS = 250


def load_env():
    try:
        with open(os.path.join(DIR, ".env")) as handle:
            for line in handle:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
    except OSError:
        pass


def get_closes(symbol, market, timeframe, lookback):
    if market == "stocks":
        from utils import stock_data
        return stock_data.closes(symbol, timeframe, lookback)
    import auto_learner as al
    frame = al.fetch_candles(symbol, timeframe, lookback)
    if frame.empty:
        return np.array([])
    import pandas as pd
    return pd.to_numeric(frame["close"]).to_numpy()


def screen(symbol, closes, strategy):
    """
    Score one symbol. Returns None when there is not enough to judge.

    Reported on the out-of-sample half only. In-sample figures are the maximum
    of many noisy draws and flatter every symbol.
    """
    import compare_strategies as cs

    split = int(len(closes) * 0.6)
    segment = closes[split:]
    if len(segment) < 100:
        return None

    variants = (MR_THRESHOLDS if strategy == "mr" else SMA_WINDOWS)
    results = []
    for params in variants:
        if strategy == "mr":
            enter, leave = cs.rsi_mean_reversion(segment, 14, *params)
        else:
            enter, leave = cs.sma_crossover(segment, *params)
        results.append(cs.run(segment, enter, leave, 5.0, 8.0))

    nets = [r["net"] for r in results]
    trades = [r["trades"] for r in results]
    best = max(range(len(results)), key=lambda i: nets[i])

    # How often the entry condition fires at all, independent of outcome.
    rsi = cs.rsi_series(segment, 14)
    opportunities = sum(1 for i in range(1, len(segment))
                        if rsi[i - 1] < 30 <= rsi[i]) if strategy == "mr" else trades[best]

    hold = cs.al.buy_and_hold_backtest(segment)["net_profit"]
    agreeing = sum(1 for n in nets if n > 0)

    # Cast out of numpy: np.bool_ and np.float64 are not JSON serialisable,
    # and this dict is written to disk for the dashboard.
    return {
        "symbol": symbol,
        "opportunities": int(opportunities),
        "net": float(nets[best]),
        "trades": int(trades[best]),
        "win_rate": float(results[best]["win_rate"]),
        "params": variants[best],
        "hold": float(hold),
        "beats_hold": bool(nets[best] > hold),
        # Fraction of neighbouring settings that are also profitable. This is
        # what separates a plateau from a single lucky cell.
        "agreement": float(agreeing / len(nets)),
        "all_nets": [float(n) for n in nets],
    }


def verdict(row):
    """Short reason a symbol does or does not qualify."""
    if row["trades"] < MIN_ENTRIES:
        return "skip", f"only {row['trades']} entries"
    if row["net"] <= 0:
        if row["opportunities"] >= 8:
            # The dangerous profile: plenty of dips, no recoveries.
            return "avoid", "dips often, does not recover"
        return "skip", "not profitable"
    if row["agreement"] < 0.6:
        return "weak", "neighbouring settings disagree"
    if not row["beats_hold"]:
        return "weak", "does not beat buy-and-hold"
    return "check", "clears every screen"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="stocks", choices=("stocks", "crypto"))
    parser.add_argument("--strategy", default="mr", choices=("mr", "sma"),
                        help="mr = RSI mean reversion, sma = SMA crossover")
    parser.add_argument("--universe", help="comma-separated symbols")
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--lookback", type=int, default=None)
    parser.add_argument("--top", type=int, default=0, help="show only the best N")
    parser.add_argument("--json", action="store_true",
                        help="also write logs/screen_<market>.json for the dashboard")
    args = parser.parse_args()

    load_env()
    import compare_strategies as cs

    is_stocks = args.market == "stocks"
    cs.FEE = cs.STOCK_FEE if is_stocks else cs.CRYPTO_FEE
    # Match the timeframe the fleet trades, or the screen ranks candidates on
    # behaviour the bots will never see.
    timeframe = args.timeframe or ("Hour" if is_stocks else "ONE_HOUR")
    lookback = args.lookback or (400 if is_stocks else 1500)

    universe = ([s.strip().upper() for s in args.universe.split(",") if s.strip()]
                if args.universe
                else (STOCK_UNIVERSE if is_stocks else CRYPTO_UNIVERSE))

    label = "RSI mean reversion" if args.strategy == "mr" else "SMA crossover"
    cost = "commission-free (~0.20% round trip)" if is_stocks else "0.80% round trip (maker)"
    print(f"\n  Screening {len(universe)} {args.market} symbols · {label} · "
          f"{timeframe}")
    print(f"  Cost model: {cost}. Results do not transfer between markets.")
    print(f"  Ranked on out-of-sample behaviour, not recent returns.\n")

    rows = []
    for symbol in universe:
        closes = get_closes(symbol, args.market, timeframe, lookback)
        if len(closes) < MIN_BARS:
            continue
        row = screen(symbol, closes, args.strategy)
        if row:
            row["verdict"], row["reason"] = verdict(row)
            rows.append(row)

    if not rows:
        print("  No symbols had enough history.\n")
        return

    order = {"check": 0, "weak": 1, "skip": 2, "avoid": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], -r["net"]))
    if args.top:
        rows = rows[:args.top]

    print(f"  {'':2}{'SYMBOL':12}{'NET':>8}{'TRADES':>8}{'WIN%':>7}"
          f"{'DIPS':>6}{'HOLD':>8}{'AGREE':>7}  BEST / NOTE")
    print("  " + "─" * 84)
    marks = {"check": "✓", "weak": "~", "skip": " ", "avoid": "✗"}
    for r in rows:
        params = ("/".join(f"{p:g}" for p in r["params"]))
        note = params if r["verdict"] == "check" else r["reason"]
        print(f"  {marks[r['verdict']]:2}{r['symbol']:12}{r['net']:>+8.2f}"
              f"{r['trades']:>8}{r['win_rate']:>6.0f}%{r['opportunities']:>6}"
              f"{r['hold']:>+8.2f}{r['agreement']*100:>6.0f}%  {note}")

    qualified = [r for r in rows if r["verdict"] == "check"]
    avoid = [r for r in rows if r["verdict"] == "avoid"]
    print(f"\n  ✓ {len(qualified)} clear every screen"
          f"   ~ {sum(1 for r in rows if r['verdict']=='weak')} marginal"
          f"   ✗ {len(avoid)} dip without recovering")
    if avoid:
        print(f"    avoid: {', '.join(r['symbol'] for r in avoid)} — these reach")
        print(f"    oversold often and keep falling, which is what mean reversion")
        print(f"    buys into repeatedly.")
    if qualified and is_stocks:
        print(f"\n  Next: .venv/bin/python add_stock.py "
              f"{qualified[0]['symbol']} --check")
    print("\n  Small samples throughout — a win rate on under ~10 trades is not"
          "\n  a number to act on. This narrows what to test, it does not"
          "\n  conclude anything.\n")

    if args.json:
        path = os.path.join(DIR, "logs", f"screen_{args.market}.json")
        payload = {
            "market": args.market,
            "strategy": label,
            "timeframe": timeframe,
            "cost_model": cost,
            "generated_at": time.time(),
            "universe_size": len(universe),
            "results": [
                {k: r[k] for k in ("symbol", "verdict", "reason", "net", "trades",
                                   "win_rate", "opportunities", "hold",
                                   "beats_hold", "agreement")}
                for r in rows
            ],
        }
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp, path)
            print(f"  Written to {os.path.relpath(path, DIR)}\n")
        except OSError as e:
            print(f"  Could not write JSON: {e}\n")


if __name__ == "__main__":
    main()
