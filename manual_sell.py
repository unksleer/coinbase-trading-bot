#!/usr/bin/env python3
import os
import sys
import json
import time
import uuid

from config.settings import Settings
from utils.state import StateManager
from utils.notifications import send_trade_notification

def get_asset_balance(client, currency: str) -> float:
    try:
        response = client.get_accounts()
        accounts_dict = response.to_dict() if hasattr(response, 'to_dict') else response
        accounts = accounts_dict.get('accounts', [])
        for account in accounts:
            if account.get('currency') == currency:
                available_balance = account.get('available_balance', {})
                return float(available_balance.get('value', 0.0))
    except Exception as e:
        print(f"Error fetching balance for {currency}: {e}")
    return 0.0

def fetch_latest_price(client, product_id: str) -> float:
    try:
        # Fallback to public candle if client is missing or error
        if client:
            try:
                # Query order book bid/ask
                bid_ask = client.get_best_bid_ask(product_ids=[product_id])
                bid_ask_dict = bid_ask.to_dict() if hasattr(bid_ask, 'to_dict') else bid_ask
                pricebooks = bid_ask_dict.get('pricebooks', [])
                if pricebooks and pricebooks[0].get('asks'):
                    return float(pricebooks[0]['asks'][0]['price'])
            except Exception as e:
                print(f"Authenticated price fetch failed: {e}. Trying public fallback...")
                
        # Public fallback
        import requests
        url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
        response = requests.get(url, params={"granularity": 60}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data:
            return float(data[0][4]) # Close price of latest candle
    except Exception as e:
        print(f"Error retrieving price: {e}")
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
                        raw_coin = filename.replace("state_", "").replace(".json", "").upper()
                        is_dry = raw_coin.endswith("_DRY") or state.get("dry_run", True)
                        coin = raw_coin.replace("_DRY", "")
                        open_positions.append({
                            "coin": coin,
                            "filename": filename,
                            "entry_price": state.get("entry_price"),
                            "holdings": state.get("holdings_base"),
                            "dry_run": is_dry
                        })
            except Exception:
                pass
    return open_positions

def main():
    print("=" * 60)
    print("COINBASE TRADING BOT - MANUAL CLOSE OUT SCRIPT")
    print("=" * 60)

    client = None
    if Settings.COINBASE_API_KEY and Settings.COINBASE_API_SECRET:
        try:
            from coinbase.rest import RESTClient
            client = RESTClient(api_key=Settings.COINBASE_API_KEY, api_secret=Settings.COINBASE_API_SECRET)
        except Exception:
            pass

    target_coin = None
    bypass_confirm = False
    explicit_dry = False
    explicit_live = False
    
    args = sys.argv[1:]
    if "--yes" in args:
        bypass_confirm = True
        args.remove("--yes")
    if "--dry" in args or "--dry-run" in args:
        explicit_dry = True
        if "--dry" in args:
            args.remove("--dry")
        if "--dry-run" in args:
            args.remove("--dry-run")
    if "--live" in args:
        explicit_live = True
        args.remove("--live")
        
    if len(args) > 0:
        target_coin = args[0].strip().upper()

    open_positions = find_open_positions()
    
    if not target_coin:
        if not open_positions:
            print("No active open positions found in any state files.")
            print("\nUsage: python manual_sell.py <coin_symbol> [--dry | --live] [--yes]")
            print("Example: python manual_sell.py btc --dry")
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

    if "-" in target_coin:
        product_id = target_coin
        coin_symbol = target_coin.split("-")[0]
    else:
        coin_symbol = target_coin
        if Settings.TRADING_PAIR.endswith("-USD"):
            product_id = f"{coin_symbol}-USD"
        else:
            product_id = f"{coin_symbol}-USDC"

    # Resolve state file name with isolation rules
    state_file = os.getenv("STATE_FILE")
    if not state_file:
        dry_file = f"state_{product_id.lower()}_dry.json"
        live_file = f"state_{product_id.lower()}.json"
        
        if explicit_dry:
            state_file = dry_file
        elif explicit_live:
            state_file = live_file
        elif os.path.exists(dry_file) and not os.path.exists(live_file):
            state_file = dry_file
        else:
            state_file = live_file

    if not os.path.exists(state_file):
        print(f"Error: State file '{state_file}' does not exist.")
        print(f"Make sure you have a bot configuration or state file for {product_id}.")
        return

    # Check is_dry_run configuration of target state file safely
    is_dry_run = True
    try:
        with open(state_file, 'r') as f:
            tmp = json.load(f)
            is_dry_run = tmp.get("dry_run", True)
    except Exception:
        if "_dry" in state_file:
            is_dry_run = True
        else:
            is_dry_run = False

    # Initialize StateManager under safe locking
    state_manager = StateManager(state_file=state_file, dry_run=is_dry_run)
    
    with state_manager.lock():
        state_manager.reload_state()
        
        position = state_manager.state.get("position", "NONE")
        entry_price = state_manager.state.get("entry_price", 0.0)
        holdings = state_manager.state.get("holdings_base", 0.0)

        if position != "LONG" or holdings <= 0.0:
            print(f"No open position found for {product_id} (State is currently {position}).")
            return

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

        if is_dry_run:
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
            if not client:
                print("Error: Coinbase API Client is not initialized.")
                return

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

            if abs(live_holdings - holdings) / max(holdings, 1e-8) > 0.05:
                print(f"Warning: Local state holdings ({holdings:.8f}) differ from Coinbase balance ({live_holdings:.8f}).")
                if live_holdings >= holdings:
                    sell_amount = holdings
                    print(f"Selling ONLY the bot's tracked size: {sell_amount:.8f} {coin_symbol}.")
                else:
                    sell_amount = live_holdings
                    print(f"Selling ALL available: {sell_amount:.8f} {coin_symbol}.")
            else:
                sell_amount = holdings

            client_order_id = str(uuid.uuid4())
            print(f"Placing live market SELL order for {sell_amount:.8f} {coin_symbol}...")
            
            try:
                order = client.market_order_sell(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    base_size=str(sell_amount)
                )
                
                order_dict = order.to_dict() if hasattr(order, 'to_dict') else (order if isinstance(order, dict) else {})
                success_resp = order_dict.get('success_response') or {}
                order_id = order_dict.get('order_id') or success_resp.get('order_id') or order_dict.get('id')
                
                print(f"Market SELL order successfully placed. Order ID: {order_id}")
                print("Waiting for fill details...")
                time.sleep(2.0)
                
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
                    print("Error: The filled size returned was 0.")
                    
            except Exception as e:
                print(f"Error placing live market sell order: {e}")

if __name__ == "__main__":
    main()
