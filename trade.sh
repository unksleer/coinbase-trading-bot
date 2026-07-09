#!/bin/bash

# Check if coin name is provided
if [ -z "$1" ]; then
    echo "Usage: ./trade.sh <coin_name> [amount_usdc]"
    echo "Examples:"
    echo "  ./trade.sh btc 145    (Trades BTC with $145 USDC)"
    echo "  ./trade.sh eth 50     (Trades ETH with $50 USDC)"
    echo "  ./trade.sh sol        (Trades SOL with default $50 USDC)"
    exit 1
fi

# Convert inputs
COIN=$(echo "$1" | tr '[:lower:]' '[:upper:]')
PAIR="${COIN}-USDC"
BOT_NAME="${1,,}-bot" # lowercase process name, e.g. eth-bot

# Set trade amount (default to 50.0 if not specified)
AMOUNT=${2:-50.0}

echo "=================================================="
echo "🚀 Launching Live Trading Bot for $PAIR"
echo "💰 Trade Amount: $AMOUNT USDC"
echo "🤖 PM2 Process Name: $BOT_NAME"
echo "=================================================="

# Delete any existing PM2 process with the same name to prevent duplicates
pm2 delete "$BOT_NAME" >/dev/null 2>&1 || true

# Start the bot under PM2 with live trading configuration
TRADING_PAIR="$PAIR" TRADE_AMOUNT_USD="$AMOUNT" DRY_RUN=False pm2 start bot.py --name "$BOT_NAME" --interpreter .venv/bin/python

# Save PM2 state to survive reboots
pm2 save

echo "--------------------------------------------------"
echo "✅ Bot '$BOT_NAME' is now running live!"
echo "📈 View live logs: pm2 logs $BOT_NAME"
echo "📊 Check status:   pm2 status"
echo "=================================================="
