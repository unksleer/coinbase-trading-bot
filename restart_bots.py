#!/usr/bin/env python3
"""
restart_bots.py — restart bots without destroying their identity.

`pm2 restart <name> --update-env` copies the CURRENT shell environment into the
process. Run it from a shell that has .env loaded and every bot inherits .env's
single TRADING_PAIR default: on 2026-08-27 that left all nine crypto bots
trading BTC-USDC and writing the same state file, with no error anywhere.

--update-env used to be necessary to pick up tuning changes. It is not any
more: utils/env_loader re-reads .env at startup with override=True, so a plain
restart already gets the new settings while pm2 keeps each process's own
identity. That is the whole contract — .env wins for tuning, the command line
wins for identity — and this script is the only restart path that honours it.

    .venv/bin/python restart_bots.py                # every bot
    .venv/bin/python restart_bots.py ada-bot xrp-bot
    .venv/bin/python restart_bots.py --crypto
    .venv/bin/python restart_bots.py --check        # report drift, change nothing
"""
import argparse
import json
import os
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
DUMP = os.path.expanduser("~/.pm2/dump.pm2")
IDENTITY_KEYS = ("TRADING_PAIR", "STOCK_TICKER", "TRADE_AMOUNT_USD",
                 "DRY_RUN", "ALPACA_PAPER", "STATE_FILE")


def pm2_processes():
    out = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=20)
    return json.loads(out.stdout or "[]")


def saved_identity():
    """Identity as recorded by `pm2 save` — what each bot was meant to be."""
    if not os.path.exists(DUMP):
        return {}
    try:
        with open(DUMP) as handle:
            entries = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {e.get("name"): {k: v for k, v in (e.get("env") or {}).items()
                            if k in IDENTITY_KEYS}
            for e in entries if e.get("name")}


def running_identity():
    result = {}
    for process in pm2_processes():
        env = process.get("pm2_env", {}) or {}
        script = os.path.basename(env.get("pm_exec_path", "") or "")
        if script not in ("bot.py", "stock_bot.py"):
            continue
        result[process.get("name")] = {
            "script": script,
            "env": {k: env.get(k) for k in IDENTITY_KEYS if env.get(k) is not None},
        }
    return result


def expected_symbol(name):
    from utils.identity import expected_prefix
    return expected_prefix(name)


def drift():
    """Bots whose running identity disagrees with their name. (name, key, is, should)."""
    problems = []
    saved = saved_identity()
    for name, info in sorted(running_identity().items()):
        want = expected_symbol(name)
        key = "TRADING_PAIR" if info["script"] == "bot.py" else "STOCK_TICKER"
        actual = (info["env"].get(key) or "").split("-")[0].upper()
        if want and actual and actual != want:
            should = (saved.get(name, {}).get(key)
                      or (f"{want}-USDC" if key == "TRADING_PAIR" else want))
            problems.append((name, key, info["env"].get(key), should))
    return problems


def restart(name, env):
    """Restart with identity restated explicitly, and without --update-env."""
    assignments = " ".join(f"{k}={v}" for k, v in sorted(env.items()) if v is not None)
    cmd = f"{assignments} pm2 restart {name} --update-env".strip()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=DIR)
    return result.returncode == 0, result.stderr.strip()[:300]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="bot names; default is all")
    parser.add_argument("--crypto", action="store_true")
    parser.add_argument("--stocks", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="report identity drift and change nothing")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override an identity value on restart, e.g. "
                             "TRADE_AMOUNT_USD=100. Repeatable. Identity keys "
                             "live in the process env, not .env, so this is the "
                             "only way to change one without losing the rest.")
    args = parser.parse_args()

    running = running_identity()
    if not running:
        sys.exit("  No bots are running under pm2.")

    problems = drift()
    if problems:
        print("\n  Identity drift — these bots are not trading what their name says:")
        for name, key, actual, should in problems:
            print(f"    {name:14} {key}={actual!r}  should be {should!r}")
    else:
        print("\n  All bots match their names.")

    if args.check:
        print()
        return 1 if problems else 0

    targets = set(args.names) or set(running)
    if args.crypto:
        targets &= {n for n, i in running.items() if i["script"] == "bot.py"}
    if args.stocks:
        targets &= {n for n, i in running.items() if i["script"] == "stock_bot.py"}
    unknown = targets - set(running)
    if unknown:
        sys.exit(f"  Not running under pm2: {', '.join(sorted(unknown))}")

    saved = saved_identity()
    print(f"\n  Restarting {len(targets)} bot(s) with identity restated.")
    failures = []
    for name in sorted(targets):
        # Prefer the saved identity: it is what the bot was registered as, and
        # the running value is exactly what may be corrupt.
        env = dict(running[name]["env"])
        env.update({k: v for k, v in saved.get(name, {}).items() if v is not None})
        for item in args.set:
            key, _, value = item.partition("=")
            if key.strip() and value.strip():
                env[key.strip()] = value.strip()
        ok, err = restart(name, env)
        key = "TRADING_PAIR" if running[name]["script"] == "bot.py" else "STOCK_TICKER"
        extra = "".join(f"  {i.split('=')[0]}={env.get(i.split('=')[0])}" for i in args.set)
        print(f"    {'✓' if ok else '✗'} {name:14} {key}={env.get(key)}{extra}"
              + (f"  {err}" if not ok else ""))
        if not ok:
            failures.append(name)

    subprocess.run(["pm2", "save"], capture_output=True, text=True)

    remaining = drift()
    if remaining:
        print("\n  ! Still drifted after restart:")
        for name, key, actual, should in remaining:
            print(f"    {name:14} {key}={actual!r} should be {should!r}")
    else:
        print("\n  Verified: every bot matches its name.")
    print()
    return 1 if failures or remaining else 0


if __name__ == "__main__":
    sys.exit(main())
