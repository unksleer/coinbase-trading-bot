#!/usr/bin/env python3
"""
add_crypto.py — register a Coinbase pair as a dry-run bot.

The crypto side had no equivalent of add_stock.py: adding a coin meant a manual
pm2 invocation and hoping the state file, log name and env prefix lined up. That
asymmetry is why stocks grew to twelve bots in an afternoon while crypto sat at
nine — the friction was entirely tooling, not intent.

Same checks as the stock version, adapted to the venue:
  - Coinbase returns candles for the pair
  - enough history exists for the indicators
  - the configured strategy is backtested on it, against buy-and-hold

Dry-run only. It refuses to start a live bot; trade.sh with its explicit
confirmation is the only path to real money, and adding that here would make it
possible to start one by accident.

Usage:
    .venv/bin/python add_crypto.py FIL              # check, backtest, register
    .venv/bin/python add_crypto.py FIL --check      # checks only
    .venv/bin/python add_crypto.py FIL --amount 25
    .venv/bin/python add_crypto.py --list
    .venv/bin/python add_crypto.py FIL --remove
"""
import argparse
import os
import re
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
MIN_CANDLES = 250
QUOTE = "USDC"


def resolve_for(prefix, env, key, fallback):
    """Settings lookup in the same order the bot uses: coin, profile, base."""
    if f"{prefix}_{key}" in env:
        return env[f"{prefix}_{key}"]
    profile = env.get(f"{prefix}_ACTIVE_PROFILE") or env.get("ACTIVE_PROFILE", "")
    if profile and f"{profile.strip().upper()}_{key}" in env:
        return env[f"{profile.strip().upper()}_{key}"]
    return env.get(key, fallback)


def fleet_defaults(env):
    """
    The settings the existing crypto bots actually run.

    Reading the SIM profile instead gives a configuration nothing runs — SMA
    5/30 on ONE_HOUR against the fleet's 10/40 on SIX_HOUR — because the nine
    existing coins each carry per-coin overrides. A coin added on profile
    defaults would quietly be a different experiment from the rest, and its
    results would not be comparable with theirs.

    Every existing coin is resolved, not just one, so a fleet that has drifted
    apart is reported rather than having one arbitrary member speak for it.
    """
    from utils import registry
    existing = sorted(set(registry.symbols("crypto")))
    if not existing:
        # Nothing running to copy. These are the values the fleet was last
        # standardised on; stated here so the fallback is a known quantity.
        return {"granularity": "SIX_HOUR", "short": 10, "long": 40,
                "source": "no bots running — using last standardised values"}

    resolved = {}
    for pair in existing:
        prefix = pair.split("-")[0].upper()
        resolved[prefix] = (
            resolve_for(prefix, env, "GRANULARITY", "SIX_HOUR"),
            int(resolve_for(prefix, env, "SMA_SHORT_WINDOW", 10)),
            int(resolve_for(prefix, env, "SMA_LONG_WINDOW", 40)),
        )

    agreed = set(resolved.values())
    common = max(agreed, key=lambda v: list(resolved.values()).count(v))
    if len(agreed) == 1:
        source = f"matching all {len(resolved)} running coins"
    else:
        odd = sorted(k for k, v in resolved.items() if v != common)
        source = (f"fleet disagrees — {', '.join(odd)} differ; "
                  f"using the majority setting")
    return {"granularity": common[0], "short": common[1], "long": common[2],
            "source": source}


def write_overrides(symbol, defaults):
    """
    Give the new coin the same per-coin overrides the rest of the fleet has.

    Without this it inherits profile values the others have overridden, and its
    results are not comparable with theirs.
    """
    path = os.path.join(DIR, ".env")
    prefix = symbol.upper()
    with open(path) as handle:
        text = handle.read()
    if f"{prefix}_GRANULARITY=" in text:
        return
    block = (f"\n# Added by add_crypto.py — matches the rest of the fleet so"
             f"\n# results are comparable across coins.\n"
             f"{prefix}_GRANULARITY={defaults['granularity']}\n"
             f"{prefix}_SMA_SHORT_WINDOW={defaults['short']}\n"
             f"{prefix}_SMA_LONG_WINDOW={defaults['long']}\n")
    with open(path, "a") as handle:
        handle.write(block)


def load_env():
    values = {}
    try:
        with open(os.path.join(DIR, ".env")) as handle:
            for line in handle:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except OSError as e:
        sys.exit(f"  Could not read .env: {e}")
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def valid_symbol(text):
    return bool(re.fullmatch(r"[A-Z0-9]{2,10}", text.upper()))


def pair_for(symbol):
    return f"{symbol.upper()}-{QUOTE}"


def pm2_names():
    import json
    try:
        out = subprocess.run(["pm2", "jlist"], capture_output=True,
                             text=True, timeout=15)
        return {p["name"] for p in json.loads(out.stdout or "[]")}
    except Exception:
        return set()


def check(symbol, granularity):
    """Preconditions. Returns (ok, lines)."""
    import auto_learner as al

    pair = pair_for(symbol)
    lines = []
    frame = al.fetch_candles(pair, granularity, 400)
    enough = not frame.empty and len(frame) >= MIN_CANDLES
    lines.append(("PASS" if enough else "FAIL",
                  f"{0 if frame.empty else len(frame)} {granularity} candles "
                  f"(need {MIN_CANDLES})"))
    if not enough:
        lines.append(("INFO", "Coinbase may not list this pair, or it is too new"))
        return False, lines

    import pandas as pd
    closes = pd.to_numeric(frame["close"]).to_numpy()
    lines.append(("INFO", f"last close ${closes[-1]:,.6g}"))
    return True, lines


def backtest(symbol, granularity, env):
    import compare_strategies as cs
    import auto_learner as al
    import pandas as pd

    cs.FEE = cs.CRYPTO_FEE
    frame = al.fetch_candles(pair_for(symbol), granularity, 400)
    if frame.empty or len(frame) < MIN_CANDLES:
        return
    closes = pd.to_numeric(frame["close"]).to_numpy()

    fleet = fleet_defaults(env)
    short, long = fleet["short"], fleet["long"]
    rsi_max = float(env.get("SIM_RSI_BUY_MAX", 100))
    rsi_min = float(env.get("SIM_RSI_BUY_MIN", 30))

    print(f"\n  Backtest — SMA {short}/{long}, RSI {rsi_min:.0f}-{rsi_max:.0f}, "
          f"{granularity}, {cs.CRYPTO_FEE*200:.2f}% round trip")
    # Say where the settings came from: the whole point is that a new coin is
    # run on the same configuration as the rest, so that claim should be
    # visible rather than assumed.
    print(f"  {'':4}settings {fleet['source']}")
    print(f"  {'':4}{'window':<12}{'net':>9}{'trades':>8}{'win%':>7}")
    result = cs.evaluate(closes, cs.sma_crossover, (short, long, rsi_max, rsi_min))
    for name in ("in_sample", "out_sample"):
        r = result[name]
        print(f"  {'':4}{name.replace('_','-'):<12}{r['net']:>+9.2f}"
              f"{r['trades']:>8}{r['win_rate']:>6.0f}%")
    hold = al.buy_and_hold_backtest(closes[int(len(closes)*0.6):])["net_profit"]
    print(f"  {'':4}{'buy & hold':<12}{hold:>+9.2f}")
    if result["out_sample"]["trades"] == 0:
        print("  !  No out-of-sample trades — this pair may never signal at "
              "these settings.")


def register(symbol, amount, granularity):
    pair = pair_for(symbol)
    name = f"{symbol.lower()}-bot"
    if name in pm2_names():
        print(f"  {name} already exists. Use --remove first.")
        return False

    # DRY_RUN is hardcoded True. Live trading goes through trade.sh, which
    # demands an explicit typed confirmation; permitting it here would make
    # starting a real-money bot a flag away from a routine action.
    cmd = (f'TRADING_PAIR={pair} TRADE_AMOUNT_USD={amount} DRY_RUN=True '
           f'{symbol.upper()}_GRANULARITY={granularity} '
           f'pm2 start {os.path.join(DIR, "bot.py")} --name {name} '
           f'--interpreter {os.path.join(DIR, ".venv/bin/python")} --update-env')
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=DIR)
    if result.returncode != 0:
        print(f"  pm2 start failed:\n{result.stderr.strip()[:400]}")
        return False
    subprocess.run(["pm2", "save"], capture_output=True, text=True)
    print(f"  Started {name} on {pair} at ${amount}/trade, {granularity}, DRY RUN.")
    return True


def remove(symbol):
    name = f"{symbol.lower()}-bot"
    if name in pm2_names():
        subprocess.run(["pm2", "delete", name], capture_output=True, text=True)
        subprocess.run(["pm2", "save"], capture_output=True, text=True)
        print(f"  Stopped and removed {name}.")
    else:
        print(f"  {name} was not running.")
    print("  State and trade files are left in place.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbol", nargs="?", help="e.g. FIL (quote is added)")
    parser.add_argument("--amount", type=float, default=None)
    parser.add_argument("--granularity", default=None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    env = load_env()
    defaults = fleet_defaults(env)
    granularity = args.granularity or defaults["granularity"]

    if args.list:
        try:
            from utils import registry
            for problem in registry.audit():
                print(f"  ! {problem}")
            running = pm2_names()
            pairs = registry.symbols("crypto")
        except Exception as e:
            sys.exit(f"  Could not read the registry: {e}")
        if not pairs:
            print("\n  No crypto bots running.\n")
            return
        print(f"\n  {'pair':14}{'bot':14}{'pm2':>9}")
        for pair in pairs:
            name = f"{pair.split('-')[0].lower()}-bot"
            print(f"  {pair:14}{name:14}"
                  f"{'running' if name in running else 'MISSING':>9}")
        print()
        return

    if not args.symbol:
        parser.error("a symbol is required unless --list is used")
    symbol = args.symbol.upper().replace(f"-{QUOTE}", "")
    if not valid_symbol(symbol):
        sys.exit(f"  {args.symbol!r} does not look like a coin symbol.")

    if args.remove:
        remove(symbol)
        return

    print(f"\n  Checking {pair_for(symbol)}")
    ok, lines = check(symbol, granularity)
    for status, text in lines:
        mark = {"PASS": "✓", "FAIL": "✗", "INFO": " "}[status]
        print(f"  {mark} {text}")
    if not ok:
        sys.exit("\n  Not added.\n")

    backtest(symbol, granularity, env)

    if args.check:
        print("\n  --check given; nothing changed.\n")
        return

    amount = args.amount or float(env.get("TRADE_AMOUNT_USD", 10))
    print()
    write_overrides(symbol, defaults)
    register(symbol, amount, granularity)
    print()


if __name__ == "__main__":
    main()
