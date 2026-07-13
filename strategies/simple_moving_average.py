import pandas as pd
from .base import BaseStrategy

class SMACrossoverStrategy(BaseStrategy):
    """
    Simple Moving Average (SMA) Crossover Strategy.
    
    Triggers a BUY signal when the short-term SMA crosses above the long-term SMA.
    Triggers a SELL signal when the short-term SMA crosses below the long-term SMA.
    """
    def __init__(self, short_window: int = 10, long_window: int = 30, min_spread_percent: float = 0.0,
                 rsi_filter_enabled: bool = False, rsi_period: int = 14, rsi_buy_min: float = 0.0, rsi_buy_max: float = 60.0, rsi_sell_min: float = 40.0,
                 ema_filter_enabled: bool = False, ema_period: int = 200):
        super().__init__(name=f"SMA_Crossover_{short_window}_{long_window}")
        self.short_window = short_window
        self.long_window = long_window
        self.min_spread_percent = min_spread_percent
        self.rsi_filter_enabled = rsi_filter_enabled
        self.rsi_period = rsi_period
        self.rsi_buy_min = rsi_buy_min
        self.rsi_buy_max = rsi_buy_max
        self.rsi_sell_min = rsi_sell_min
        self.ema_filter_enabled = ema_filter_enabled
        self.ema_period = ema_period
        
        self.last_short = None
        self.last_long = None
        self.last_spread_percent = None
        self.last_rsi = None
        self.last_ema = None

    def get_required_candles_count(self) -> int:
        required = self.long_window + 2
        if self.rsi_filter_enabled:
            required = max(required, self.rsi_period + 2)
        if self.ema_filter_enabled:
            required = max(required, self.ema_period + 2)
        return required

    def evaluate(self, df: pd.DataFrame) -> str:
        """
        Evaluates the DataFrame for an SMA crossover with optional RSI & EMA filters.
        Assumes the DataFrame is sorted by time in ascending order (oldest to newest).
        """
        if len(df) < self.get_required_candles_count():
            return "HOLD"

        # Ensure 'close' column is numeric
        closes = pd.to_numeric(df['close'])

        # Calculate moving averages
        short_sma = closes.rolling(window=self.short_window).mean()
        long_sma = closes.rolling(window=self.long_window).mean()

        # Get the last two values to check for a crossover
        prev_short = short_sma.iloc[-2]
        curr_short = short_sma.iloc[-1]
        
        prev_long = long_sma.iloc[-2]
        curr_long = long_sma.iloc[-1]

        # Check if any values are NaN
        if pd.isna(prev_short) or pd.isna(curr_short) or pd.isna(prev_long) or pd.isna(curr_long):
            return "HOLD"

        # Store basic SMA values for external access
        self.last_short = curr_short
        self.last_long = curr_long
        self.last_spread_percent = (abs(curr_short - curr_long) / curr_long) * 100

        # Calculate RSI if enabled
        curr_rsi = None
        if self.rsi_filter_enabled:
            delta = closes.diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            # Wilder's smoothing using ewm
            avg_gain = gain.ewm(alpha=1/self.rsi_period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/self.rsi_period, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, 1e-9)
            rsi = 100 - (100 / (1 + rs))
            curr_rsi = rsi.iloc[-1]
            self.last_rsi = curr_rsi

        # Calculate EMA trend if enabled
        curr_ema = None
        if self.ema_filter_enabled:
            ema = closes.ewm(span=self.ema_period, adjust=False).mean()
            curr_ema = ema.iloc[-1]
            self.last_ema = curr_ema

        # Initial Crossover check:
        signal = "HOLD"
        # BUY signal: short-term crosses ABOVE long-term
        if prev_short < prev_long and curr_short > curr_long:
            signal = "BUY"
        # SELL signal: short-term crosses BELOW long-term
        elif prev_short > prev_long and curr_short < curr_long:
            signal = "SELL"

        # Apply filters to signal
        if signal == "BUY":
            # 1. Spread filter
            if self.min_spread_percent > 0.0:
                if self.last_spread_percent < self.min_spread_percent:
                    return "HOLD"
            # 2. RSI filter
            if self.rsi_filter_enabled and curr_rsi is not None:
                if curr_rsi > self.rsi_buy_max or curr_rsi < self.rsi_buy_min:
                    return "HOLD"
            # 3. EMA trend filter
            if self.ema_filter_enabled and curr_ema is not None:
                if closes.iloc[-1] <= curr_ema:
                    return "HOLD"

        elif signal == "SELL":
            # 1. Spread filter
            if self.min_spread_percent > 0.0:
                if self.last_spread_percent < self.min_spread_percent:
                    return "HOLD"
            # 2. RSI filter
            if self.rsi_filter_enabled and curr_rsi is not None:
                if curr_rsi < self.rsi_sell_min:
                    return "HOLD"
            # 3. EMA trend filter
            if self.ema_filter_enabled and curr_ema is not None:
                if closes.iloc[-1] >= curr_ema:
                    return "HOLD"

        return signal

