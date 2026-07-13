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
from strategies.simple_moving_average import SMACrossoverStrategy

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
    state_manager = StateManager(
        state_file=f"state_{Settings.TRADING_PAIR.lower()}.json",
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
        
    # 4. Initialize Strategy (SMA Crossover with RSI and EMA Trend Filters)
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
            
            # Fetch historical candle data
            if client:
                df = fetch_candles_authenticated(client, Settings.TRADING_PAIR, Settings.GRANULARITY, required_candles + 10)
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
                if break_even_active and latest_price <= net_breakeven_price:
                    signal = "SELL"
                    sell_reason = f"BREAK_EVEN (Price fell back to break-even price ${net_breakeven_price:.4f} after peaking at net +{highest_net_pl_pct:.2f}%)"
                    logger.warning(f"🚨 Risk management triggered: {sell_reason}")
                
                # Check Stop Loss
                elif Settings.STOP_LOSS_PERCENT > 0.0 and price_change_percent <= -Settings.STOP_LOSS_PERCENT:
                    signal = "SELL"
                    sell_reason = f"STOP_LOSS (Price fell {price_change_percent:.2f}% below entry ${entry_price:.2f})"
                    logger.warning(f"🚨 Risk management triggered: {sell_reason}")
                
                # Check Take Profit
                elif Settings.TAKE_PROFIT_PERCENT > 0.0 and price_change_percent >= Settings.TAKE_PROFIT_PERCENT:
                    signal = "SELL"
                    sell_reason = f"TAKE_PROFIT (Price rose {price_change_percent:.2f}% above entry ${entry_price:.2f})"
                    logger.info(f"💰 Risk management triggered: {sell_reason}")

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
            
            # Calculate additional market metrics
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
            if Settings.RSI_FILTER_ENABLED:
                if rsi_val is not None:
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
                
            summary_lines.append("="*50)
            
            logger.info("\n" + "\n".join(summary_lines))

            # Action: BUY
            if signal == "BUY":
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
                        "SMA Bullish Cross",
                        f"Short SMA: {short_val:.3f}" if short_val is not None else "Short SMA: N/A",
                        f"Long SMA: {long_val:.3f}" if long_val is not None else "Long SMA: N/A",
                        f"Spread: {spread_val:.2f}%" if spread_val is not None else "Spread: N/A",
                        f"RSI: {rsi_str}",
                        f"EMA Filter: {ema_filter_status}",
                        "Timestamp:",
                        f"{timestamp_utc}",
                        "*"*40
                    ]
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
                                "spread": spread_val
                            }
                            trade = state_manager.record_trade(
                                trade_type="BUY",
                                price=latest_price,
                                amount_usd=net_spend,
                                amount_base=acquired_base,
                                fee_usd=mock_fee,
                                reason="SMA Bullish Cross",
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
                                f"Reason:    SMA Short window crossed above SMA Long window with spread {spread_val:.4f}%",
                                "*"*40
                            ]
                            logger.info("\n" + "\n".join(order_lines))
                    else:
                        # Live BUY
                        try:
                            # Generate unique client order ID
                            client_order_id = str(uuid.uuid4())
                            logger.info(f"Placing live market BUY order. Client Order ID: {client_order_id}")
                            
                            order = client.market_order_buy(
                                client_order_id=client_order_id,
                                product_id=Settings.TRADING_PAIR,
                                quote_size=str(Settings.TRADE_AMOUNT_USD)
                            )
                            
                            # Note: Detailed fill price and actual fees may need to be fetched via get_order,
                            # but we can record the trade with the best estimation or query the filled order.
                            # Safely extract order ID from CreateOrderResponse
                            order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
                            success_resp = order_dict.get('success_response') or {}
                            order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
                            logger.info(f"Market BUY order placed. Order ID: {order_id}. Fetching fill details...")
                            
                            # Pause briefly to allow order to fill
                            time.sleep(1.5)
                            fill_details = client.get_order(order_id=order_id)
                            
                            if hasattr(fill_details, 'to_dict'):
                                fill_dict = fill_details.to_dict()
                            else:
                                fill_dict = fill_details
                                
                            order_info = fill_dict.get('order', {})
                            
                            # Extract exact execution details
                            # Depending on fill details, exact filled values are in 'cum_value' / 'filled_size' etc.
                            filled_val_usd = float(order_info.get('filled_value', Settings.TRADE_AMOUNT_USD))
                            filled_size_base = float(order_info.get('filled_size', 0.0))
                            fee_usd = float(order_info.get('total_fees', 0.0))
                            avg_price = float(order_info.get('avg_price', latest_price))
                            
                            if filled_size_base > 0:
                                indicators_dict = {
                                    "sma_short": short_val,
                                    "sma_long": long_val,
                                    "ema": ema_val,
                                    "rsi": rsi_val,
                                    "spread": spread_val
                                }
                                trade = state_manager.record_trade(
                                    trade_type="BUY",
                                    price=avg_price,
                                    amount_usd=filled_val_usd,
                                    amount_base=filled_size_base,
                                    fee_usd=fee_usd,
                                    reason="SMA Bullish Cross",
                                    indicators=indicators_dict
                                )
                                logger.info(f"Live BUY filled: {filled_size_base:.8f} {base_asset} at average price ${avg_price:,.2f} USD (Total cost: ${filled_val_usd:.2f}, Fee: ${fee_usd:.2f})")
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
                                logger.error("Live order filled size was 0. Order might still be pending or rejected.")
                        except Exception as e:
                            logger.error(f"Failed to execute live BUY order: {e}")
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
                            # 1. Fetch exact balance of base currency to sell
                            holdings = get_asset_balance(client, base_asset)
                            logger.info(f"Retrieved live {base_asset} account balance: {holdings:.8f}")
                            
                            if holdings <= 1e-8:
                                logger.warning(f"No available {base_asset} balance found to sell. Resetting local position to NONE.")
                                state_manager.state["position"] = "NONE"
                                state_manager.save_state()
                            else:
                                # 2. Place market sell order
                                client_order_id = str(uuid.uuid4())
                                logger.info(f"Placing live market SELL order for {holdings:.8f} {base_asset}. Client Order ID: {client_order_id}")
                                
                                order = client.market_order_sell(
                                    client_order_id=client_order_id,
                                    product_id=Settings.TRADING_PAIR,
                                    base_size=str(holdings)
                                )
                                
                                # Safely extract order ID from CreateOrderResponse
                                order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
                                success_resp = order_dict.get('success_response') or {}
                                order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
                                logger.info(f"Market SELL order placed. Order ID: {order_id}. Fetching fill details...")
                                
                                # Pause briefly to allow order to fill
                                time.sleep(1.5)
                                fill_details = client.get_order(order_id=order_id)
                                
                                if hasattr(fill_details, 'to_dict'):
                                    fill_dict = fill_details.to_dict()
                                else:
                                    fill_dict = fill_details
                                    
                                order_info = fill_dict.get('order', {})
                                
                                # Extract exact execution details
                                filled_val_usd = float(order_info.get('filled_value', holdings * latest_price))
                                filled_size_base = float(order_info.get('filled_size', holdings))
                                fee_usd = float(order_info.get('total_fees', 0.0))
                                avg_price = float(order_info.get('avg_price', latest_price))
                                
                                if filled_size_base > 0:
                                    trade = state_manager.record_trade(
                                        trade_type="SELL",
                                        price=avg_price,
                                        amount_usd=filled_val_usd,
                                        amount_base=filled_size_base,
                                        fee_usd=fee_usd,
                                        reason=sell_reason
                                    )
                                    logger.info(f"Live SELL filled: Sold {filled_size_base:.8f} {base_asset} at average price ${avg_price:,.2f} USD (Realized: ${filled_val_usd:.2f}, Fee: ${fee_usd:.2f}). Realized PnL: ${trade['realized_pnl_usd']:.2f} USD")
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
                                    logger.error("Live order filled size was 0. Order might still be pending or rejected.")
                        except Exception as e:
                            logger.error(f"Failed to execute live SELL order: {e}")
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
