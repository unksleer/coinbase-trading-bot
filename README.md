# Coinbase Systematic Trading Bot & Market Screener

A modular, secure, and automated cryptocurrency algorithmic trading system designed for Coinbase. The system implements an optimized Simple Moving Average (SMA) Crossover strategy with trend/momentum filters (RSI & EMA), runs multiple live bots simultaneously with isolated state tracking, and includes an automated multi-asset market screener that emails alerts on buy signals.

---

## 🚀 Key Features

* **Multi-Asset Live Bots**: Run isolated trading bots for multiple coins (e.g., BTC, ETH, SOL) concurrently. State and trade histories are kept separate (e.g., `state_btc-usdc.json`, `state_eth-usdc.json`) to prevent conflicts.
* **Instant Trade Shortcut**: Launch live trading bots for any cryptocurrency instantly with a single command using the [`trade.sh`](file:///home/leeunks/coinbase-trading-bot/trade.sh) script.
* **Systematic Screener & Alerts**: A market scanner ([`daily_report.py`](file:///home/leeunks/coinbase-trading-bot/daily_report.py)) runs in the background every 10 minutes, compiling real-time prices, 24h changes, RSI, and SMA spreads for 15 top assets.
* **Alert-Only Emails**: Receives rich HTML emails only when active **`BUY`** signals are triggered, keeping your inbox clean.
* **Advanced Backtesting**: Run historical simulations with custom trade sizes or test compounding portfolio growth (using 100% of cash) with [`backtest.py`](file:///home/leeunks/coinbase-trading-bot/backtest.py).
* **Grid Search Optimizer**: Find the best windows, spreads, stop-loss, and take-profit parameters using the built-in backtest optimizer.

---

## 📁 Repository Structure

* [`bot.py`](file:///home/leeunks/coinbase-trading-bot/bot.py): Main orchestration script for running a trading bot process.
* [`backtest.py`](file:///home/leeunks/coinbase-trading-bot/backtest.py): Backtesting engine for historical strategy simulation.
* [`scanner.py`](file:///home/leeunks/coinbase-trading-bot/scanner.py): CLI screener that scans 15 popular USD coins on Coinbase.
* [`daily_report.py`](file:///home/leeunks/coinbase-trading-bot/daily_report.py): Emailling report script integrated with a 10-minute crontab scheduler.
* [`trade.sh`](file:///home/leeunks/coinbase-trading-bot/trade.sh): Command-line shortcut script to start a live bot.
* [`.env`](file:///home/leeunks/coinbase-trading-bot/.env): Environment variables containing API keys, SMTP credentials, and parameters.
* [`config/settings.py`](file:///home/leeunks/coinbase-trading-bot/config/settings.py): Application configuration loading and validation logic.
* [`utils/state.py`](file:///home/leeunks/coinbase-trading-bot/utils/state.py): Position state and trade history persistence manager.

---

## 🛠️ Usage Instructions

### 1. Launching Live Trading Bots (`trade.sh` Shortcut)
Use the executable helper script to start live bots in the background using PM2. PM2 manages the processes, auto-resurrects them on crashes, and restores them on system reboots.

```bash
# Syntax: ./trade.sh <coin_name> [amount_usdc]
# Note: If no amount is specified, it defaults to 50.0 USDC.

# Examples:
./trade.sh btc 145    # Starts a live BTC-USDC bot trading $145 USDC per position
./trade.sh eth 100    # Starts a live ETH-USDC bot trading $100 USDC per position
./trade.sh sol 50     # Starts a live SOL-USDC bot trading $50 USDC per position
```

### 2. Monitoring Bots (PM2 Commands)
Manage your running bots in real-time:
```bash
pm2 status             # Check which bots are currently active
pm2 logs               # View live logs for all running bots
pm2 logs btc-bot       # View live logs for a specific bot
pm2 stop btc-bot       # Pause the Bitcoin bot
pm2 delete btc-bot     # Stop and remove the Bitcoin bot process
pm2 save               # Save active bots list to survive system reboots
```

### 3. Running Backtests & Strategy Optimizations
Run historical simulations using the [`backtest.py`](file:///home/leeunks/coinbase-trading-bot/backtest.py) engine.

```bash
# Run baseline backtest using default .env parameters ($10 fixed trade size)
.venv/bin/python backtest.py

# Run backtest simulating 100% cash compounding portfolio allocation
.venv/bin/python backtest.py --trade-amount 0

# Run a custom trade size backtest (e.g. $150 trades) on 2,000 candles
.venv/bin/python backtest.py --trade-amount 150 --candles 2000

# Run grid search optimizer to discover top-performing parameter combinations
.venv/bin/python backtest.py --optimize
```

### 4. Running the Market Screener
Execute a manual scan across 15 high-liquidity Coinbase pairs:
```bash
.venv/bin/python scanner.py
```

### 5. Viewing Screener & Alert Logs
The 10-minute automated email screener runs silently via the system cron. You can view the full market metrics history inside the log:
```bash
tail -n 25 logs/daily_report.log       # View the last scan results table
tail -f logs/daily_report.log          # Watch scans in real-time
```

---

## ⚙️ Configuration (`.env` Parameters)

Edit [`.env`](file:///home/leeunks/coinbase-trading-bot/.env) to configure settings. Major strategy parameters include:

* **Moving Average Crossover**:
  * `SMA_SHORT_WINDOW=5` (Fast line: average price of the last 5 hours)
  * `SMA_LONG_WINDOW=20` (Slow line: average price of the last 20 hours)
  * `SMA_MIN_SPREAD_PERCENT=0.1` (Required crossover spread percentage to buy)
* **Uptrend Filter**:
  * `EMA_TREND_FILTER_ENABLED=True`
  * `EMA_TREND_PERIOD=200` (Filters buys to only execute when price is above the 200-hour EMA)
* **Hype Filter**:
  * `RSI_FILTER_ENABLED=True`
  * `RSI_BUY_MAX=60` (Filters buys to only execute when the 14-period RSI is below 60)
* **Risk Controls**:
  * `TAKE_PROFIT_PERCENT=5.0` (Sells position immediately at a 5% gain)
  * `STOP_LOSS_PERCENT=1.0` (Sells position immediately at a 1% loss)
