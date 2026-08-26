#!/usr/bin/env python3
"""
compare_strategies.py — test a strategy class, not a parameter set.

The nightly learner searches parameters within one strategy: SMA crossover. It
has tested thousands of combinations and never found one that passes validation,
which is evidence about the strategy class rather than about the parameters.
This compares classes head to head under one cost model.

Every variant pays the same fees, the same slippage and uses the same exit
regime the live bot runs, so differences are attributable to the entry logic
rather than to accounting.

Reports in-sample and out-of-sample separately. A best-of-N in-sample figure is
the maximum of many noisy draws and will flatter any strategy; the out-of-sample
column is the one that means anything.

Usage:
    .venv/bin/python compare_strategies.py
    .venv/bin/python compare_strategies.py --granularity ONE_DAY
"""
import argparse
import statistics

import numpy as np
import pandas as pd

import auto_learner as al

PAIRS = ["BTC-USDC", "ETH-USDC", "SOL-USDC", "ADA-USDC", "LINK-USDC",
         "NEAR-USDC", "SUI-USDC", "XRP-USDC", "RENDER-USDC"]

FEE = al.REAL_FEE_RATE
SLIPPAGE = al.DEFAULT_SLIPPAGE_BPS / 10000.0
TRADE_USD = 10.0


def rsi_series(closes, period):
    series = pd.Series(closes)
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    return (100 - (100 / (1 + avg_gain / avg_loss.replace(0, 1e-9)))).to_numpy()


def run(closes, entry_signal, exit_signal, stop_loss_pct, take_profit_pct,
        trailing_pct=1.5, trailing_activation_pct=1.5):
    """
    Shared execution and cost model.

    entry_signal(i) and exit_signal(i, entry_price) are predicates, so a
    strategy class is expressed only by those two functions and everything
    downstream — fees, slippage, stops — is held constant.
    """
    cash, holdings, entry_price = 100.0, 0.0, 0.0
    position = False
    peak = 0.0
    pnls = []

    for i in range(1, len(closes)):
        price = closes[i]
        if price != price:
            continue

        if not position:
            if entry_signal(i) and cash >= TRADE_USD:
                fill = price * (1.0 + SLIPPAGE)
                fee = TRADE_USD * FEE
                holdings = (TRADE_USD - fee) / fill
                entry_price, peak = fill, fill
                cash -= TRADE_USD
                position = True
            continue

        peak = max(peak, price)
        change = (price - entry_price) / entry_price * 100.0
        leave = False
        if stop_loss_pct > 0 and change <= -stop_loss_pct:
            leave = True
        elif take_profit_pct > 0 and change >= take_profit_pct:
            leave = True
        elif (trailing_pct > 0 and trailing_activation_pct > 0
              and change >= trailing_activation_pct
              and price <= peak * (1.0 - trailing_pct / 100.0)):
            leave = True
        elif exit_signal(i, entry_price):
            leave = True

        if leave:
            fill = price * (1.0 - SLIPPAGE)
            proceeds = holdings * fill
            proceeds -= proceeds * FEE
            pnls.append(proceeds - TRADE_USD)
            cash += proceeds
            holdings, entry_price, peak = 0.0, 0.0, 0.0
            position = False

    if position and holdings > 0:
        fill = closes[-1] * (1.0 - SLIPPAGE)
        proceeds = holdings * fill
        proceeds -= proceeds * FEE
        pnls.append(proceeds - TRADE_USD)
        cash += proceeds

    wins = sum(1 for p in pnls if p > 0)
    return {
        "net": sum(pnls),
        "trades": len(pnls),
        "win_rate": 100.0 * wins / len(pnls) if pnls else 0.0,
        "pnls": pnls,
    }


def sma_crossover(closes, short_w, long_w, rsi_max=57.0, rsi_min=30.0):
    series = pd.Series(closes)
    short = series.rolling(short_w).mean().to_numpy()
    long = series.rolling(long_w).mean().to_numpy()
    rsi = rsi_series(closes, 14)

    def enter(i):
        if short[i - 1] != short[i - 1] or long[i - 1] != long[i - 1]:
            return False
        if not (short[i - 1] < long[i - 1] and short[i] > long[i]):
            return False
        return rsi[i] == rsi[i] and rsi_min <= rsi[i] <= rsi_max

    def leave(i, _entry):
        return (short[i - 1] > long[i - 1] and short[i] < long[i])

    return enter, leave


def rsi_mean_reversion(closes, period, oversold, overbought):
    rsi = rsi_series(closes, period)

    def enter(i):
        return (rsi[i - 1] == rsi[i - 1] and rsi[i] == rsi[i]
                and rsi[i - 1] < oversold <= rsi[i])

    def leave(i, _entry):
        return (rsi[i - 1] == rsi[i - 1] and rsi[i] == rsi[i]
                and rsi[i - 1] > overbought >= rsi[i])

    return enter, leave


def evaluate(closes, builder, args, split=0.6):
    """Fit nothing; just report the same variant on both halves of the data."""
    cut = int(len(closes) * split)
    out = {}
    for name, segment in (("in_sample", closes[:cut]), ("out_sample", closes[cut:])):
        enter, leave = builder(segment, *args)
        out[name] = run(segment, enter, leave, 5.0, 8.0)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--granularity", default="ONE_HOUR",
                        choices=sorted(al.GRANULARITY_MAP))
    parser.add_argument("--candles", type=int, default=1500)
    args = parser.parse_args()

    print(f"\n  Strategy classes on {args.granularity}, {args.candles} candles, "
          f"{len(PAIRS)} coins")
    print(f"  Fees {FEE*100:.2f}%/side · slippage {SLIPPAGE*100:.2f}%/side · "
          f"${TRADE_USD:.0f}/trade · SL 5% · TP 8% · trail 1.5%\n")

    variants = [
        ("SMA 5/30 (live config)", sma_crossover, (5, 30)),
        ("SMA 10/40", sma_crossover, (10, 40)),
        ("SMA 20/60", sma_crossover, (20, 60)),
        ("RSI mean-rev 30/70", rsi_mean_reversion, (14, 30.0, 70.0)),
        ("RSI mean-rev 25/75", rsi_mean_reversion, (14, 25.0, 75.0)),
        ("RSI mean-rev 35/65", rsi_mean_reversion, (14, 35.0, 65.0)),
        ("RSI mean-rev 20/80", rsi_mean_reversion, (14, 20.0, 80.0)),
    ]

    series = {}
    for pair in PAIRS:
        df = al.fetch_candles(pair, args.granularity, args.candles)
        if not df.empty and len(df) > 200:
            series[pair] = pd.to_numeric(df["close"]).to_numpy()
    if not series:
        print("  No candle data available.")
        return

    # Buy-and-hold must be measured on the same out-of-sample segment the
    # strategies are scored on, or the comparison spans different periods.
    split = 0.6
    hold = sum(al.buy_and_hold_backtest(c[int(len(c) * split):])["net_profit"]
               for c in series.values())
    hold_coins = sum(1 for c in series.values()
                     if al.buy_and_hold_backtest(c[int(len(c) * split):])["net_profit"] > 0)

    print(f"  {'variant':26} {'in-sample':>11} {'OUT-SAMPLE':>12} "
          f"{'trades':>7} {'win%':>6} {'coins +':>8}")
    print("  " + "-" * 76)
    for label, builder, params in variants:
        ins = outs = 0.0
        trades = 0
        pnls = []
        positive = 0
        for closes in series.values():
            res = evaluate(closes, builder, params)
            ins += res["in_sample"]["net"]
            outs += res["out_sample"]["net"]
            trades += res["out_sample"]["trades"]
            pnls += res["out_sample"]["pnls"]
            positive += res["out_sample"]["net"] > 0
        wr = 100.0 * sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0
        print(f"  {label:26} {ins:>+11.2f} {outs:>+12.2f} {trades:>7} "
              f"{wr:>5.0f}% {positive:>5}/{len(series)}")

    print("  " + "-" * 76)
    print(f"  {'buy and hold':26} {'':>11} {hold:>+12.2f} {'—':>7} {'—':>6} "
          f"{hold_coins:>5}/{len(series)}")
    print("\n  Out-of-sample is the column that matters. In-sample flatters every"
          "\n  variant, which is exactly how the parameter search has been"
          "\n  producing candidates that then fail validation.\n")


if __name__ == "__main__":
    main()
