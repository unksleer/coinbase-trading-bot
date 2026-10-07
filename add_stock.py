#!/usr/bin/env python3
"""
add_stock.py — register a stock ticker as a paper-trading bot.

Adding a ticker by hand means getting a pm2 invocation, an .env entry and a
state-file name consistent with each other. Each is easy to get subtly wrong in
a way that fails silently: a bot that starts but never trades, or one the
reports do not know about.

Everything is checked before anything is changed, and the checks are the point:
  - Alpaca will actually accept orders for the symbol
  - enough history exists to compute the indicators
  - the strategy is backtested on that symbol, so its behaviour is known
    before it is put in front of money rather than after

Usage:
    .venv/bin/python add_stock.py NVDA              # check, backtest, register
    .venv/bin/python add_stock.py NVDA --check      # checks only, change nothing
    .venv/bin/python add_stock.py NVDA --amount 25  # override trade size
    .venv/bin/python add_stock.py --list            # show registered tickers
    .venv/bin/python add_stock.py NVDA --remove     # stop and deregister
"""
import argparse
import os
import re
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(DIR, ".env")
WATCHLIST_KEY = "STOCK_WATCHLIST"
MIN_BARS = 200


def load_env():
    """Read .env into os.environ without clobbering an existing value."""
    values = {}
    try:
        with open(ENV_PATH) as handle:
            for line in handle:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except OSError as e:
        sys.exit(f"Could not read {ENV_PATH}: {e}")
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def watchlist(values):
    raw = values.get(WATCHLIST_KEY, "")
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def write_watchlist(tickers):
    """Rewrite the watchlist line, appending the key if it is absent."""
    joined = ",".join(sorted(set(tickers)))
    with open(ENV_PATH) as handle:
        lines = handle.readlines()

    for i, line in enumerate(lines):
        if line.strip().startswith(f"{WATCHLIST_KEY}="):
            lines[i] = f"{WATCHLIST_KEY}={joined}\n"
            break
    else:
        lines.append(
            "\n# Tickers with a running paper bot. Read by compare_strategies.py\n"
            "# and add_stock.py; keep it in step with the pm2 process list.\n"
            f"{WATCHLIST_KEY}={joined}\n"
        )

    with open(ENV_PATH, "w") as handle:
        handle.writelines(lines)


def pm2_names():
    try:
        import json
        out = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=15)
        return {p["name"] for p in json.loads(out.stdout or "[]")}
    except Exception:
        return set()


def valid_ticker(text):
    return bool(re.fullmatch(r"[A-Z][A-Z.\-]{0,9}", text.upper()))


def check(ticker):
    """Run every precondition. Returns (ok, list_of_lines)."""
    from utils import stock_data

    lines = []
    tradable, detail = stock_data.is_tradable(ticker)
    lines.append(("PASS" if tradable else "FAIL",
                  f"tradable on Alpaca{(' — ' + detail) if detail else ''}"))
    if not tradable:
        return False, lines

    closes = stock_data.closes(ticker, "Hour", 400)
    enough = len(closes) >= MIN_BARS
    lines.append(("PASS" if enough else "FAIL",
                  f"{len(closes)} hourly bars available (need {MIN_BARS})"))
    if not enough:
        return False, lines

    lines.append(("INFO", f"last close ${closes[-1]:,.2f}"))
    return True, lines


def backtest(ticker):
    """Show how the configured strategy behaves on this symbol."""
    import compare_strategies as cs
    from utils import stock_data

    cs.FEE = cs.STOCK_FEE
    closes = stock_data.closes(ticker, "Hour", 400)
    if len(closes) < MIN_BARS:
        return

    short = int(os.getenv(f"{ticker}_SMA_SHORT_WINDOW",
                          os.getenv("STOCK_SMA_SHORT_WINDOW", "10")))
    long = int(os.getenv(f"{ticker}_SMA_LONG_WINDOW",
                         os.getenv("STOCK_SMA_LONG_WINDOW", "30")))

    print(f"\n  Backtest — SMA {short}/{long}, commission-free, hourly bars")
    print(f"  {'':4}{'window':<12}{'net':>9}{'trades':>8}{'win%':>7}")
    result = cs.evaluate(closes, cs.sma_crossover, (short, long))
    for name in ("in_sample", "out_sample"):
        r = result[name]
        print(f"  {'':4}{name.replace('_', '-'):<12}{r['net']:>+9.2f}"
              f"{r['trades']:>8}{r['win_rate']:>6.0f}%")
    hold = cs.al.buy_and_hold_backtest(closes[int(len(closes) * 0.6):])["net_profit"]
    print(f"  {'':4}{'buy & hold':<12}{hold:>+9.2f}")
    if result["out_sample"]["trades"] == 0:
        print("  !  No out-of-sample trades — this symbol may rarely signal.")


def register(ticker, amount, values):
    name = f"{ticker.lower()}-bot"
    if name in pm2_names():
        print(f"  {name} already exists in pm2. Remove it first, or use --remove.")
        return False

    run_env = {**os.environ, "STOCK_TICKER": str(ticker), "TRADE_AMOUNT_USD": str(amount),
               "ALPACA_PAPER": str(values.get("ALPACA_PAPER", "True"))}
    cmd = ["pm2", "start", os.path.join(DIR, "stock_bot.py"), "--name", name,
           "--interpreter", os.path.join(DIR, ".venv/bin/python"), "--update-env"]
    result = subprocess.run(cmd, env=run_env, capture_output=True, text=True, cwd=DIR)
    if result.returncode != 0:
        print(f"  pm2 start failed:\n{result.stderr.strip()[:400]}")
        return False

    write_watchlist(watchlist(values) + [ticker])
    subprocess.run(["pm2", "save"], capture_output=True, text=True)
    print(f"  Started {name} at ${amount}/trade and added to {WATCHLIST_KEY}.")
    return True


def remove(ticker, values):
    name = f"{ticker.lower()}-bot"
    if name in pm2_names():
        subprocess.run(["pm2", "delete", name], capture_output=True, text=True)
        subprocess.run(["pm2", "save"], capture_output=True, text=True)
        print(f"  Stopped and removed {name}.")
    else:
        print(f"  {name} was not running.")
    remaining = [t for t in watchlist(values) if t != ticker]
    write_watchlist(remaining)
    print(f"  Removed from {WATCHLIST_KEY}. State and trade files are left in place.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker", nargs="?", help="e.g. NVDA")
    parser.add_argument("--amount", type=float, default=None,
                        help="USD per trade (default: TRADE_AMOUNT_USD)")
    parser.add_argument("--check", action="store_true",
                        help="run checks and backtest, change nothing")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    values = load_env()

    if args.list:
        # Consistency check: a bot running but absent from the watchlist, or
        # listed with no bot, means something got half-added or half-removed.
        try:
            from utils import registry
            for problem in registry.audit():
                print(f"  ! {problem}")
        except Exception:
            pass
        running = pm2_names()
        tickers = watchlist(values)
        if not tickers:
            print(f"\n  {WATCHLIST_KEY} is empty.\n")
            return
        print(f"\n  {'ticker':10}{'bot':14}{'pm2':>8}")
        for t in tickers:
            name = f"{t.lower()}-bot"
            print(f"  {t:10}{name:14}{'running' if name in running else 'MISSING':>8}")
        print()
        return

    if not args.ticker:
        parser.error("a ticker is required unless --list is used")
    ticker = args.ticker.upper()
    if not valid_ticker(ticker):
        sys.exit(f"  {ticker!r} does not look like a ticker symbol.")

    if args.remove:
        remove(ticker, values)
        return

    print(f"\n  Checking {ticker}")
    ok, lines = check(ticker)
    for status, text in lines:
        mark = {"PASS": "✓", "FAIL": "✗", "INFO": " "}[status]
        print(f"  {mark} {text}")
    if not ok:
        sys.exit("\n  Not added.\n")

    backtest(ticker)

    if args.check:
        print("\n  --check given; nothing changed.\n")
        return

    amount = args.amount or float(values.get("TRADE_AMOUNT_USD", 10))
    print()
    register(ticker, amount, values)
    print()


if __name__ == "__main__":
    main()
