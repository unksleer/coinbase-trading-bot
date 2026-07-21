#!/bin/bash
set -euo pipefail

# Dynamic script directory resolution
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if coin name is provided
if [ -z "${1:-}" ]; then
    echo "Usage: ./trade.sh <coin_name> [amount_usdc] [--live]"
    echo "Examples:"
    echo "  ./trade.sh btc 10         (Dry-runs BTC with simulated $10 USDC)"
    echo "  ./trade.sh sol 50 --live  (Interactive live trading confirmation for SOL)"
    exit 1
fi

COIN=$(echo "$1" | tr '[:lower:]' '[:upper:]')
PAIR="${COIN}-USDC"
BOT_NAME="${1,,}-bot"
AMOUNT="${2:-10.0}"

# Parse --live flag
IS_LIVE=false
if [ "${3:-}" = "--live" ] || [ "${2:-}" = "--live" ]; then
    IS_LIVE=true
fi

# Dry Run is default
DRY_RUN=True
if [ "$IS_LIVE" = true ]; then
    echo "=================================================="
    echo "⚠️  WARNING: REQUESTING LIVE TRADING MODE ⚠️"
    echo "=================================================="
    echo "This will trade REAL MONEY on Coinbase!"
    echo "Pair: $PAIR"
    echo "Order Size: $AMOUNT USDC"
    echo "--------------------------------------------------"
    
    # 1. Verify approved product (must be USDC or USD pair on Coinbase)
    if [[ ! "$PAIR" =~ ^[A-Z0-9]+-USD(C)?$ ]]; then
        echo "❌ ERROR: Invalid product format '$PAIR'. Exiting."
        exit 1
    fi
    
    # 2. Verify order limit safety
    if (( $(echo "$AMOUNT > 500.0" | bc -l) )); then
        echo "❌ ERROR: Order size exceeds maximum safety limit ($AMOUNT > 500.0 USD). Exiting."
        exit 1
    fi
    
    # 3. Require typed acknowledgement
    echo "To confirm, please type exactly: I_CONFIRM_LIVE_TRADING"
    read -p "Confirmation: " CONFIRM_TXT
    
    if [ "$CONFIRM_TXT" != "I_CONFIRM_LIVE_TRADING" ]; then
        echo "❌ Confirmation failed. Aborting live trading."
        exit 1
    fi
    
    DRY_RUN=False
    echo "✅ Live trading confirmed!"
else
    echo "=================================================="
    echo "🤖 Running in DRY RUN (Simulation) Mode"
    echo "=================================================="
fi

# Clean existing PM2 process if any
pm2 delete "$BOT_NAME" >/dev/null 2>&1 || true

# Pre-flight startup check (run bot once with --check flag to ensure it doesn't crash before PM2 persists it)
# We will pass CHECK_STARTUP=True env variable to bot.py
echo "Running pre-flight startup checks..."
if ! TRADING_PAIR="$PAIR" TRADE_AMOUNT_USD="$AMOUNT" DRY_RUN="$DRY_RUN" CHECK_STARTUP=True .venv/bin/python "$DIR/bot.py"; then
    echo "❌ Startup safety checks FAILED! Process will not be persisted in PM2."
    exit 1
fi
echo "✅ Pre-flight checks passed."

# Start bot under PM2 with exponential backoff restart delay
# --exp-backoff-restart-delay 1000 will double delay on restarts (1s, 2s, 4s...) up to 10 minutes to prevent crash looping
TRADING_PAIR="$PAIR" TRADE_AMOUNT_USD="$AMOUNT" DRY_RUN="$DRY_RUN" pm2 start "$DIR/bot.py" \
    --name "$BOT_NAME" \
    --interpreter "$DIR/.venv/bin/python" \
    --exp-backoff-restart-delay 1000 \
    --max-restarts 10

# Save PM2 state
pm2 save

echo "=================================================="
if [ "$DRY_RUN" = False ]; then
    echo "🔥 Bot '$BOT_NAME' is now RUNNING LIVE!"
else
    echo "✅ Bot '$BOT_NAME' is now running in Dry Run (simulation) mode."
fi
echo "📈 View live logs: pm2 logs $BOT_NAME"
echo "=================================================="
