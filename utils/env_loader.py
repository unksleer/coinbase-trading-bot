"""
Load .env so that editing it actually changes what a bot does.

`load_dotenv` does not override variables already present in the environment,
and pm2 caches the environment a process was first started with. Together those
mean a plain `pm2 restart` silently keeps the OLD settings: nine stock bots ran
with RSI 30/70 for a day after .env said 35/65, with nothing in the logs to say
the file had been ignored.

The obvious fix — override=True — breaks a different thing. Each bot is told
which instrument it is on the pm2 command line, and .env carries a default for
the same keys. Overriding blindly makes every stock bot believe it is whatever
TRADING_PAIR happens to say.

So: .env wins for tuning, the command line wins for identity.
"""
import os

from dotenv import load_dotenv

# Set per process when the bot is launched. These say WHICH bot this is, and
# .env's values for them are defaults meant for a single manual run — letting
# them win would point every bot at the same instrument.
PROCESS_IDENTITY = (
    "TRADING_PAIR",
    "STOCK_TICKER",
    "TRADE_AMOUNT_USD",
    "DRY_RUN",
    "ALPACA_PAPER",
    "STATE_FILE",
)


def load(env_path=None, identity=PROCESS_IDENTITY):
    """
    Load .env with tuning values taking precedence over any stale inherited
    copy, while leaving this process's identity intact.

    Returns the set of keys whose value changed, so a caller can log that a
    restart actually picked something up rather than assuming it did.
    """
    if env_path is None:
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

    preserved = {key: os.environ[key] for key in identity if key in os.environ}
    before = dict(os.environ)

    load_dotenv(env_path, override=True)
    os.environ.update(preserved)

    return {key for key, value in os.environ.items()
            if before.get(key) != value and key not in preserved}
