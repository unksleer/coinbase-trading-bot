import os
import sys
import time
import pandas as pd
import numpy as np
import requests
from datetime import datetime

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

def fetch_historical_data(product_id: str, granularity: str, total_candles: int = 1500) -> pd.DataFrame:
    print(f"Fetching {total_candles} historical candles ({granularity}) for {product_id}...")
    gran_seconds = GRANULARITY_MAP.get(granularity, 3600)
    all_candles = []
    end_time = datetime.utcnow()
    chunk_size = 300
    fetched = 0
    
    while fetched < total_candles:
        limit = min(chunk_size, total_candles - fetched)
        start_time = end_time - pd.Timedelta(seconds=limit * gran_seconds)
        url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
        params = {
            "granularity": gran_seconds,
            "start": start_time.isoformat(),
            "end": end_time.isoformat()
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not data:
                break
            all_candles.extend(data)
            fetched += len(data)
            oldest_unix = min([item[0] for item in data])
            end_time = datetime.utcfromtimestamp(oldest_unix)
            time.sleep(0.2)
        except Exception as e:
            print(f"Error fetching chunk: {e}")
            break
            
    if not all_candles:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_candles, columns=['start', 'low', 'high', 'open', 'close', 'volume'])
    df['start'] = pd.to_numeric(df['start'])
    df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
    return df

def run_fast_backtest(closes, high_low, short_sma, long_sma, min_spread, stop_loss, take_profit,
                      rsi_vals, ema_vals, start_cash=100.0, trade_amount_usd=10.0, fee_rate=0.012,
                      rsi_buy_max=70.0, rsi_sell_min=40.0, ema_enabled=False):
    """
    Super fast backtest using precalculated arrays.
    """
    cash = start_cash
    holdings = 0.0
    entry_price = 0.0
    position = "NONE"
    
    completed_trades = 0
    winning_trades = 0
    total_trades = 0
    
    sum_gains = 0.0
    sum_losses = 0.0
    
    # Pre-calculate boolean indicators
    # crossover triggers when short crosses long
    # We will do loop from 1 to len(closes)
    # precalculate cross_buy and cross_sell
    prev_shorts = short_sma[:-1]
    curr_shorts = short_sma[1:]
    prev_longs = long_sma[:-1]
    curr_longs = long_sma[1:]
    
    # We shift closes so loop index aligns
    # Let's run a simple step-by-step state machine simulation
    n = len(closes)
    
    for i in range(1, n):
        price = closes[i]
        
        # Check stop loss / take profit first if we are LONG
        if position == "LONG":
            price_change = ((price - entry_price) / entry_price) * 100
            
            # SL or TP check
            if (stop_loss > 0.0 and price_change <= -stop_loss) or (take_profit > 0.0 and price_change >= take_profit):
                # Sell
                gross_usd = holdings * price
                fee = gross_usd * fee_rate
                net_usd = gross_usd - fee
                pnl = (price - entry_price) * holdings - fee
                
                cash += net_usd
                completed_trades += 1
                if pnl > 0:
                    winning_trades += 1
                    sum_gains += pnl
                else:
                    sum_losses += abs(pnl)
                    
                holdings = 0.0
                entry_price = 0.0
                position = "NONE"
                continue # Moved out of position, proceed to next candle
                
        # Crossover check
        ps, cs = short_sma[i-1], short_sma[i]
        pl, cl = long_sma[i-1], long_sma[i]
        
        if cs > cl and ps <= pl: # BUY signal
            if position == "NONE":
                # Evaluate filters
                spread = (abs(cs - cl) / cl) * 100
                if spread >= min_spread:
                    if rsi_vals is None or rsi_vals[i] <= rsi_buy_max:
                        if not ema_enabled or ema_vals is None or price > ema_vals[i]:
                            # Execute BUY
                            trade_amount = cash if trade_amount_usd <= 0.0 else min(trade_amount_usd, cash)
                            if trade_amount > 0:
                                fee = trade_amount * fee_rate
                                net_spend = trade_amount - fee
                                holdings = net_spend / price
                                cash -= trade_amount
                                entry_price = price
                                position = "LONG"
                                total_trades += 1
                                
        elif cs < cl and ps >= pl: # SELL signal
            if position == "LONG":
                # Evaluate filters (spread/RSI/EMA if applicable on sell, but standard strategy crossover)
                # To match strategies/simple_moving_average.py:
                # strategy sell checks: spread, RSI, EMA
                spread = (abs(cs - cl) / cl) * 100
                if spread >= min_spread:
                    if rsi_vals is None or rsi_vals[i] >= rsi_sell_min:
                        if not ema_enabled or ema_vals is None or price <= ema_vals[i]:
                            # Execute SELL
                            gross_usd = holdings * price
                            fee = gross_usd * fee_rate
                            net_usd = gross_usd - fee
                            pnl = (price - entry_price) * holdings - fee
                            
                            cash += net_usd
                            completed_trades += 1
                            if pnl > 0:
                                winning_trades += 1
                                sum_gains += pnl
                            else:
                                sum_losses += abs(pnl)
                                
                            holdings = 0.0
                            entry_price = 0.0
                            position = "NONE"
                            
    final_value = cash + (holdings * closes[-1])
    net_profit = final_value - start_cash
    win_rate = (winning_trades / completed_trades * 100) if completed_trades > 0 else 0.0
    profit_factor = (sum_gains / sum_losses) if sum_losses > 0 else (999.0 if sum_gains > 0 else 1.0)
    
    return {
        "net_profit": net_profit,
        "percent": (net_profit / start_cash) * 100,
        "completed": completed_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor
    }

def main():
    pair = "NEAR-USD"
    granularity = "ONE_HOUR"
    candles = 3000
    fee_rate = 0.012
    
    df = fetch_historical_data(pair, granularity, candles)
    if df.empty:
        print("Failed to fetch historical data")
        return
        
    print(f"Data loaded: {len(df)} candles.")
    
    closes = pd.to_numeric(df['close']).to_numpy()
    
    # Pre-calculate RSI 14
    delta = pd.to_numeric(df['close']).diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    rsi_14 = (100 - (100 / (1 + rs))).to_numpy()
    
    # Pre-calculate EMA 200
    ema_200 = pd.to_numeric(df['close']).ewm(span=200, adjust=False).mean().to_numpy()
    
    # Parameter lists
    shorts = [5, 8, 10, 12, 15]
    longs = [20, 25, 30, 35, 40, 50]
    spreads = [0.0]
    stop_losses = [2.0, 3.0, 4.0, 5.0, 6.0]
    take_profits = [6.0, 8.0, 10.0, 12.0, 15.0]
    
    results = []
    
    # Precalculate SMAs
    sma_cache = {}
    closes_series = pd.to_numeric(df['close'])
    for w in set(shorts + longs):
        sma_cache[w] = closes_series.rolling(w).mean().to_numpy()
        
    print("Running grid search optimization...")
    start_time = time.time()
    
    for s in shorts:
        for l in longs:
            if l <= s:
                continue
            
            s_sma = sma_cache[s]
            l_sma = sma_cache[l]
            
            for sp in spreads:
                for sl in stop_losses:
                    for tp in take_profits:
                        # Test with compounding portfolio (trade_amount_usd = 0.0)
                        res = run_fast_backtest(
                            closes=closes,
                            high_low=None,
                            short_sma=s_sma,
                            long_sma=l_sma,
                            min_spread=sp,
                            stop_loss=sl,
                            take_profit=tp,
                            rsi_vals=rsi_14,
                            ema_vals=ema_200,
                            trade_amount_usd=0.0, # Compounding 100% of cash
                            fee_rate=fee_rate,
                            ema_enabled=True
                        )
                        
                        results.append({
                            "short": s,
                            "long": l,
                            "spread": sp,
                            "stop_loss": sl,
                            "take_profit": tp,
                            "profit": res["net_profit"],
                            "percent": res["percent"],
                            "trades": res["completed"],
                            "win_rate": res["win_rate"],
                            "profit_factor": res["profit_factor"]
                        })
                        
    elapsed = time.time() - start_time
    print(f"Optimized search completed in {elapsed:.2f} seconds!")
    
    opt_df = pd.DataFrame(results)
    opt_df = opt_df.sort_values(by="profit", ascending=False).reset_index(drop=True)
    
    print("\nTOP 15 PARAMETER CONFIGURATIONS (Sorted by Compounded Net Profit):")
    print(opt_df.head(15).to_string(index=False))

if __name__ == "__main__":
    main()
