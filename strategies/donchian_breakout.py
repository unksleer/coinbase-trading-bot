"""
Donchian breakout — own a coin while it is making new highs.

BUY when the latest closed candle closes above the highest close of the
`entry_bars` candles before it. SELL when it closes below the lowest close of
the `exit_bars` candles before it. Nothing else: no oscillator, no average,
no target. The exit is the only thing that decides how much of a move is
kept, and that is deliberate.

Why this class and not another. At a 1.0% round trip the crypto bots cannot
profit from moves that average 1%, and no parameter of a fast rule changes
that. A breakout rule holds for weeks and its winners are several times its
losers, so the fee is a rounding error on the trades that matter. The cost is
the profile: roughly a third of trades win, and the rule lags a straight
rally because it buys after the move has started and sells after it has
turned. Backtested on 517 days of stored six-hour candles, 80/40 was +$78
against -$79 for holding the same nine coins, and adding an 8% take-profit
(the SIM profile's) raised the win rate to 60% while cutting the net to +$20.
Capping the winners is how this rule dies.

The bot's break-even invariant — a strategy exit may not close at a net loss
— applies here as it does everywhere, so a losing breakout is held until it
either recovers past cost or hits the hard stop. In backtest that improved
the rule (+$128, 50% win rate) rather than hurting it, but it means the stop
is doing real work and must stay set.
"""
import pandas as pd

from .base import BaseStrategy


class DonchianBreakoutStrategy(BaseStrategy):
    def __init__(self, entry_bars: int = 80, exit_bars: int = 40):
        super().__init__(name=f"Donchian_Breakout_{entry_bars}_{exit_bars}")
        if entry_bars < 2 or exit_bars < 2:
            raise ValueError("entry_bars and exit_bars must both be at least 2")
        self.entry_bars = int(entry_bars)
        self.exit_bars = int(exit_bars)

        # The bot reads these for its indicator line; a breakout rule has no
        # averages, so they stay None and the line simply omits them.
        self.last_short = None
        self.last_long = None
        self.last_spread_percent = None
        self.last_rsi = None
        self.last_ema = None
        self.last_channel_high = None
        self.last_channel_low = None
        self.last_decision = {}

    def get_required_candles_count(self) -> int:
        # The channel is built from the bars BEFORE the latest one, plus one
        # spare so a feed that is short by a single candle does not evaluate
        # against a shorter channel than configured.
        return max(self.entry_bars, self.exit_bars) + 2

    def evaluate(self, df: pd.DataFrame) -> str:
        if len(df) < self.get_required_candles_count():
            self.last_decision = {"raw_signal": "HOLD", "final_signal": "HOLD",
                                  "blocked_by": "INSUFFICIENT_DATA",
                                  "insufficient_data": True}
            return "HOLD"

        closes = pd.to_numeric(df["close"]).to_numpy(dtype=float)
        latest = float(closes[-1])
        # Prior bars only. Including the latest close in its own channel would
        # make a new high impossible by definition.
        channel_high = float(closes[-1 - self.entry_bars:-1].max())
        channel_low = float(closes[-1 - self.exit_bars:-1].min())
        self.last_channel_high = channel_high
        self.last_channel_low = channel_low

        signal = "HOLD"
        if latest > channel_high:
            signal = "BUY"
        elif latest < channel_low:
            signal = "SELL"

        self.last_decision = {
            "raw_signal": signal,
            "final_signal": signal,
            "blocked_by": "",
            "channel_high": channel_high,
            "channel_low": channel_low,
            "close": latest,
        }
        return signal
