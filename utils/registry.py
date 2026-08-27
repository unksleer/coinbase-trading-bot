"""
One place that knows which bots exist.

Every consumer used to keep its own list: the dashboard had a hardcoded BOT_MAP,
auto_learner has a COINS table, compare_strategies has PAIRS, screen_candidates
has two universes. Adding a bot meant remembering all of them, and forgetting
one failed silently — nine stock bots added on 2026-08-26 were invisible on the
dashboard because its map was never updated.

PM2 is the authority: a bot exists if a process is running it. This reads that
and nothing else, so a bot added by any route shows up everywhere at once.

Identification is by SCRIPT, not by environment variable. Stock bots inherit
TRADING_PAIR from .env when launched from a shell that loaded it, so keying on
env vars made every stock bot claim to be BTC-USD. Which script a process runs
is unambiguous.
"""
import json
import os
import subprocess

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CRYPTO_SCRIPT = "bot.py"
STOCK_SCRIPT = "stock_bot.py"


def _pm2_processes():
    try:
        result = subprocess.run(["pm2", "jlist"], capture_output=True,
                                text=True, timeout=10)
        return json.loads(result.stdout or "[]")
    except Exception:
        return []


def bots():
    """
    Every running bot, as a list of dicts.

    Each has: name, market ('crypto'|'stocks'), symbol, status, script.
    """
    found = []
    for process in _pm2_processes():
        env = process.get("pm2_env", {}) or {}
        script = os.path.basename(env.get("pm_exec_path", "") or "")
        name = process.get("name", "")

        if script == CRYPTO_SCRIPT:
            symbol = env.get("TRADING_PAIR")
            market = "crypto"
        elif script == STOCK_SCRIPT:
            symbol = env.get("STOCK_TICKER")
            market = "stocks"
        else:
            continue
        if not symbol:
            continue

        found.append({
            "name": name,
            "market": market,
            "symbol": symbol.upper(),
            "status": env.get("status", "unknown"),
            "script": script,
        })
    return sorted(found, key=lambda b: (b["market"], b["symbol"]))


def symbols(market=None, online_only=False):
    """Symbols for a market, or all markets when market is None."""
    return [b["symbol"] for b in bots()
            if (market is None or b["market"] == market)
            and (not online_only or b["status"] == "online")]


def crypto_pairs(fallback=None):
    """
    Crypto pairs with a running bot.

    fallback is used when pm2 is unreachable — a caller that would otherwise
    process nothing is usually better off with the known set than with an empty
    list it silently treats as "no work to do".
    """
    found = symbols("crypto")
    return found or (fallback or [])


def stock_tickers(fallback=None):
    found = symbols("stocks")
    return found or (fallback or [])


def audit(env_path=None):
    """
    Report where the running fleet and the recorded config disagree.

    Returns a list of human-readable problems; empty means consistent.
    """
    problems = []
    running = bots()

    env_path = env_path or os.path.join(DIR, ".env")
    values = {}
    try:
        with open(env_path) as handle:
            for line in handle:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except OSError:
        problems.append(f"could not read {env_path}")
        return problems

    watchlist = {t.strip().upper()
                 for t in values.get("STOCK_WATCHLIST", "").split(",") if t.strip()}
    stock_running = {b["symbol"] for b in running if b["market"] == "stocks"}

    for missing in sorted(stock_running - watchlist):
        problems.append(f"{missing} has a running bot but is not in STOCK_WATCHLIST")
    for stale in sorted(watchlist - stock_running):
        problems.append(f"{stale} is in STOCK_WATCHLIST but has no running bot")

    for bot in running:
        if bot["status"] != "online":
            problems.append(f"{bot['name']} is {bot['status']}, not online")

    return problems
