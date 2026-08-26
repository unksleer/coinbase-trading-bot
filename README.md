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
* **Stock & Crypto Screeners**: Run screeners for both crypto pairs ([`scanner.py`](file:///home/leeunks/coinbase-trading-bot/scanner.py), hourly under PM2 cron) and stocks ([`stock_scanner.py`](file:///home/leeunks/coinbase-trading-bot/stock_scanner.py)).
* **Email PnL Statements**: Integrated daily HTML report ([`daily_pnl_report.py`](file:///home/leeunks/coinbase-trading-bot/daily_pnl_report.py)) summarizes realized and unrealized performance for all online bots.
* **Full Signal Audit Trail**: Every candle decision — accepted, or rejected with the reason — is written to `logs/signal_log_<pair>.csv`. Filters that block good setups are invisible in the trade ledger; this is where they show up.
* **Edge Analysis Dashboard**: Separates gross edge from fee drag, breaks exits down by reason, and measures how many entries ever travelled far enough to cover the round trip. See [Dashboard](#-dashboard) below.
* **Entry Quality Tooling**: [`analyze_entries.py`](file:///home/leeunks/coinbase-trading-bot/analyze_entries.py) reports which entry conditions produce follow-through against the measured fee floor, with a per-pair breakdown so a result carried by one lucky coin is visible as such.

## 🧠 Auto-Learner Architecture (`auto_learner.py`)

A core component of this system is the nightly auto-learner. Instead of guessing parameters, the bot uses a 4-phase data-driven loop to adapt to market conditions:

1. **Diagnose**: Reads the actual sim trade history (`completed_trades_*_dry.csv`) and classifies a specific failure mode (e.g., `WHIPSAW`, `SL_TOO_TIGHT`, `TP_TOO_LOOSE`).
2. **Adapt**: Adjusts the grid search parameter bounds specifically to fix the diagnosed failure mode (e.g., searching for much longer SMA windows if whipsawing). It skips any parameter combinations that historically failed.
3. **Decide**: Backtests candidates across two unseen validation folds and records an evidence-backed recommendation. Under the default `LEARNER_MODE=ADVISORY` it **cannot** edit `.env` or restart bots — see the review workflow below.
4. **Graduate**: Evaluates sim coins against the five criteria in [`utils/graduation.py`](file:///home/leeunks/coinbase-trading-bot/utils/graduation.py) — ≥20 trades, win rate ≥55%, net P&L > $0, worst trade ≥ −$2.50, and per-trade Sharpe ≥ 0.35. Every criterion must pass; the weighted score out of 100 is for display only.

### Backtest fidelity

The learner's verdicts are only meaningful if it simulates the strategy the bot
actually trades. `quick_backtest` applies the bot's full BUY filter chain —
separation floor and ceiling, RSI band, EMA trend — in the same order and with
the same RSI maths as [`strategies/simple_moving_average.py`](file:///home/leeunks/coinbase-trading-bot/strategies/simple_moving_average.py).
Exit and filter settings are read from `.env` through the same
coin → profile → base resolution the bot uses, and logged on every run:

```
Exit regime from .env: trailing 1.5% (arms at +1.5%)
Entry filters from .env: RSI 30.0-55.0 (p14)
```

A parity test drives identical price data through both the live strategy and the
backtest filter and asserts they agree on every crossover candle, so the two
cannot drift apart silently.

When entry filters block nearly every crossover, the learner says so directly
rather than reporting an empty result:

```
All 480 combos produced <5 trades in the training window. The entry filters
(sep>=0.25%, RSI 40.0-55.0 (p14), price>EMA200) are blocking nearly every
crossover, so no parameter set can be evaluated.
```

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

### 5. Sim Performance Report (`sim_report.py`)

```bash
.venv/bin/python sim_report.py              # terminal (plain text)
.venv/bin/python sim_report.py --coin LINK  # one coin
.venv/bin/python sim_report.py --html       # the emailed HTML body
```

Summary-first: portfolio gross/fees/net with a one-line diagnosis of *which*
problem it is, a readiness leaderboard ranked by graduation score, then per-coin
detail in the same order. The nightly learner emails this as
`multipart/alternative` — HTML with the plain-text version as fallback.

### 6. Entry Quality Analysis (`analyze_entries.py`)

Reports which entry conditions actually produce follow-through. Because every
round trip costs a fixed percentage in fees, an entry whose price never travels
further than that fee is a guaranteed loss regardless of how the exit is tuned.

```bash
.venv/bin/python analyze_entries.py

# Evaluate a different candidate filter
.venv/bin/python analyze_entries.py --rsi-low 45 --rsi-high 60 --max-sma-sep 0.15
```

Buckets entries by RSI and crossover separation, shows the share that cleared
the fee floor, and breaks a candidate filter down per pair — a filter that looks
profitable overall but is carried by one lucky coin is not a filter. It flags
its own sample size when a result rests on too few trades to trust.

### 7. Stock Bots (`add_stock.py`)

Stock trades on Alpaca are **commission-free**, so the ~1.2% round-trip fee that
dominates every crypto result is absent. Results are not transferable between
the two markets.

```bash
.venv/bin/python add_stock.py --list          # registered tickers vs pm2
.venv/bin/python add_stock.py NVDA --check    # validate + backtest, change nothing
.venv/bin/python add_stock.py NVDA            # register and start
.venv/bin/python add_stock.py NVDA --amount 25
.venv/bin/python add_stock.py NVDA --remove   # stop and deregister
```

Every check runs before anything changes: that Alpaca will accept orders for the
symbol, that enough history exists for the indicators, and a backtest of the
configured strategy on that symbol against buy-and-hold. Registration starts the
pm2 process, adds the ticker to `STOCK_WATCHLIST` and runs `pm2 save`.

**Strategy selection.** `stock_bot.py` runs `STOCK_STRATEGY` (or
`<TICKER>_STRATEGY`), one of:

| value | entry | exit |
| :--- | :--- | :--- |
| `SMA_CROSSOVER` *(default)* | bullish SMA cross, RSI inside band | bearish SMA cross |
| `RSI_MEAN_REVERSION` | RSI crosses **up** through `STOCK_RSI_OVERSOLD` | RSI crosses **down** through `STOCK_RSI_OVERBOUGHT` |

Stops, targets, trailing stop and risk limits are shared, so the two differ only
in entry and strategy exit. An unrecognised value refuses to start rather than
falling back silently.

Measured across 15 symbols out-of-sample, mean reversion was positive on both
hourly and daily bars at every threshold tested (66–75% win rate) against
crossover's 35–44%. Commission-free execution is why: it takes many small moves
that a 1.2% fee would erase.

**Nightly learning.** [`stock_learner.py`](file:///home/leeunks/coinbase-trading-bot/stock_learner.py)
evaluates every watchlist ticker across both strategy classes, requires the
winner to beat buy-and-hold and to be corroborated by its neighbouring settings,
and is advisory only — it never edits `.env`. It runs as part of the nightly
learner and its output is included in the emailed report.

```bash
.venv/bin/python stock_learner.py
.venv/bin/python stock_learner.py --ticker NVDA --timeframe Day
```

Compare strategy classes across the watchlist with:

```bash
.venv/bin/python compare_strategies.py --market stocks --granularity Hour
.venv/bin/python compare_strategies.py --market stocks --granularity Day --lookback-days 1800
```

### 8. Scheduled Jobs (PM2 cron)

Both run once per firing and then stop; `--no-autorestart` is required or PM2
will treat the exit as a crash and restart them in a loop.

```bash
# Nightly learner (04:00)
pm2 start auto_learner.py --name learner --interpreter .venv/bin/python \
    --cron "0 4 * * *" --no-autorestart

# Hourly market screener (:05 past the hour)
pm2 start scanner.py --name scanner --interpreter .venv/bin/python \
    --cron "5 * * * *" --no-autorestart

pm2 save    # persist across reboot
```

The screener writes `logs/market_metrics.json`, which backs the dashboard's
Screener Scoreboard. The dashboard warns if that file goes more than three hours
stale, since that means the job stopped.

### Learner V2 review workflow

The nightly learner defaults to `LEARNER_MODE=ADVISORY`. It can analyze and
email recommendations, but it cannot edit `.env` or restart bots.

```bash
# List evidence-backed recommendations
.venv/bin/python learner_review.py

# Approve one exact recommendation for a later paper-only learner run
.venv/bin/python learner_review.py --approve RECOMMENDATION_ID
```

Applying approvals additionally requires `LEARNER_MODE=APPLY_APPROVED`.
Approvals never enable live trading and do not bypass the normal graduation
criteria.

---

## 📊 Dashboard

`pm2 start dashboard.py` serves on port 8080. Beyond live positions and process
status, it answers whether the strategy is working — as opposed to what it is
doing right now:

| Panel | What it answers |
| :--- | :--- |
| **Where the Money Goes** | Gross edge − fees = net, with the measured per-trade fee floor. A strategy can pick direction well and still lose if the round trip costs more than the move it captures; shown as separate cards that subtraction is left to the reader. |
| **Cumulative Net P&L** | The only view with a time axis. Mark `.env` changes against it to see whether a change helped. |
| **Exits by Reason** | Count and average net per exit path. Warns when one reason dominates and loses. |
| **Entry Follow-Through** | How many entries ever moved far enough to cover the round trip, against the fee floor. |
| **Signal Funnel** | Crossovers seen → blocked by which filter → entries taken. An over-tight filter is invisible in the trade ledger, where it looks identical to a quiet market. |
| **Go-Live Readiness** | Per-coin progress against all five graduation criteria, closest first. Evaluated by `utils/graduation.py` — the same code the nightly report renders and the promotion gate enforces, so one coin cannot show two readiness figures. |

### Price provenance

Position P&L is only as trustworthy as the price behind it. Prices resolve
**live ticker → the bot's last closed candle → entry price**, and every price
carries its source and age. Non-live prices are badged, and P&L derived from a
stale price renders grey rather than red or green, so it is never mistaken for a
confirmed gain or loss.

This matters: prices previously came from the screener file, which had gone 34
days without an update. An open LINK position was reported at a stale $8.68
against a live $11.51 — a $2.56 loss that did not exist. Coins missing from that
file fell back to entry price and showed exactly $0.00 unrealized, which reads
as a real value rather than a missing one.

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

Strategy parameters — SMA windows, the RSI band, separation bounds, stops and
the trailing stop — are documented per profile in
[`trading_profiles.md`](file:///home/leeunks/coinbase-trading-bot/trading_profiles.md),
along with why the fee floor drives several of those values.

> **Removed:** `MIN_BULLISH_MARKET_RATIO` gated buys on a market-breadth ratio
> read from `logs/market_metrics.json`. That file had gone 34 days without an
> update, pinning the ratio at `1.0` against a `0.35` threshold, so the gate had
> never blocked a single trade. It was removed rather than left implying a
> protection that was absent. The scanner now runs hourly (see §6 above); if you
> want breadth gating back, restore it with a staleness check that fails closed.
