# Coinbase Systematic Trading Bot & Market Screener

A modular, secure, and automated cryptocurrency algorithmic trading system designed for Coinbase. The system implements an optimized Simple Moving Average (SMA) Crossover strategy with trend/momentum filters (RSI & EMA), runs multiple live bots simultaneously with isolated state tracking, and includes an automated multi-asset market screener that emails alerts on buy signals.

---

## 🛡️ Safety & Concurrency Safeguards

This bot is engineered with strict production safety safeguards to protect capital and state integrity:

1. **Dry-Run / Live-Mode Isolation**: 
   * **State Separation**: Live bots write to `state_<pair>.json` while simulated bots write to `state_<pair>_dry.json`. There is zero overlap.
   * **Log Separation**: Transactions are routed to isolated files: live trades use `trades.csv` / `completed_trades.csv`, while simulations use `trades_dry.csv` / `completed_trades_dry.csv`.
2. **Durable Order Journal**: Logs every trade intent with a generated UUID client-order ID *before* submitting it to Coinbase. Unresolved orders (from sudden crashes/reboots) are reconciled against the exchange on startup and continuously during the execution loop.
3. **Atomic Persistence & Backups**: State saves write to a temporary `.tmp` file, flush/sync to disk using `os.fsync`, and atomically swap to the main destination. Maintains a `.bak` backup file to recover from write failures.
4. **Concurrency Locking**: Utilizes a hybrid OS-level file lock (`fcntl.flock`) and thread lock (`threading.RLock`) to prevent multiple bot processes or CLI tools from mutating the same state file simultaneously.
5. **Fail-Closed Design**: In live mode, any corrupt state parser errors or schema validation mismatches will halt execution and fail-closed immediately rather than silently resetting holdings.
6. **Risk Circuit Breakers**:
   * **Stale Data Cutoff**: Stops trading if market data is older than 5 minutes.
   * **API Error Circuit Breaker**: Exits and halts trading if repeated Coinbase connection failures occur (default limit: 5).
   * **Consecutive Loss Cooldown**: Halts buying if consecutive transactions resulted in losses within a set cooldown window.
   * **Capped Exposure**: Enforces strict limit checks on order value and total product exposure.

---

## 🚀 Key Features

* **Multi-Asset Live Bots**: Run isolated trading bots for multiple coins (e.g., BTC, ETH, SOL) concurrently.
* **Instant Trade Shortcut**: Launch live trading bots for any cryptocurrency instantly with a single command using the [`trade.sh`](file:///home/leeunks/coinbase-trading-bot/trade.sh) script.
* **Coin-Specific Overrides**: Finetune trading settings per asset directly in [.env](file:///home/leeunks/coinbase-trading-bot/.env) (e.g. `SOL_SMA_SHORT_WINDOW=12`).
* **ATR-Based Risk Management**: Set volatility-adapted stops (e.g., 3.0x ATR Stop Loss / 6.0x ATR Take Profit) instead of static percentages.
* **Stock & Crypto Screeners**: Run screeners for both crypto pairs ([`scanner.py`](file:///home/leeunks/coinbase-trading-bot/scanner.py)) and stocks ([`stock_scanner.py`](file:///home/leeunks/coinbase-trading-bot/stock_scanner.py)).
* **Email PnL Statements**: Integrated daily HTML report ([`daily_pnl_report.py`](file:///home/leeunks/coinbase-trading-bot/daily_pnl_report.py)) summarizes realized and unrealized performance for all online bots.

---

## 🛠️ Usage Instructions

### 1. Launching Trading Bots (`trade.sh` Shortcut)
The helper script runs in **Dry-Run (Simulation) by default**. To launch a live bot, you must pass the `--live` flag and type the explicit acknowledgement:

```bash
# Start a simulated dry-run BTC-USDC bot
./trade.sh btc 10

# Start a live, real-money BTC-USDC bot (requires typing: I_CONFIRM_LIVE_TRADING)
./trade.sh btc 50 --live
```

### 2. Monitoring Bots (PM2 Commands)
```bash
pm2 status             # Check active bots
pm2 logs               # View live logs
pm2 logs btc-bot       # View live logs for specific bot
pm2 delete btc-bot     # Stop and remove specific bot
```

### 3. Manually Closing Positions (`manual_sell.py`)
Close an open position manually under lock safety. Targets dry-run files by default or via argument overrides:

```bash
# List all active open positions
.venv/bin/python manual_sell.py

# Close simulated BTC position manually
.venv/bin/python manual_sell.py btc --dry

# Close live BTC position manually
.venv/bin/python manual_sell.py btc --live
```

### 4. Viewing Performance Analytics
```bash
# Display performance analytics for simulated dry-run trades
.venv/bin/python view_analytics.py --dry

# Display performance analytics for live production trades
.venv/bin/python view_analytics.py
```

---

## ⚙️ Configuration (`.env` Parameters)

Edit [`.env`](file:///home/leeunks/coinbase-trading-bot/.env) to configure settings. Major safety settings include:

```ini
# --- Global Safety Configs ---
DRY_RUN=True                          # Default is True. Set to False for live trading.
EMERGENCY_KILL_SWITCH=False           # Set to True to instantly disable bot executions.
APPROVED_PRODUCT_ALLOWLIST=BTC-USDC,ETH-USDC,SOL-USDC   # List of allowed pairs.

# --- Risk Limits ---
MAX_VAL_PER_ORDER=100.0               # Maximum USD value allowed per order.
MAX_EXPOSURE_PER_PRODUCT=500.0        # Maximum exposure allowed per pair.
MIN_QUOTE_RESERVE=10.0                # Minimum cash to retain.
MAX_TRADES_PER_DAY=10                 # Maximum daily buy/sell cycles.
CONSECUTIVE_LOSS_COOLDOWN_TRADES=3    # Limit on consecutive losses before cooldown.
CONSECUTIVE_LOSS_COOLDOWN_SECONDS=3600 # Loss cooldown duration in seconds.
STALE_MARKET_DATA_CUTOFF_SECONDS=300  # Cutoff age for stale market candles.
REPEATED_API_ERROR_LIMIT=5            # Error limit before circuit breaker trips.
```
