import os
import sys
import time
import uuid
import requests
import pandas as pd
from datetime import datetime

from config.settings import Settings
from utils.logger import setup_logger
from utils.state import StateManager
from utils.notifications import send_trade_notification
from strategies import SMACrossoverStrategy, RSIMeanReversionStrategy

# Initialize logger
logger = setup_logger(name="coinbase_bot", log_file="logs/bot.log")

# Granularity string to seconds mapping
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
    """
    Fallback method to fetch candle data from Coinbase Exchange's public API.
    Does not require authentication and is ideal for dry-run simulations
    when API keys are not yet configured.
    """
    logger.debug(f"Fetching public candle data for {product_id}")
    granularity_seconds = GRANULARITY_MAP.get(granularity, 60)
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
    params = {
        "granularity": granularity_seconds
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Public exchange API returns a list of lists:
        # [ [time, low, high, open, close, volume], ... ]
        # It returns them in descending order (newest first)
        df = pd.DataFrame(data, columns=['start', 'low', 'high', 'open', 'close', 'volume'])
        
        # Sort ascending (oldest first)
        df['start'] = pd.to_numeric(df['start'])
        df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Error fetching public candle data: {e}")
        return pd.DataFrame()

def fetch_candles_authenticated(client, product_id: str, granularity: str, count: int) -> pd.DataFrame:
    """
    Fetches candle data using the official Coinbase Advanced Trade RESTClient.
    """
    logger.debug(f"Fetching authenticated candle data for {product_id}")
    granularity_seconds = GRANULARITY_MAP.get(granularity, 60)
    end_time = int(time.time())
    start_time = end_time - (count * granularity_seconds)
    
    try:
        # Fetch candles
        response = client.get_candles(
            product_id=product_id,
            start=str(start_time),
            end=str(end_time),
            granularity=granularity
        )
        
        # Parse the response object
        if hasattr(response, 'to_dict'):
            response_dict = response.to_dict()
        else:
            response_dict = response
            
        candles_list = response_dict.get('candles', [])
        if not candles_list:
            logger.warning("No candles returned from Coinbase Advanced Trade API. Trying public fallback...")
            return fetch_candles_public(product_id, granularity)
            
        # The Advanced Trade API returns candles as a list of dicts:
        # [ {'start': '...', 'open': '...', 'high': '...', 'low': '...', 'close': '...', 'volume': '...'}, ... ]
        df = pd.DataFrame(candles_list)
        
        # Ensure all columns are numeric
        for col in ['start', 'open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col])
                
        # Sort ascending (oldest first)
        df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Error fetching authenticated candles: {e}. Trying public fallback...")
        return fetch_candles_public(product_id, granularity)

def get_asset_balance(client, currency: str) -> float:
    """
    Queries Coinbase for the available balance of a specific currency asset.
    """
    try:
        response = client.get_accounts()
        if hasattr(response, 'to_dict'):
            accounts_dict = response.to_dict()
        else:
            accounts_dict = response
            
        accounts = accounts_dict.get('accounts', [])
        for account in accounts:
            acct_currency = account.get('currency')
            if acct_currency == currency:
                available_balance = account.get('available_balance', {})
                return float(available_balance.get('value', 0.0))
    except Exception as e:
        logger.error(f"Error fetching balance for {currency}: {e}")
    return 0.0

def get_product_precisions(client, product_id: str):
    """
    Fetches product details from Coinbase and returns the number of decimal places
    for price (quote_increment) and base size (base_increment).
    """
    try:
        product = client.get_product(product_id=product_id)
        product_dict = product.to_dict() if hasattr(product, 'to_dict') else product
        
        base_inc = product_dict.get('base_increment', '0.00000001')
        quote_inc = product_dict.get('quote_increment', '0.01')
        
        def get_decimal_places(increment_str: str) -> int:
            try:
                inc_float = float(increment_str)
                if inc_float >= 1.0:
                    return 0
                if '.' in increment_str:
                    return len(increment_str.split('.')[1].rstrip('0'))
                return 0
            except Exception:
                return 8
                
        price_decimals = get_decimal_places(quote_inc)
        size_decimals = get_decimal_places(base_inc)
        
        return price_decimals, size_decimals
    except Exception as e:
        logger.error(f"Error fetching product precision for {product_id}: {e}")
        return 2, 8

def main():
    logger.info("=========================================")
    logger.info("Initializing Coinbase Automatic Trading Bot")
    logger.info("=========================================")
    
    # 1. Validate configuration
    config_errors = Settings.validate()
    if config_errors:
        for error in config_errors:
            logger.error(error)
        logger.error("Configuration validation failed. Exiting.")
        sys.exit(1)
        
    # Log the running mode
    if Settings.DRY_RUN:
        logger.info("Running mode: DRY RUN (Simulation - no real orders will be placed)")
    else:
        logger.info("Running mode: LIVE TRADING (Real money will be traded!)")
        
    logger.info(f"Trading Pair: {Settings.TRADING_PAIR}")
    logger.info(f"Granularity: {Settings.GRANULARITY}")
    logger.info(f"Trade Amount: ${Settings.TRADE_AMOUNT_USD:.2f} USD")
    logger.info(f"Loop Interval: {Settings.LOOP_INTERVAL_SECONDS} seconds")
    
    # Extract base and quote assets (e.g. BTC-USD -> BTC is base, USD is quote)
    try:
        base_asset, quote_asset = Settings.TRADING_PAIR.split('-')
    except ValueError:
        logger.error(f"Invalid TRADING_PAIR format: {Settings.TRADING_PAIR}. Expected format like 'BTC-USD'.")
        sys.exit(1)
        
    # 2. Initialize State Manager
    state_file = os.getenv("STATE_FILE", f"state_{Settings.TRADING_PAIR.lower()}.json")
    state_manager = StateManager(
        state_file=state_file,
        dry_run=Settings.DRY_RUN
    )
    
    # 3. Initialize Coinbase API Client (if credentials are provided)
    client = None
    if Settings.COINBASE_API_KEY and Settings.COINBASE_API_SECRET:
        try:
            from coinbase.rest import RESTClient
            client = RESTClient(api_key=Settings.COINBASE_API_KEY, api_secret=Settings.COINBASE_API_SECRET)
            logger.info("Coinbase REST Client successfully initialized.")
            
            # Test connectivity in live mode
            if not Settings.DRY_RUN:
                logger.info("Testing live connectivity...")
                balance = get_asset_balance(client, quote_asset)
                logger.info(f"Live connectivity successful. Current {quote_asset} balance: {balance:.2f}")
        except Exception as e:
            logger.error(f"Failed to initialize Coinbase client: {e}")
            if not Settings.DRY_RUN:
                logger.error("Live trading requires a valid API client. Exiting.")
                sys.exit(1)
    else:
        if not Settings.DRY_RUN:
            logger.error("API keys are missing, but DRY_RUN is set to False. Live trading requires API keys. Exiting.")
            sys.exit(1)
        logger.info("API keys not provided. Running Dry Run using public market data feeds.")
        
    # 4. Initialize Strategy
    if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION":
        strategy = RSIMeanReversionStrategy(
            rsi_period=Settings.RSI_PERIOD,
            rsi_oversold=Settings.RSI_OVERSOLD,
            rsi_overbought=Settings.RSI_OVERBOUGHT
        )
    else:
        strategy = SMACrossoverStrategy(
            short_window=Settings.SMA_SHORT_WINDOW,
            long_window=Settings.SMA_LONG_WINDOW,
            min_spread_percent=Settings.SMA_MIN_SPREAD_PERCENT,
            rsi_filter_enabled=Settings.RSI_FILTER_ENABLED,
            rsi_period=Settings.RSI_PERIOD,
            rsi_buy_min=Settings.RSI_BUY_MIN,
            rsi_buy_max=Settings.RSI_BUY_MAX,
            rsi_sell_min=Settings.RSI_SELL_MIN,
            ema_filter_enabled=Settings.EMA_TREND_FILTER_ENABLED,
            ema_period=Settings.EMA_TREND_PERIOD
        )
    logger.info(f"Strategy initialized: {strategy.name}")
    required_candles = strategy.get_required_candles_count()
    logger.info(f"Strategy requires at least {required_candles} historical candles.")
    
    logger.info("Bot setup completed. Starting main trading loop.")
    
    # 5. Main Loop
    try:
        while True:
            logger.info("--- Starting market check ---")
            
            # Reload state from disk in case of external/manual modifications
            state_manager.reload_state()
            
            # Fetch historical candle data
            if client:
                df = fetch_candles_authenticated(client, Settings.TRADING_PAIR, Settings.GRANULARITY, max(300, required_candles + 10))
            else:
                df = fetch_candles_public(Settings.TRADING_PAIR, Settings.GRANULARITY)
                
            if df.empty or len(df) < required_candles:
                logger.warning(f"Insufficient candle data (got {len(df)}/required {required_candles}). Retrying next loop.")
                time.sleep(Settings.LOOP_INTERVAL_SECONDS)
                continue
                
            # Get latest market price and completed candles for strategy evaluation
            # (Wait for candle to close: drop the current active/open candle from indicator calculations)
            latest_candle = df.iloc[-1]
            latest_price = float(latest_candle['close'])
            latest_time_unix = int(latest_candle['start'])
            latest_time_str = datetime.utcfromtimestamp(latest_time_unix).isoformat() + "Z"
            
            logger.info(f"Latest candle price (real-time): ${latest_price:,.2f} USD (at {latest_time_str})")
            
            granularity_seconds = GRANULARITY_MAP.get(Settings.GRANULARITY, 60)
            current_time = int(time.time())
            
            if current_time < latest_time_unix + granularity_seconds:
                # The latest candle is incomplete/open. Evaluate indicators on the last closed candle.
                df_completed = df.iloc[:-1].reset_index(drop=True)
            else:
                # The latest candle is already closed.
                df_completed = df.copy()
            
            # Calculate additional market metrics (moved up to support ATR risk management)
            # 1. Distance Below Recent High
            recent_high = float(df['high'].max())
            dist_high_usd = recent_high - latest_price
            dist_high_pct = (dist_high_usd / recent_high) * 100.0 if recent_high > 0.0 else 0.0
            
            # 2. ATR (14-period)
            high_prices = df['high']
            low_prices = df['low']
            prev_close_prices = df['close'].shift(1)
            tr1 = high_prices - low_prices
            tr2 = (high_prices - prev_close_prices).abs()
            tr3 = (low_prices - prev_close_prices).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_val = tr.rolling(window=14).mean().iloc[-1]
            
            # 3. Volume vs 20-period average
            latest_volume = float(df['volume'].iloc[-1])
            avg_volume_20 = float(df['volume'].rolling(window=20).mean().iloc[-1])
            volume_pct = (latest_volume / avg_volume_20) * 100.0 if avg_volume_20 > 0.0 else 0.0

            # 4. Calculate 14-period RSI Series for exit checks (weakening check)
            closes_series = pd.to_numeric(df['close'])
            delta = closes_series.diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, 1e-9)
            rsi_series = 100 - (100 / (1 + rs))
            
            current_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0
            prev_rsi = float(rsi_series.iloc[-2]) if len(rsi_series) > 1 else current_rsi
            rsi_weakening = current_rsi < prev_rsi

            # Evaluate strategy
            signal = strategy.evaluate(df_completed)
            logger.info(f"Strategy evaluation signal (on closed candle): {signal} (Current Position: {state_manager.state['position']})")
            
            # Check Risk Management (Stop Loss / Take Profit / Break-even Stop)
            sell_reason = "STRATEGY"
            if state_manager.state["position"] == "LONG" and state_manager.state["entry_price"] > 0:
                price_change_percent = ((latest_price - state_manager.state["entry_price"]) / state_manager.state["entry_price"]) * 100
                entry_price = state_manager.state["entry_price"]
                holdings = state_manager.state.get("holdings_base", 0.0)
                
                # Fee calculations
                f = Settings.FEE_RATE_PERCENT / 100.0
                net_pl_pct = (latest_price * (1.0 - f) - entry_price * (1.0 + f)) / (entry_price * (1.0 + f)) * 100.0
                
                # Calculate if break-even stop is active (based on highest net profit reached)
                break_even_active = False
                highest_price = state_manager.state.get("highest_price_since_entry", entry_price)
                highest_net_pl_pct = 0.0
                if entry_price > 0.0:
                    highest_net_pl_pct = (highest_price * (1.0 - f) - entry_price * (1.0 + f)) / (entry_price * (1.0 + f)) * 100.0
                    
                if Settings.BREAK_EVEN_TRIGGER_PERCENT > 0.0 and highest_net_pl_pct >= Settings.BREAK_EVEN_TRIGGER_PERCENT:
                    break_even_active = True
                
                # Check Break-even Stop (trigger at net break-even price, i.e. net P/L <= 0.05% to lock in positive PnL)
                net_breakeven_price = entry_price * (1.0 + f + 0.0005) / (1.0 - f)
                
                # ATR Threshold (fallback to current if entry wasn't saved)
                entry_atr = state_manager.state.get("entry_indicators", {}).get("atr", None)
                atr_threshold = entry_atr if entry_atr is not None else atr_val

                if break_even_active and latest_price <= net_breakeven_price:
                    signal = "SELL"
                    sell_reason = f"BREAK_EVEN (Price fell back to break-even price ${net_breakeven_price:.4f} after peaking at net +{highest_net_pl_pct:.2f}%)"
                    logger.warning(f"🚨 Risk management triggered: {sell_reason}")
                
                # Check ATR-based Stop Loss (bypassed if in exit-only mode)
                elif not Settings.DISABLE_BUYS and Settings.STOP_LOSS_ATR_MULTIPLE > 0.0 and atr_threshold is not None:
                    sl_price = entry_price - (Settings.STOP_LOSS_ATR_MULTIPLE * atr_threshold)
                    if latest_price <= sl_price:
                        signal = "SELL"
                        sell_reason = f"STOP_LOSS_ATR (Price fell below ATR stop price ${sl_price:.2f} using multiple {Settings.STOP_LOSS_ATR_MULTIPLE} * ATR ${atr_threshold:.2f})"
                        logger.warning(f"🚨 Risk management triggered: {sell_reason}")
                
                # Check percentage-based Stop Loss (bypassed if in exit-only mode)
                elif not Settings.DISABLE_BUYS and Settings.STOP_LOSS_PERCENT > 0.0 and price_change_percent <= -Settings.STOP_LOSS_PERCENT:
                    signal = "SELL"
                    sell_reason = f"STOP_LOSS (Price fell {price_change_percent:.2f}% below entry ${entry_price:.2f})"
                    logger.warning(f"🚨 Risk management triggered: {sell_reason}")
                
                # Check ATR-based Take Profit
                if signal != "SELL":
                    if Settings.TAKE_PROFIT_ATR_MULTIPLE > 0.0 and atr_threshold is not None:
                        tp_price = entry_price + (Settings.TAKE_PROFIT_ATR_MULTIPLE * atr_threshold)
                        if latest_price >= tp_price:
                            signal = "SELL"
                            sell_reason = f"TAKE_PROFIT_ATR (Price rose above ATR profit price ${tp_price:.2f} using multiple {Settings.TAKE_PROFIT_ATR_MULTIPLE} * ATR ${atr_threshold:.2f})"
                            logger.info(f"💰 Risk management triggered: {sell_reason}")
                    
                    # Check percentage-based Take Profit
                    elif Settings.TAKE_PROFIT_PERCENT > 0.0 and price_change_percent >= Settings.TAKE_PROFIT_PERCENT:
                        signal = "SELL"
                        sell_reason = f"TAKE_PROFIT (Price rose {price_change_percent:.2f}% above entry ${entry_price:.2f})"
                        logger.info(f"💰 Risk management triggered: {sell_reason}")
                
                # Check Exit-only Break-even Target (close position at net $0 loss/gain once profitable)
                if signal != "SELL" and Settings.DISABLE_BUYS and net_pl_pct >= 0.0:
                    signal = "SELL"
                    sell_reason = f"EXIT_ONLY_BREAK_EVEN (Exit-only mode: price recovered to net break-even at net +{net_pl_pct:.2f}% inclusive of all transaction fees)"
                    logger.info(f"💰 Exit-only risk management: {sell_reason}")
                
                # If strategy returned SELL, but we configured disabling strategy exits, override to HOLD
                # unless the trade is profitable net of fees AND RSI is weakening.
                if signal == "SELL" and sell_reason == "STRATEGY":
                    if Settings.DISABLE_STRATEGY_EXITS:
                        is_profitable = (net_pl_pct > 0.0)
                        if is_profitable and rsi_weakening:
                            logger.info(f"Strategy returned SELL signal (Bearish crossover). Allowing exit because trade is profitable (+{net_pl_pct:.2f}%) and RSI is weakening (current: {current_rsi:.2f} < previous: {prev_rsi:.2f}).")
                        else:
                            reason_details = []
                            if not is_profitable:
                                reason_details.append(f"not profitable ({net_pl_pct:+.2f}%)")
                            if not rsi_weakening:
                                reason_details.append(f"RSI not weakening ({current_rsi:.2f} >= {prev_rsi:.2f})")
                            logger.info(f"Strategy returned SELL signal (Bearish crossover), but DISABLE_STRATEGY_EXITS is enabled. Ignoring exit to hold position because trade is {', '.join(reason_details)}.")
                            signal = "HOLD"

            # Fetch last calculated strategy values
            short_val = getattr(strategy, "last_short", None)
            long_val = getattr(strategy, "last_long", None)
            spread_val = getattr(strategy, "last_spread_percent", None)
            rsi_val = getattr(strategy, "last_rsi", None)
            ema_val = getattr(strategy, "last_ema", None)
            
            mode_str = "SIMULATED / DRY RUN" if Settings.DRY_RUN else "LIVE TRADING"
            
            # If in LONG position, update metrics
            if state_manager.state["position"] == "LONG":
                state_manager.update_position_metrics(latest_price)
            
            
            def format_vol(v):
                if v >= 1_000_000:
                    return f"{v/1_000_000:.1f}M"
                if v >= 1_000:
                    return f"{v/1_000:.1f}K"
                return f"{v:.1f}"
            
            summary_lines = [
                "="*50,
                f"MARKET CHECK SUMMARY ({mode_str})",
                "-"*50,
                f"Trading Pair:      {Settings.TRADING_PAIR}",
                f"Current Price:     ${latest_price:,.2f} USD",
                f"Evaluation Candle: {datetime.utcfromtimestamp(int(df_completed.iloc[-1]['start'])).strftime('%Y-%m-%d %H:%M UTC')}",
                f"Short SMA ({Settings.SMA_SHORT_WINDOW}):   ${short_val:,.2f} USD" if short_val is not None else f"Short SMA ({Settings.SMA_SHORT_WINDOW}):   N/A",
                f"Long SMA ({Settings.SMA_LONG_WINDOW}):    ${long_val:,.2f} USD" if long_val is not None else f"Long SMA ({Settings.SMA_LONG_WINDOW}):    N/A"
            ]
            if spread_val is not None:
                summary_lines.append(f"Spread:            {spread_val:.4f}% (Min Req: {Settings.SMA_MIN_SPREAD_PERCENT:.2f}%)")
            
            summary_lines.append(f"Dist Below High:   -${dist_high_usd:,.2f} USD (-{dist_high_pct:.2f}%)" if dist_high_usd > 0.0 else f"Dist Below High:   $0.00 USD (0.00%)")
            atr_format = f"${atr_val:,.4f} USD" if latest_price < 10.0 else f"${atr_val:,.2f} USD"
            summary_lines.append(f"ATR (14):          {atr_format}")
            summary_lines.append(f"Volume vs 20-Avg:  {volume_pct:.2f}% (Latest: {format_vol(latest_volume)} vs Avg: {format_vol(avg_volume_20)})")
            if Settings.RSI_FILTER_ENABLED or Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION":
                if rsi_val is not None:
                    if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION":
                        summary_lines.append(f"RSI ({Settings.RSI_PERIOD}):         {rsi_val:.2f} (Oversold: {Settings.RSI_OVERSOLD:.1f}, Overbought: {Settings.RSI_OVERBOUGHT:.1f})")
                    else:
                        summary_lines.append(f"RSI ({Settings.RSI_PERIOD}):         {rsi_val:.2f} (Buy Req: {Settings.RSI_BUY_MIN:.1f}-{Settings.RSI_BUY_MAX:.1f})")
                else:
                    summary_lines.append(f"RSI ({Settings.RSI_PERIOD}):         N/A")
            if Settings.EMA_TREND_FILTER_ENABLED:
                summary_lines.append(f"EMA ({Settings.EMA_TREND_PERIOD}):        ${ema_val:,.2f} USD" if ema_val is not None else f"EMA ({Settings.EMA_TREND_PERIOD}):        N/A")
                if ema_val is not None:
                    dist_usd = latest_price - ema_val
                    dist_pct = (dist_usd / ema_val) * 100.0
                    sign = "+" if dist_usd >= 0 else "-"
                    summary_lines.append(f"Distance Above EMA{Settings.EMA_TREND_PERIOD}: {sign}${abs(dist_usd):,.2f} USD ({dist_pct:+.2f}%)")
            
            if signal == "SELL" and sell_reason != "STRATEGY":
                summary_lines.append(f"Signal:            {signal} (Reason: {sell_reason})")
            else:
                summary_lines.append(f"Signal:            {signal}")
            summary_lines.append(f"Current Position:  {state_manager.state['position']}")
            
            # Additional position details if LONG
            if state_manager.state["position"] == "LONG":
                entry_price = state_manager.state.get("entry_price", 0.0)
                holdings = state_manager.state.get("holdings_base", 0.0)
                
                pl_pct = 0.0
                pl_usd = 0.0
                net_pl_pct = 0.0
                net_pl_usd = 0.0
                f = Settings.FEE_RATE_PERCENT / 100.0
                if entry_price > 0.0:
                    pl_pct = ((latest_price - entry_price) / entry_price) * 100
                    pl_usd = (latest_price - entry_price) * holdings
                    net_pl_pct = (latest_price * (1.0 - f) - entry_price * (1.0 + f)) / (entry_price * (1.0 + f)) * 100.0
                    net_pl_usd = holdings * (latest_price * (1.0 - f) - entry_price * (1.0 + f))
                
                highest_price = state_manager.state.get("highest_price_since_entry", entry_price)
                if highest_price == 0.0:
                    highest_price = entry_price
                highest_pl_pct = ((highest_price - entry_price) / entry_price * 100) if entry_price > 0.0 else 0.0
                highest_pl_usd = (highest_price - entry_price) * holdings
                highest_net_pl_pct = ((highest_price * (1.0 - f) - entry_price * (1.0 + f)) / (entry_price * (1.0 + f)) * 100.0) if entry_price > 0.0 else 0.0
                highest_net_pl_usd = holdings * (highest_price * (1.0 - f) - entry_price * (1.0 + f)) if entry_price > 0.0 else 0.0
                
                lowest_price = state_manager.state.get("lowest_price_since_entry", entry_price)
                if lowest_price == 0.0:
                    lowest_price = entry_price
                lowest_pl_pct = ((lowest_price - entry_price) / entry_price * 100) if entry_price > 0.0 else 0.0
                lowest_pl_usd = (lowest_price - entry_price) * holdings
                lowest_net_pl_pct = ((lowest_price * (1.0 - f) - entry_price * (1.0 + f)) / (entry_price * (1.0 + f)) * 100.0) if entry_price > 0.0 else 0.0
                lowest_net_pl_usd = holdings * (lowest_price * (1.0 - f) - entry_price * (1.0 + f)) if entry_price > 0.0 else 0.0
                
                max_drawdown = state_manager.state.get("max_drawdown_since_entry", 0.0)
                
                hours_held = 0.0
                entry_time_str = state_manager.get_entry_time()
                if entry_time_str:
                    try:
                        t_str = entry_time_str.rstrip('Z')
                        if '.' in t_str:
                            fmt = "%Y-%m-%dT%H:%M:%S.%f"
                        else:
                            fmt = "%Y-%m-%dT%H:%M:%S"
                        entry_dt = datetime.strptime(t_str, fmt)
                        hours_held = (datetime.utcnow() - entry_dt).total_seconds() / 3600.0
                    except Exception:
                        pass
                
                # Calculate if break-even stop is active for logging
                break_even_active = False
                if Settings.BREAK_EVEN_TRIGGER_PERCENT > 0.0 and highest_net_pl_pct >= Settings.BREAK_EVEN_TRIGGER_PERCENT:
                    break_even_active = True
                
                trailing_stop_active = False
                
                summary_lines.append(f"Entry Price:       ${entry_price:,.2f} USD")
                summary_lines.append(f"Current P/L:       {pl_pct:+.2f}% (${pl_usd:+.2f} USD) | Net: {net_pl_pct:+.2f}% (${net_pl_usd:+.2f} USD)")
                summary_lines.append(f"Maximum Favorable: {highest_pl_pct:+.2f}% (${highest_pl_usd:+.2f} USD) | Net: {highest_net_pl_pct:+.2f}% (${highest_net_pl_usd:+.2f} USD)")
                summary_lines.append(f"Maximum Adverse:   {lowest_pl_pct:+.2f}% (${lowest_pl_usd:+.2f} USD) | Net: {lowest_net_pl_pct:+.2f}% (${lowest_net_pl_usd:+.2f} USD)")
                summary_lines.append(f"Lowest Drawdown:   {max_drawdown:.2f}%")
                summary_lines.append(f"Hours Held:        {hours_held:.2f} hours")
                summary_lines.append(f"Trailing Stop?:    {'Yes' if trailing_stop_active else 'No'}")
                if Settings.BREAK_EVEN_TRIGGER_PERCENT > 0.0:
                    summary_lines.append(f"Break-even Stop?:  {'Yes (Active)' if break_even_active else f'No (Trigger at net +{Settings.BREAK_EVEN_TRIGGER_PERCENT:.2f}%)'}")
                if Settings.STOP_LOSS_ATR_MULTIPLE > 0.0 and atr_threshold is not None:
                    sl_price = entry_price - (Settings.STOP_LOSS_ATR_MULTIPLE * atr_threshold)
                    summary_lines.append(f"ATR Stop Loss:     ${sl_price:.2f} (Entry ATR: ${atr_threshold:.2f}, Multiple: {Settings.STOP_LOSS_ATR_MULTIPLE}x)")
                if Settings.TAKE_PROFIT_ATR_MULTIPLE > 0.0 and atr_threshold is not None:
                    tp_price = entry_price + (Settings.TAKE_PROFIT_ATR_MULTIPLE * atr_threshold)
                    summary_lines.append(f"ATR Take Profit:   ${tp_price:.2f} (Entry ATR: ${atr_threshold:.2f}, Multiple: {Settings.TAKE_PROFIT_ATR_MULTIPLE}x)")
                
            summary_lines.append("="*50)
            
            logger.info("\n" + "\n".join(summary_lines))

            # Action: BUY
            if signal == "BUY" and not Settings.DISABLE_BUYS:
                if state_manager.state["position"] == "NONE":
                    ema_filter_status = "DISABLED"
                    if Settings.EMA_TREND_FILTER_ENABLED:
                        ema_filter_status = "PASS" if (ema_val is not None and latest_price > ema_val) else "FAIL"
                    
                    rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "N/A"
                    timestamp_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                    
                    buy_signal_lines = [
                        "*"*40,
                        "BUY SIGNAL",
                        "Reason:",
                        "RSI Recovered from Oversold" if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION" else "SMA Bullish Cross",
                    ]
                    if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION":
                        buy_signal_lines.append(f"RSI: {rsi_str} (Oversold: {Settings.RSI_OVERSOLD})")
                    else:
                        buy_signal_lines.extend([
                            f"Short SMA: {short_val:.3f}" if short_val is not None else "Short SMA: N/A",
                            f"Long SMA: {long_val:.3f}" if long_val is not None else "Long SMA: N/A",
                            f"Spread: {spread_val:.2f}%" if spread_val is not None else "Spread: N/A",
                            f"RSI: {rsi_str}",
                            f"EMA Filter: {ema_filter_status}"
                        ])
                    buy_signal_lines.extend([
                        "Timestamp:",
                        f"{timestamp_utc}",
                        "*"*40
                    ])
                    logger.info("\n" + "\n".join(buy_signal_lines))
                    
                    if Settings.DRY_RUN:
                        # Simulate BUY
                        cash = state_manager.state["cash_usd"]
                        if cash < Settings.TRADE_AMOUNT_USD:
                            logger.warning(f"Insufficient simulated cash (${cash:.2f}) to execute trade of ${Settings.TRADE_AMOUNT_USD:.2f}")
                        else:
                            # Calculate mock base currency acquired (assume 0.2% mock fee)
                            mock_fee = Settings.TRADE_AMOUNT_USD * 0.002
                            net_spend = Settings.TRADE_AMOUNT_USD - mock_fee
                            acquired_base = net_spend / latest_price
                            
                            indicators_dict = {
                                "sma_short": short_val,
                                "sma_long": long_val,
                                "ema": ema_val,
                                "rsi": rsi_val,
                                "spread": spread_val,
                                "atr": atr_val
                            }
                            trade = state_manager.record_trade(
                                trade_type="BUY",
                                price=latest_price,
                                amount_usd=net_spend,
                                amount_base=acquired_base,
                                fee_usd=mock_fee,
                                reason="RSI Recovered from Oversold" if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION" else "SMA Bullish Cross",
                                indicators=indicators_dict
                            )
                            logger.info(f"Simulated BUY executed: Acquired {acquired_base:.8f} {base_asset} for ${Settings.TRADE_AMOUNT_USD:.2f} USD (Fee: ${mock_fee:.4f})")
                            send_trade_notification(
                                trade_type="BUY",
                                pair=Settings.TRADING_PAIR,
                                price=latest_price,
                                amount_usd=net_spend,
                                amount_base=acquired_base,
                                fee_usd=mock_fee,
                                is_dry_run=True
                            )
                            
                            order_lines = [
                                "*"*40,
                                "[SIMULATED ORDER EXECUTED]",
                                f"Product:   {Settings.TRADING_PAIR}",
                                f"Side:      BUY",
                                f"Price:     ${latest_price:,.2f} USD",
                                f"Size:      {acquired_base:.8f} {base_asset}",
                                f"Fee:       ${mock_fee:.4f} USD",
                                f"Timestamp: {datetime.utcnow().isoformat()}Z",
                                f"Reason:    RSI recovered above oversold threshold {Settings.RSI_OVERSOLD:.1f}" if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION" else f"Reason:    SMA Short window crossed above SMA Long window with spread {spread_val:.4f}%",
                                "*"*40
                            ]
                            logger.info("\n" + "\n".join(order_lines))
                    else:
                        # Live BUY
                        try:
                            # 1. Fetch bid/ask & precisions
                            price_decimals, size_decimals = get_product_precisions(client, Settings.TRADING_PAIR)
                            
                            logger.info("Fetching best bid/ask for limit order placement...")
                            bid_ask = client.get_best_bid_ask(product_ids=[Settings.TRADING_PAIR])
                            bid_ask_dict = bid_ask.to_dict() if hasattr(bid_ask, 'to_dict') else bid_ask
                            pricebooks = bid_ask_dict.get('pricebooks', [])
                            if not pricebooks or not pricebooks[0].get('bids'):
                                raise ValueError("Could not fetch bids from Coinbase order book.")
                                
                            best_bid = float(pricebooks[0]['bids'][0]['price'])
                            
                            # For buying, place limit order at the best bid (Maker price)
                            limit_price = round(best_bid, price_decimals)
                            base_size_raw = Settings.TRADE_AMOUNT_USD / limit_price
                            base_size = round(base_size_raw, size_decimals)
                            
                            limit_price_str = f"{limit_price:.{price_decimals}f}"
                            base_size_str = f"{base_size:.{size_decimals}f}"
                            
                            # Generate unique client order ID
                            client_order_id = str(uuid.uuid4())
                            logger.info(f"Placing live Limit BUY order (Maker, Post-Only). Price: {limit_price_str}, Size: {base_size_str}, Client Order ID: {client_order_id}")
                            
                            order = client.limit_order_gtc_buy(
                                client_order_id=client_order_id,
                                product_id=Settings.TRADING_PAIR,
                                base_size=base_size_str,
                                limit_price=limit_price_str,
                                post_only=True
                            )
                            
                            order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
                            success_resp = order_dict.get('success_response') or {}
                            order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
                            
                            if not order_id:
                                error_resp = order_dict.get('error_response') or {}
                                error_msg = error_resp.get('message', 'Unknown API Error')
                                raise ValueError(f"Coinbase order creation failed: {error_msg}")
                                
                            logger.info(f"Limit BUY order placed. Order ID: {order_id}. Waiting for fill...")
                            
                            # Wait for limit order to fill
                            filled = False
                            wait_time = 0
                            max_wait = 60
                            order_info = {}
                            
                            while wait_time < max_wait:
                                time.sleep(5)
                                wait_time += 5
                                fill_details = client.get_order(order_id=order_id)
                                fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                                order_info = fill_dict.get('order', {})
                                status = order_info.get('status')
                                logger.info(f"Order status checking: {status} (waited {wait_time}s)")
                                
                                if status == 'FILLED':
                                    filled = True
                                    break
                                elif status in ['CANCELLED', 'EXPIRED', 'FAILED']:
                                    break
                                    
                            if not filled:
                                filled_size_base = float(order_info.get('filled_size', 0.0))
                                if filled_size_base > 0:
                                    logger.warning(f"Limit order {order_id} partially filled. Size filled: {filled_size_base}. Cancelling remainder.")
                                    filled = True
                                
                                logger.warning(f"Limit order {order_id} did not fill completely. Attempting to cancel.")
                                try:
                                    client.cancel_orders(order_ids=[order_id])
                                except Exception as cancel_err:
                                    logger.error(f"Failed to cancel order {order_id}: {cancel_err}")
                                    
                            # Retrieve final details
                            fill_details = client.get_order(order_id=order_id)
                            fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                            order_info = fill_dict.get('order', {})
                            
                            filled_val_usd = float(order_info.get('filled_value', 0.0))
                            filled_size_base = float(order_info.get('filled_size', 0.0))
                            fee_usd = float(order_info.get('total_fees', 0.0))
                            avg_price = float(order_info.get('avg_price', limit_price))
                            
                            if filled_size_base > 0:
                                indicators_dict = {
                                    "sma_short": short_val,
                                    "sma_long": long_val,
                                    "ema": ema_val,
                                    "rsi": rsi_val,
                                    "spread": spread_val,
                                    "atr": atr_val
                                }
                                trade = state_manager.record_trade(
                                    trade_type="BUY",
                                    price=avg_price,
                                    amount_usd=filled_val_usd,
                                    amount_base=filled_size_base,
                                    fee_usd=fee_usd,
                                    reason="RSI Recovered from Oversold" if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION" else "SMA Bullish Cross",
                                    indicators=indicators_dict
                                )
                                logger.info(f"Live Limit BUY filled: {filled_size_base:.8f} {base_asset} at average price ${avg_price:,.2f} USD (Total cost: ${filled_val_usd:.2f}, Fee: ${fee_usd:.2f})")
                                send_trade_notification(
                                    trade_type="BUY",
                                    pair=Settings.TRADING_PAIR,
                                    price=avg_price,
                                    amount_usd=filled_val_usd,
                                    amount_base=filled_size_base,
                                    fee_usd=fee_usd,
                                    is_dry_run=False
                                )
                            else:
                                logger.error("Limit order was cancelled or expired with 0 size filled.")
                        except Exception as e:
                            logger.error(f"Failed to execute live limit BUY order: {e}")
                else:
                    logger.info("BUY signal triggered, but position is already LONG. Holding current position.")
            
            elif signal == "BUY" and Settings.DISABLE_BUYS:
                if state_manager.state["position"] == "NONE":
                    logger.info("BUY signal triggered, but buying is disabled (Exit-Only mode). Skipping entry.")
                else:
                    logger.info("BUY signal triggered, but position is already LONG. Holding current position.")
            
            # Action: SELL
            elif signal == "SELL":
                if state_manager.state["position"] == "LONG":
                    ema_filter_status = "DISABLED"
                    if Settings.EMA_TREND_FILTER_ENABLED:
                        ema_filter_status = "PASS" if (ema_val is not None and latest_price < ema_val) else "FAIL"
                    
                    rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "N/A"
                    timestamp_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                    
                    reason_str = "SMA Bearish Cross"
                    if sell_reason == "STOP_LOSS":
                        reason_str = "Stop Loss Triggered"
                    elif sell_reason == "TAKE_PROFIT":
                        reason_str = "Take Profit Triggered"
                    elif "STOP_LOSS" in sell_reason:
                        reason_str = "Stop Loss Triggered"
                    elif "TAKE_PROFIT" in sell_reason:
                        reason_str = "Take Profit Triggered"
                    elif "BREAK_EVEN" in sell_reason:
                        reason_str = "Break-even Stop Triggered"
                    
                    sell_signal_lines = [
                        "*"*40,
                        "SELL SIGNAL",
                        "Reason:",
                        f"{reason_str}",
                        f"Short SMA: {short_val:.3f}" if short_val is not None else "Short SMA: N/A",
                        f"Long SMA: {long_val:.3f}" if long_val is not None else "Long SMA: N/A",
                        f"Spread: {spread_val:.2f}%" if spread_val is not None else "Spread: N/A",
                        f"RSI: {rsi_str}",
                        f"EMA Filter: {ema_filter_status}",
                        "Timestamp:",
                        f"{timestamp_utc}",
                        "*"*40
                    ]
                    logger.info("\n" + "\n".join(sell_signal_lines))
                    
                    if Settings.DRY_RUN:
                        # Simulate SELL
                        holdings = state_manager.state["holdings_base"]
                        if holdings <= 0:
                            logger.warning("Simulated position is LONG in state, but holdings count is 0. Resetting position to NONE.")
                            state_manager.state["position"] = "NONE"
                            state_manager.save_state()
                        else:
                            # Calculate mock USD returned (assume 0.2% mock fee)
                            gross_usd = holdings * latest_price
                            mock_fee = gross_usd * 0.002
                            net_usd = gross_usd - mock_fee
                            
                            trade = state_manager.record_trade(
                                trade_type="SELL",
                                price=latest_price,
                                amount_usd=gross_usd,
                                amount_base=holdings,
                                fee_usd=mock_fee,
                                reason=sell_reason
                            )
                            logger.info(f"Simulated SELL executed: Sold {holdings:.8f} {base_asset} for ${net_usd:.2f} USD (Fee: ${mock_fee:.4f}). Realized PnL: ${trade['realized_pnl_usd']:.2f} USD")
                            send_trade_notification(
                                trade_type="SELL",
                                pair=Settings.TRADING_PAIR,
                                price=latest_price,
                                amount_usd=gross_usd,
                                amount_base=holdings,
                                fee_usd=mock_fee,
                                is_dry_run=True,
                                realized_pnl=trade.get('realized_pnl_usd', 0.0)
                            )
                            
                            reason_str = sell_reason if sell_reason != "STRATEGY" else f"SMA Short window crossed below SMA Long window with spread {spread_val:.4f}%"
                            order_lines = [
                                "*"*40,
                                "[SIMULATED ORDER EXECUTED]",
                                f"Product:   {Settings.TRADING_PAIR}",
                                f"Side:      SELL",
                                f"Price:     ${latest_price:,.2f} USD",
                                f"Size:      {holdings:.8f} {base_asset}",
                                f"Fee:       ${mock_fee:.4f} USD",
                                f"Timestamp: {datetime.utcnow().isoformat()}Z",
                                f"Reason:    {reason_str}",
                                f"PnL:       ${trade['realized_pnl_usd']:.2f} USD",
                                "*"*40
                            ]
                            logger.info("\n" + "\n".join(order_lines))
                    else:
                        # Live SELL
                        try:
                            # 1. Fetch local state holdings and verify against exchange account balance
                            state_holdings = state_manager.state.get("holdings_base", 0.0)
                            exchange_balance = get_asset_balance(client, base_asset)
                            holdings = min(state_holdings, exchange_balance)
                            logger.info(f"Local state holdings: {state_holdings:.8f}, Exchange balance: {exchange_balance:.8f}. Selling: {holdings:.8f}")
                            
                            if holdings <= 1e-8:
                                logger.warning(f"No available {base_asset} balance found to sell (state: {state_holdings:.8f}, exchange: {exchange_balance:.8f}). Resetting local position to NONE.")
                                state_manager.state["position"] = "NONE"
                                state_manager.save_state()
                            else:
                                # 2. Fetch ask price & precisions
                                price_decimals, size_decimals = get_product_precisions(client, Settings.TRADING_PAIR)
                                
                                logger.info("Fetching best bid/ask for limit order placement...")
                                bid_ask = client.get_best_bid_ask(product_ids=[Settings.TRADING_PAIR])
                                bid_ask_dict = bid_ask.to_dict() if hasattr(bid_ask, 'to_dict') else bid_ask
                                pricebooks = bid_ask_dict.get('pricebooks', [])
                                if not pricebooks or not pricebooks[0].get('asks'):
                                    raise ValueError("Could not fetch asks from Coinbase order book.")
                                    
                                best_ask = float(pricebooks[0]['asks'][0]['price'])
                                
                                # For selling, place limit order at the best ask (Maker price)
                                limit_price = round(best_ask, price_decimals)
                                base_size = round(holdings, size_decimals)
                                
                                limit_price_str = f"{limit_price:.{price_decimals}f}"
                                base_size_str = f"{base_size:.{size_decimals}f}"
                                
                                # 3. Place limit sell order
                                client_order_id = str(uuid.uuid4())
                                logger.info(f"Placing live Limit SELL order (Maker, Post-Only). Price: {limit_price_str}, Size: {base_size_str}, Client Order ID: {client_order_id}")
                                
                                order = client.limit_order_gtc_sell(
                                    client_order_id=client_order_id,
                                    product_id=Settings.TRADING_PAIR,
                                    base_size=base_size_str,
                                    limit_price=limit_price_str,
                                    post_only=True
                                )
                                
                                # Safely extract order ID from CreateOrderResponse
                                order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
                                success_resp = order_dict.get('success_response') or {}
                                order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
                                
                                if not order_id:
                                    error_resp = order_dict.get('error_response') or {}
                                    error_msg = error_resp.get('message', 'Unknown API Error')
                                    raise ValueError(f"Coinbase order creation failed: {error_msg}")
                                    
                                logger.info(f"Limit SELL order placed. Order ID: {order_id}. Waiting for fill...")
                                
                                # Wait for limit order to fill
                                filled = False
                                wait_time = 0
                                max_wait = 60
                                order_info = {}
                                
                                while wait_time < max_wait:
                                    time.sleep(5)
                                    wait_time += 5
                                    fill_details = client.get_order(order_id=order_id)
                                    fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                                    order_info = fill_dict.get('order', {})
                                    status = order_info.get('status')
                                    logger.info(f"Order status checking: {status} (waited {wait_time}s)")
                                    
                                    if status == 'FILLED':
                                        filled = True
                                        break
                                    elif status in ['CANCELLED', 'EXPIRED', 'FAILED']:
                                        break
                                        
                                if not filled:
                                    filled_size_base = float(order_info.get('filled_size', 0.0))
                                    if filled_size_base > 0:
                                        logger.warning(f"Limit order {order_id} partially filled. Size filled: {filled_size_base}. Cancelling remainder.")
                                        filled = True
                                    
                                    logger.warning(f"Limit order {order_id} did not fill completely. Attempting to cancel.")
                                    try:
                                        client.cancel_orders(order_ids=[order_id])
                                    except Exception as cancel_err:
                                        logger.error(f"Failed to cancel order {order_id}: {cancel_err}")
                                
                                # Retrieve final details
                                fill_details = client.get_order(order_id=order_id)
                                fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                                order_info = fill_dict.get('order', {})
                                
                                filled_val_usd = float(order_info.get('filled_value', base_size * limit_price))
                                filled_size_base = float(order_info.get('filled_size', base_size))
                                fee_usd = float(order_info.get('total_fees', 0.0))
                                avg_price = float(order_info.get('avg_price', limit_price))
                                
                                if filled_size_base > 0:
                                    trade = state_manager.record_trade(
                                        trade_type="SELL",
                                        price=avg_price,
                                        amount_usd=filled_val_usd,
                                        amount_base=filled_size_base,
                                        fee_usd=fee_usd,
                                        reason=sell_reason
                                    )
                                    logger.info(f"Live Limit SELL filled: Sold {filled_size_base:.8f} {base_asset} at average price ${avg_price:,.2f} USD (Realized: ${filled_val_usd:.2f}, Fee: ${fee_usd:.2f}). Realized PnL: ${trade['realized_pnl_usd']:.2f} USD")
                                    send_trade_notification(
                                        trade_type="SELL",
                                        pair=Settings.TRADING_PAIR,
                                        price=avg_price,
                                        amount_usd=filled_val_usd,
                                        amount_base=filled_size_base,
                                        fee_usd=fee_usd,
                                        is_dry_run=False,
                                        realized_pnl=trade.get('realized_pnl_usd', 0.0)
                                    )
                                else:
                                    logger.error("Limit order was cancelled or expired with 0 size filled.")
                        except Exception as e:
                            logger.error(f"Failed to execute live limit SELL order: {e}")
                else:
                    logger.info("SELL signal triggered, but position is already NONE. Doing nothing.")
            
            else:
                # signal is HOLD/NONE
                # Output brief status report
                portfolio_value = state_manager.state["cash_usd"] + (state_manager.state["holdings_base"] * latest_price)
                logger.info(f"Position: {state_manager.state['position']} | Cash: ${state_manager.state['cash_usd']:.2f} USD | Holdings: {state_manager.state['holdings_base']:.8f} {base_asset} | Portfolio Value: ${portfolio_value:.2f} USD")
            
            logger.info(f"Sleeping for {Settings.LOOP_INTERVAL_SECONDS} seconds before next check.")
            time.sleep(Settings.LOOP_INTERVAL_SECONDS)
            
    except KeyboardInterrupt:
        logger.info("Bot execution interrupted by user. Exiting gracefully.")
    except Exception as e:
        logger.critical(f"Unhandled exception in trading loop: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
