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
| Fee rate | 0.60%/side (`FEE_RATE_PERCENT=0.60`) | Coinbase tier-1 **maker** rate. The bot places `limit_order_gtc`, so this is the normal path. The market-order exit fallback pays the **taker** rate of 1.20%. |
| Roundtrip cost | **1.207% measured** across 51 sim trades | A trade must clear this gross to net anything. Current average gross move is 0.861% — structurally underwater. |
| SMA | Short=5, Long=30, **all nine coins** | Per-coin overrides commented out 2026-08-24. They were hand-set, never learner-derived. Identical settings make coins comparable — otherwise you cannot tell a coin difference from a config difference. |
| Trade size | $10, **all nine coins** | Equal weighting; no coin is privileged, BTC included. |
| Stop-loss | 5% | 3% too tight for 1h noise |
| Take-profit | 8% | Realistic 1h trend target clearing fees |
| Trailing stop | 1.5% trail, arms at +1.5% | Was 3.0%/2.0%, which needed a ~4.3% peak to break even and never fired once in 51 trades. Hourly ATR is 1.4–2.2%, so the trail cannot go below that without firing on noise. |
| EMA200 filter | ON for CONSERVATIVE, OFF for SIM | See Profiles section |
| RSI buy band | **[30-55] SIM, [40-55] CONSERVATIVE** | Ceiling lowered from 70 on 2026-08-24. RSI ≥55 entries lost $4.11 over 24 trades at a 12% win rate. |
| Separation ceiling | `SMA_MAX_SPREAD_PERCENT`, unset (disabled) | Rejects a crossover that has already extended. BUY only. |

**Hourly noise floor:** ATR is 1.4–2.2% of price per candle. Any threshold tighter
than that fires on noise rather than signal. This is the constraint behind the
trailing-stop and take-profit values, and the reason sub-hourly granularity was
abandoned.

## Profile Assignments

| Bot | Profile | EMA Filter | RSI Band | Purpose |
|---|---|---|---|---|
| btc-bot | SIM | OFF | 30–55 | Data collection (moved off CONSERVATIVE 2026-08-24) |
| eth-bot | SIM | OFF | 30–55 | Data collection (moved off CONSERVATIVE 2026-08-24) |
| near-bot | SIM | OFF | 30–55 | Data collection (moved off CONSERVATIVE 2026-08-24) |
| ada-bot | SIM | OFF | 30–55 | Data collection |
| link-bot | SIM | OFF | 30–55 | Data collection |
| render-bot | SIM | OFF | 30–55 | Data collection |
| sol-bot | SIM | OFF | 30–55 | Data collection |
| sui-bot | SIM | OFF | 30–55 | Data collection |
| xrp-bot | SIM | OFF | 30–55 | Data collection |
| spy/qqq/lpl-bot | STOCK | — | 40–70 | Alpaca paper, commission-free |

> `etc-bot` no longer exists. The three stock bots trade a different venue with
> no commission, so never average their results into crypto fee analysis.

> **⚠ CONSERVATIVE cannot trade as configured.** The 0.25% separation floor and
> the EMA200 filter compound: btc/eth/near took 0, 0 and 1 entries per 1500
> candles under it, and neither filter alone was the blocker — dropping both
> raises them to 18, 21 and 23. All nine crypto bots now run SIM so their data is
> comparable; CONSERVATIVE is reserved for live trading and needs both filters
> loosened before anything points at it.

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
- Coinbase Advanced Trade tier-1 is 0.60% **maker** (the bot's normal path via
  `limit_order_gtc`); 1.20% is the **taker** rate, paid only on the market-order
  exit fallback
- All backtests and sims ran against double the real cost for weeks
- Fixed: `FEE_RATE_PERCENT=0.60`

## Silent Failures Found 2026-08-24

Four things were broken in ways that produced no error and looked like normal
operation. Worth pattern-matching against before trusting any component.

### The learner optimised a strategy the bot doesn't run
`quick_backtest` modelled only crossover + SL/TP/trailing. The bot also applies
RSI, separation and EMA filters. Every recommendation was scored against the
wrong strategy. Fixed: the backtest now applies the full BUY chain, and config
is threaded from `.env` instead of defaulted. A parity test asserts the two
agree on every crossover candle.

### BTC, ETH and ADA were never optimised
Their `-USDC` products return HTTP 200 with an **empty candle list**, not 404,
so the `-USD` fallback never fired. They were skipped every run, silently.
Coverage was 6 coins, not 9. **Any Coinbase `-USDC` fetch needs the `-USD`
fallback on empty-200 as well as 404.**

### The dashboard reported a loss that did not exist
Position P&L came from the scanner file, which had gone 34 days without an
update because `scanner.py` was never scheduled. LINK showed −$2.56 against a
live price making it −$0.22. Coins absent from the file fell back to entry price
and showed exactly $0.00 unrealized. Fixed: live → candle → entry resolution
with source and age attached to every price.

### The market-regime filter had never fired
`MIN_BULLISH_MARKET_RATIO=0.35` read that same dead file, so the ratio was
pinned at 1.0 and the gate passed every time. **Removed**, along with the
dashboard gauge showing "100% bullish". A risk control that cannot fire is worse
than none, because it implies protection that is absent. If restored, it needs a
staleness check that fails closed.

## Risk Model

**All risk limits are per bot**, read from that bot's own state file. There is no
cross-bot aggregation — portfolio exposure is the per-bot value × 9.

| Setting | Per bot | × 9 |
|---|---|---|
| `MAX_VAL_PER_ORDER` | $15 | — |
| `MAX_EXPOSURE_PER_PRODUCT` | $15 | $135 |
| `MAX_DAILY_LOSS` | $3.00 | $27 |
| `MAX_TRADES_PER_DAY` | 6 | 54 |

At $10/trade, all nine holding deploys $90; all nine stopping out at once loses
~$5.59. **Catastrophic loss is not the risk at this size — fee drag is.** 51
trades have cost $6.13 in fees against a +$4.36 gross edge.

**Never raise `TRADE_AMOUNT_USD` to chase returns.** Fees are a percentage, so
scaling multiplies gross and fees equally; a negative-expectancy system just
loses faster. Required before any increase: 30+ trades since the last change,
positive net, positive on a majority of coins, and at least one graduated coin.

## Instrumentation

| Source | Use |
|---|---|
| `logs/signal_log_<pair>.csv` | Every candle decision incl. `blocked_by`. The only place an over-tight filter is visible — in the trade ledger it looks identical to a quiet market. |
| `analyze_entries.py` | Which entry conditions produce follow-through vs the measured fee floor, per pair. |
| Dashboard Signal Funnel | Crossovers seen → blocked by which filter → entries taken. |
| Dashboard waterfall | Gross vs fees vs net. Distinguishes a cost problem from a signal problem. |

## PM2 Process Names

| Process | Function |
|---|---|
| btc-bot, eth-bot, near-bot | CONSERVATIVE profile |
| ada-bot, link-bot, render-bot, sol-bot, sui-bot, xrp-bot | SIM profile altcoin bots |
| spy-bot, qqq-bot, lpl-bot | Alpaca stock paper bots |
| learner | auto_learner.py — cron `0 4 * * *`, `--no-autorestart` |
| scanner | scanner.py — cron `5 * * * *`, `--no-autorestart` |
| dashboard | Web dashboard server, port 8080 |
