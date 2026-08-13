# Coinbase Trading Bot Rules

## MANDATORY: Session Start Protocol

At the start of EVERY session in this workspace, before taking any action:

1. Read `/home/leeunks/coinbase-trading-bot/.agents/skills/trading_bot_strategy/SKILL.md` for full architecture context.
2. Run `tail -50 logs/auto_learner.log` to see what the learner diagnosed last night.
3. Before ANY parameter change, read `logs/param_history.json` and `completed_trades_*_dry.csv`.
4. Never change settings based on intuition. Every change must be justified by a specific failure mode from real trade data.

This project has weeks of prior decisions. **Ignoring context causes the same mistakes to repeat.**

---


### Trading Bot Management & User Expectations
- **Explain Strategy Triggers:** When discussing a bot's activity or lack thereof, ALWAYS proactively explain the specific mathematical triggers it is waiting for (e.g., "The Short SMA is currently above the Long SMA, so the bot is waiting for a crossover event"). Do not assume the user knows that algorithmic strategies may sit idle for hours or days.
- **Differentiate Market Trend vs Strategy:** Clearly separate broad market conditions (e.g., "The dashboard says bullish") from the strict entry criteria of the bot's specific strategy.
- **PM2 Log Visibility:** If the user reports that a bot has "no activity" or "no log movement," first verify that `logging.StreamHandler(sys.stdout)` is correctly configured in Python. PM2 hides `stderr` in `error.log`, which can make healthy bots appear frozen if standard Python logging is used without `sys.stdout`.

---

### Strategy Architecture — What Was Decided and Why (August 2026)

#### Fee Rate
- **ALWAYS use `FEE_RATE_PERCENT=0.60`** — this is the actual Coinbase Advanced Trade taker fee at basic volume tier (<$10K/30d). The old value of 1.20% was the legacy Coinbase Pro rate and caused every backtest and sim to simulate double the real cost.
- **Roundtrip cost = 1.20%** (buy + sell). A trade must capture an avg gross move of **≥3.6%** to be consistently profitable after fees and losses.

#### Timeframe
- **ALWAYS use `GRANULARITY=ONE_HOUR`** for all bots. Never revert to ONE_MINUTE or FIFTEEN_MINUTE.
  - ONE_MINUTE: avg gross move was 0.054% — fees are 22× larger than the move. Mathematically impossible to profit.
  - FIFTEEN_MINUTE: avg gross move was 0.3–1.0% — still below the 1.2% roundtrip threshold.
  - ONE_HOUR: avg gross move is 1–3% — viable with the right SMA windows.

#### SMA Windows
- **Baseline: SMA 5/30 on ONE_HOUR** for all coins. Short=5h, Long=30h.
- BTC uses 8/21 (tuned separately from historical backtest).
- Never use SMA windows tuned for 15-minute candles (e.g., 10/35, 3/10) on 1-hour data — they whipsaw constantly.

#### Stop-Loss and Take-Profit
- **SL=5%, TP=8%** for all standard sim coins. SL=3% is too tight for 1-hour candles (normal hourly noise is 1–2%).
- BTC uses SL=5%, TP=10%.

#### Profiles
- **CONSERVATIVE**: Full filters on — EMA200 trend filter + RSI[40-70]. Used by BTC and NEAR (closest to live graduation).
- **SIM**: EMA filter OFF, RSI[30-70]. Used by SOL, SUI, XRP, ETC, LINK, RENDER. Allows data collection in any market direction.
- **Never use AGGRESSIVE or HYPER profiles** for any active bot. They produced whipsaw losses on 1-minute candles and have been superseded.

#### EMA200 Filter Behavior
- When ALL altcoins show `price is below EMA200`, it means the market is in a short-term downtrend. **This is expected behavior**, not a bug.
- The SIM profile disables EMA filtering so sim bots can collect trade data during downtrends.
- BTC stays protected by the EMA filter at all times since it is live-adjacent.

---

### Auto-Learner Architecture (auto_learner.py)

The learner runs nightly at 4am via PM2 cron. It uses a **4-phase data-driven loop**:

1. **Diagnose** — reads `completed_trades_*_dry.csv` and classifies a failure mode:
   - `WHIPSAW`: >60% of exits are SMA crossover reversals with avg hold <6h → widen SMA windows
   - `SL_TOO_TIGHT`: >50% stop-loss exits with avg MAE <5% → widen stop-loss
   - `FEE_DRAG`: avg gross move < roundtrip fee × 0.8 → search much longer windows
   - `TP_TOO_LOOSE`: high avg MFE but low win rate → tighten take-profit
   - `OK`: win rate ≥40% → fine-tune only

2. **Adapt** — adjusts grid search bounds based on failure mode. WHIPSAW searches short=8-20, long=30-80. SL_TOO_TIGHT searches SL=5-8%.

3. **Skip** — `logs/param_history.json` records every parameter combination tried and its result. Failed combos (negative backtest profit) are **never retried**.

4. **Decide** — only writes to `.env` if: params changed AND improvement ≥5% AND backtest net profit > $0.

- **Never manually tune SMA windows without first reading `logs/param_history.json`** to see what has already been tried and failed.
- **Never manually tune SMA windows without first reading `completed_trades_*_dry.csv`** to understand the actual failure mode.

---

### Graduation Criteria (Sim → Live)
A sim coin is only promoted to live when ALL three are met over ≥20 completed trades:
- Win rate ≥ 55%
- Net PnL > $0
- Worst single trade ≥ -$2.50

**Never manually promote a coin to live.** The learner sends an email alert automatically when graduation criteria are met.

---

### What Not To Do (Learned from Weeks of Failures)
- **Do not change settings without first diagnosing the failure mode** from actual trade data. Changing settings blindly caused weeks of back-and-forth with no improvement.
- **Do not lower SL% to stop losses** — tight stops cause MORE stop-losses in volatile hourly markets.
- **Do not increase trading frequency** (shorter candles, shorter SMA windows) to "get more data faster" — it amplifies fee drag and always results in more losses.
- **Do not disable RSI filter entirely** — RSI <30 entries are genuinely bad (extreme oversold, high reversal risk). RSI min=30 is the floor.
- **Do not change granularity per-coin back to ONE_MINUTE or FIFTEEN_MINUTE** — this was the root cause of 44 consecutive losses.

---

### Stock Bot Architecture (Alpaca — August 2026)

#### Bot File
- `stock_bot.py` — same SMA crossover + RSI strategy as `bot.py` but uses Alpaca API
- Tickers: SPY, QQQ, LPL. PM2 names: `spy-bot`, `qqq-bot`, `lpl-bot`

#### Alpaca Credentials (.env)
```
ALPACA_API_KEY=...          # from alpaca.markets → Paper Trading → API Keys
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2
ALPACA_PAPER=True           # True = paper, False = real money
```

#### Data Feed — CRITICAL
- **ALWAYS use `feed=DataFeed.IEX`** in `StockBarsRequest`
- The default SIP feed requires a paid Alpaca subscription and will error:
  `subscription does not permit querying recent SIP data`
- IEX is the free tier included with all Alpaca accounts

#### Key Differences vs Crypto Bots
- **Market hours gate**: NYSE 9:30am–4:00pm ET, Mon–Fri only. Bot sleeps outside hours.
- **Commission-free**: No fee hurdle — SL/TP can be tighter than crypto
- **SMA windows**: 10/30 on 1-hour bars (vs crypto's 5/30)
- **SL/TP**: SPY=2%/4%, QQQ=2.5%/5%, LPL=3%/6%
- **PDT rule**: SMA 10/30 holds overnight so PDT is rarely triggered
- **Fractional shares**: Alpaca supports fractional — $10/trade works

#### PM2 Launch Commands
```bash
STOCK_TICKER=SPY TRADE_AMOUNT_USD=10 ALPACA_PAPER=True pm2 start stock_bot.py --name spy-bot --interpreter .venv/bin/python --no-autorestart
STOCK_TICKER=QQQ TRADE_AMOUNT_USD=10 ALPACA_PAPER=True pm2 start stock_bot.py --name qqq-bot --interpreter .venv/bin/python --no-autorestart
STOCK_TICKER=LPL TRADE_AMOUNT_USD=10 ALPACA_PAPER=True pm2 start stock_bot.py --name lpl-bot --interpreter .venv/bin/python --no-autorestart
```

#### Graduation to Live (Same Criteria as Crypto)
- Win rate ≥ 55% over ≥ 20 trades, Net PnL > $0, Worst trade ≥ -$2.50
- Only then: set `ALPACA_PAPER=False` in `.env` and restart
- **Never switch to live manually** — wait for learner alert

---

### Defensive Programming & Bug Prevention

To stop introducing runtime crashes into the bots and cron jobs, ALWAYS adhere to the following rules when writing or modifying Python code in this workspace:

1. **Pandas/Numpy JSON Serialization**: 
   - Never pass raw Pandas or Numpy types (like `np.bool_`, `np.int64`, `np.float64`) directly to `json.dump()`. 
   - They are NOT natively serializable and will cause a `TypeError` crash. 
   - ALWAYS explicitly cast them using `bool(x)`, `float(x)`, `int(x)` or use a custom `json.JSONEncoder` class that handles Numpy types.

2. **Fault Isolation in Cron Jobs (Auto-Learner)**:
   - Non-critical operations (like saving a parameter history ledger) MUST be wrapped in their own `try...except` blocks.
   - If a file save or minor calculation fails, it should log the error and *continue*, ensuring the primary function (like sending the daily email report) still executes.

3. **Explicit Import Verification**:
   - When adding new blocks of code (e.g., parsing JSON strings, checking dates), physically check the top of the file to confirm `import json`, `import datetime`, or `import re` exists. Do not assume it is already imported just because it is a standard library.

4. **API Tier Awareness**:
   - When integrating new APIs (like Alpaca), explicitly verify data feed endpoints against the free-tier limitations (e.g., strictly using `DataFeed.IEX` instead of default `SIP`).

