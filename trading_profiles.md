# Bot Trading Profiles

A profile is a named set of strategy parameters selected by `ACTIVE_PROFILE` in
[.env](file:///home/leeunks/coinbase-trading-bot/.env), or per coin via
`<COIN>_ACTIVE_PROFILE`.

**Five profiles are defined:** `CONSERVATIVE`, `SIM`, `AGGRESSIVE`, `HYPER`, and
`STOCK`. The three that drive the live crypto bots are documented below; `HYPER`
is a scalping experiment and `STOCK` applies to the Alpaca stock bots, which
trade commission-free and therefore need different thresholds.

**Current assignment:** all nine crypto bots run `SIM` via
`<COIN>_ACTIVE_PROFILE=SIM`. `ACTIVE_PROFILE=CONSERVATIVE` remains the default
for anything unassigned, and is the profile intended for live trading — it is
deliberately left strict and is not currently driving any bot.

BTC, ETH and NEAR were moved from `CONSERVATIVE` to `SIM` on 2026-08-24. Under
`CONSERVATIVE` they were taking 0, 0 and 1 entries per 1500 candles: the 0.25%
separation floor and the EMA200 trend filter compound, and neither alone was the
blocker. Dropping both raises them to 18, 21 and 23 entries — roughly one every
three days.

### All nine coins on hourly (2026-08-30) — reversing the six-hour move

Every coin runs `ONE_HOUR` candles with `SMA 10/40`.

**This was reversed with eyes open, and the backtest argues against it.** The
table below still stands: hourly is negative at every SMA window tested, and
six-hour showed a seven-cell plateau. That is a structure argument, not a lucky
cell, and it is the stronger *profitability* evidence.

What it could not survive was the evidence rate. Measured over 100 days of
history and confirmed against six live days:

| | fleet trades/month | per coin | time to 20 trades/coin |
|---|---|---|---|
| `SIX_HOUR` | 14.4 | 1.6 | ~12.5 months |
| `ONE_HOUR` | 90.0 | 10.0 | ~2 months |

In six live days the nine six-hour bots produced **one** completed trade (NEAR,
−$0.20), and zero BUY signals in the 99 candle-observations after the 2026-08-27
restart. Nothing was being filtered; the entry does not fire often enough on a
40-candle average spanning ten days.

The honest statement of the trade: **hourly is expected to lose slowly.** The
1.207% fee floor is unchanged and every backtest window says negative. What it
buys is a real answer in two months instead of twelve, in paper, where a losing
trade costs nothing and an unanswered question costs everything. If hourly
proves negative on 20 real trades per coin, that is a verdict — which is more
than six-hour was on track to deliver by 2027.

Revisit once ~20 hourly trades per coin exist. If they confirm the backtest,
crypto is settled and the capital question moves to stocks.

#### The six-hour case, kept for the record

This started as a four-coin arm with five held on `ONE_HOUR` as a control. The
control was dropped the same day: it measured a configuration already shown
negative at every parameter, so it confirmed a known answer while halving the
rate at which the open question gets answered. The 53 completed hourly trades on
record are the historical baseline instead.

Every hourly SMA window tested is negative out of sample. Six-hour shows a
plateau — `8/30` through `15/45` all positive, peaking at `10/40`–`12/40` — and
seven adjacent cells agreeing is structure rather than a lucky cell:

```
              5/30   8/30   8/35  10/35  10/40  12/40  12/45  15/45  20/60
ONE_HOUR      -9.2   -2.4   -4.6   -3.0   -5.3   -5.7   -4.5   -2.2   -4.2
SIX_HOUR      -0.3   +0.6   +1.1   +1.6   +3.8   +4.1   +3.4   +2.3   -1.9
ONE_DAY       -0.9   -6.3   +2.2   -1.7   +2.8   +3.4   +0.4   -0.8   -0.2
```

Reproduce with
[compare_strategies.py](file:///home/leeunks/coinbase-trading-bot/compare_strategies.py).

**Expected rate**, measured over 375 days rather than extrapolated from one
window: **0.92 entries per coin per month** after filters (the RSI band passes
48% of crossovers). Across nine coins that is ~8.3/month — about 40 trades in
five months.

An earlier estimate of 0.35/coin/month came from a single quiet 120-day
out-of-sample slice and was roughly 2.6× too pessimistic. Measure rates over the
longest history available, not over whichever window a backtest happened to use.

Judge on per-trade quality and on whether net turns positive, not on volume.
This is ~6× slower per coin than hourly by design — frequency traded for moves
large enough to clear the 1.207% round trip.

> Six-hour buy-and-hold returned +$11.39 over the same window against the
> strategy's +$4.35. Six-hour is where this strategy stops destroying value, not
> yet where it beats doing nothing.

### The other five run identical parameters

Per-coin overrides for `SMA_SHORT_WINDOW`, `SMA_LONG_WINDOW`, `STOP_LOSS_PERCENT`
and `TAKE_PROFIT_PERCENT` were commented out on 2026-08-24. They were hand-set
rather than learner-derived — the learner has never written to `.env`, having run
in `ADVISORY` mode throughout.

Nine coins on identical settings are nine clean samples of one strategy. Nine
coins on different settings are one sample each of nine strategies: if SOL
outperforms SUI you cannot tell whether that is the coin or the fact that SOL ran
`15/60` while SUI ran `5/30`. During a learning phase, comparability is worth
more than per-coin tuning fitted on a handful of trades.

All nine now trade `$10` on `SMA 5/30`, `SL 5%`, `TP 8%`, `RSI 30–55`. The old
values remain in `.env` as comments, and per-coin tuning is worth revisiting once
the strategy has a proven edge to tune.

---

## 1. Parameters Table

Values reflect [.env](file:///home/leeunks/coinbase-trading-bot/.env) as configured.
`—` means the key is unset, so the code default applies.

| Parameter Key | Conservative | Sim | Aggressive | Purpose & Behavior |
| :--- | :---: | :---: | :---: | :--- |
| **`SMA_SHORT_WINDOW`** | `5` | `5` | `10` | Short-term SMA period, in candles. |
| **`SMA_LONG_WINDOW`** | `30` | `30` | `30` | Long-term SMA period, in candles. |
| **`SMA_MIN_SPREAD_PERCENT`** | `0.25` | `0.0` | `0.0` | Minimum separation between the two SMAs for a BUY to pass. `0` disables. See the warning below. |
| **`SMA_MAX_SPREAD_PERCENT`** | `—` | `—` | `—` | Maximum separation at entry. Rejects a crossover that has already extended, where too little travel remains to clear the round-trip fee. BUY only. `0` disables. |
| **`RSI_FILTER_ENABLED`** | `True` | `True` | `False` | Activates the RSI band filter. |
| **`RSI_PERIOD`** | `14` | `14` | `14` | RSI lookback window. |
| **`RSI_BUY_MIN`** | `40` | `30` | `45` | Blocks a BUY when RSI is below this. |
| **`RSI_BUY_MAX`** | `57` | `57` | `60` | Blocks a BUY when RSI is above this. `70` → `55` on 2026-08-24, relaxed to `57` on 2026-08-26; see §7. |
| **`RSI_SELL_MIN`** | `40` | `40` | `40` | Blocks a SELL when RSI is below this, to avoid selling into an oversold dip. |
| **`EMA_TREND_FILTER_ENABLED`** | `True` | `False` | `False` | When `True`, a BUY requires price above the long-term EMA. |
| **`EMA_TREND_PERIOD`** | `200` | `200` | `200` | EMA trend filter window. |
| **`MIN_VOLUME_RATIO`** | `1.0` | `1.0` | `—` | Requires current volume at or above this multiple of the 20-period average. |
| **`STOP_LOSS_PERCENT`** | `5.0` | `5.0` | `5.0` | Sells if price falls this far below entry. |
| **`TAKE_PROFIT_PERCENT`** | `8.0` | `8.0` | `8.0` | Sells if price rises this far above entry. |
| **`BREAK_EVEN_TRIGGER_PERCENT`** | `1.5` | `1.5` | `1.5` | Once net P&L reaches this, arms a stop at the fee-adjusted break-even price. |
| **`TRAILING_STOP_ACTIVATION_PERCENT`** | `1.5` | `1.5` | `—` | Gain required before the trailing stop arms. |
| **`TRAILING_STOP_PERCENT`** | `1.5` | `1.5` | `—` | Distance the trailing stop sits below the peak. See §7. |
| **`STOP_LOSS_ATR_MULTIPLE`** | `—` | `—` | `—` | If > 0, replaces `STOP_LOSS_PERCENT` with `Entry − (Multiple × Entry ATR)`. |
| **`TAKE_PROFIT_ATR_MULTIPLE`** | `—` | `—` | `—` | If > 0, replaces `TAKE_PROFIT_PERCENT` with `Entry + (Multiple × Entry ATR)`. |

> **⚠ `CONSERVATIVE` cannot trade as configured.** The 0.25% separation floor
> combined with the EMA200 filter admits almost nothing — only 1 of 51 recorded
> sim entries ever had separation ≥ 0.25%. No bot uses this profile now, but
> loosen both before pointing anything at it. The nightly learner reports the
> condition explicitly when every parameter combination yields too few trades to
> evaluate.

---

## 2. The BUY Filter Chain

A bullish crossover must clear every filter, applied in this order
([strategies/simple_moving_average.py](file:///home/leeunks/coinbase-trading-bot/strategies/simple_moving_average.py)).
The first failure is recorded in `blocked_by`:

| Order | Filter | `blocked_by` |
| :---: | :--- | :--- |
| 1 | Separation below `SMA_MIN_SPREAD_PERCENT` | `SPREAD` |
| 2 | Separation above `SMA_MAX_SPREAD_PERCENT` | `SPREAD_MAX` |
| 3 | RSI outside `RSI_BUY_MIN`–`RSI_BUY_MAX` | `RSI` |
| 4 | Price at or below the EMA trend line | `EMA` |

Only the BUY path is filtered. A SELL is never blocked by an entry-quality
filter, since that could strand an open position.

Every decision, accepted or rejected, is written to
`logs/signal_log_<pair>.csv`. The dashboard's Signal Funnel aggregates
`blocked_by` across pairs, which is what makes an over-tight filter visible —
in the trade ledger it is indistinguishable from a quiet market.

---

## 3. Coin-Specific Overrides

Resolution order, implemented by `get_env_with_profile` in
[config/settings.py](file:///home/leeunks/coinbase-trading-bot/config/settings.py):

1. `<COIN_PREFIX>_<KEY>` — e.g. `SOL_SMA_SHORT_WINDOW`
2. `<ACTIVE_PROFILE>_<KEY>` — e.g. `SIM_SMA_SHORT_WINDOW`
3. `<KEY>` — the bare key
4. The code default

The coin prefix is taken from `TRADING_PAIR` (`SOL` from `SOL-USDC`). The same
order is mirrored in `auto_learner.py` and `dashboard.py`, so all three read
identical values for a given coin.

```ini
# Custom overrides for SOL-USDC
SOL_SMA_SHORT_WINDOW=15
SOL_SMA_LONG_WINDOW=60
SOL_TAKE_PROFIT_PERCENT=12.0
```

---

## 4. ATR-Based Risk Management

Optional volatility-scaled thresholds replacing the static percentages:

* **ATR Stop Loss:** triggers below `Entry − (STOP_LOSS_ATR_MULTIPLE × Entry ATR)`
* **ATR Take Profit:** triggers above `Entry + (TAKE_PROFIT_ATR_MULTIPLE × Entry ATR)`

The ATR at entry is stored in `entry_indicators` in the state file, so the
thresholds stay fixed for the life of the trade rather than moving with
volatility.

---

## 5. Switching Profiles

```ini
# Options: CONSERVATIVE, SIM, AGGRESSIVE, HYPER, STOCK
ACTIVE_PROFILE=SIM
```

Settings are read at startup, so a change requires a restart:

```bash
.venv/bin/python restart_bots.py
```

Use this rather than `pm2 restart all`. It restates each bot's identity and
verifies it afterwards; a restart that carries .env into the process
environment gives every bot the same `TRADING_PAIR`, which on 2026-08-27 put
eight crypto bots on BTC-USDC within one second without any error being
raised.

---

## 6. Hardened Risk Controls

**Every limit is enforced per bot**, against that bot's own state file
([utils/risk_engine.py](file:///home/leeunks/coinbase-trading-bot/utils/risk_engine.py)
reads `state["trades"]`). There is no cross-bot aggregation, so the portfolio
figure is the per-bot value × the number of running bots — nine currently.

| Setting | Per bot | × 9 bots | Rationale |
| :--- | :---: | :---: | :--- |
| `MAX_VAL_PER_ORDER` | `$15` | — | Above `TRADE_AMOUNT_USD=10` so it never binds, but a mistyped trade amount cannot place a large order. |
| `MAX_EXPOSURE_PER_PRODUCT` | `$15` | `$135` | One position per coin at a time. |
| `MAX_DAILY_LOSS` | `$3.00` | `$27` | Worst coin-day on record is −$0.63, so ~5× headroom. |
| `MAX_TRADES_PER_DAY` | `6` | `54` | Churn circuit breaker. Busiest coin-day on record is 3 trades. |
| `CONSECUTIVE_LOSS_COOLDOWN_TRADES` | `3` | — | Halts buying for 3 hours after three straight losses. |

**Scale of risk at current sizing:** all nine holding simultaneously deploys
`$90`. If every one stopped out at once — which has never happened — the loss is
about `$5.59` (each position: `$0.50` stop + `$0.12` fees). Catastrophic loss is
not the live risk at this size; fee drag is.

### Scale-up rule

Fees are a percentage of notional, so raising `TRADE_AMOUNT_USD` multiplies gross
and fees equally. Scaling a negative-expectancy system loses money faster, not
slower. Do not increase it until, on the **current** configuration:

1. 30+ completed trades since the last parameter change
2. Net P&L positive across those trades
3. Positive on a majority of coins, not carried by one
4. The learner graduates at least one coin

Then raise in steps — `$10 → $25 → $50` — re-verifying all four at each step, and
raise `MAX_VAL_PER_ORDER`, `MAX_EXPOSURE_PER_PRODUCT` and `MAX_DAILY_LOSS`
alongside it.

| Trade size | Fee/trade | 100 trades | Worst-case day (9 coins) |
| :---: | :---: | :---: | :---: |
| `$10` | `$0.12` | `$12` | `$5.59` |
| `$50` | `$0.60` | `$60` | `$27.93` |
| `$100` | `$1.21` | `$121` | `$55.86` |

Other settings available per profile and per coin prefix:

* **`MAX_VAL_PER_ORDER`** — maximum quote value per single order
* **`MAX_EXPOSURE_PER_PRODUCT`** — maximum holding value per product
* **`MIN_QUOTE_RESERVE`** — minimum quote balance held back
* **`MAX_TRADES_PER_DAY`** — trade cap per 24 hours
* **`CONSECUTIVE_LOSS_COOLDOWN_TRADES`** — losses that trigger a cooldown
* **`CONSECUTIVE_LOSS_COOLDOWN_SECONDS`** — cooldown duration
* **`REPEATED_API_ERROR_LIMIT`** — Coinbase error circuit breaker
* **`STALE_MARKET_DATA_CUTOFF_SECONDS`** — market data expiry

> **Removed:** `MIN_BULLISH_MARKET_RATIO` gated buys on a market-breadth ratio
> read from `logs/market_metrics.json`. That file had gone 34 days without an
> update, pinning the ratio at `1.0` against a `0.35` threshold, so the gate had
> never once blocked a trade. It was removed rather than left implying a
> protection that was absent. The scanner now runs hourly under PM2 cron.

---

## 7. Why the Fee Floor Drives These Values

At Coinbase's tier-1 maker rate of 0.60% per side, a round trip costs about
**1.2% of notional**. Measured across 51 sim trades the realised drag was
**1.207%**. Because the fee is a percentage, trade size does not change it —
scaling up multiplies gross and fees equally.

Two parameter choices follow directly:

**`RSI_BUY_MAX` is 57, not 70.** Entries above RSI 55 lost $4.11 across 24
trades at a 12% win rate, and follow-through fell monotonically as entry RSI
rose. `RSI_BUY_MIN` was deliberately left alone — the evidence for an upper
bound is 24 trades, for a lower bound only 6.

The ceiling was first set to 55, then relaxed to 57 two days later. Live
crossovers cluster tightly around the threshold — of six observed, the RSI
values were 54.5, 54.9, 55.1, 56.0, 56.7 and 57.03 — so a cut at 55 rejected
four of six, including one by a tenth of a point. Entry rate fell from ~3.1 to
~1.2 per day, pushing time-to-graduation from 1.9 to 5.2 months.

On the 51-trade history, ceilings of 55 and 57 are statistically
indistinguishable (net +$2.15 vs +$2.23) while 57 keeps 25% more trades. Paying
a 3× slowdown in data collection for a threshold difference the evidence cannot
resolve was the wrong trade. 57 admits five of the six observed crossovers.

Bands above 57 are progressively worse per trade — 58-60 at −$0.22 and 60-65 at
−$0.25 — so do not relax further without new evidence.

**The trailing stop must exceed one candle of noise.** Hourly ATR runs 1.4–2.2%
of price, so a trail tighter than that fires on noise, while a wider one needs
the peak to clear roughly `trail + 1.2%` before it can lock in a profit. The
previous `3.0%` trail combined with a `2.0%` activation required a ~4.3% peak to
break even and never once triggered in 51 trades.

Run [analyze_entries.py](file:///home/leeunks/coinbase-trading-bot/analyze_entries.py)
to re-derive these against current data:

```bash
.venv/bin/python analyze_entries.py
```

> These values were fitted on 19–27 trades and are a hypothesis under forward
> test, not a validated edge. The learner's graduation gate — 20+ trades, ≥55%
> win rate, positive net P&L, worst trade ≥ −$2.50 — is the check that matters
> before trading live.
