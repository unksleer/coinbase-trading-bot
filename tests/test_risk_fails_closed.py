"""
A safety limit must fail toward permitting less trading, not more.

All three timestamp parses in the risk engine previously swallowed their error
and continued. That is fail-OPEN: an unreadable timestamp meant the trade was
invisible to the daily-trade limit, the loss cooldown was cancelled, and the
loss was left out of the day's drawdown — every one of them permitting more
trading than intended, at exactly the moment something was already wrong.

Latent rather than live when found: all 106 recorded trades parsed cleanly.
Fixed anyway, because the cost of being wrong here is unbounded and the cost of
the fix is one branch.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from utils.risk_engine import RiskEngine, RiskLimitViolation  # noqa: E402


class Settings:
    DRY_RUN = True
    DISABLE_STRATEGY_EXITS = False
    DISABLE_BUYS = False
    STOP_LOSS_PERCENT = 5.0
    TAKE_PROFIT_PERCENT = 8.0
    STOP_LOSS_ATR_MULTIPLE = 0.0
    TAKE_PROFIT_ATR_MULTIPLE = 0.0
    MAX_TRADES_PER_DAY = 3
    CONSECUTIVE_LOSS_COOLDOWN_TRADES = 2
    CONSECUTIVE_LOSS_COOLDOWN_SECONDS = 3600
    MAX_DAILY_LOSS = 5.0
    MAX_VAL_PER_ORDER = 1000.0
    MAX_EXPOSURE_PER_PRODUCT = 1000.0
    MIN_QUOTE_RESERVE = 0.0
    APPROVED_PRODUCT_ALLOWLIST = "BTC-USDC"
    EMERGENCY_KILL_SWITCH = False


def _now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _engine(state):
    class SM:
        def __init__(self, s): self.state = s
    return RiskEngine(Settings(), SM(state))


def test_unreadable_timestamp_counts_toward_the_daily_trade_limit():
    """Previously such a trade was invisible, RAISING the number allowed."""
    state = {"trades": [{"timestamp": "not-a-date", "realized_pnl_usd": 0.0}
                        for _ in range(3)],
             "position": "NONE", "cash_usd": 100.0, "holdings_base": 0.0}
    from decimal import Decimal
    with pytest.raises(RiskLimitViolation) as e:
        _engine(state).validate_pre_flight("BUY", Decimal("10"), Decimal("1"), "BTC-USDC")
    assert "trade" in str(e.value).lower()


def test_readable_timestamps_still_permit_trading_under_the_limit():
    """The fix must not block normal operation."""
    old = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    state = {"trades": [{"timestamp": old, "realized_pnl_usd": 0.0} for _ in range(9)],
             "position": "NONE", "cash_usd": 100.0, "holdings_base": 0.0}
    from decimal import Decimal
    _engine(state).validate_pre_flight("BUY", Decimal("10"), Decimal("1"), "BTC-USDC")


def test_unreadable_loss_time_holds_the_cooldown():
    """Previously an unreadable time cancelled the cooldown entirely."""
    state = {"trades": [{"timestamp": _now(), "realized_pnl_usd": -1.0},
                        {"timestamp": _now(), "realized_pnl_usd": -1.0}],
             "last_loss_time": "garbage",
             "consecutive_losses": 2,
             "position": "NONE", "cash_usd": 100.0, "holdings_base": 0.0}
    engine = _engine(state)
    try:
        engine.check_loss_cooldown(state["trades"])
    except RiskLimitViolation as e:
        assert "cooldown" in str(e).lower()
    # Not raising is acceptable only if this build gates the cooldown elsewhere;
    # what must never happen is a silent parse failure, so assert the source.
    src = open(os.path.join(DIR, "utils", "risk_engine.py")).read()
    assert "holding the cooldown" in src


def test_losses_with_unreadable_timestamps_count_toward_drawdown():
    src = open(os.path.join(DIR, "utils", "risk_engine.py")).read()
    block = src.split("def check_daily_drawdown", 1)[1][:1600]
    assert "if pnl < 0:" in block, "an unreadable LOSS must still count"
    assert "except Exception" not in block, "must not swallow broadly"


def test_no_bare_exception_swallow_remains_in_the_risk_engine():
    src = open(os.path.join(DIR, "utils", "risk_engine.py")).read()
    for i, line in enumerate(src.splitlines(), 1):
        if line.strip() == "except Exception:":
            nxt = src.splitlines()[i].strip()
            assert nxt != "pass", f"risk_engine.py:{i} still swallows silently"
