#!/usr/bin/env python3
import os
import csv
from datetime import datetime

def load_trades(csv_path="trades.csv"):
    if not os.path.exists(csv_path):
        return []
    
    trades = []
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append({
                    "timestamp": row["timestamp"],
                    "pair": row["pair"],
                    "type": row["type"],
                    "price": float(row["price"]),
                    "amount_usd": float(row["amount_usd"]),
                    "amount_base": float(row["amount_base"]),
                    "fee_usd": float(row["fee_usd"]),
                    "realized_pnl_usd": float(row["realized_pnl_usd"]),
                    "portfolio_value_usd": float(row["portfolio_value_usd"]),
                    "dry_run": row["dry_run"] == "True"
                })
    except Exception as e:
        print(f"Error loading trades CSV: {e}")
    return trades

def print_analytics():
    trades = load_trades()
    if not trades:
        print("No trades found in trades.csv. Make sure the file exists and has records.")
        return

    # Filter completed trades (Sells)
    sells = [t for t in trades if t["type"] == "SELL"]
    buys = [t for t in trades if t["type"] == "BUY"]
    
    total_trades = len(trades)
    completed_roundtrips = len(sells)
    
    total_fees = sum(t["fee_usd"] for t in trades)
    total_realized_pnl = sum(t["realized_pnl_usd"] for t in sells) # realized_pnl is only recorded on sell
    
    winning_trades = [t for t in sells if t["realized_pnl_usd"] > 0]
    losing_trades = [t for t in sells if t["realized_pnl_usd"] <= 0]
    
    win_rate = (len(winning_trades) / completed_roundtrips * 100) if completed_roundtrips > 0 else 0.0
    
    best_trade = max(sells, key=lambda x: x["realized_pnl_usd"]) if sells else None
    worst_trade = min(sells, key=lambda x: x["realized_pnl_usd"]) if sells else None

    # Group by Coin/Pair
    pair_stats = {}
    for t in trades:
        pair = t["pair"]
        if pair not in pair_stats:
            pair_stats[pair] = {"buys": 0, "sells": [], "fees": 0.0}
        
        pair_stats[pair]["fees"] += t["fee_usd"]
        if t["type"] == "BUY":
            pair_stats[pair]["buys"] += 1
        elif t["type"] == "SELL":
            pair_stats[pair]["sells"].append(t)

    print("\n" + "="*60)
    print("      COINBASE TRADING BOT PERFORMANCE ANALYTICS      ")
    print("="*60)
    print(f"Total Transactions:   {total_trades} ({len(buys)} BUYs, {len(sells)} SELLs)")
    print(f"Completed Roundtrips: {completed_roundtrips}")
    print(f"Total Fees Paid:      ${total_fees:.4f} USD")
    print(f"Net Realized PnL:     ${total_realized_pnl:+.4f} USD")
    print(f"Win Rate:             {win_rate:.2f}% ({len(winning_trades)} W / {len(losing_trades)} L)")
    
    if best_trade:
        print(f"Best Trade:           {best_trade['pair']} SELL on {best_trade['timestamp'][:16]} | {best_trade['realized_pnl_usd']:+.4f} USD")
    if worst_trade:
        print(f"Worst Trade:          {worst_trade['pair']} SELL on {worst_trade['timestamp'][:16]} | {worst_trade['realized_pnl_usd']:+.4f} USD")
    
    print("\n" + "-"*60)
    print("                    BREAKDOWN BY ASSET                    ")
    print("-"*60)
    print(f"{'Asset':<12} | {'Buys':<5} | {'Sells':<5} | {'Win Rate':<8} | {'Fees Paid':<10} | {'Net PnL':<10}")
    print("-"*60)
    
    for pair, stats in sorted(pair_stats.items()):
        pair_sells = stats["sells"]
        pair_completed = len(pair_sells)
        pair_wins = len([s for s in pair_sells if s["realized_pnl_usd"] > 0])
        pair_win_rate = (pair_wins / pair_completed * 100) if pair_completed > 0 else 0.0
        pair_pnl = sum(s["realized_pnl_usd"] for s in pair_sells)
        
        win_rate_str = f"{pair_win_rate:.1f}%" if pair_completed > 0 else "N/A"
        pnl_str = f"${pair_pnl:+.4f}"
        
        print(f"{pair:<12} | {stats['buys']:<5} | {pair_completed:<5} | {win_rate_str:<8} | ${stats['fees']:<9.2f} | {pnl_str:<10}")
    
    print("="*60 + "\n")

if __name__ == "__main__":
    print_analytics()
