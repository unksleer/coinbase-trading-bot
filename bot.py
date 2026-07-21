import os
import sys
import time
import uuid
import logging
import requests
import pandas as pd
from decimal import Decimal
from datetime import datetime

from config.settings import Settings
from utils.logger import setup_logger
from utils.state import StateManager, CorruptStateError
from utils.notifications import send_trade_notification
from strategies import SMACrossoverStrategy, RSIMeanReversionStrategy
from utils.order_journal import (
    OrderJournal,
    quantize_value,
    STATE_SUBMITTING,
    STATE_OPEN,
    STATE_PARTIALLY_FILLED,
    STATE_FILLED,
    STATE_CANCELLED,
    STATE_FAILED,
    STATE_UNKNOWN
)
from utils.risk_engine import RiskEngine, RiskLimitViolation

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
        df = pd.DataFrame(data, columns=['start', 'low', 'high', 'open', 'close', 'volume'])
        df['start'] = pd.to_numeric(df['start'])
        df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Error fetching public candle data for {product_id}: {e}")
        # Fallback: If it's a -USDC pair, try the -USD equivalent
        if product_id.endswith("-USDC"):
            usd_pair = product_id.replace("-USDC", "-USD")
            logger.info(f"Retrying public candle fetch with USD equivalent pair: {usd_pair}")
            return fetch_candles_public(usd_pair, granularity)
        return pd.DataFrame()

def fetch_candles_authenticated(client, product_id: str, granularity: str, count: int) -> pd.DataFrame:
    """
    Fetches candle data using the Coinbase RESTClient.
    """
    logger.debug(f"Fetching authenticated candle data for {product_id}")
    granularity_seconds = GRANULARITY_MAP.get(granularity, 60)
    end_time = int(time.time())
    start_time = end_time - (count * granularity_seconds)
    
    try:
        response = client.get_candles(
            product_id=product_id,
            start=str(start_time),
            end=str(end_time),
            granularity=granularity
        )
        
        if hasattr(response, 'to_dict'):
            response_dict = response.to_dict()
        else:
            response_dict = response
            
        candles_list = response_dict.get('candles', [])
        if not candles_list:
            logger.warning("No candles returned from Coinbase Advanced Trade API. Trying public fallback...")
            return fetch_candles_public(product_id, granularity)
            
        df = pd.DataFrame(candles_list)
        for col in ['start', 'open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col])
        df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Error fetching authenticated candles for {product_id}: {e}")
        if product_id.endswith("-USDC"):
            usd_pair = product_id.replace("-USDC", "-USD")
            logger.info(f"Retrying authenticated candle fetch with USD equivalent: {usd_pair}")
            try:
                response = client.get_candles(
                    product_id=usd_pair,
                    start=str(start_time),
                    end=str(end_time),
                    granularity=granularity
                )
                if hasattr(response, 'to_dict'):
                    response_dict = response.to_dict()
                else:
                    response_dict = response
                candles_list = response_dict.get('candles', [])
                if candles_list:
                    df = pd.DataFrame(candles_list)
                    for col in ['start', 'open', 'high', 'low', 'close', 'volume']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col])
                    df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
                    return df
            except Exception as inner_e:
                logger.error(f"Error fetching authenticated candles for USD equivalent {usd_pair}: {inner_e}")
                
        logger.warning("Trying public fallback...")
        return fetch_candles_public(product_id, granularity)

def get_asset_balance(client, currency: str) -> float:
    """
    Queries Coinbase for the available balance of a specific asset.
    """
    try:
        response = client.get_accounts()
        accounts_dict = response.to_dict() if hasattr(response, 'to_dict') else response
        accounts = accounts_dict.get('accounts', [])
        for account in accounts:
            if account.get('currency') == currency:
                available_balance = account.get('available_balance', {})
                return float(available_balance.get('value', 0.0))
    except Exception as e:
        logger.error(f"Error fetching balance for {currency}: {e}")
        raise  # Propagate balance request failures
    return 0.0

def get_product_precisions(client, product_id: str):
    """
    Fetches quote_increment and base_increment precisions from Coinbase.
    """
    try:
        product = client.get_product(product_id=product_id)
        product_dict = product.to_dict() if hasattr(product, 'to_dict') else product
        base_inc = product_dict.get('base_increment', '0.00000001')
        quote_inc = product_dict.get('quote_increment', '0.01')
        return quote_inc, base_inc
    except Exception as e:
        logger.error(f"Error fetching product precision for {product_id}: {e}")
        raise

def main():
    logger.info("=========================================")
    logger.info("Initializing Coinbase Automatic Trading Bot")
    logger.info("=========================================")
    
    # 1. Validate config
    config_errors = Settings.validate()
    if config_errors:
        for error in config_errors:
            logger.error(error)
        logger.error("Configuration validation failed. Exiting.")
        sys.exit(1)
        
    if Settings.DRY_RUN:
        logger.info("Running mode: DRY RUN (Simulation - no real orders will be placed)")
    else:
        logger.info("Running mode: LIVE TRADING (Real money will be traded!)")
        
    logger.info(f"Trading Pair: {Settings.TRADING_PAIR}")
    logger.info(f"Granularity: {Settings.GRANULARITY}")
    logger.info(f"Trade Amount: ${Settings.TRADE_AMOUNT_USD:.2f} USD")
    logger.info(f"Loop Interval: {Settings.LOOP_INTERVAL_SECONDS} seconds")
    
    try:
        base_asset, quote_asset = Settings.TRADING_PAIR.split('-')
    except ValueError:
        logger.error(f"Invalid TRADING_PAIR format: {Settings.TRADING_PAIR}.")
        sys.exit(1)
        
    # 2. Initialize State Manager (Fails closed on corrupt live state)
    state_file = os.getenv("STATE_FILE")
    if not state_file:
        suffix = "_dry" if Settings.DRY_RUN else ""
        state_file = f"state_{Settings.TRADING_PAIR.lower()}{suffix}.json"
    try:
        state_manager = StateManager(
            state_file=state_file,
            dry_run=Settings.DRY_RUN
        )
    except CorruptStateError as cse:
        logger.critical(f"FATAL: State initialization aborted due to corruption: {cse}")
        sys.exit(1)

    # 3. Initialize Order Journal & Risk Engine
    order_journal = OrderJournal(state_manager)
    risk_engine = RiskEngine(Settings, state_manager)
    
    client = None
    if Settings.COINBASE_API_KEY and Settings.COINBASE_API_SECRET:
        try:
            from coinbase.rest import RESTClient
            client = RESTClient(api_key=Settings.COINBASE_API_KEY, api_secret=Settings.COINBASE_API_SECRET)
            logger.info("Coinbase REST Client successfully initialized.")
            
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
            logger.error("API keys are missing in live mode. Exiting.")
            sys.exit(1)
        logger.info("API keys not provided. Running Dry Run using public market data feeds.")
        
    # 4. Startup Reconciliation
    logger.info("Performing startup order reconciliation...")
    if not order_journal.reconcile_unresolved_orders(client):
        logger.critical("Startup order reconciliation failed. Exiting.")
        sys.exit(1)

    # 5. Startup Risk Checks
    try:
        risk_engine.check_daily_drawdown()
    except RiskLimitViolation as rlv:
        logger.critical(f"Startup Risk Check Failed: {rlv}. Exiting.")
        sys.exit(1)

    # If CHECK_STARTUP flag is active (from trade.sh check phase)
    if os.getenv("CHECK_STARTUP", "False").lower() in ("true", "1", "yes"):
        logger.info("Pre-flight startup safety checks successful. Exiting successfully.")
        sys.exit(0)

    # 6. Initialize Strategy
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
    
    # 7. Main Loop
    try:
        while True:
            logger.info("--- Starting market check ---")
            
            with state_manager.lock():
                # Reload state from disk under lock
                state_manager.reload_state()
                
                # Continuous reconciliation of unresolved orders
                order_journal.reconcile_unresolved_orders(client)
                
                # Continuous daily drawdown checks
                try:
                    risk_engine.check_daily_drawdown()
                except RiskLimitViolation as rlv:
                    logger.critical(f"Daily drawdown limit violated: {rlv}. Exiting.")
                    sys.exit(1)
                
                # Fetch candle data
                try:
                    if client:
                        df = fetch_candles_authenticated(client, Settings.TRADING_PAIR, Settings.GRANULARITY, max(300, required_candles + 10))
                    else:
                        df = fetch_candles_public(Settings.TRADING_PAIR, Settings.GRANULARITY)
                    risk_engine.reset_api_errors()
                except Exception as api_err:
                    logger.error(f"API Error fetching candles: {api_err}")
                    if not risk_engine.record_api_error():
                        logger.critical(" trippped repeated API errors breaker. Exiting.")
                        sys.exit(1)
                    time.sleep(Settings.LOOP_INTERVAL_SECONDS)
                    continue

                if df.empty or len(df) < required_candles:
                    logger.warning(f"Insufficient candle data (got {len(df)}/required {required_candles}). Retrying next loop.")
                    time.sleep(Settings.LOOP_INTERVAL_SECONDS)
                    continue
                    
                latest_candle = df.iloc[-1]
                latest_price = float(latest_candle['close'])
                latest_time_unix = int(latest_candle['start'])
                latest_time_str = datetime.utcfromtimestamp(latest_time_unix).isoformat() + "Z"
                
                logger.info(f"Latest candle price (real-time): ${latest_price:,.2f} USD (at {latest_time_str})")
                
                # Stale market data cutoff check
                try:
                    risk_engine.check_market_data_freshness(latest_time_unix)
                except RiskLimitViolation as rlv:
                    logger.error(f"Market data freshness validation failed: {rlv}. Skipping cycle.")
                    time.sleep(Settings.LOOP_INTERVAL_SECONDS)
                    continue

                granularity_seconds = GRANULARITY_MAP.get(Settings.GRANULARITY, 60)
                current_time = int(time.time())
                
                if current_time < latest_time_unix + granularity_seconds:
                    df_completed = df.iloc[:-1].reset_index(drop=True)
                else:
                    df_completed = df.copy()
                
                # Calculate metrics
                recent_high = float(df['high'].max())
                dist_high_usd = recent_high - latest_price
                dist_high_pct = (dist_high_usd / recent_high) * 100.0 if recent_high > 0.0 else 0.0
                
                # ATR
                high_prices = df['high']
                low_prices = df['low']
                prev_close_prices = df['close'].shift(1)
                tr = pd.concat([high_prices - low_prices, (high_prices - prev_close_prices).abs(), (low_prices - prev_close_prices).abs()], axis=1).max(axis=1)
                atr_val = tr.rolling(window=14).mean().iloc[-1]
                
                latest_volume = float(df['volume'].iloc[-1])
                avg_volume_20 = float(df['volume'].rolling(window=20).mean().iloc[-1])
                volume_pct = (latest_volume / avg_volume_20) * 100.0 if avg_volume_20 > 0.0 else 0.0

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
                
                # Risk Exits Check (Stops are mandatory and CANNOT be blocked by strategy filters)
                sell_reason = "STRATEGY"
                if state_manager.state["position"] == "LONG" and state_manager.state["entry_price"] > 0:
                    price_change_percent = ((latest_price - state_manager.state["entry_price"]) / state_manager.state["entry_price"]) * 100
                    entry_price = state_manager.state["entry_price"]
                    holdings = state_manager.state.get("holdings_base", 0.0)
                    
                    f = Settings.FEE_RATE_PERCENT / 100.0
                    net_pl_pct = (latest_price * (1.0 - f) - entry_price * (1.0 + f)) / (entry_price * (1.0 + f)) * 100.0
                    highest_price = state_manager.state.get("highest_price_since_entry", entry_price)
                    highest_net_pl_pct = (highest_price * (1.0 - f) - entry_price * (1.0 + f)) / (entry_price * (1.0 + f)) * 100.0
                    
                    break_even_active = Settings.BREAK_EVEN_TRIGGER_PERCENT > 0.0 and highest_net_pl_pct >= Settings.BREAK_EVEN_TRIGGER_PERCENT
                    net_breakeven_price = entry_price * (1.0 + f + 0.0005) / (1.0 - f)
                    
                    entry_atr = state_manager.state.get("entry_indicators", {}).get("atr", None)
                    atr_threshold = entry_atr if entry_atr is not None else atr_val

                    # Stops check
                    if break_even_active and latest_price <= net_breakeven_price:
                        signal = "SELL"
                        sell_reason = f"BREAK_EVEN (Price fell to break-even price ${net_breakeven_price:.4f})"
                    elif not Settings.DISABLE_BUYS and Settings.STOP_LOSS_ATR_MULTIPLE > 0.0 and atr_threshold is not None:
                        sl_price = entry_price - (Settings.STOP_LOSS_ATR_MULTIPLE * atr_threshold)
                        if latest_price <= sl_price:
                            signal = "SELL"
                            sell_reason = f"STOP_LOSS_ATR (Price fell below ${sl_price:.2f})"
                    elif not Settings.DISABLE_BUYS and Settings.STOP_LOSS_PERCENT > 0.0 and price_change_percent <= -Settings.STOP_LOSS_PERCENT:
                        signal = "SELL"
                        sell_reason = f"STOP_LOSS (Price fell {price_change_percent:.2f}% below entry ${entry_price:.2f})"
                    elif Settings.TAKE_PROFIT_ATR_MULTIPLE > 0.0 and atr_threshold is not None:
                        tp_price = entry_price + (Settings.TAKE_PROFIT_ATR_MULTIPLE * atr_threshold)
                        if latest_price >= tp_price:
                            signal = "SELL"
                            sell_reason = f"TAKE_PROFIT_ATR (Price rose above ${tp_price:.2f})"
                    elif Settings.TAKE_PROFIT_PERCENT > 0.0 and price_change_percent >= Settings.TAKE_PROFIT_PERCENT:
                        signal = "SELL"
                        sell_reason = f"TAKE_PROFIT (Price rose {price_change_percent:.2f}% above entry ${entry_price:.2f})"
                    elif Settings.DISABLE_BUYS and net_pl_pct >= 0.0:
                        signal = "SELL"
                        sell_reason = "EXIT_ONLY_BREAK_EVEN"

                    # If strategy requested SELL, verify strategy exits aren't disabled
                    if signal == "SELL" and sell_reason == "STRATEGY":
                        if Settings.DISABLE_STRATEGY_EXITS:
                            is_profitable = (net_pl_pct > 0.0)
                            if is_profitable and rsi_weakening:
                                logger.info("Allowing exit: profitable and RSI weakening.")
                            else:
                                logger.info("Ignoring strategy exit because DISABLE_STRATEGY_EXITS is enabled.")
                                signal = "HOLD"

                # Update position drawdown metrics
                if state_manager.state["position"] == "LONG":
                    state_manager.update_position_metrics(latest_price)

                short_val = getattr(strategy, "last_short", None)
                long_val = getattr(strategy, "last_long", None)
                spread_val = getattr(strategy, "last_spread_percent", None)
                rsi_val = getattr(strategy, "last_rsi", None)
                ema_val = getattr(strategy, "last_ema", None)

                # Process Actions
                # BUY
                if signal == "BUY" and not Settings.DISABLE_BUYS:
                    if state_manager.state["position"] == "NONE":
                        if Settings.DRY_RUN:
                            # Simulate BUY
                            cash = state_manager.state["cash_usd"]
                            if cash >= Settings.TRADE_AMOUNT_USD:
                                mock_fee = Settings.TRADE_AMOUNT_USD * 0.002
                                net_spend = Settings.TRADE_AMOUNT_USD - mock_fee
                                acquired_base = net_spend / latest_price
                                indicators_dict = {
                                    "sma_short": short_val, "sma_long": long_val,
                                    "ema": ema_val, "rsi": rsi_val, "spread": spread_val, "atr": atr_val
                                }
                                state_manager.record_trade(
                                    trade_type="BUY", price=latest_price,
                                    amount_usd=net_spend, amount_base=acquired_base,
                                    fee_usd=mock_fee, reason="SMA Bullish Cross", indicators=indicators_dict
                                )
                                logger.info(f"[DRY RUN] BUY: {acquired_base:.8f} base acquired.")
                        else:
                            # Live BUY
                            try:
                                quote_inc, base_inc = get_product_precisions(client, Settings.TRADING_PAIR)
                                
                                max_attempts = 3
                                attempt = 0
                                filled = False
                                
                                while attempt < max_attempts and not filled:
                                    attempt += 1
                                    logger.info(f"Limit BUY attempt {attempt}/{max_attempts}...")
                                    
                                    bid_ask = client.get_best_bid_ask(product_ids=[Settings.TRADING_PAIR])
                                    bid_ask_dict = bid_ask.to_dict() if hasattr(bid_ask, 'to_dict') else bid_ask
                                    pricebooks = bid_ask_dict.get('pricebooks', [])
                                    if not pricebooks or not pricebooks[0].get('bids'):
                                        logger.error("Could not fetch bids from Coinbase order book.")
                                        time.sleep(2)
                                        continue
                                        
                                    best_bid = float(pricebooks[0]['bids'][0]['price'])
                                    d_best_bid = Decimal(str(best_bid))
                                    d_limit_price = quantize_value(d_best_bid, quote_inc)
                                    d_base_size_raw = Decimal(str(Settings.TRADE_AMOUNT_USD)) / d_limit_price
                                    d_base_size = quantize_value(d_base_size_raw, base_inc)
                                    
                                    # Pre-flight Risk checks
                                    try:
                                        risk_engine.validate_pre_flight("BUY", d_limit_price, d_base_size, Settings.TRADING_PAIR)
                                    except RiskLimitViolation as rlv:
                                        logger.error(f"Pre-flight Risk Check Violated: {rlv}. Skipping attempt.")
                                        break
                                        
                                    client_order_id = str(uuid.uuid4())
                                    
                                    # Log order intent BEFORE submitting order
                                    order_journal.log_intent(client_order_id, Settings.TRADING_PAIR, "BUY", d_base_size, d_limit_price)
                                    
                                    try:
                                        order = client.limit_order_gtc_buy(
                                            client_order_id=client_order_id,
                                            product_id=Settings.TRADING_PAIR,
                                            base_size=str(d_base_size),
                                            limit_price=str(d_limit_price),
                                            post_only=True
                                        )
                                        order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
                                        success_resp = order_dict.get('success_response') or {}
                                        order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
                                        
                                        if not order_id:
                                            raise ValueError("Coinbase order submission returned invalid order ID")
                                            
                                        order_journal.update_order_id(client_order_id, order_id)
                                        
                                        # Wait fill
                                        wait_time = 0
                                        max_wait = 60
                                        while wait_time < max_wait:
                                            time.sleep(5)
                                            wait_time += 5
                                            fill_details = client.get_order(order_id=order_id)
                                            fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                                            order_info = fill_dict.get('order', {})
                                            
                                            order_journal.update_order_status(client_order_id, order_info)
                                            tracked_order = order_journal.get_order(client_order_id)
                                            if tracked_order["status"] == STATE_FILLED:
                                                filled = True
                                                break
                                            elif tracked_order["status"] in (STATE_CANCELLED, STATE_FAILED):
                                                break
                                                
                                        if not filled:
                                            logger.warning(f"Limit order {order_id} not filled completely. Cancelling.")
                                            try:
                                                client.cancel_orders(order_ids=[order_id])
                                                time.sleep(2)
                                                fill_details = client.get_order(order_id=order_id)
                                                fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                                                order_journal.update_order_status(client_order_id, fill_dict.get('order', {}))
                                            except Exception as cancel_err:
                                                logger.error(f"Failed to cancel order: {cancel_err}")
                                                
                                        # Check final fill
                                        tracked_order = order_journal.get_order(client_order_id)
                                        filled_size = Decimal(tracked_order["filled_size"])
                                        filled_val = Decimal(tracked_order["filled_value"])
                                        fee_usd = Decimal(tracked_order["fee_usd"])
                                        
                                        if filled_size > 0:
                                            filled = True
                                            indicators_dict = {
                                                "sma_short": short_val, "sma_long": long_val,
                                                "ema": ema_val, "rsi": rsi_val, "spread": spread_val, "atr": atr_val
                                            }
                                            trade = state_manager.record_trade(
                                                trade_type="BUY", price=float(d_limit_price),
                                                amount_usd=float(filled_val), amount_base=float(filled_size),
                                                fee_usd=float(fee_usd), reason="SMA Bullish Cross", indicators=indicators_dict
                                            )
                                            logger.info(f"Live limit BUY filled: {filled_size} at average {d_limit_price}")
                                            send_trade_notification("BUY", Settings.TRADING_PAIR, float(d_limit_price), float(filled_val), float(filled_size), float(fee_usd), False)
                                        else:
                                            logger.error("Limit order cancelled with 0 filled.")
                                    except Exception as inner_e:
                                        logger.error(f"Error during live Limit BUY execution: {inner_e}")
                                        time.sleep(2)
                            except Exception as e:
                                logger.error(f"Failed live Limit BUY execution block: {e}")

                # SELL
                elif signal == "SELL":
                    if state_manager.state["position"] == "LONG":
                        if Settings.DRY_RUN:
                            holdings = state_manager.state["holdings_base"]
                            if holdings > 0:
                                gross_usd = holdings * latest_price
                                mock_fee = gross_usd * 0.002
                                net_usd = gross_usd - mock_fee
                                trade = state_manager.record_trade(
                                    trade_type="SELL", price=latest_price,
                                    amount_usd=gross_usd, amount_base=holdings,
                                    fee_usd=mock_fee, reason=sell_reason
                                )
                                logger.info(f"[DRY RUN] Sold {holdings:.8f} base. PnL: ${trade['realized_pnl_usd']:.2f}")
                        else:
                            # Live SELL
                            try:
                                state_holdings = state_manager.state.get("holdings_base", 0.0)
                                exchange_balance = get_asset_balance(client, base_asset)
                                holdings = min(state_holdings, exchange_balance)
                                logger.info(f"Local state holdings: {state_holdings:.8f}, Exchange balance: {exchange_balance:.8f}. Selling: {holdings:.8f}")
                                
                                if holdings <= 1e-8:
                                    logger.warning("No balance to sell. Resetting local state position to NONE.")
                                    state_manager.state["position"] = "NONE"
                                    state_manager.save_state()
                                else:
                                    quote_inc, base_inc = get_product_precisions(client, Settings.TRADING_PAIR)
                                    
                                    max_attempts = 3
                                    attempt = 0
                                    filled = False
                                    remaining_holdings = holdings
                                    
                                    while attempt < max_attempts and not filled and remaining_holdings > 1e-8:
                                        attempt += 1
                                        logger.info(f"Limit SELL attempt {attempt}/{max_attempts}...")
                                        
                                        bid_ask = client.get_best_bid_ask(product_ids=[Settings.TRADING_PAIR])
                                        bid_ask_dict = bid_ask.to_dict() if hasattr(bid_ask, 'to_dict') else bid_ask
                                        pricebooks = bid_ask_dict.get('pricebooks', [])
                                        if not pricebooks or not pricebooks[0].get('asks'):
                                            logger.error("Could not fetch asks from order book.")
                                            time.sleep(2)
                                            continue
                                            
                                        best_ask = float(pricebooks[0]['asks'][0]['price'])
                                        d_best_ask = Decimal(str(best_ask))
                                        d_limit_price = quantize_value(d_best_ask, quote_inc)
                                        d_base_size = quantize_value(Decimal(str(remaining_holdings)), base_inc)
                                        
                                        if d_base_size <= 1e-8:
                                            break
                                            
                                        # Pre-flight Risk checks
                                        try:
                                            risk_engine.validate_pre_flight("SELL", d_limit_price, d_base_size, Settings.TRADING_PAIR)
                                        except RiskLimitViolation as rlv:
                                            logger.error(f"Pre-flight Risk Check Violated on Sell: {rlv}. Skipping attempt.")
                                            break
                                            
                                        client_order_id = str(uuid.uuid4())
                                        order_journal.log_intent(client_order_id, Settings.TRADING_PAIR, "SELL", d_base_size, d_limit_price)
                                        
                                        try:
                                            order = client.limit_order_gtc_sell(
                                                client_order_id=client_order_id,
                                                product_id=Settings.TRADING_PAIR,
                                                base_size=str(d_base_size),
                                                limit_price=str(d_limit_price),
                                                post_only=True
                                            )
                                            order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
                                            success_resp = order_dict.get('success_response') or {}
                                            order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
                                            
                                            if not order_id:
                                                raise ValueError("Coinbase Limit SELL submission returned invalid order ID")
                                                
                                            order_journal.update_order_id(client_order_id, order_id)
                                            
                                            wait_time = 0
                                            max_wait = 60
                                            while wait_time < max_wait:
                                                time.sleep(5)
                                                wait_time += 5
                                                fill_details = client.get_order(order_id=order_id)
                                                fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                                                order_info = fill_dict.get('order', {})
                                                
                                                order_journal.update_order_status(client_order_id, order_info)
                                                tracked_order = order_journal.get_order(client_order_id)
                                                if tracked_order["status"] == STATE_FILLED:
                                                    filled = True
                                                    break
                                                elif tracked_order["status"] in (STATE_CANCELLED, STATE_FAILED):
                                                    break
                                                    
                                            if not filled:
                                                try:
                                                    client.cancel_orders(order_ids=[order_id])
                                                    time.sleep(2)
                                                    fill_details = client.get_order(order_id=order_id)
                                                    fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                                                    order_journal.update_order_status(client_order_id, fill_dict.get('order', {}))
                                                except Exception as cancel_err:
                                                    logger.error(f"Failed to cancel order: {cancel_err}")
                                                    
                                            tracked_order = order_journal.get_order(client_order_id)
                                            filled_size = Decimal(tracked_order["filled_size"])
                                            filled_val = Decimal(tracked_order["filled_value"])
                                            fee_usd = Decimal(tracked_order["fee_usd"])
                                            
                                            if filled_size > 0:
                                                trade = state_manager.record_trade(
                                                    trade_type="SELL", price=float(d_limit_price),
                                                    amount_usd=float(filled_val), amount_base=float(filled_size),
                                                    fee_usd=float(fee_usd), reason=sell_reason
                                                )
                                                remaining_holdings -= float(filled_size)
                                                logger.info(f"Live Limit SELL partial/full fill: {filled_size} sold.")
                                                send_trade_notification("SELL", Settings.TRADING_PAIR, float(d_limit_price), float(filled_val), float(filled_size), float(fee_usd), False, trade.get('realized_pnl_usd', 0.0))
                                                if remaining_holdings < 1e-8:
                                                    filled = True
                                            else:
                                                logger.error("Limit SELL order cancelled with 0 filled.")
                                        except Exception as inner_e:
                                            logger.error(f"Error during Live Limit SELL: {inner_e}")
                                            time.sleep(2)

                                    # Fallback: Market Sell if still not closed
                                    if remaining_holdings > 1e-8:
                                        logger.warning(f"Limit order exits failed to fully close position. Remaining: {remaining_holdings:.8f}. Placing MARKET exit...")
                                        try:
                                            d_base_size = quantize_value(Decimal(str(remaining_holdings)), base_inc)
                                            client_order_id = str(uuid.uuid4())
                                            order_journal.log_intent(client_order_id, Settings.TRADING_PAIR, "SELL", d_base_size, Decimal(str(latest_price)))
                                            
                                            order = client.market_order_sell(
                                                client_order_id=client_order_id,
                                                product_id=Settings.TRADING_PAIR,
                                                base_size=str(d_base_size)
                                            )
                                            order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
                                            success_resp = order_dict.get('success_response') or {}
                                            order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
                                            
                                            if order_id:
                                                order_journal.update_order_id(client_order_id, order_id)
                                                time.sleep(2)
                                                fill_details = client.get_order(order_id=order_id)
                                                fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                                                order_info = fill_dict.get('order', {})
                                                
                                                order_journal.update_order_status(client_order_id, order_info)
                                                tracked_order = order_journal.get_order(client_order_id)
                                                
                                                filled_size = Decimal(tracked_order["filled_size"])
                                                filled_val = Decimal(tracked_order["filled_value"])
                                                fee_usd = Decimal(tracked_order["fee_usd"])
                                                avg_price = float(order_info.get('avg_price', latest_price))
                                                
                                                if filled_size > 0:
                                                    trade = state_manager.record_trade(
                                                        trade_type="SELL", price=avg_price,
                                                        amount_usd=float(filled_val), amount_base=float(filled_size),
                                                        fee_usd=float(fee_usd), reason=f"{sell_reason}_MARKET_FALLBACK"
                                                    )
                                                    logger.info(f"Market SELL fallback executed successfully: Sold {filled_size}")
                                                    send_trade_notification("SELL", Settings.TRADING_PAIR, avg_price, float(filled_val), float(filled_size), float(fee_usd), False, trade.get('realized_pnl_usd', 0.0))
                                        except Exception as market_err:
                                            logger.critical(f"Critical failure during Market SELL fallback execution: {market_err}")
                            except Exception as e:
                                logger.error(f"Failed live Limit SELL block: {e}")
                else:
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
