"""
Historical stock bars from Alpaca.

Separate from the crypto path because the economics differ in the one way that
has mattered most: Alpaca equity trades are commission-free, so the ~1.2%
round-trip fee that made every crypto configuration unprofitable is absent here.
A strategy that cannot work on crypto may still work on equities for that reason
alone, and vice versa — results are not transferable between the two.

Data comes from the free IEX feed. It covers less volume than SIP, so bars can
differ slightly from consolidated tape, but it is the feed the live stock bot
trades on, which is what makes it the right one to backtest against.
"""
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

# Bars newer than this are withheld on the free feed; requesting them returns an
# error rather than an empty tail.
FREE_FEED_DELAY_MINUTES = 20

TIMEFRAMES = ("Hour", "Day")


def _client():
    from alpaca.data.historical import StockHistoricalDataClient

    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY not set. Stock data needs them; "
            "load .env first."
        )
    return StockHistoricalDataClient(key, secret)


def fetch_bars(ticker, timeframe="Hour", lookback_days=400):
    """
    Return a DataFrame of OHLCV bars for one ticker, oldest first.

    Returns an empty frame rather than raising when a ticker has no data, so a
    bad symbol in a list does not abort the whole run.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import Adjustment, DataFeed

    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {TIMEFRAMES}, got {timeframe!r}")
    unit = TimeFrame.Hour if timeframe == "Hour" else TimeFrame.Day

    end = datetime.now(timezone.utc) - timedelta(minutes=FREE_FEED_DELAY_MINUTES)
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=unit,
        start=end - timedelta(days=lookback_days),
        end=end,
        # Split and dividend adjusted, or a split shows up as a crash and the
        # strategy "reacts" to a price move that never happened.
        adjustment=Adjustment.ALL,
        feed=DataFeed.IEX,
    )
    try:
        frame = _client().get_stock_bars(request).df
    except Exception:
        return pd.DataFrame()

    if frame is None or frame.empty:
        return pd.DataFrame()
    if isinstance(frame.index, pd.MultiIndex):
        try:
            frame = frame.xs(ticker, level=0)
        except KeyError:
            return pd.DataFrame()
    return frame.sort_index()


def closes(ticker, timeframe="Hour", lookback_days=400):
    """Close prices as a numpy array, or an empty array when unavailable."""
    frame = fetch_bars(ticker, timeframe, lookback_days)
    if frame.empty or "close" not in frame:
        import numpy as np
        return np.array([])
    return pd.to_numeric(frame["close"]).to_numpy()


def is_tradable(ticker):
    """
    Whether Alpaca will accept orders for this symbol.

    Checked before a ticker is added, so an untradable symbol fails at setup
    with a clear message rather than silently never trading.
    """
    try:
        from alpaca.trading.client import TradingClient

        key = os.getenv("ALPACA_API_KEY", "")
        secret = os.getenv("ALPACA_SECRET_KEY", "")
        paper = os.getenv("ALPACA_PAPER", "True").strip().lower() not in ("false", "0", "no")
        asset = TradingClient(key, secret, paper=paper).get_asset(ticker.upper())
        return bool(asset.tradable), getattr(asset, "name", "") or ""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"
