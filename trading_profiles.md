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
three days. Their coin-specific SMA windows, stops and targets are unaffected,
since coin keys outrank profile keys.

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
| **`RSI_BUY_MAX`** | `55` | `55` | `60` | Blocks a BUY when RSI is above this. Lowered from `70` to `55` on 2026-08-24; see §7. |
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
pm2 restart all
```

---

## 6. Hardened Risk Controls

Available per profile and per coin prefix:

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

**`RSI_BUY_MAX` is 55, not 70.** Entries above RSI 55 lost $4.11 across 24
trades at a 12% win rate, and follow-through fell monotonically as entry RSI
rose. `RSI_BUY_MIN` was deliberately left alone — the evidence for an upper
bound is 24 trades, for a lower bound only 6.

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
