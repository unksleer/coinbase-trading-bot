import os
import sys
import time
import requests
import pandas as pd
from datetime import datetime

# Add the project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.settings import Settings
from strategies.simple_moving_average import SMACrossoverStrategy

# List of top USD pairs on Coinbase to scan (USD pairs are widely supported by the public API)
COINS_TO_SCAN = [
    'BTC-USD', 'ETH-USD', 'SOL-USD', 'AVAX-USD', 'LINK-USD', 
    'ADA-USD', 'XRP-USD', 'DOGE-USD', 'NEAR-USD', 'SUI-USD', 
    'APT-USD', 'RENDER-USD', 'ICP-USD', 'DOT-USD', 'LTC-USD'
]


# Granularity mapping
GRANULARITY_MAP = {
    'ONE_MINUTE': 60,
    'FIVE_MINUTE': 300,
    'FIFTEEN_MINUTE': 900,
    'THIRTY_MINUTE': 1800,
    'ONE_HOUR': 3600,
    'TWO_HOUR': 7200,
    'SIX_HOUR': 21600,
    'ONE_DAY': 86400
}

def fetch_candles_public(product_id: str, granularity: str) -> pd.DataFrame:
    granularity_seconds = GRANULARITY_MAP.get(granularity, 60)
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
    params = {
        "granularity": granularity_seconds
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=['start', 'low', 'high', 'open', 'close', 'volume'])
        df['start'] = pd.to_numeric(df['start'])
        df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
        return df
    except Exception as e:
        return pd.DataFrame()

def main():
    print("="*75)
    print(f"COINBASE MULTI-ASSET CRYPTO SCREENER & MOVER SCANNER")
    print(f"Time: {datetime.utcnow().isoformat()}Z | Granularity: {Settings.GRANULARITY}")
    print("="*75)
    print("Scanning market pairs for crossover signals...\n")

    # Initialize the strategy using .env settings
    strategy = SMACrossoverStrategy(
        short_window=Settings.SMA_SHORT_WINDOW,
        long_window=Settings.SMA_LONG_WINDOW,
        min_spread_percent=Settings.SMA_MIN_SPREAD_PERCENT,
        rsi_filter_enabled=Settings.RSI_FILTER_ENABLED,
        rsi_period=Settings.RSI_PERIOD,
        rsi_buy_max=Settings.RSI_BUY_MAX,
        rsi_sell_min=Settings.RSI_SELL_MIN,
        ema_filter_enabled=Settings.EMA_TREND_FILTER_ENABLED,
        ema_period=Settings.EMA_TREND_PERIOD
    )
    required_candles = strategy.get_required_candles_count()

    results = []

    for coin in COINS_TO_SCAN:
        print(f"Scanning {coin:11} ... ", end="", flush=True)
        df = fetch_candles_public(coin, Settings.GRANULARITY)
        
        if df.empty or len(df) < required_candles:
            print("Skipped (insufficient historical data)")
            time.sleep(0.2)
            continue
            
        latest_price = float(df.iloc[-1]['close'])
        signal = strategy.evaluate(df)
        
        # Extract indicators calculated during evaluation
        short_sma = getattr(strategy, "last_short", None)
        long_sma = getattr(strategy, "last_long", None)
        spread = getattr(strategy, "last_spread_percent", None)
        rsi = getattr(strategy, "last_rsi", None)
        ema = getattr(strategy, "last_ema", None)
        
        # Calculate 24h change
        granularity_seconds = GRANULARITY_MAP.get(Settings.GRANULARITY, 3600)
        day_seconds = 86400
        candles_in_24h = int(day_seconds / granularity_seconds)
        
        change_24h = 0.0
        if len(df) > candles_in_24h:
            past_price = float(df.iloc[-candles_in_24h]['close'])
            change_24h = ((latest_price - past_price) / past_price) * 100
        
        above_ema = False
        if ema is not None and latest_price > ema:
            above_ema = True
            
        results.append({
            "Coin": coin.split('-')[0],
            "Price": latest_price,
            "24h Change %": change_24h,
            "RSI": rsi if rsi is not None else 0.0,
            "Spread %": spread if spread is not None else 0.0,
            "Signal": signal,
            "Above EMA": above_ema
        })
        print(f"Done (Signal: {signal})")
        time.sleep(0.2)
        
    if not results:
        print("\nNo pairs scanned successfully.")
        return
        
    scan_df = pd.DataFrame(results)
    
    # Sort helper to prioritize BUY signals
    def sort_key(row):
        sig_val = 2 if row['Signal'] == 'BUY' else (0 if row['Signal'] == 'SELL' else 1)
        return (sig_val, row['Spread %'], row['24h Change %'])
        
    scan_df['sort_val'] = scan_df.apply(sort_key, axis=1)
    scan_df = scan_df.sort_values(by='sort_val', ascending=False).drop(columns=['sort_val']).reset_index(drop=True)
    
    print("\n" + "="*75)
    print("SCREENER RESULTS")
    print("="*75)
    
    pd.options.display.float_format = '{:,.4f}'.format
    print(scan_df.to_string(index=False))
    print("="*75)
    
    # Export metrics scoreboard to logs/market_metrics.json
    try:
        os.makedirs("logs", exist_ok=True)
        total_scanned = len(results)
        bullish_count = len([r for r in results if r["Above EMA"]])
        bullish_ratio = bullish_count / total_scanned if total_scanned > 0 else 0.0
        
        scoreboard = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "market_bullish_ratio": bullish_ratio,
            "bullish_count": bullish_count,
            "total_scanned": total_scanned,
            "results": results
        }
        
        # Write atomically
        temp_path = "logs/market_metrics.json.tmp"
        final_path = "logs/market_metrics.json"
        with open(temp_path, 'w') as f:
            import json
            json.dump(scoreboard, f, indent=4)
        os.replace(temp_path, final_path)
    except Exception as e:
        print(f"Error saving scoreboard JSON: {e}")
    
    buys = scan_df[scan_df['Signal'] == 'BUY']
    if not buys.empty:
        print("\n🚀 ACTIVE BUY SIGNALS DETECTED:")
        for idx, row in buys.iterrows():
            print(f" * {row['Coin']}: Price ${row['Price']:,.4f} | Spread {row['Spread %']:.2f}% | RSI {row['RSI']:.1f}")
    else:
        print("\nℹ️ No active BUY signals triggered at the moment.")
        
    sells = scan_df[scan_df['Signal'] == 'SELL']
    if not sells.empty:
        print("\n⚠️ ACTIVE SELL SIGNALS DETECTED:")
        for idx, row in sells.iterrows():
            print(f" * {row['Coin']}: Price ${row['Price']:,.4f} | Spread {row['Spread %']:.2f}% | RSI {row['RSI']:.1f}")

if __name__ == "__main__":
    main()
