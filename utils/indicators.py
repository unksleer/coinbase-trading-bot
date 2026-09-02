"""
Indicator maths, matched to what the existing bots already compute.

Written fresh rather than extracted, because extracting would mean editing
bot.py and strategies/simple_moving_average.py while nine crypto bots and
twelve equity bots are running against a pre-registered experiment. The
formulas here are transcriptions, and a test asserts they agree with the
originals to floating-point tolerance — if the two ever drift, that test fails
rather than two strategies quietly disagreeing about what "RSI 40" means.

Every function is causal: the value at index i uses only data up to and
including i. That is not a stylistic point. An opening-range strategy decides
whether to enter using indicators computed at the breakout candle, and a single
centred or forward-filled window would let it see the future and turn a
worthless rule into a profitable-looking one.
"""
import pandas as pd


def rsi(closes, period=14):
    """
    Wilder's RSI. Matches strategies/simple_moving_average.py exactly,
    including the 1e-9 guard that keeps a zero average loss finite.
    """
    closes = pd.Series(closes, dtype="float64")
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def ema(closes, span=200):
    """Exponential moving average. Matches the SMA strategy's EMA filter."""
    return pd.Series(closes, dtype="float64").ewm(span=span, adjust=False).mean()


def atr(high, low, close, period=14):
    """
    Average true range, simple mean of true range. Matches bot.py:370.

    Note this is the rolling-mean form, not Wilder's smoothing. Kept that way
    deliberately: agreeing with the running bots matters more here than
    matching a textbook, and a strategy compared against them must measure
    volatility the same way.
    """
    high = pd.Series(high, dtype="float64")
    low = pd.Series(low, dtype="float64")
    close = pd.Series(close, dtype="float64")
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def volume_ratio(volume, window=20):
    """
    Each bar's volume against the mean of the preceding `window` bars.

    The mean EXCLUDES the current bar. Including it is the classic quiet
    look-ahead in a volume filter: a huge breakout bar inflates its own
    baseline and the ratio understates exactly the move being tested for.
    """
    volume = pd.Series(volume, dtype="float64")
    baseline = volume.shift(1).rolling(window=window).mean()
    return volume / baseline
