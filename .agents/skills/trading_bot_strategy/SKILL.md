---
name: trading_bot_strategy
description: >
  Knowledge base for the Coinbase trading bot project. ALWAYS load this at the
  start of any session in this workspace. Triggers on any mention of: bot,
  trading, trade, sim, simulation, strategy, SMA, EMA, RSI, crossover, candle,
  granularity, fee, profit, loss, win rate, stop loss, take profit, learner,
  auto_learner, pm2, graduation, live, dry run, backtest, SOL, BTC, SUI, XRP,
  ETC, LINK, NEAR, RENDER, settings, parameters, .env, or any coin name.
  Contains all architectural decisions made through August 2026 and what was
  learned from weeks of failed parameter iterations. Reading this skill prevents
  repeating mistakes that were already tried and failed.
---

# Trading Bot Strategy — Full Knowledge Base

## Quick Reference: Current Architecture

| Setting | Value | Reason |
|---|---|---|
| Granularity | ONE_HOUR | Only timeframe with moves large enough to clear fees |
| Fee rate | 0.60% (FEE_RATE_PERCENT=0.60) | Actual Coinbase Advanced Trade taker fee |
| Roundtrip cost | 1.20% | Need avg gross move ≥3.6% to be profitable |
| SMA baseline | Short=5, Long=30 | 5h/30h crossover on hourly data |
| BTC SMA | Short=8, Long=21 | Separately tuned via historical backtest |
| Stop-loss | 5% | 3% too tight for 1h noise |
| Take-profit | 8% (BTC: 10%) | Realistic 1h trend target clearing fees |
| EMA200 filter | ON for CONSERVATIVE, OFF for SIM | See Profiles section |
| RSI range | [30-70] for SIM, [40-70] for CONSERVATIVE | |

## Profile Assignments

| Bot | Profile | EMA Filter | RSI Min | Purpose |
|---|---|---|---|---|
| btc-bot | CONSERVATIVE | ON | 40 | Live-adjacent, full protection |
| near-bot | CONSERVATIVE | ON | 40 | Closest sim to graduation |
| sol-bot | SIM | OFF | 30 | Data collection |
| sui-bot | SIM | OFF | 30 | Data collection |
| xrp-bot | SIM | OFF | 30 | Data collection |
| etc-bot | SIM | OFF | 30 | Data collection |
| link-bot | SIM | OFF | 30 | Data collection |
| render-bot | SIM | OFF | 30 | Data collection |

## Auto-Learner: How to Diagnose

Before any manual setting change, always run this diagnosis:

```bash
# 1. Check param history to see what's already been tried
cat logs/param_history.json | python3 -c "
import json,sys
h=json.load(sys.stdin)
for pair,trials in h.items():
    print(f'{pair}: {len(trials)} trials')
    for t in trials[-3:]:
        p=t['params']; bt=t['backtest']
        print(f'  SMA {p[\"short\"]}/{p[\"long\"]} SL={p[\"stop_loss\"]} TP={p[\"take_profit\"]} → profit=\${bt[\"net_profit\"]:.2f} WR={bt[\"win_rate\"]:.1f}%')
"

# 2. Check actual sim trade failure modes
python3 -c "
import csv, glob, collections
for f in glob.glob('completed_trades_*_dry.csv'):
    trades = list(csv.DictReader(open(f, errors='replace')))
    if not trades: continue
    exits = collections.Counter(t['exit_reason'] for t in trades)
    pnls = [float(t.get('pnl_usd',0)) for t in trades]
    holds = [float(t.get('holding_time_hours',0)) for t in trades]
    print(f'{f}: {len(trades)} trades  WR={sum(1 for p in pnls if p>0)/len(pnls)*100:.1f}%  net=\${sum(pnls):.2f}')
    print(f'  avg hold={sum(holds)/len(holds):.1f}h  exits={dict(exits)}')
"
```

## Auto-Learner: Failure Modes and Actions

The learner classifies each coin into one failure mode per run. Here is what each means and what it does:

### WHIPSAW
- **Symptom:** >60% of exits are "SMA Bearish Cross" with avg hold <6h
- **Cause:** SMA windows too short — price oscillates through the crossover threshold repeatedly
- **Action:** Searches longer windows (short=8-20, long=30-80)
- **What NOT to do:** Do not shorten windows to "get more crossovers" — makes it worse

### SL_TOO_TIGHT
- **Symptom:** >50% of exits are "Stop Loss Triggered" with avg MAE <5%
- **Cause:** Normal hourly price noise (1-2%) is reaching the stop-loss level before any trend develops
- **Action:** Searches wider SL (5-8%)
- **What NOT to do:** Do not tighten SL further — that causes even more stop-outs

### FEE_DRAG
- **Symptom:** Avg gross price move per trade < 0.96% (< 80% of the 1.2% roundtrip fee)
- **Cause:** On the wrong timeframe OR SMA windows so short that only tiny moves are captured
- **Action:** Searches very long windows (short=10-25, long=40-100)
- **Note:** After switching to ONE_HOUR, this should be rare. If it reappears, do NOT switch timeframes — widen windows instead.

### TP_TOO_LOOSE
- **Symptom:** High avg MFE (price went far in our favor) but win rate <30%
- **Cause:** Price reaches a profitable level but reverses before hitting the TP target
- **Action:** Tightens TP to ~70-90% of avg MFE
- **What NOT to do:** Do not widen TP further

### OK
- **Symptom:** Win rate ≥40%
- **Action:** Fine-tunes SL/TP around observed MFE/MAE values. Does not change SMA windows.

## Key Files

| File | Purpose |
|---|---|
| `.env` | All bot configuration. Source of truth for running parameters. |
| `logs/param_history.json` | Permanent memory of every parameter set tried. Learner skips failed combos. |
| `logs/best_params.json` | Last run's best backtest result per coin. |
| `logs/auto_learner.log` | Full log of every nightly learner run including failure modes and decisions. |
| `completed_trades_*_dry.csv` | Actual sim trade history per coin. Primary input for diagnosis. |
| `completed_trades_bak.csv` | Full historical live+sim trade archive (all eras). |
| `logs/sim_archive_*/` | Archived sim state before major resets. |
| `strategies/simple_moving_average.py` | Core strategy logic: SMA crossover, EMA filter (line 112-114), RSI filter (line 108-110). |
| `auto_learner.py` | 4-phase learning loop. Runs at 4am via PM2 cron. |
| `sim_report.py` | Prints performance summary for all sim coins. |

## Graduation Criteria (Sim → Live)

A coin can only be promoted to live trading when ALL three are met over ≥20 completed sim trades:
1. Win rate ≥ 55%
2. Net PnL > $0
3. Worst single trade ≥ -$2.50

The learner checks this every night and sends an email alert if met. **Never manually promote a coin.**

## What Was Tried and Failed (August 2026)

### ONE_MINUTE candles (SOL, SUI — AGGRESSIVE profile)
- 44 trades, 0 wins, -$10.26 net
- Avg gross move: +0.054% vs 2.40% roundtrip fee (fee was 44× the move)
- Root cause: mathematically impossible regardless of parameters

### FIFTEEN_MINUTE candles (ETC, LINK, NEAR, XRP, RENDER)
- Multiple runs, 0% win rate
- Avg gross move 0.3-1.0% vs 2.40% roundtrip fee
- Root cause: still below fee threshold

### ONE_HOUR with SMA 5/50 (CONSERVATIVE era, Jul 21-29)
- 6 trades, 0 wins, -$8.65 net
- Avg hold: 117 hours (5 days)
- Root cause: SMA long=50 is too slow — held through full bear market reversals
- Stop-loss at 3% was too tight for 5-day holds

### Fee rate bug
- `FEE_RATE_PERCENT=1.20` was set to the old Coinbase Pro rate
- Actual Coinbase Advanced Trade fee is 0.60% taker
- All backtests and sims ran against double the real cost for weeks
- Fixed: `FEE_RATE_PERCENT=0.60`

## PM2 Process Names

| Process | Function |
|---|---|
| btc-bot | BTC-USDC live-adjacent sim |
| sol-bot, sui-bot, xrp-bot | SIM profile altcoin bots |
| etc-bot, link-bot, near-bot, render-bot | SIM profile altcoin bots |
| learner | auto_learner.py — cron 0 4 * * * |
| dashboard | Web dashboard server |
