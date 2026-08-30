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

## Objective: EVIDENCE RATE, not backtest profit

This is paper trading. A trade costs nothing; data is the scarce resource.
Settings are tuned to **maximise trades per day subject to net staying
positive**, not to maximise expected profit. Optimising profit while producing
0.7 trades a day was the wrong objective and produced months without a verdict.

Net positive is the constraint that keeps the data meaningful — a config that
loses teaches you about a config nobody would run.

### Restarting bots

```bash
.venv/bin/python restart_bots.py            # all bots
.venv/bin/python restart_bots.py --crypto
.venv/bin/python restart_bots.py --check    # report identity drift only
```

**Never restart with `--update-env` from a shell that has .env loaded.** The
advice that used to be here —

```bash
set -a && . ./.env && set +a && pm2 restart <bots> --update-env   # DO NOT
```

— copies the whole shell environment into every process, including .env's
single `TRADING_PAIR`. On 2026-08-27 at 14:51 that put **eight crypto bots on
BTC-USDC within one second**, all writing the same state file. Caught 36
minutes later; no trades were lost, which was luck rather than design.

Nothing errored; every bot started, logged healthy and polled a real market.
The only symptom was per-coin trade files that stopped growing — on six-hour
candles, indistinguishable from a quiet market.

That workaround is also no longer needed. `utils/env_loader` re-reads .env at
startup with `override=True`, so a plain restart already picks up tuning
changes while pm2 keeps each process's own identity — .env wins for tuning, the
command line wins for identity. `restart_bots.py` is the only restart path that
honours both, and it verifies the result afterwards.

Bots now refuse to start if their pm2 name disagrees with their resolved symbol
(`utils/identity.py`), and `registry.audit()` reports drift in already-running
bots, so this failure cannot recur silently.

*(The earlier symptom of the same env-caching problem: nine stock bots silently
kept RSI 30/70 after .env said 35/65.)*

## ⛔ CONFIGURATION FREEZE UNTIL 2026-10-10

**Do not change strategy, timeframe, thresholds or stops.** Adding symbols is
fine — it raises the evidence rate without changing what is being tested.

Six config changes were made on 2026-08-26 alone. Each resets the forward-test
clock on a system whose feedback loop is days per trade, so no configuration
ever ran long enough to produce evidence. **Change velocity exceeded evidence
velocity.** That is the reason months of work produced no verdict — not any
individual parameter.

Expected rate, to judge against instead of a feeling:

| | bots | config | rate |
|---|---|---|---|
| stocks | 12 | mean reversion 35/65 | ~27/month |
| crypto | 9 | SMA 10/40 SIX_HOUR — **PARKED** | ~14/month |

Judge stocks on the **pooled** count, not per ticker — the strategy is what is
under test, and 12 tickers reach 20 pooled trades ~12x faster than any one of
them alone. The dashboard shows this above the per-symbol cards.

A day with zero trades is normal. A **week** with zero stock trades is a signal
— check `restart_bots.py --check` first, then the signal logs for `blocked_by`.

Checkpoints: **2026-09-18** ~20 stock trades (first read) · **2026-10-10** ~40
(decide).

**Crypto is a control, not a candidate.** The best variant flips with the
timeframe — SMA wins at six-hour, mean reversion at hourly — which is the
signature of noise at a 1.207% round trip. Stocks are where the stable result
is: mean reversion positive on both timeframes at all four thresholds.

**Crypto is PARKED as of 2026-08-30** — finished, not pending. 53 forward
trades: gross **+0.794%**/trade against a **1.206%** fee, net −0.412%. 19% win
rate, net −$2.18, fails 3 of 5 criteria. The strategy picks direction and still
earns less than it costs to run.

It was briefly moved to hourly for evidence rate and moved back: hourly's best
gross is 1.17% against the 1.206% fee, so it cannot clear costs at any
parameter tested. **Do not re-open crypto on backtest evidence.** Re-open only
if the economics change — a materially lower fee tier, or maker fees well under
0.6%. The bot already uses `post_only`, so the order-type lever is spent.

**When a search is exhaustive and finds nothing, the answer is outside the box —
but changing the box repeatedly is how you never find out.**

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
| RSI buy band | **[30-57] SIM, [40-57] CONSERVATIVE** | 70 → 55 on 2026-08-24, relaxed to 57 on 2026-08-26. RSI ≥55 entries lost $4.11 over 24 trades, but live crossovers cluster at 54.5–57.0, so a cut at 55 rejected 4 of 6 and tripled time-to-graduation. 55 vs 57 is indistinguishable on 51 trades (+$2.15 vs +$2.23) while 57 keeps 25% more. **Do not relax past 57** — 58-60 is −$0.22/trade, 60-65 is −$0.25. |
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

## Six-Hour Experiment (started 2026-08-26)

**All nine coins run `SIX_HOUR` with SMA 10/40.** Began as a 4-coin arm with an
hourly control; the control was dropped same-day because it measured a config
already proven negative, halving the rate at which the open question resolves.
The 53 hourly trades on record are the baseline.

Rate: **0.92 entries/coin/month** after filters, measured over 375 days (RSI band
passes 48% of crossovers). Nine coins ≈ 8.3/month ≈ 40 trades in 5 months.

> An earlier estimate of 0.35/coin/month came from one quiet 120-day window and
> was 2.6× too pessimistic. **Measure rates over the longest history available**,
> not over whatever window a backtest happened to use.

**Every hourly SMA window is negative out-of-sample.** Six-hour shows a plateau
(8/30 through 15/45 all positive, peak 10/40–12/40). Daily alternates sign =
noise. RSI mean-reversion looked strong on hourly (67% win rate) but is the
worst variant on daily — also noise. Run `compare_strategies.py` to reproduce.

The learner **only ever searched inside the hourly row**: granularity is not in
its parameter space, and `SIX_HOUR` was silently backtested as `ONE_HOUR` until
the GRANULARITY_MAP fix. It could not have found this. When a search is
exhaustive and finds nothing, question the box before questioning the contents.

Six-hour is ~14× slower (0.23 vs 3.24 entries/day; ~27 months to 20 trades/coin).
Judge that arm on per-trade quality, not volume.

**Buy-and-hold still beats it** (+$11.39 vs +$4.35 over the same window). Six-hour
is where this strategy stops destroying value, not where it wins.

## Which bots exist — `utils/registry.py`

**PM2 is the single source of truth.** `registry.bots()` discovers the fleet and
every consumer reads it: the dashboard, `auto_learner.COINS`,
`compare_strategies.PAIRS`, `stock_learner`. Hardcoded lists remain only as
fallbacks for when pm2 is unreachable.

Adding a bot by any route makes it appear everywhere at once. Nine stock bots
added on 2026-08-26 were invisible on the dashboard because its `BOT_MAP` was
hardcoded and nobody remembered to edit it — the failure was silent, which is
the whole problem with a fixed list.

**Identify bots by SCRIPT, never by environment variable.** Stock bots inherit
`TRADING_PAIR` from `.env` when launched from a shell that loaded it, so keying
on env vars made all nine report as BTC-USD. `bot.py` means crypto,
`stock_bot.py` means stocks.

`registry.audit()` reports drift between the running fleet and `.env`; it runs
automatically on `add_stock.py --list`.

## Architecture: what is shared, what is not

`utils/venue.py` is the boundary. Above it everything is shared and must behave
identically for both markets; below it live the only genuine differences.

| shared (never fork per market) | venue-specific (`utils/venue.py`) |
|---|---|
| position accounting, stops, trailing | order submission |
| risk limits, cooldowns | response parsing |
| atomic state + backup (`utils/state.py`) | fee rate |
| order journal + reconciliation | market hours |
| graduation (`utils/graduation.py`) | |

**A new venue is a new adapter, not a new bot.** The two bots diverged in the
parts that protect capital — the stock bot had no journal, no reconciliation, no
risk engine, and wrote state with a bare `json.dump` that corrupts on a crash
mid-write. That was drift, not design, and it was invisible because paper
trading never punished it.

Per-asset variation belongs in `.env` through the coin/ticker → profile → base
resolution. That is deliberately unconstrained, because volatility and liquidity
genuinely differ per asset. **Safety machinery must never vary per asset**,
which is why it sits above the boundary.

## Stocks vs Crypto — Different Economics

**Alpaca equities are commission-free.** Round-trip cost is slippage only
(~0.20%) against crypto's 1.20%. That single term flips which strategies work,
so **never transfer a crypto finding to stocks or the reverse.**

Measured across 15 symbols, both timeframes, out-of-sample:

| variant | Hour | Day | win rate |
|---|---|---|---|
| SMA 5/30 (what stock_bot runs) | +1.60 | +4.45 | 35–44% |
| **RSI mean-rev 30/70** | **+9.06** | **+13.39** | **66–75%** |
| RSI mean-rev 25/75 | +5.63 | +3.43 | 67–70% |
| RSI mean-rev 20/80 | +3.51 | +6.60 | 73–100% |
| buy and hold | +32.17 | +84.05 | — |

All four mean-reversion thresholds are positive on **both** timeframes — a
plateau, unlike crypto where the winner flipped between timeframes (noise).
Mean reversion is clearly the better class here, and `stock_bot.py` does not
implement it; it is hardcoded to SMA crossover.

Buy-and-hold still beats everything, but the sample covers a strong bull market
(13/15 symbols positive holding).

**Live config: `STOCK_STRATEGY=RSI_MEAN_REVERSION` 30/70, SL 5% / TP 8%,
all three tickers.** Switched 2026-08-26.

**Stops must be wide for mean reversion.** Its premise is that the dip
finishes and reverses, so a tight stop exits before that happens. Measured
across 15 symbols out-of-sample:

| SL / TP | net | win% |
|---|---|---|
| 2.0 / 4.0 (SPY's old setting) | +0.49 | 44% |
| 3.0 / 6.0 | +6.22 | 58% |
| 5.0 / 8.0 (chosen) | +8.47 | 64% |
| 6.0 / 10.0 | +8.66 | 67% |
| 8.0 / 12.0 | +7.30 | 68% |

Plateau from 4.0 to 8.0; 5.0/8.0 sits inside it rather than on the peak. The
old per-ticker stops were tuned for crossover and are commented out, not
deleted. **A stop tuned for one strategy class can be actively harmful to
another — re-derive it when changing class.**

`stock_bot.py` supports both classes via `STOCK_STRATEGY` /
`<TICKER>_STRATEGY`: `SMA_CROSSOVER` (code default) or `RSI_MEAN_REVERSION`. Stops,
targets, trailing and risk limits are shared; only entry and strategy-exit
differ. An unknown value refuses to start rather than defaulting.

`stock_learner.py` runs nightly inside auto_learner and is included in the
emailed report. It evaluates both classes per ticker, requires the winner to
beat buy-and-hold AND to be corroborated by neighbouring settings (the
lucky-cell guard the crypto grid search lacked), and is advisory only.

Add or remove tickers with `add_stock.py`; it validates tradability, checks
history, and backtests before registering anything. Tickers live in
`STOCK_WATCHLIST`.

## Candidate Screening

`screen_candidates.py --market {stocks,crypto} --json` ranks symbols on
**mechanism fit**, not recent returns, and writes `logs/screen_<market>.json`
for the dashboard. Runs nightly under PM2 cron.

Mean reversion needs a symbol to reach oversold **and then recover**. One that
dips often without recovering is a downtrend — high entry frequency, and the
exact failure mode. That gets an explicit `avoid` verdict rather than a high rank.

First run: **stocks 12/41 qualify, crypto 2/18.** Same screen, same strategy;
the difference is the 1.207% fee against commission-free.

Two currently-traded symbols land in `avoid`: **QQQ** (13 dips, −0.13) and
**SUI-USDC** (−1.39). Worth reviewing before adding more of the same.

Verdicts: `check` clears everything · `weak` marginal · `skip` too few entries ·
`avoid` dips without recovering.

## Graduation (sim -> live)

Defined once in `utils/graduation.py`. Five criteria, **all must pass**; the
score out of 100 is display only.

| Criterion | Threshold | Weight |
|---|---|---|
| Completed trades | ≥ 20 | 25 |
| Win rate | ≥ 55% | 25 |
| Net P&L | > $0 | 25 |
| Worst single trade | ≥ −$2.50 | 15 |
| Per-trade Sharpe | ≥ 0.35 | 10 |

**This was three inconsistent implementations until 2026-08-26.**
`check_graduation` gated promotion on four criteria, `sim_report.py` displayed a
five-criterion score in the nightly email, and the dashboard counted four — so
one coin showed two different readiness figures, and the gate that actually
promotes was *weaker* than the score shown.

The Sharpe check was also broken: it multiplied by `sqrt(252)`, the annualisation
factor for daily returns, against per-trade P&L on positions held ~20 hours.
That inflated every value 15.87x, so it passed whenever the mean was positive —
duplicating the net-P&L criterion. The threshold of 0.35 is derived from the
observed win/loss distributions: simulated 20-trade coins meeting the other four
criteria have a median raw Sharpe of 0.515 and a 10th percentile of 0.270, so
0.35 rejects the lumpiest fifth without blocking the median.

**Never add a graduation criterion outside `utils/graduation.py`.**

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
