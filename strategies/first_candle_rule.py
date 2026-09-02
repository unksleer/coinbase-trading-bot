"""
First Candle Rule — experimental opening-range breakout.

The session's first completed candle defines a range. A later candle CLOSING
above that range's high, with volume and trend confirmation, is the entry
signal. Nothing here is shared with the SMA or mean-reversion strategies and
none of its results are mixed with theirs.

Two design choices carry most of the risk of self-deception, so both are
explicit.

**Confirmed close, never an intrabar touch.** A wick through the opening high
is not a signal. Testing on touches would let the backtest enter at a price
that existed for a moment and may never have been fillable, and it inflates
signal counts with exactly the false breakouts the rule is meant to avoid.

**Entry is the NEXT candle's open, plus slippage.** Entering at the breakout
level itself assumes a fill at a price already gone by the time the candle
closed. That single assumption is the difference between a strategy and a
backtest artefact.

Every rejection is recorded with its reason. A session where nothing happened
is evidence about the rule, and a filter that rejects everything looks
identical to a market that never set up unless the reasons are counted.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional

from utils import indicators


# Rejection reasons, named so they can be counted. Anything not listed here is
# a bug rather than an outcome.
NO_OPENING_CANDLE = "no_opening_candle"
OPENING_CANDLE_UNFINISHED = "opening_candle_unfinished"
NO_BREAKOUT = "no_breakout_close_above_range_high"
VOLUME_TOO_LOW = "volume_below_threshold"
BELOW_EMA = "price_below_ema200"
RSI_OUT_OF_BAND = "rsi_outside_band"
NO_NEXT_CANDLE = "no_candle_to_enter_on"
STOP_NOT_BELOW_ENTRY = "stop_not_below_entry"
RISK_EXCEEDS_LIMIT = "risk_exceeds_limit"
TARGET_BELOW_COSTS = "target_below_round_trip_cost"
ALREADY_ENTERED = "already_entered_this_session"
INSUFFICIENT_HISTORY = "insufficient_history_for_indicators"


@dataclass
class Decision:
    """One session's evaluation. Recorded whether or not it produced a trade."""
    session: str
    instrument: str
    accepted: bool = False
    reason: Optional[str] = None

    open_high: Optional[float] = None
    open_low: Optional[float] = None
    open_mid: Optional[float] = None
    open_volume: Optional[float] = None

    breakout_ts: Optional[int] = None
    breakout_close: Optional[float] = None
    volume_ratio: Optional[float] = None
    ema200: Optional[float] = None
    rsi: Optional[float] = None
    atr: Optional[float] = None

    entry_ts: Optional[int] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    stop_distance: Optional[float] = None
    target_distance: Optional[float] = None
    reward_risk: Optional[float] = None
    risk_usd: Optional[float] = None
    expected_fees_usd: Optional[float] = None
    expected_slippage_usd: Optional[float] = None

    def as_row(self):
        return asdict(self)


@dataclass
class Config:
    """
    Parameters, all explicit so a run can be reproduced from its record.

    fee_per_side and slippage_per_side are fractions, not percents.
    """
    volume_multiple: float = 1.5
    volume_lookback: int = 20
    ema_period: int = 200
    rsi_period: int = 14
    rsi_min: float = 40.0
    rsi_max: float = 70.0
    atr_period: int = 14
    atr_stop_multiple: float = 1.0
    min_reward_risk: float = 2.0
    position_usd: float = 10.0
    max_risk_usd: float = 0.50
    fee_per_side: float = 0.0
    slippage_per_side: float = 0.0


def evaluate_session(candles, config, session_label, instrument,
                     history=None, skip=None):
    """
    Decide one session. `candles` is that session's bars, oldest first, each a
    dict with ts/open/high/low/close/volume. `history` is the bars BEFORE this
    session, needed for EMA200 and the volume baseline.

    `skip` names confirmation checks to bypass, for ablation. Bypassing a
    check must actually skip it — approximating "no EMA filter" by setting the
    period to 1 makes EMA equal close, so every signal is rejected and the
    ablation reads as though the filter were essential.

    Returns a Decision. Never raises on ordinary market conditions.
    """
    skip = skip or set()
    d = Decision(session=session_label, instrument=instrument)

    if not candles:
        d.reason = NO_OPENING_CANDLE
        return d

    opening = candles[0]
    d.open_high = float(opening["high"])
    d.open_low = float(opening["low"])
    d.open_mid = (d.open_high + d.open_low) / 2.0
    d.open_volume = float(opening["volume"])

    # Indicators are computed over history + this session, and READ at the
    # breakout bar's index. Computing them over the whole session and reading
    # the last value would let the decision see candles that had not printed.
    prior = list(history or [])
    series = prior + list(candles)
    need = max(config.ema_period, config.volume_lookback + 1,
               config.atr_period + 1, config.rsi_period + 1)
    if len(series) < need:
        d.reason = INSUFFICIENT_HISTORY
        return d

    closes = [float(c["close"]) for c in series]
    highs = [float(c["high"]) for c in series]
    lows = [float(c["low"]) for c in series]
    volumes = [float(c["volume"]) for c in series]

    ema = indicators.ema(closes, config.ema_period)
    rsi = indicators.rsi(closes, config.rsi_period)
    atr = indicators.atr(highs, lows, closes, config.atr_period)
    vratio = indicators.volume_ratio(volumes, config.volume_lookback)

    offset = len(prior)
    # Candle 0 is the opening range itself and cannot be its own breakout.
    for i in range(1, len(candles)):
        idx = offset + i
        candle = candles[i]
        close = float(candle["close"])

        if close <= d.open_high:
            continue                                   # not a breakout; keep looking

        # A breakout candle exists. From here every rejection is final for the
        # session: one entry attempt per session, taken at the first breakout.
        d.breakout_ts = int(candle["ts"])
        d.breakout_close = close
        d.volume_ratio = float(vratio.iloc[idx]) if vratio.iloc[idx] == vratio.iloc[idx] else None
        d.ema200 = float(ema.iloc[idx])
        d.rsi = float(rsi.iloc[idx])
        d.atr = float(atr.iloc[idx]) if atr.iloc[idx] == atr.iloc[idx] else None

        if "volume" not in skip and (
                d.volume_ratio is None or d.volume_ratio < config.volume_multiple):
            d.reason = VOLUME_TOO_LOW
            return d
        if "ema" not in skip and close <= d.ema200:
            d.reason = BELOW_EMA
            return d
        if "rsi" not in skip and not (config.rsi_min <= d.rsi <= config.rsi_max):
            d.reason = RSI_OUT_OF_BAND
            return d

        if i + 1 >= len(candles):
            # Breakout on the session's last candle: nothing left to enter on.
            d.reason = NO_NEXT_CANDLE
            return d

        entry_candle = candles[i + 1]
        raw_entry = float(entry_candle["open"])
        d.entry_ts = int(entry_candle["ts"])
        # Slippage is paid against you on entry.
        d.entry_price = raw_entry * (1.0 + config.slippage_per_side)

        # Stop: the structurally valid choice with the SMALLER risk.
        candidates = [d.open_mid]
        if d.atr:
            candidates.append(d.entry_price - config.atr_stop_multiple * d.atr)
        valid = [s for s in candidates if s < d.entry_price]
        if not valid:
            d.reason = STOP_NOT_BELOW_ENTRY
            return d
        d.stop_price = max(valid)                      # closest stop = smallest risk
        d.stop_distance = d.entry_price - d.stop_price

        quantity = config.position_usd / d.entry_price
        d.risk_usd = d.stop_distance * quantity
        if d.risk_usd > config.max_risk_usd:
            d.reason = RISK_EXCEEDS_LIMIT
            return d

        d.target_distance = d.stop_distance * config.min_reward_risk
        d.target_price = d.entry_price + d.target_distance
        d.reward_risk = config.min_reward_risk

        d.expected_fees_usd = config.position_usd * config.fee_per_side * 2
        d.expected_slippage_usd = config.position_usd * config.slippage_per_side * 2

        # The gross move to target must clear everything the round trip costs.
        gross_target_usd = d.target_distance * quantity
        if gross_target_usd <= d.expected_fees_usd + d.expected_slippage_usd:
            d.reason = TARGET_BELOW_COSTS
            return d

        d.accepted = True
        d.reason = None
        return d

    d.reason = NO_BREAKOUT
    return d
