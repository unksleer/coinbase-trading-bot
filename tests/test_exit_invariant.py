"""
A discretionary exit may not close a position at a net loss.

The trailing stop was arithmetically incapable of profit over part of its
range. It arms at ACTIVATION and exits TRAIL below the peak, so on crypto at
1.5/1.5 against a 1.206% round trip, every trade peaking between +1.5% and
+2.71% was a guaranteed net loss the moment it fired — armed below the level
where it could pay. Stocks had the same shape at 1.0/1.5 on a commission-free
venue, where any peak between +1.0% and +1.5% exited BELOW entry.

Measured on 53 real crypto trades: 22 cleared the fee at some point and only 10
closed positive, giving back 2.94% from peak on average.

Fixed as an invariant, not by retuning the percentages, because retuned numbers
can be changed back by anyone editing .env and the trap returns silently. These
tests therefore assert the RULE, and that protective exits stay exempt.
"""
import os
import re
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)


def _src(name):
    with open(os.path.join(DIR, name)) as handle:
        return handle.read()


# ── the arithmetic that motivated it ────────────────────────────────────────

@pytest.mark.parametrize("peak,trail,fee,profitable", [
    (1.5, 1.5, 1.206, False),   # exits at 0.0% -> -1.21% net
    (2.0, 1.5, 1.206, False),
    (2.7, 1.5, 1.206, False),   # exits at +1.2% -> -0.01% net
    (3.0, 1.5, 1.206, True),
    (1.0, 1.5, 0.0,   False),   # stocks: exits BELOW entry
    (1.4, 1.5, 0.0,   False),
    (2.0, 1.5, 0.0,   True),
])
def test_trailing_stop_below_breakeven_cannot_profit(peak, trail, fee, profitable):
    assert ((peak - trail) - fee > 0) is profitable


def test_crypto_breakeven_peak_is_above_the_activation_level():
    """
    The trap in one line: the trail arms at 1.5% but cannot pay until 2.71%.
    """
    trail, fee, activation = 1.5, 1.206, 1.5
    assert trail + fee > activation


# ── crypto ──────────────────────────────────────────────────────────────────

def test_crypto_defines_the_invariant_from_net_pl():
    src = _src("bot.py")
    assert "covers_costs = net_pl_pct >= 0.0" in src


def test_crypto_trailing_stop_is_gated():
    src = _src("bot.py")
    line = next(l for l in src.splitlines()
                if "trailing_armed and latest_price <= trailing_stop_price" in l)
    assert "covers_costs" in line, "trailing stop must not fire below break-even"


def test_crypto_strategy_exit_is_gated():
    src = _src("bot.py")
    assert re.search(r'sell_reason == "STRATEGY" and not covers_costs', src)


@pytest.mark.parametrize("reason", ["STOP_LOSS", "STOP_LOSS_ATR", "BREAK_EVEN"])
def test_crypto_protective_exits_stay_exempt(reason):
    """
    Realising a loss is exactly what a stop is for. Gating it would convert a
    bounded loss into an unbounded one — the loss-aversion trap this fix must
    not become.
    """
    src = _src("bot.py")
    block = src[src.index("if break_even_active"):src.index("if trailing_armed and signal")]
    branch = next(l for l in block.splitlines() if f'sell_reason = f"{reason}' in l
                  or f'sell_reason = f"{reason} ' in l)
    idx = block.index(branch)
    condition = block[:idx].rsplit("elif", 1)[-1] if "elif" in block[:idx] else block[:idx]
    assert "covers_costs" not in condition.split("\n")[0], \
        f"{reason} must fire regardless of profitability"


def test_crypto_take_profit_is_not_gated():
    """Take-profit is above break-even by construction; gating is pointless."""
    src = _src("bot.py")
    line = next(l for l in src.splitlines() if "TAKE_PROFIT (Price rose" in l)
    assert "covers_costs" not in line


# ── stocks ──────────────────────────────────────────────────────────────────

def test_stock_defines_the_invariant():
    assert "covers_costs = chg_pct >= 0.0" in _src("stock_bot.py")


def test_stock_trailing_stop_is_gated():
    src = _src("stock_bot.py")
    line = next(l for l in src.splitlines() if "price <= trail_price" in l)
    assert "covers_costs" in line


def test_stock_strategy_exit_is_gated():
    src = _src("stock_bot.py")
    assert "if strategy_exit and not covers_costs:" in src


def test_stock_stop_loss_stays_exempt():
    src = _src("stock_bot.py")
    line = next(l for l in src.splitlines() if "chg_pct <= -STOP_LOSS_PCT" in l)
    assert "covers_costs" not in line


# ── the held-back exit must be visible ──────────────────────────────────────

def test_both_bots_log_when_an_exit_is_held_back():
    """
    A silently suppressed exit is indistinguishable from a bot that stopped
    working — the failure mode this project keeps hitting.
    """
    assert "Holding through strategy exit" in _src("bot.py")
    assert "Holding through strategy exit" in _src("stock_bot.py")
    assert "below break-even" in _src("bot.py")
    assert "below entry" in _src("stock_bot.py")
