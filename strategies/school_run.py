"""
School Run — Tom Hougaard's opening-range breakout, both directions.

The rule as he trades it on the DAX: the market opens at 09:00 CET, the
09:15-09:30 candle is the reference, a buy stop sits a few ticks above its
high and a sell stop a few ticks below its low. Whichever fills, the other is
cancelled. The protective stop is the opposite side of the reference candle.
As the trade works, the stop is moved to breakeven and then trailed; the trade
is flat by the close.

Mapped onto SPY: the session opens 09:30 ET, so the reference candle is the
SECOND fifteen-minute bar, 09:45-10:00 ET. `ref_index` makes that explicit and
testable rather than an assumption buried in an index.

Three things differ from the First Candle Rule and each is deliberate, not a
loosening.

**Entry on touch, at the stop level.** A stop order fills when price trades
through it, so entering at the level plus slippage is the honest model of a
resting order — unlike "enter at the breakout close", which assumes a price
already gone. The wick that FCR must ignore is exactly what fills a stop.

**Both sides.** Long on a break of the high, short on a break of the low. One
trade per session; the untriggered side is cancelled once the other fills.

**No indicator filters.** Hougaard's version has none. Adding them here would
test a different rule and call it his.

Ambiguity is recorded, never resolved optimistically. A bar that touches both
entry levels also spans the stop of whichever side filled first; with
fifteen-minute bars the order of events inside it is unknowable, so the
session is scored as entered and stopped in one bar and flagged so it can be
counted separately.
"""
from dataclasses import dataclass, asdict
from typing import Optional

LONG, SHORT = "long", "short"

# Rejection reasons. Anything not listed is a bug rather than an outcome.
SESSION_TOO_SHORT = "session_shorter_than_reference_candle"
NO_BAR_AFTER_REFERENCE = "no_bar_after_reference_candle"
NO_TRIGGER = "neither_stop_triggered"
RANGE_TOO_WIDE = "reference_range_above_max"
RANGE_TOO_NARROW = "reference_range_below_min"


@dataclass
class Config:
    """
    Parameters, all explicit so a run can be reproduced from its record.

    Distances are fractions of price, not percents. `*_r` values are multiples
    of the initial risk (entry to stop). `None` disables that exit.
    """
    ref_index: int = 1                    # 0 = opening bar, 1 = second bar
    entry_offset: float = 0.0002          # "a few ticks": 0.02% of price
    stop_offset: float = 0.0002           # stop sits this far beyond the range
    target_r: Optional[float] = None      # fixed take-profit, in R
    breakeven_r: Optional[float] = 1.0    # move stop to entry after this MFE
    trail_r: Optional[float] = None       # trail stop this far behind the extreme
    max_range_pct: Optional[float] = None  # skip sessions with a wider candle
    min_range_pct: Optional[float] = None  # skip sessions with a narrower candle
    position_usd: float = 100.0
    fee_per_side: float = 0.0
    slippage_per_side: float = 0.0


@dataclass
class Decision:
    """One session's evaluation. Recorded whether or not it produced a trade."""
    session: str
    instrument: str
    accepted: bool = False
    reason: Optional[str] = None

    ref_ts: Optional[int] = None
    ref_high: Optional[float] = None
    ref_low: Optional[float] = None
    range_pct: Optional[float] = None
    buy_level: Optional[float] = None
    sell_level: Optional[float] = None

    direction: Optional[str] = None
    entry_ts: Optional[int] = None
    entry_index: Optional[int] = None     # index into the session's bars
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    risk_per_unit: Optional[float] = None
    both_sides_same_bar: bool = False

    def as_row(self):
        return asdict(self)


def levels(ref_candle, config):
    """Entry and stop levels from one reference candle. Pure, for tests."""
    high, low = float(ref_candle["high"]), float(ref_candle["low"])
    return {
        "buy": high * (1.0 + config.entry_offset),
        "sell": low * (1.0 - config.entry_offset),
        "long_stop": low * (1.0 - config.stop_offset),
        "short_stop": high * (1.0 + config.stop_offset),
    }


def evaluate_session(candles, config, session_label, instrument):
    """
    Decide one session. `candles` is that session's bars, oldest first, each a
    dict with ts/open/high/low/close/volume.

    Returns a Decision. Never raises on ordinary market conditions.
    """
    d = Decision(session=session_label, instrument=instrument)

    if len(candles) <= config.ref_index:
        d.reason = SESSION_TOO_SHORT
        return d

    ref = candles[config.ref_index]
    d.ref_ts = int(ref["ts"])
    d.ref_high = float(ref["high"])
    d.ref_low = float(ref["low"])
    d.range_pct = (d.ref_high - d.ref_low) / d.ref_low * 100.0
    lv = levels(ref, config)
    d.buy_level, d.sell_level = lv["buy"], lv["sell"]

    if config.max_range_pct is not None and d.range_pct > config.max_range_pct:
        d.reason = RANGE_TOO_WIDE
        return d
    if config.min_range_pct is not None and d.range_pct < config.min_range_pct:
        d.reason = RANGE_TOO_NARROW
        return d

    if config.ref_index + 1 >= len(candles):
        d.reason = NO_BAR_AFTER_REFERENCE
        return d

    for i in range(config.ref_index + 1, len(candles)):
        bar = candles[i]
        high, low = float(bar["high"]), float(bar["low"])
        hit_buy = high >= d.buy_level
        hit_sell = low <= d.sell_level
        if not (hit_buy or hit_sell):
            continue

        if hit_buy and hit_sell:
            # Order of events inside the bar is unknowable. Assume the side
            # nearer the bar's open filled first; the exit simulation will see
            # the same bar span the stop and score it as a same-bar loss.
            d.both_sides_same_bar = True
            mid = (d.ref_high + d.ref_low) / 2.0
            d.direction = LONG if float(bar["open"]) >= mid else SHORT
        else:
            d.direction = LONG if hit_buy else SHORT

        d.entry_ts = int(bar["ts"])
        d.entry_index = i
        if d.direction == LONG:
            d.entry_price = d.buy_level * (1.0 + config.slippage_per_side)
            d.stop_price = lv["long_stop"]
        else:
            d.entry_price = d.sell_level * (1.0 - config.slippage_per_side)
            d.stop_price = lv["short_stop"]
        d.risk_per_unit = abs(d.entry_price - d.stop_price)
        d.accepted = True
        d.reason = None
        return d

    d.reason = NO_TRIGGER
    return d


STOP, TARGET, TIME = "stop", "target", "time_exit"


def simulate_exit(decision, candles, config):
    """
    Walk forward from the entry bar to the session's last bar.

    Returns (exit_ts, exit_price, reason, mfe_pct, mae_pct, stop_moved).
    Exit price excludes slippage; the caller applies it.

    Per bar, in this order: the stop (checked against the adverse extreme),
    then the target. When one bar spans both, the stop wins — the pessimistic
    assumption, and the same one the FCR backtester makes. Stop moves
    (breakeven, trail) are applied AFTER the bar closes, so a stop never acts
    on the very bar whose high justified moving it.
    """
    sign = 1.0 if decision.direction == LONG else -1.0
    entry = decision.entry_price
    stop = decision.stop_price
    risk = decision.risk_per_unit
    target = None
    if config.target_r is not None:
        target = entry + sign * config.target_r * risk

    mfe = mae = 0.0
    best = entry                       # most favourable extreme seen so far
    stop_moved = False

    for bar in candles[decision.entry_index:]:
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        favourable = high if sign > 0 else low
        adverse = low if sign > 0 else high
        mfe = max(mfe, sign * (favourable - entry) / entry * 100.0)
        mae = min(mae, sign * (adverse - entry) / entry * 100.0)

        if sign * (adverse - stop) <= 0:
            return int(bar["ts"]), stop, STOP, mfe, mae, stop_moved
        if target is not None and sign * (favourable - target) >= 0:
            return int(bar["ts"]), target, TARGET, mfe, mae, stop_moved

        # Bar closed without an exit. Now, and only now, move the stop.
        best = max(best, favourable) if sign > 0 else min(best, favourable)
        gained_r = sign * (best - entry) / risk if risk else 0.0
        if config.breakeven_r is not None and gained_r >= config.breakeven_r:
            if sign * (entry - stop) > 0:
                stop, stop_moved = entry, True
        if config.trail_r is not None and gained_r >= (config.breakeven_r or 0.0):
            trailed = best - sign * config.trail_r * risk
            if sign * (trailed - stop) > 0:
                stop, stop_moved = trailed, True

    last = candles[-1]
    return int(last["ts"]), float(last["close"]), TIME, mfe, mae, stop_moved


def pnl(decision, exit_price, config):
    """Net dollars for the trade, with exit slippage and fees applied."""
    sign = 1.0 if decision.direction == LONG else -1.0
    filled = exit_price * (1.0 - sign * config.slippage_per_side)
    qty = config.position_usd / decision.entry_price
    gross = sign * (filled - decision.entry_price) * qty
    fees = config.position_usd * config.fee_per_side * 2
    return gross, fees, gross - fees, filled
