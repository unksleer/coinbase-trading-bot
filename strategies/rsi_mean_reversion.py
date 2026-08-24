import pandas as pd
from .base import BaseStrategy

class RSIMeanReversionStrategy(BaseStrategy):
    """
    RSI Mean Reversion Strategy.
    
    Triggers a BUY signal when the RSI recovers from oversold territory (crosses above rsi_oversold).
    Triggers a SELL signal when the RSI falls back from overbought territory (crosses below rsi_overbought).
    """
    def __init__(self, rsi_period: int = 14, rsi_oversold: float = 30.0, rsi_overbought: float = 70.0):
        super().__init__(name=f"RSI_MeanReversion_{rsi_period}_{rsi_oversold}_{rsi_overbought}")
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.last_rsi = None

        # Attributes for compatible logging with bot's indicators display
        self.last_short = None
        self.last_long = None
        self.last_spread_percent = None
        self.last_ema = None

    def get_required_candles_count(self) -> int:
        # We need enough candles to calculate the RSI smoothing
        return self.rsi_period + 10

    def evaluate(self, df: pd.DataFrame) -> str:
        if len(df) < self.get_required_candles_count():
            self.last_decision = {"raw_signal": "HOLD", "final_signal": "HOLD",
                                  "blocked_by": "INSUFFICIENT_DATA", "insufficient_data": True}
            return "HOLD"

        closes = pd.to_numeric(df['close'])
        
        # Calculate RSI
        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        
        # Wilder's smoothing using ewm
        avg_gain = gain.ewm(alpha=1/self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/self.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-9)
        rsi_series = 100 - (100 / (1 + rs))
        
        curr_rsi = float(rsi_series.iloc[-1])
        prev_rsi = float(rsi_series.iloc[-2])
        self.last_rsi = curr_rsi
        
        signal = "HOLD"
        
        # BUY when RSI rises back above the oversold line (indicating upward momentum shift)
        if prev_rsi < self.rsi_oversold and curr_rsi >= self.rsi_oversold:
            signal = "BUY"
        # SELL when RSI falls back below the overbought line (indicating downward momentum shift)
        elif prev_rsi > self.rsi_overbought and curr_rsi <= self.rsi_overbought:
            signal = "SELL"

        # This strategy has no post-crossover filters, so raw and final always agree.
        self.last_decision = {
            "raw_signal": signal,
            "final_signal": signal,
            "blocked_by": "",
            "rsi": curr_rsi,
        }
        return signal
