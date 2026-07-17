import argparse
import sys
import time
import pandas as pd
import numpy as np
import requests
from datetime import datetime
from strategies import SMACrossoverStrategy, RSIMeanReversionStrategy

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

def fetch_historical_data(product_id: str, granularity: str, total_candles: int = 1000) -> pd.DataFrame:
    """
    Fetches historical candle data from Coinbase Exchange public API,
    paginating backwards to get the requested number of candles.
    """
    print(f"Fetching {total_candles} historical candles ({granularity}) for {product_id}...")
    gran_seconds = GRANULARITY_MAP.get(granularity, 3600)
    
    all_candles = []
    end_time = datetime.utcnow()
    
    # Coinbase limits to 300 candles per request
    chunk_size = 300
    fetched = 0
    
    active_product_id = product_id
    
    while fetched < total_candles:
        limit = min(chunk_size, total_candles - fetched)
        # Calculate start time for this chunk
        start_time = end_time - pd.Timedelta(seconds=limit * gran_seconds)
        
        url = f"https://api.exchange.coinbase.com/products/{active_product_id}/candles"
        params = {
            "granularity": gran_seconds,
            "start": start_time.isoformat(),
            "end": end_time.isoformat()
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code in (400, 404) and active_product_id.endswith("-USDC"):
                fallback_product_id = active_product_id[:-4] + "USD"
                print(f"Candles request for {active_product_id} failed with {response.status_code}. Falling back to {fallback_product_id}...")
                active_product_id = fallback_product_id
                url = f"https://api.exchange.coinbase.com/products/{active_product_id}/candles"
                response = requests.get(url, params=params, timeout=10)
                
            response.raise_for_status()
            data = response.json()
            
            if not data:
                break
                
            all_candles.extend(data)
            fetched += len(data)
            
            # Move end_time back to the oldest start time in this batch
            # Data format: [[time, low, high, open, close, volume], ...]
            oldest_unix = min([item[0] for item in data])
            end_time = datetime.utcfromtimestamp(oldest_unix)
            
            # Sleep briefly to respect rate limits
            time.sleep(0.2)
        except Exception as e:
            print(f"Error fetching chunk: {e}")
            break
            
    if not all_candles:
        return pd.DataFrame()
        
    df = pd.DataFrame(all_candles, columns=['start', 'low', 'high', 'open', 'close', 'volume'])
    df['start'] = pd.to_numeric(df['start'])
    # Sort ascending (oldest first)
    df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
    return df


def run_backtest(df: pd.DataFrame, short_window: int, long_window: int, min_spread: float, stop_loss: float, take_profit: float,
                 rsi_enabled: bool = False, rsi_period: int = 14, rsi_buy_max: float = 60.0, rsi_sell_min: float = 40.0,
                 ema_enabled: bool = False, ema_period: int = 200, start_cash: float = 100.0, gran_seconds: int = 3600,
                 trade_amount_usd: float = 10.0, fee_rate: float = 0.006, strategy_type: str = "SMA_CROSSOVER",
                 rsi_oversold: float = 30.0, rsi_overbought: float = 70.0,
                 stop_loss_atr_multiple: float = 0.0, take_profit_atr_multiple: float = 0.0) -> dict:
    """
    Simulates trading over the historical DataFrame.
    """
    if strategy_type == "RSI_MEAN_REVERSION":
        strategy = RSIMeanReversionStrategy(
            rsi_period=rsi_period,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought
        )
    else:
        strategy = SMACrossoverStrategy(
            short_window=short_window,
            long_window=long_window,
            min_spread_percent=min_spread,
            rsi_filter_enabled=rsi_enabled,
            rsi_period=rsi_period,
            rsi_buy_max=rsi_buy_max,
            rsi_sell_min=rsi_sell_min,
            ema_filter_enabled=ema_enabled,
            ema_period=ema_period
        )
    required = strategy.get_required_candles_count()
    
    # Precompute ATR (14-period)
    high_prices = df['high']
    low_prices = df['low']
    prev_close = df['close'].shift(1)
    tr1 = high_prices - low_prices
    tr2 = (high_prices - prev_close).abs()
    tr3 = (low_prices - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.rolling(window=14).mean()
    
    cash = start_cash
    holdings = 0.0
    entry_price = 0.0
    entry_atr = 0.0
    position = "NONE"
    
    trades = []
    buy_index = None
    portfolio_values = []
    
    # We loop through the dataframe starting from the first index that has enough history
    for i in range(required, len(df)):
        # Slice the dataframe up to index i to simulate real-time stream
        sub_df = df.iloc[i - required:i + 1]
        
        latest_candle = sub_df.iloc[-1]
        price = float(latest_candle['close'])
        
        signal = strategy.evaluate(sub_df)
        
        # Risk management check
        if position == "LONG" and entry_price > 0:
            price_change = ((price - entry_price) / entry_price) * 100
            
            # Check ATR Stop Loss
            atr_sl_triggered = False
            if stop_loss_atr_multiple > 0.0 and entry_atr > 0.0:
                sl_price = entry_price - (stop_loss_atr_multiple * entry_atr)
                if price <= sl_price:
                    signal = "SELL"
                    atr_sl_triggered = True
            
            # Check percentage Stop Loss
            if not atr_sl_triggered and stop_loss > 0.0 and price_change <= -stop_loss:
                signal = "SELL"
                
            # Check ATR Take Profit
            if signal != "SELL":
                atr_tp_triggered = False
                if take_profit_atr_multiple > 0.0 and entry_atr > 0.0:
                    tp_price = entry_price + (take_profit_atr_multiple * entry_atr)
                    if price >= tp_price:
                        signal = "SELL"
                        atr_tp_triggered = True
                
                # Check percentage Take Profit
                if not atr_tp_triggered and take_profit > 0.0 and price_change >= take_profit:
                    signal = "SELL"
                
        # Handle trades
        if signal == "BUY" and position == "NONE":
            # BUY using trade_amount_usd (or total cash if total cash is less, or 100% of cash if trade_amount_usd <= 0)
            trade_amount = cash if trade_amount_usd <= 0.0 else min(trade_amount_usd, cash)
            if trade_amount > 0:
                fee = trade_amount * fee_rate
                net_spend = trade_amount - fee
                acquired = net_spend / price
                cash -= trade_amount
                holdings += acquired
                entry_price = price
                entry_atr = float(atr_series.iloc[i]) if not pd.isna(atr_series.iloc[i]) else 0.0
                position = "LONG"
                buy_index = i
                trades.append({"type": "BUY", "price": price, "amount_usd": net_spend, "holdings": holdings, "pnl": 0.0})
                
        elif signal == "SELL" and position == "LONG":
            if holdings > 0:
                gross_usd = holdings * price
                fee = gross_usd * fee_rate
                net_usd = gross_usd - fee
                pnl = (price - entry_price) * holdings - fee
                cash += net_usd
                
                duration = i - buy_index if buy_index is not None else 0
                trades.append({
                    "type": "SELL",
                    "price": price,
                    "amount_usd": gross_usd,
                    "holdings": 0.0,
                    "pnl": pnl,
                    "duration": duration
                })
                holdings = 0.0
                entry_price = 0.0
                position = "NONE"
                buy_index = None
                
        # Track portfolio value for drawdown
        curr_val = cash + (holdings * price)
        portfolio_values.append(curr_val)
                
    completed_trades = [t for t in trades if t["type"] == "SELL"]
    winning_trades = [t for t in completed_trades if t["pnl"] > 0]
    losing_trades = [t for t in completed_trades if t["pnl"] <= 0]
    
    win_rate = (len(winning_trades) / len(completed_trades) * 100) if completed_trades else 0.0
    avg_win = np.mean([t["pnl"] for t in winning_trades]) if winning_trades else 0.0
    avg_loss = np.mean([t["pnl"] for t in losing_trades]) if losing_trades else 0.0
    max_win = np.max([t["pnl"] for t in winning_trades]) if winning_trades else 0.0
    max_loss = np.min([t["pnl"] for t in losing_trades]) if losing_trades else 0.0
    
    # Profit factor
    sum_gains = sum([t["pnl"] for t in winning_trades])
    sum_losses = abs(sum([t["pnl"] for t in losing_trades]))
    profit_factor = (sum_gains / sum_losses) if sum_losses > 0 else (np.inf if sum_gains > 0 else 1.0)
    
    # Average trade duration
    avg_duration = np.mean([t["duration"] for t in completed_trades]) if completed_trades else 0.0
    
    # Maximum drawdown
    running_max = start_cash
    max_drawdown = 0.0
    for val in portfolio_values:
        running_max = max(running_max, val)
        if running_max > 0:
            dd = (running_max - val) / running_max * 100
            max_drawdown = max(max_drawdown, dd)
            
    final_value = cash + (holdings * float(df.iloc[-1]['close']))
    net_profit = final_value - start_cash
    return {
        "final_value": final_value,
        "net_profit": net_profit,
        "profit_percent": (net_profit / start_cash) * 100,
        "total_trades": len(trades),
        "completed_trades": len(completed_trades),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_win": max_win,
        "max_loss": max_loss,
        "winning_trades_count": len(winning_trades),
        "losing_trades_count": len(losing_trades),
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "avg_duration": avg_duration,
        "trades": trades
    }

def main():
    parser = argparse.ArgumentParser(description="Coinbase Trading Bot Backtester")
    parser.add_argument("--pair", type=str, default="BTC-USD", help="Trading pair (default: BTC-USD)")
    parser.add_argument("--granularity", type=str, default="ONE_HOUR", help="Candle size: ONE_MINUTE, ONE_HOUR, etc.")
    parser.add_argument("--candles", type=int, default=1000, help="Total candles to backtest (default: 1000)")
    parser.add_argument("--optimize", action="store_true", help="Run grid search optimizer to find best parameters")
    parser.add_argument("--trade-amount", type=float, default=10.0, help="USD trade size (default: 10.0, set to 0 to use all available cash)")
    parser.add_argument("--fee-rate", type=float, default=0.006, help="Fee rate per trade, e.g. 0.006 for 0.6 percent (default: 0.006)")
    args = parser.parse_args()
    
    # Set TRADING_PAIR environment variable so Settings loaded dynamically matches the backtested pair
    import os
    os.environ["TRADING_PAIR"] = args.pair
    
    df = fetch_historical_data(args.pair, args.granularity, args.candles)

    if df.empty:
        print("Error: Could not retrieve historical data.")
        sys.exit(1)
        
    print(f"Data retrieved: {len(df)} candles from {datetime.utcfromtimestamp(df.iloc[0]['start']).isoformat()} to {datetime.utcfromtimestamp(df.iloc[-1]['start']).isoformat()}")
    
    gran_seconds = GRANULARITY_MAP.get(args.granularity, 3600)
    
    if args.optimize:
        print("\n" + "="*50)
        print("RUNNING GRID SEARCH OPTIMIZATION")
        print("="*50)
        
        # Default filters from Settings
        from config.settings import Settings
        
        # Ranges to optimize
        shorts = [5, 10, 15, 20]
        longs = [20, 30, 40, 50]
        spreads = [0.0, 0.1, 0.25, 0.5]
        stop_losses = [1.0, 2.0, 3.0]
        take_profits = [3.0, 5.0, 10.0]
        
        results = []
        
        for s in shorts:
            for l in longs:
                if l <= s:
                    continue
                for sp in spreads:
                    for sl in stop_losses:
                        for tp in take_profits:
                            res = run_backtest(
                                df, s, l, sp, sl, tp,
                                rsi_enabled=Settings.RSI_FILTER_ENABLED,
                                rsi_period=Settings.RSI_PERIOD,
                                rsi_buy_max=Settings.RSI_BUY_MAX,
                                rsi_sell_min=Settings.RSI_SELL_MIN,
                                ema_enabled=Settings.EMA_TREND_FILTER_ENABLED,
                                ema_period=Settings.EMA_TREND_PERIOD,
                                gran_seconds=gran_seconds,
                                trade_amount_usd=args.trade_amount,
                                fee_rate=args.fee_rate
                            )
                            results.append({
                                "short": s,
                                "long": l,
                                "spread": sp,
                                "stop_loss": sl,
                                "take_profit": tp,
                                "profit": res["net_profit"],
                                "percent": res["profit_percent"],
                                "trades": res["completed_trades"]
                            })
                            
        opt_df = pd.DataFrame(results)
        opt_df = opt_df.sort_values(by="profit", ascending=False).reset_index(drop=True)
        
        print("\nTOP 10 PARAMETER CONFIGURATIONS:")
        print(opt_df.head(10).to_string(index=False))
    else:
        # Default backtest using .env or standard settings
        from config.settings import Settings
        
        res = run_backtest(
            df,
            short_window=Settings.SMA_SHORT_WINDOW,
            long_window=Settings.SMA_LONG_WINDOW,
            min_spread=Settings.SMA_MIN_SPREAD_PERCENT,
            stop_loss=Settings.STOP_LOSS_PERCENT,
            take_profit=Settings.TAKE_PROFIT_PERCENT,
            rsi_enabled=Settings.RSI_FILTER_ENABLED,
            rsi_period=Settings.RSI_PERIOD,
            rsi_buy_max=Settings.RSI_BUY_MAX,
            rsi_sell_min=Settings.RSI_SELL_MIN,
            ema_enabled=Settings.EMA_TREND_FILTER_ENABLED,
            ema_period=Settings.EMA_TREND_PERIOD,
            gran_seconds=gran_seconds,
            trade_amount_usd=args.trade_amount,
            fee_rate=args.fee_rate,
            strategy_type=Settings.STRATEGY_TYPE,
            rsi_oversold=Settings.RSI_OVERSOLD,
            rsi_overbought=Settings.RSI_OVERBOUGHT,
            stop_loss_atr_multiple=Settings.STOP_LOSS_ATR_MULTIPLE,
            take_profit_atr_multiple=Settings.TAKE_PROFIT_ATR_MULTIPLE
        )
        
        print("\n" + "="*50)
        print("BACKTEST RESULTS (CURRENT CONFIGURATION)")
        print("="*50)
        print(f"Strategy Type:       {Settings.STRATEGY_TYPE}")
        if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION":
            print(f"RSI Period:          {Settings.RSI_PERIOD}")
            print(f"RSI Oversold (Buy):  {Settings.RSI_OVERSOLD}")
            print(f"RSI Overbought(Sell): {Settings.RSI_OVERBOUGHT}")
        else:
            print(f"Short Window:        {Settings.SMA_SHORT_WINDOW}")
            print(f"Long Window:         {Settings.SMA_LONG_WINDOW}")
            print(f"Min Spread %:        {Settings.SMA_MIN_SPREAD_PERCENT}%")
            print(f"RSI Filter:          {Settings.RSI_FILTER_ENABLED} (Period: {Settings.RSI_PERIOD}, Buy Max: {Settings.RSI_BUY_MAX}, Sell Min: {Settings.RSI_SELL_MIN})")
            print(f"EMA Trend Filter:    {Settings.EMA_TREND_FILTER_ENABLED} (Period: {Settings.EMA_TREND_PERIOD})")
        print(f"Stop Loss %:         {Settings.STOP_LOSS_PERCENT}%")
        print(f"Take Profit %:       {Settings.TAKE_PROFIT_PERCENT}%")
        print(f"Stop Loss ATR Mult:  {Settings.STOP_LOSS_ATR_MULTIPLE}x")
        print(f"Take Profit ATR Mult:{Settings.TAKE_PROFIT_ATR_MULTIPLE}x")
        print(f"Simulated Fee Rate:  {args.fee_rate * 100:.2f}% per trade")
        print(f"Trade Size:          ${args.trade_amount:.2f} USD" if args.trade_amount > 0.0 else "Trade Size:          100% of Cash")
        print("-"*50)
        print(f"Total Actions:       {res['total_trades']}")
        print(f"Completed Trades:    {res['completed_trades']}")
        print(f"Winning Trades:      {res['winning_trades_count']}")
        print(f"Losing Trades:       {res['losing_trades_count']}")
        print(f"Win Rate:            {res['win_rate']:.2f}%")
        print(f"Profit Factor:       {res['profit_factor']:.2f}" if res['profit_factor'] != np.inf else "Profit Factor:       N/A")
        print(f"Max Drawdown:        {res['max_drawdown']:.4f}%")
        
        # Calculate duration in hours/minutes
        avg_dur = res['avg_duration']
        avg_dur_hours = (avg_dur * gran_seconds) / 3600
        print(f"Avg Trade Duration:  {avg_dur:.1f} candles ({avg_dur_hours:.2f} hours)")
        
        print(f"Average Win:         ${res['avg_win']:.4f} USD" if res['avg_win'] > 0 else "Average Win:         N/A")
        print(f"Average Loss:        ${res['avg_loss']:.4f} USD" if res['avg_loss'] < 0 or res['avg_loss'] > 0 else "Average Loss:        N/A")
        print(f"Max Win:             ${res['max_win']:.4f} USD" if res['max_win'] > 0 else "Max Win:             N/A")
        print(f"Max Loss:            ${res['max_loss']:.4f} USD" if res['max_loss'] < 0 or res['max_loss'] > 0 else "Max Loss:            N/A")
        print(f"Final Value:         ${res['final_value']:.2f} USD")
        print(f"Net Profit:          ${res['net_profit']:.2f} USD ({res['profit_percent']:.4f}%)")
        print("="*50 + "\n")

if __name__ == "__main__":
    main()
