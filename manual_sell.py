#!/usr/bin/env python3
import os
import sys
import json
import uuid
import time
import requests
from datetime import datetime

# Add the project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.settings import Settings
from utils.state import StateManager
from utils.notifications import send_trade_notification

def fetch_latest_price(client, product_id: str) -> float:
    """
    Fetches the latest spot price for a product pair.
    Tries authenticated RESTClient get_candles first, then public candles, then public ticker,
    and falls back to USD equivalent for USDC pairs if delisted.
    """
    # 1. Try authenticated RESTClient if available
    if client:
        try:
            end_time = int(time.time())
            start_time = end_time - 300
            response = client.get_candles(
                product_id=product_id,
                start=str(start_time),
                end=str(end_time),
                granularity="ONE_MINUTE"
            )
            if hasattr(response, 'to_dict'):
                response_dict = response.to_dict()
            else:
                response_dict = response
            candles = response_dict.get('candles', [])
            if candles:
                import pandas as pd
                df = pd.DataFrame(candles)
                if not df.empty and 'start' in df.columns:
                    df['start'] = pd.to_numeric(df['start'])
                    df = df.sort_values(by='start', ascending=True).reset_index(drop=True)
                    return float(df.iloc[-1]['close'])
                elif candles:
                    return float(candles[0].get('close', 0.0))
        except Exception:
            pass

    # 2. Try public candles endpoint for the exact product ID
    url_candles = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
    try:
        response = requests.get(url_candles, params={"granularity": 60}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data:
                return float(data[0][4])
    except Exception:
        pass

    # 3. Try public ticker endpoint for the exact product ID
    url_ticker = f"https://api.exchange.coinbase.com/products/{product_id}/ticker"
    try:
        response = requests.get(url_ticker, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data.get("price", 0.0))
    except Exception:
        pass

    # 4. Fallback: If it's a -USDC pair, try the -USD equivalent ticker
    if product_id.endswith("-USDC"):
        usd_pair = product_id.replace("-USDC", "-USD")
        try:
            response = requests.get(f"https://api.exchange.coinbase.com/products/{usd_pair}/ticker", timeout=10)
            if response.status_code == 200:
                data = response.json()
                return float(data.get("price", 0.0))
        except Exception:
            pass

    return 0.0

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
        print(f"Error fetching balance for {currency}: {e}")
    return 0.0

def find_open_positions():
    """
    Scans the directory for state_*.json files with active LONG positions.
    """
    open_positions = []
    for filename in os.listdir("."):
        if filename.startswith("state_") and filename.endswith(".json"):
            try:
                with open(filename, 'r') as f:
                    state = json.load(f)
                    if state.get("position") == "LONG":
                        # Extract coin name
                        coin = filename.replace("state_", "").replace(".json", "").upper()
                        open_positions.append({
                            "coin": coin,
                            "filename": filename,
                            "entry_price": state.get("entry_price"),
                            "holdings": state.get("holdings_base"),
                            "dry_run": state.get("dry_run", True)
                        })
            except Exception:
                pass
    return open_positions

def main():
    print("=" * 60)
    print("COINBASE TRADING BOT - MANUAL CLOSE OUT SCRIPT")
    print("=" * 60)

    # Initialize client early if API credentials exist
    client = None
    if Settings.COINBASE_API_KEY and Settings.COINBASE_API_SECRET:
        try:
            from coinbase.rest import RESTClient
            client = RESTClient(api_key=Settings.COINBASE_API_KEY, api_secret=Settings.COINBASE_API_SECRET)
        except Exception:
            pass

    # 1. Parse command line arguments
    target_coin = None
    bypass_confirm = False
    
    args = sys.argv[1:]
    if "--yes" in args:
        bypass_confirm = True
        args.remove("--yes")
        
    if len(args) > 0:
        target_coin = args[0].strip().upper()

    # 2. If no coin specified, list open positions
    open_positions = find_open_positions()
    
    if not target_coin:
        if not open_positions:
            print("No active open positions found in any state files.")
            print("\nUsage: python manual_sell.py <coin_symbol> [--yes]")
            print("Example: python manual_sell.py btc")
            return
        
        print("Active open positions found:")
        for idx, pos in enumerate(open_positions, 1):
            mode = "DRY RUN" if pos["dry_run"] else "LIVE"
            print(f" {idx}. {pos['coin']} ({mode})")
            print(f"    Holdings:    {pos['holdings']:.8f}")
            print(f"    Entry Price: ${pos['entry_price']:.2f}")
            
        print("\nPlease specify which coin you want to sell.")
        print("Example: python manual_sell.py btc")
        return

    # Normalize coin/pair name
    # e.g. BTC -> BTC-USDC (or BTC-USD depending on Settings)
    if "-" in target_coin:
        product_id = target_coin
        coin_symbol = target_coin.split("-")[0]
        quote_symbol = target_coin.split("-")[1]
    else:
        coin_symbol = target_coin
        # Match Settings pair convention or default to USDC
        if Settings.TRADING_PAIR.endswith("-USD"):
            product_id = f"{coin_symbol}-USD"
            quote_symbol = "USD"
        else:
            product_id = f"{coin_symbol}-USDC"
            quote_symbol = "USDC"

    state_file = os.getenv("STATE_FILE", f"state_{product_id.lower()}.json")
    
    if not os.path.exists(state_file):
        print(f"Error: State file '{state_file}' does not exist.")
        print(f"Make sure you have a bot configuration or state file for {product_id}.")
        return

    # Load State Manager
    try:
        with open(state_file, 'r') as f:
            state_data = json.load(f)
    except Exception as e:
        print(f"Error reading state file {state_file}: {e}")
        return

    is_dry_run = state_data.get("dry_run", True)
    position = state_data.get("position", "NONE")
    entry_price = state_data.get("entry_price", 0.0)
    holdings = state_data.get("holdings_base", 0.0)

    if position != "LONG" or holdings <= 0.0:
        print(f"No open position found for {product_id} (State is currently {position}).")
        return

    # Get latest price
    latest_price = fetch_latest_price(client, product_id)
    if latest_price == 0.0:
        print("Could not retrieve latest price. Aborting.")
        return

    price_diff_pct = ((latest_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
    mode_str = "DRY RUN (SIMULATION)" if is_dry_run else "LIVE REAL-MONEY"

    print(f"Target Pair:  {product_id}")
    print(f"Mode:         {mode_str}")
    print(f"Position:     LONG")
    print(f"Holdings:     {holdings:.8f} {coin_symbol}")
    print(f"Entry Price:  ${entry_price:,.2f} USD")
    print(f"Latest Price: ${latest_price:,.2f} USD")
    print(f"Current P/L:  {price_diff_pct:+.2f}%")
    print("-" * 60)

    if not bypass_confirm:
        try:
            confirm = input(f"Are you sure you want to execute a manual SELL to close this position? [y/N]: ")
            if confirm.lower() not in ("y", "yes"):
                print("Operation cancelled.")
                return
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
            return

    # Initialize StateManager using the proper state file
    state_manager = StateManager(state_file=state_file, dry_run=is_dry_run)

    if is_dry_run:
        # Dry Run Sell Execution
        f = Settings.FEE_RATE_PERCENT / 100.0
        amount_usd = holdings * latest_price
        fee_usd = amount_usd * f
        
        trade = state_manager.record_trade(
            trade_type="SELL",
            price=latest_price,
            amount_usd=amount_usd,
            amount_base=holdings,
            fee_usd=fee_usd,
            reason="MANUAL_EXIT"
        )
        
        print("\n" + "*"*40)
        print("[SIMULATED ORDER EXECUTED]")
        print(f"Product:   {product_id}")
        print(f"Side:      SELL")
        print(f"Price:     ${latest_price:,.2f} USD")
        print(f"Size:      {holdings:.8f} {coin_symbol}")
        print(f"Fee:       ${fee_usd:.4f} USD")
        print(f"PnL:       ${trade['realized_pnl_usd']:.2f} USD")
        print("*"*40)
        
        # Send Email Notification if enabled
        send_trade_notification(
            trade_type="SELL",
            pair=product_id,
            price=latest_price,
            amount_usd=amount_usd,
            amount_base=holdings,
            fee_usd=fee_usd,
            is_dry_run=True,
            realized_pnl=trade.get("realized_pnl_usd", 0.0)
        )
        print("\nPosition successfully closed out in state file.")
        
    else:
        # Live Sell Execution
        if not client:
            print("Error: Coinbase API Client is not initialized. Please verify COINBASE_API_KEY and COINBASE_API_SECRET in your .env.")
            return

        # Fetch actual Coinbase balance
        print(f"Fetching actual balance of {coin_symbol} in Coinbase account...")
        live_holdings = get_asset_balance(client, coin_symbol)
        print(f"Available Coinbase Balance: {live_holdings:.8f} {coin_symbol}")

        if live_holdings <= 1e-8:
            print(f"Warning: No available Coinbase balance found for {coin_symbol}.")
            confirm_reset = input("Would you like to reset the local bot state to NONE anyway? [y/N]: ")
            if confirm_reset.lower() in ("y", "yes"):
                state_manager.state["position"] = "NONE"
                state_manager.state["entry_price"] = 0.0
                state_manager.state["holdings_base"] = 0.0
                state_manager.save_state()
                print("Local bot state has been reset.")
            return

        # Handle difference in holdings safely
        if abs(live_holdings - holdings) / max(holdings, 1e-8) > 0.05:
            print(f"Warning: Local state holdings ({holdings:.8f}) differ from Coinbase account balance ({live_holdings:.8f}).")
            if live_holdings >= holdings:
                sell_amount = holdings
                print(f"Coinbase balance is larger. Selling ONLY the bot's tracked size: {sell_amount:.8f} {coin_symbol}.")
            else:
                sell_amount = live_holdings
                print(f"Coinbase balance is smaller. Selling ALL available: {sell_amount:.8f} {coin_symbol}.")
        else:
            sell_amount = holdings

        # Place market sell order
        client_order_id = str(uuid.uuid4())
        print(f"Placing live market SELL order for {sell_amount:.8f} {coin_symbol}. Order ID will be fetched...")
        
        try:
            order = client.market_order_sell(
                client_order_id=client_order_id,
                product_id=product_id,
                base_size=str(sell_amount)
            )
            
            # Extract order ID
            order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
            success_resp = order_dict.get('success_response') or {}
            order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
            
            print(f"Market SELL order successfully placed. Order ID: {order_id}")
            print("Waiting for fill details...")
            time.sleep(2.0)
            
            # Fetch fill details
            fill_details = client.get_order(order_id=order_id)
            fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
            order_info = fill_dict.get('order', {})
            
            filled_val_usd = float(order_info.get('filled_value', sell_amount * latest_price))
            filled_size_base = float(order_info.get('filled_size', sell_amount))
            fee_usd = float(order_info.get('total_fees', 0.0))
            avg_price = float(order_info.get('avg_price', latest_price))
            
            if filled_size_base > 0:
                trade = state_manager.record_trade(
                    trade_type="SELL",
                    price=avg_price,
                    amount_usd=filled_val_usd,
                    amount_base=filled_size_base,
                    fee_usd=fee_usd,
                    reason="MANUAL_EXIT"
                )
                print("\n" + "*"*40)
                print("[LIVE ORDER EXECUTED & FILLED]")
                print(f"Product:   {product_id}")
                print(f"Price:     ${avg_price:,.2f} USD")
                print(f"Size:      {filled_size_base:.8f} {coin_symbol}")
                print(f"Fee:       ${fee_usd:.2f} USD")
                print(f"Total:     ${filled_val_usd:.2f} USD")
                print(f"PnL:       ${trade['realized_pnl_usd']:.2f} USD")
                print("*"*40)
                
                # Send Email Notification
                send_trade_notification(
                    trade_type="SELL",
                    pair=product_id,
                    price=avg_price,
                    amount_usd=filled_val_usd,
                    amount_base=filled_size_base,
                    fee_usd=fee_usd,
                    is_dry_run=False,
                    realized_pnl=trade.get("realized_pnl_usd", 0.0)
                )
                print("\nLive trade recorded and position successfully closed out.")
            else:
                print("Error: The filled size returned was 0. Check your Coinbase account status.")
                
        except Exception as e:
            print(f"Error placing live market sell order: {e}")

if __name__ == "__main__":
    main()
