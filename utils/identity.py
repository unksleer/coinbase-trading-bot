"""
Assert that a bot is trading the instrument its name says it trades.

On 2026-08-27 at 14:51:33 a single `pm2 restart --update-env`, issued from a
shell that had .env loaded, replaced eight crypto bots' identities with .env's
single TRADING_PAIR default within one second. ada-bot, xrp-bot and six others
all watched BTC-USDC and all wrote state_btc-usdc_dry.json. It was caught 36
minutes later and no trades were lost.

The duration was luck. Nothing failed: every bot started, logged healthy, and
polled a real market. The only symptom available was an absence — per-coin
trade files that stop growing — which is indistinguishable from a quiet market
on six-hour candles, and this fleet can genuinely go days without a trade. It
was found only because an unrelated script asked the registry which coins were
running and got BTC-USDC nine times.

pm2 sets `name` in the process environment and never overwrites it, so the
process name is the one piece of identity a bad restart cannot corrupt.
Comparing it against the resolved symbol turns a silent divergence into a
refusal to start.

Bots not started under pm2 (tests, manual runs) have no name to check against
and are left alone.
"""
import os
import sys


# Every bot pm2 runs is named "<symbol>-bot": ada-bot, wmt-bot, tsla-bot.
# Requiring the suffix means a name that does not follow the convention is
# treated as carrying no instrument, rather than having one guessed out of it —
# a process legitimately named "worker" must not be read as trading WORKER.
BOT_SUFFIXES = ("-bot", "_bot")


def expected_prefix(process_name):
    """
    The symbol a bot named `ada-bot` should be trading.

    Returns None when the name does not follow the convention, so anything
    unconventional is ignored rather than guessed at.
    """
    if not process_name:
        return None
    stem = process_name.strip().lower()
    for suffix in BOT_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)].strip("-_")
            return stem.upper() or None
    return None


def check(symbol, process_name=None):
    """
    Compare a resolved symbol against the pm2 process name.

    Returns None when they agree or when there is nothing to compare, and an
    explanatory string when they do not.
    """
    if process_name is None:
        process_name = os.environ.get("name", "")
    expected = expected_prefix(process_name)
    if not expected or not symbol:
        return None

    actual = symbol.split("-")[0].strip().upper()
    if actual == expected:
        return None

    return (
        f"Identity mismatch: pm2 process {process_name!r} should be trading "
        f"{expected}, but resolved to {symbol!r}.\n"
        f"    This normally means a restart overwrote the process environment — "
        f"`pm2 restart <name> --update-env` from a shell holding .env will give "
        f"every bot .env's single default.\n"
        f"    Refusing to start rather than silently trading the wrong "
        f"instrument and writing another bot's state file.\n"
        f"    Fix with: .venv/bin/python restart_bots.py {process_name}"
    )


def enforce(symbol, logger=None, process_name=None):
    """Exit rather than trade the wrong instrument."""
    problem = check(symbol, process_name)
    if problem is None:
        return
    if logger is not None:
        logger.critical(problem)
    else:
        print(problem, file=sys.stderr)
    sys.exit(1)
