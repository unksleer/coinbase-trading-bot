#!/usr/bin/env python3
import os
import sys
import time
import argparse
import requests
import pandas as pd
import numpy as np
from datetime import datetime

# Standard stock list to scan
DEFAULT_STOCKS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX', 'AMD', 'AVGO',
    'SPY', 'QQQ', 'DIA', 'IWM'
]

def fetch_stock_candles(ticker: str, days: int = 365) -> pd.DataFrame:
    """
    Fetches daily stock candles from Yahoo Finance chart API.
    """
    # We fetch a bit more data to ensure we have enough to calculate indicators
    range_str = "2y" if days > 365 else "1y"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={range_str}&interval=1d"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        res_json = response.json()
        
        result = res_json["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]
        
        if not timestamps or not quote.get("close"):
            return pd.DataFrame()
            
        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": quote.get("open", []),
            "high": quote.get("high", []),
            "low": quote.get("low", []),
            "close": quote.get("close", []),
            "volume": quote.get("volume", [])
        })
        
        # Drop rows with missing values
        df = df.dropna().reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()

def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculates smoothed RSI (Wilder's) using numpy to avoid pandas slicing limitations.
    """
    delta = prices.diff().values
    gain = np.clip(delta, 0, None)
    loss = -np.clip(delta, None, 0)
    
    avg_gain = np.zeros_like(prices, dtype=float)
    avg_loss = np.zeros_like(prices, dtype=float)
    
    # Initialize first valid value
    avg_gain[period] = np.mean(gain[1:period+1])
    avg_loss[period] = np.mean(loss[1:period+1])
    
    # Wilder's smoothing
    for i in range(period + 1, len(prices)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
        
    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    rsi = 100 - (100 / (1 + rs))
    rsi[:period] = np.nan
    return pd.Series(rsi, index=prices.index)

def main():
    parser = argparse.ArgumentParser(description="Multi-Asset Stock Screener & Trend Scanner")
    parser.add_argument("--tickers", type=str, help="Comma-separated list of custom tickers to scan")
    parser.add_argument("--short", type=int, default=50, help="Short SMA window (default: 50)")
    parser.add_argument("--long", type=int, default=200, help="Long SMA window (default: 200)")
    args = parser.parse_args()
    
    tickers = args.tickers.split(",") if args.tickers else DEFAULT_STOCKS
    
    print("="*85)
    print(f"STOCK TREND SCREENER & VOLATILITY SCANNER")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %I:%M %p')} | Method: SMA Crossover ({args.short}/{args.long})")
    print("="*85)
    print("Scanning Yahoo Finance daily chart data...\n")
    
    results = []
    
    for ticker in tickers:
        ticker = ticker.strip().upper()
        print(f"Scanning {ticker:10} ... ", end="", flush=True)
        
        # Fetch data
        df = fetch_stock_candles(ticker, days=args.long + 100)
        
        if df.empty or len(df) < args.long:
            print("Skipped (insufficient data)")
            time.sleep(0.1)
            continue
            
        # Calculate moving averages
        df['sma_short'] = df['close'].rolling(window=args.short).mean()
        df['sma_long'] = df['close'].rolling(window=args.long).mean()
        
        # Calculate RSI
        df['rsi'] = calculate_rsi(df['close'], period=14)
        
        # Calculate 1d Price Change
        latest_price = float(df['close'].iloc[-1])
        past_price = float(df['close'].iloc[-2])
        change_1d = ((latest_price - past_price) / past_price) * 100
        
        # Get indicators
        short_val = df['sma_short'].iloc[-1]
        long_val = df['sma_long'].iloc[-1]
        rsi_val = df['rsi'].iloc[-1]
        
        # Determine signals
        signal = "HOLD"
        
        # Check crossover
        prev_short = df['sma_short'].iloc[-2]
        prev_long = df['sma_long'].iloc[-2]
        
        if prev_short <= prev_long and short_val > long_val:
            signal = "GOLDEN CROSS (BUY)"
        elif prev_short >= prev_long and short_val < long_val:
            signal = "DEATH CROSS (SELL)"
        else:
            # Check if recently crossed in last 3 days
            crossed_buy = False
            crossed_sell = False
            for offset in range(2, 5):
                if offset <= len(df):
                    s_past = df['sma_short'].iloc[-offset]
                    l_past = df['sma_long'].iloc[-offset]
                    s_prev = df['sma_short'].iloc[-offset+1]
                    l_prev = df['sma_long'].iloc[-offset+1]
                    if s_past <= l_past and s_prev > l_prev:
                        crossed_buy = True
                    elif s_past >= l_past and s_prev < l_prev:
                        crossed_sell = True
            
            if crossed_buy:
                signal = "RECENT GOLDEN CROSS"
            elif crossed_sell:
                signal = "RECENT DEATH CROSS"
            else:
                signal = "BULLISH RUN" if short_val > long_val else "BEARISH RUN"
                
        # Calculate Spread
        spread = ((short_val - long_val) / long_val) * 100
        
        results.append({
            "Ticker": ticker,
            "Price": latest_price,
            "1d Change %": change_1d,
            "SMA Short": short_val,
            "SMA Long": long_val,
            "Spread %": spread,
            "RSI (14)": rsi_val,
            "Signal": signal
        })
        print(f"Done (Signal: {signal})")
        time.sleep(0.1) # Soft delay to be polite to the API
        
    if not results:
        print("\nNo stocks scanned successfully.")
        return
        
    scan_df = pd.DataFrame(results)
    
    # Sort: Golden Crosses first, then Bullish Runs, then Bearish/Death
    def sort_key(row):
        sig = row['Signal']
        if "GOLDEN CROSS (BUY)" in sig:
            return (4, row['1d Change %'])
        elif "RECENT GOLDEN CROSS" in sig:
            return (3, row['1d Change %'])
        elif "BULLISH RUN" in sig:
            return (2, row['Spread %'])
        elif "BEARISH RUN" in sig:
            return (1, row['Spread %'])
        else:
            return (0, row['1d Change %'])
            
    scan_df['sort_val'] = scan_df.apply(sort_key, axis=1)
    scan_df = scan_df.sort_values(by='sort_val', ascending=False).drop(columns=['sort_val']).reset_index(drop=True)
    
    print("\n" + "="*85)
    print("SCREENER RESULTS")
    print("="*85)
    
    pd.options.display.float_format = '{:,.2f}'.format
    print(scan_df.to_string(index=False))
    print("="*85)
    
    # Alert Summary
    buy_signals = [r for r in results if "GOLDEN CROSS" in r['Signal']]
    sell_signals = [r for r in results if "DEATH CROSS" in r['Signal']]
    
    if buy_signals:
        print("\n🚀 GOLDEN CROSS BUY SIGNALS DETECTED:")
        for r in buy_signals:
            print(f" * {r['Ticker']}: Price ${r['Price']:.2f} | RSI {r['RSI (14)']:.1f} | Spread {r['Spread %']:.2f}% | {r['Signal']}")
            
    if sell_signals:
        print("\n⚠️ DEATH CROSS SELL SIGNALS DETECTED:")
        for r in sell_signals:
            print(f" * {r['Ticker']}: Price ${r['Price']:.2f} | RSI {r['RSI (14)']:.1f} | Spread {r['Spread %']:.2f}% | {r['Signal']}")
            
if __name__ == "__main__":
    main()
