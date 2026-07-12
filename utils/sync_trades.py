import os
import json
import csv
from datetime import datetime

def sync_trades_to_csv():
    base_dir = "/home/leeunks/coinbase-trading-bot"
    csv_path = os.path.join(base_dir, "trades.csv")
    
    # Header for the CSV
    headers = [
        "timestamp", 
        "pair", 
        "type", 
        "price", 
        "amount_usd", 
        "amount_base", 
        "fee_usd", 
        "realized_pnl_usd", 
        "portfolio_value_usd",
        "dry_run"
    ]
    
    all_trades = []
    
    # Iterate through all files in directory
    for filename in os.listdir(base_dir):
        if filename.startswith("state_") and filename.endswith(".json"):
            # Extract coin name
            coin = filename.replace("state_", "").replace(".json", "").upper()
            filepath = os.path.join(base_dir, filename)
            
            try:
                with open(filepath, 'r') as f:
                    state = json.load(f)
                    
                dry_run = state.get("dry_run", True)
                trades = state.get("trades", [])
                
                for trade in trades:
                    all_trades.append({
                        "timestamp": trade.get("timestamp"),
                        "pair": coin,
                        "type": trade.get("type"),
                        "price": trade.get("price"),
                        "amount_usd": trade.get("amount_usd"),
                        "amount_base": trade.get("amount_base"),
                        "fee_usd": trade.get("fee_usd", 0.0),
                        "realized_pnl_usd": trade.get("realized_pnl_usd", 0.0),
                        "portfolio_value_usd": trade.get("portfolio_value_usd", 0.0),
                        "dry_run": dry_run
                    })
            except Exception as e:
                print(f"Error reading {filename}: {e}")
                
    # Sort trades chronologically
    def parse_time(t_str):
        try:
            # Handle formats like "2026-07-10T23:36:24.068938Z"
            return datetime.strptime(t_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S.%f")
        except ValueError:
            try:
                return datetime.strptime(t_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                return datetime.min

    all_trades.sort(key=lambda x: parse_time(x["timestamp"]))
    
    # Write to CSV
    try:
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=headers)
            writer.writeheader()
            for trade in all_trades:
                writer.writerow(trade)
        print(f"Successfully consolidated {len(all_trades)} trades to {csv_path}")
    except Exception as e:
        print(f"Failed to write CSV: {e}")

if __name__ == "__main__":
    sync_trades_to_csv()
