#!/usr/bin/env python3
"""
analyze_correlation.py — how many independent bets the fleet actually holds.

Pooling twelve tickers reads as twelve times the evidence. It is not: SPY and
QQQ correlate at 0.95 on hourly returns, PG and XLP at 0.70. When one signals
the other usually signals on the same market move, so that is one bet placed
twice. The measured figure for the live watchlist is ~4.9 effective symbols out
of 12, meaning a pooled trade count overstates the evidence roughly threefold.

That matters directly: the trades needed to confirm an edge go as the square of
the noise-to-signal ratio, so overstating n by 3x understates time-to-verdict
by 3x.

Correlation is computed from price returns rather than trade outcomes. Trades
keyed by exit time almost never overlap between symbols, so a trade-level
correlation is not merely noisy — it is undefined, and would report perfect
independence purely from having no overlapping observations.

Written to logs/correlations.json with a timestamp so the dashboard can show
its age and refuse to present a stale figure as current.

    .venv/bin/python analyze_correlation.py
    .venv/bin/python analyze_correlation.py --market crypto
"""
import argparse
import json
import os
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DIR, "logs", "correlations.json")


def stock_returns(tickers):
    from utils import stock_data
    import pandas as pd
    import numpy as np
    series = {}
    for ticker in tickers:
        frame = stock_data.fetch_bars(ticker, "Hour", 400)
        if frame.empty or "close" not in frame:
            continue
        closes = pd.to_numeric(frame["close"])
        closes.index = pd.to_datetime(closes.index)
        rets = np.log(closes).diff().dropna()
        series[ticker] = {k.isoformat(): float(v) for k, v in rets.items()}
    return series


def crypto_returns(pairs):
    import auto_learner as al
    import pandas as pd
    import numpy as np
    series = {}
    for pair in pairs:
        frame = al.fetch_candles(pair, "SIX_HOUR", 400)
        if frame.empty or "close" not in frame:
            continue
        closes = pd.to_numeric(frame["close"])
        closes.index = pd.to_datetime(frame.index)
        rets = np.log(closes).diff().dropna()
        series[pair] = {str(k): float(v) for k, v in rets.items()}
    return series


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=("stocks", "crypto", "both"),
                        default="both")
    args = parser.parse_args()

    from utils import env_loader, evidence, registry
    env_loader.load()

    payload = {"generated_at": time.time(), "markets": {}}
    existing = {}
    if os.path.exists(OUT):
        try:
            with open(OUT) as handle:
                existing = json.load(handle).get("markets", {})
        except (OSError, ValueError):
            existing = {}

    wanted = ("stocks", "crypto") if args.market == "both" else (args.market,)
    for market in wanted:
        if market == "stocks":
            symbols = registry.stock_tickers()
            series = stock_returns(symbols)
        else:
            symbols = registry.crypto_pairs()
            series = crypto_returns(symbols)

        if len(series) < 2:
            print(f"  {market}: only {len(series)} symbols with data — skipped")
            continue

        summary = evidence.summarise(series, pooled_trades=0)
        payload["markets"][market] = summary
        print(f"  {market}: {summary['symbols']} symbols, "
              f"{summary['effective_symbols']} effective "
              f"(mean rho {summary['mean_correlation']}, "
              f"max {summary['max_correlation']})")

    # Keep any market this run did not refresh, rather than dropping it.
    for market, value in existing.items():
        payload["markets"].setdefault(market, value)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as handle:
        json.dump(payload, handle, indent=1)
    print(f"  wrote {os.path.relpath(OUT, DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
