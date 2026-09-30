# Decision log

Every change, the reason for it, and what the numbers actually said — including
where earlier entries in this file were wrong. Newest first.

The purpose is not a changelog. It is to stop the same ground being re-covered:
several decisions here reversed earlier ones, and in at least two cases the
reversal happened because a claim was checked rather than repeated.

**Conventions.** A claim with no interval attached is a point estimate and
should be read as one. "Measured" means from live trades; "backtested" means
from historical simulation, which is weaker. Effective trades ≠ pooled trades.

**Entries are not rewritten.** Each records what was believed and measured at
the time, including figures later shown to be wrong — the 1.206% fee used
before 2026-09-02, for instance. A later entry corrects; the earlier one
stands, because a log that edits its past cannot be audited. Reference
documents (README, trading_profiles) are kept current instead.

---

## 2026-09-30 — Dashboard labelled Donchian bots as SMA crossover

**Found while answering "why are these holding so long".** The signal table
reported all nine crypto bots as `SMA_CROSSOVER` nine days after they were
switched to `DONCHIAN_BREAKOUT`. Cause: the crypto signal log has no
`strategy` column, so the dashboard read `None` and fell back to a default.
Invisible while every bot was `IN TRADE`; the moment one went flat it would
have been described in crossover vocabulary — `NO CROSS`, `BELOW` — for a
strategy that has no crossover.

**Fix:** one resolver, mirroring how each bot resolves its own strategy —
crypto through coin → active profile → base on `STRATEGY_TYPE`, stocks through
`<TICKER>_STRATEGY` then `STOCK_STRATEGY` — used by both dashboard paths so
they cannot disagree. The log column is a last-resort fallback only. A label a
bot never wrote is not a fact about the bot; its configuration is.

Verified live: 9 × `DONCHIAN_BREAKOUT`, 12 × `RSI_MEAN_REVERSION`.

**Also recorded:** the assistant initially read the Sep 21 configuration as an
unrecorded freeze violation before checking `git log`, which held the commit,
the registration and this file's entry the whole time. Same failure this log
has criticised in others — asserting before checking. Nothing was changed on
that basis.

---

## 2026-09-21 — Crypto restarted on a breakout class; stock exits left alone after a sweep

**The stock question first.** Nine live trades since 2026-09-04 read as small
wins and large losses (avg win +$1.00, avg loss −$3.55), and the earlier entry
in this file called that a structural defect. `backtest_stock_exits.py` held
the RSI 35/65 entry fixed and varied only the exits over 400 days of hourly
bars on the 12-ticker basket:

| Exit structure | Trades | Win | Net | Last 40% |
| :--- | :---: | :---: | :---: | :---: |
| Live: 5% stop, 8% target, trail, RSI exit | 261 | 72% | +$64 | +$5 |
| 3% stop | 339 | 58% | +$9 | −$15 |
| 2% stop | 416 | 49% | −$2 | −$26 |
| 1.5% stop | 477 | 43% | −$22 | −$41 |
| Symmetric 2%/2% | 413 | 53% | +$5 | −$12 |
| Symmetric 1%/1% | 583 | 50% | −$76 | −$65 |
| Buy and hold | | | +$157 | −$18 |

Every step toward smaller, more frequent wins made it worse. The asymmetry is
how mean reversion pays: the wide stop is what lets a dip revert. **The
"structural defect" claim of 2026-09-21 (earlier today) is withdrawn.** The
live configuration is the best of the ten tested and the only one positive in
the down-market tail. Stocks: no change; the v3 registration runs on.

**Crypto: class change, not parameter change.** The SMA crossover won one
trade in five and could not clear the 1.0% round trip on any timeframe (see
2026-08-30). `backtest_crypto_classes.py` compared classes on the 517 stored
days, nine coins, $100 each, same costs, buy-and-hold as the bar (−$79 over
the window; folds +$281, −$210, −$344, −$61, +$441):

| Class | Trades | Win | Net | vs hold | Folds > hold |
| :--- | :---: | :---: | :---: | :---: | :---: |
| SMA 10/40 six-hour (was live) | 308 | 28% | −$219 | −$140 | 2/5 |
| RSI 30/70 six-hour | 214 | 24% | −$24 | +$55 | 3/5 |
| Donchian 20/10 daily | 99 | 30% | +$19 | +$98 | 2/5 |
| **Donchian 80/40 six-hour** | 98 | 31% | **+$78** | +$156 | 3/5 |
| Trend: above 50-day SMA, 2% band | 81 | 25% | +$15 | +$94 | 2/5 |
| Rotation: top 3 by 60-day, if positive | 31 | 42% | +$40 | +$119 | 2/5 |

Neighbours of 80/40 (60/30 +$62, 100/40 +$31, daily 15/7 to 25/12 all
positive) make it a plateau, not a tuned point. With the bot's break-even
invariant applied, 80/40 + 15% stop backtests at +$128, 50% win rate, 28-day
average hold. **Adding the SIM profile's 8% take-profit raises the win rate
to 60% and cuts the net to +$20** — the clearest evidence in this file that
the "consistent small wins" profile is what costs kill here.

**Deployed:** `strategies/donchian_breakout.py`, `BREAKOUT` profile in `.env`
with every exit knob set explicitly (SL 15, TP 0, trail 0, BE 0, no filters),
all nine coins switched to it, SMA-era state and trade CSVs moved to
`crypto_archive_before_breakout_20260921/`, fresh state at $1000 simulated
cash per coin, sealed as `2026-09-crypto-donchian-breakout` (stop 2027-01-15
or 20 effective trades, whichever is later). Win rate is explicitly not a
criterion; any TAKE_PROFIT or TRAILING_STOP exit in the logs means the profile
was changed and the run stops.

**Pipeline check on day one:** the nine bots' first closed-candle signals
(five BUY, four HOLD) match the channel computed from the candle store for
every pair. Five bots are long from the restart.

**Honest weaknesses.** Nothing beat holding in the rally folds, and the
choppy fold was negative for every class. This is a lose-less-in-crashes
profile, not a demonstrated edge; ~11 trades per coin per year means a small
edge cannot be confirmed for a long time. What it can do quickly is rule out
a disaster and show that live behaviour matches the backtest's trade rate.

---

## 2026-09-21 — School Run backtested on SPY: cost-dominated, not run as a paper bot

**What:** Tom Hougaard's School Run (buy stop above and sell stop below a
reference candle, stop on the far side, flat by the close) implemented as
`strategies/school_run.py` and backtested by `backtest_school_run.py` on the
501 stored SPY sessions, 2024-09-03 to 2026-09-02. The DAX 09:15–09:30 CET
candle is the second bar after the open, so the SPY reference is the
09:45–10:00 ET bar (`--ref-candle 1`); the opening bar was also run.

**What the numbers said (backtested, $100, 0.05% slippage per side):**

| Exit variant | Trades | Win | Net | PF |
| :--- | :---: | :---: | :---: | :---: |
| Breakeven at 1R, close at end of day (the rule) | 501 | 28% | −$44.18 | 0.58 |
| No management, close at end of day | 501 | 34% | −$39.73 | 0.65 |
| Fixed 1R target | 501 | 42% | −$39.47 | 0.61 |
| Fixed 2R target | 501 | 34% | −$42.33 | 0.63 |
| Breakeven at 1R, trail 1R | 501 | 37% | −$42.85 | 0.58 |
| Buy and hold, same window | | | +$39.91 | |

Every fold of the five-fold walk-forward is negative. Opening bar as reference:
−$43.14. With slippage set to zero the same variants net +$5.57 to +$10.37
(gross edge ≈ +0.02% per trade); breakeven sits at roughly 1 basis point per
side. The reference candle is a median 0.22% wide and breaks in every session,
so this is a coin flip with a 0.10% toll, not a rule with an edge that costs
ate. No exit variant changes the sign.

**Decision:** not promoted to a paper bot. A paper run yields one trade per
session and would take a year to say what the store said in a minute.

**Also today:** the 09:00 daily PnL email cron entry is commented out (not
deleted) in the user crontab. The dashboard is the record while everything is
paper; the email added nothing it does not show. `EMAIL_NOTIFICATIONS_ENABLED`
and per-trade notifications are untouched.

---

## 2026-09-14 — Operational pass from an outside review

An external review (ChatGPT Astra) of the repo produced seven operational
findings and one analytical claim. **Six of seven findings were correct**; the
analytical claim was wrong. The four findings that could not affect trading
behaviour were fixed here; the rest wait.

### The wrong claim, because it is instructive

> "Crypto is slightly negative even before fees."

Gross P&L summed in dollars is −$0.81 — one $100 trade (SOL, −$5.02 gross)
against 55 trades at $10 (+$4.21 gross). The size-invariant gross is **+0.662%
per trade**, positive. The review summed dollars across the 2026-09-04 size
change, which is the exact defect the graduation gate was converted away from
on the same day. Its conclusion — no demonstrated edge — happens to be right,
for the reason it did not give: the interval includes zero. A reviewer
without this log in front of it will make that mistake.

### Fixed

1. **Runtime trade CSVs untracked.** 29 files — every `completed_trades_*.csv`,
   `trades.csv`, `trades_dry.csv`, and 17 `.lock` files that were gitignored
   but still tracked. The checkout was permanently dirty and trade data sat
   one `git add -A` from a commit. Backed up, `git rm --cached`, still on disk.
2. **SPY gap report was counting every night as a gap** — 502 gap runs across
   501 sessions. `gaps()` now takes a session key; only consecutive bars in
   the same session are compared. Real result: **2 intra-session gaps**, at
   **2024-12-24 13:30 ET and 2025-07-03 13:15 ET** — Christmas Eve and the day
   before July 4th, both **early-close days** (13:00 ET). Those are not
   missing regular-hours bars; they are *after-hours* bars the collector let
   through, because `in_regular_session` uses a fixed 15:45 close. So the
   report is now right and it exposed a smaller defect of the same family:
   early-close days are contaminated with sparse after-hours prints. Roughly
   four days a year; the opening range is unaffected. Needs the same market
   calendar. Recorded, not fixed. A whole missing weekday is reported as a
   count — 21 in the span, consistent with ~10 holidays a year — labelled
   "holiday or missed collection" because the two are indistinguishable
   without that calendar.
3. **Collector output was filed in the learner log.** `auto_learner` called
   `logging.basicConfig` at import, so six importers had their logging
   hijacked. Moved into `configure_logging()`, called only from `__main__`.
4. **Stale fee text** in README, SKILL.md and trading_profiles corrected to the
   0.40% maker rate. This log's past entries are deliberately left as written.

### Deferred, by choice

- 5 of 9 crypto bots carry a 04:00 pm2 cron restart overlapping the learner.
- 776 MB of pm2 logs, no rotation.
- `shell=True` in four scripts.

None affects trading. Next review point: **2026-10-04**.

---

## 2026-09-10 — The dashboard froze in the browser and said nothing

**Reported:** email said SOL had stopped out; the dashboard still showed it
`IN TRADE`. The server was right — state file `NONE`, sell in the CSV, API
returning `position=NONE`, dashboard running the 09-04 fix since the minute it
was committed. The browser was showing a render from before 12:37Z.

**Cause:** the page polls every 30 s, and on any failed fetch it silently
returns. The table keeps its last render and the `Updated:` stamp — set only on
success — freezes with it. Browsers also throttle or suspend timers in
background tabs. A tab left open across either shows hours-old positions
under a small timestamp nobody reads. Nothing on the page distinguishes
"current" from "frozen".

**Fix:** track the last successful update per panel; after three missed polls
the stamp becomes a visible warning with the true age; a failed fetch says so
instead of pretending; and the page re-fetches immediately when the tab
regains visibility.

**Pattern:** the fourth instance of a value whose source updates on a
different clock than the thing it describes — this time the clock was the
browser's own timer, and the display was the source.

---

## 2026-09-04 — The signal table showed positions up to six hours stale

**Reported:** ADA had sold but showed `IN TRADE`; BTC was in a trade but showed
`ENTERING`. Both on the same page as an `active_positions` panel that had them
right.

**Cause:** the signal table took `position` from the last row of the bot's
signal log. The bot writes that row only when a **closed candle** is
evaluated — on six-hour candles, four times a day. Trades happen *between*
closes, when the 60-second loop acts on the already-closed candle's signal.
So the row's `position` lags the truth by up to six hours. ADA sold at 14:48Z
and BTC bought at 14:12Z; the last signal-log row for both was logged at 12:00Z.

The `active_positions` panel was right because it reads **state files**
through `utils.positions`, and a state file is written on every trade.

**Fix:** both signal-table paths — the signal-log reader and the PM2-scrape
fallback — now take `position` from the state files. The signal log still
supplies indicators and `blocked_by`, which only change per candle anyway.

**Pattern, again:** a value derived from a record that updates on a different
clock than the thing it describes. Same family as the market-status echo
(2026-09-01) and the scanner price (2026-08-27).

---

## 2026-09-04 — Crypto to $100 as well; three dormant defects surfaced

**Why:** the operator will trade at $100 live and wanted paper at the same
scale. The stated reason for doing it despite crypto being parked — "these
little changes bring out former bugs" — was correct three times over.

### Required changes

- **Risk caps** `MAX_VAL_PER_ORDER` / `MAX_EXPOSURE_PER_PRODUCT` **15 → 150.**
  These govern crypto only and are **live-money guards**; the loud comment in
  `.env` says to re-check them before any bot runs with `DRY_RUN=False`.
- **Wallets** topped up **+$900 each** (not set to $1,000), preserving
  accounting continuity for ADA and BTC, which were `LONG`. Done with all nine
  bots stopped, since state files are written by running processes.
- **Restart at $100** via `restart_bots.py --crypto --set TRADE_AMOUNT_USD=100`.

### Defect 1 — the learner would have reverted the size

`COINS_FALLBACK` hardcodes `amount: 10` per coin and passed it straight to
`restart_bot()`. Granularity from the same dict was already resolved from
`.env`; amount was not. An `APPLY_APPROVED` learner run would have silently
shrunk every $100 bot back to $10 inside a parameter update. Now reads the
running process's `TRADE_AMOUNT_USD`, falling back only when pm2 is unreachable.

### Defect 2 — a per-coin override defeated process identity, and deleting it did not help

`.env:219 BTC_TRADE_AMOUNT_USD=10`. `get_env_with_profile` checks
`{COIN}_{KEY}` before the plain key, and the per-coin form is not in
`PROCESS_IDENTITY`, so it outranked the pm2 command line. **btc-bot was started
at $100 and ran at $10.**

Removing the line from `.env` **did not fix it** — the bot restarted and still
logged $10. `/proc/<pid>/environ` showed `BTC_TRADE_AMOUNT_USD=10` alongside
`TRADE_AMOUNT_USD=100`, plus **97 other `.env` keys** snapshotted at the bot's
original start during the 2026-08-27 identity incident. **pm2 stores the
environment a process was first started with and replays it on every
restart.** `override=True` overwrites keys `.env` has; it cannot delete keys
`.env` lacks. All nine bots carry the snapshot; only BTC's prefix matched.

Fixed in `env_loader`, which now **strips** any `*_{IDENTITY_KEY}` variant
after loading, wherever it came from. A test asserts a contaminated
environment resolves to the command-line value. A second test fails if a
per-coin identity override reappears in `.env`.

This also means every "restart picks up `.env`" claim since 2026-08-27 was
only true for keys `.env` *sets*. A key *removed* from `.env` stayed live in
every bot until this fix.

### Defect 3 — restart counters are not evidence after a stop

`pm2 stop` then `pm2 restart` does not increment `restart_time` or reset
`pm_uptime`; nine bots showed 13-hour uptimes seconds after restarting. The
counters I had been using as before/after proof are unreliable across a stop.
`/proc/<pid>/environ` is authoritative — a restarted process has a new PID and
its environ is fixed at exec — and is what verification uses now.

### Verified

All nine at $100 by process environ; ETH `$100` pre-flight passes where a `$15`
cap rejected it; ADA and BTC still `LONG` with original `holdings_base`; audit
clean; 22/22 online.

---

## 2026-09-04 — Paper position size $10 → $100 (stocks and FCR only)

**Why:** to see dollar figures at the intended scale. Percentage returns are
scale-invariant, so this changes no evidence; a $0.08 win at $10 and a $0.80
win at $100 are the same 0.8%.

**Crypto stays at $10.** It is parked, and its simulated cash sits at $88–100
per bot — a $100 order cannot be funded without editing state files under
running processes. Not worth it for a market that is not being tuned.

**Risk limits untouched.** `MAX_VAL_PER_ORDER` and `MAX_EXPOSURE_PER_PRODUCT`
are $15, which would block a $100 order — but the stock and FCR bots never go
through the risk engine (0 references), so the caps only govern crypto, which
is unchanged.

**Found while doing it:** the graduation gate's worst-trade floor was **−$2.50
in dollars**. That is a −25% trade at $10 (never triggers) and a −2.5% trade at
$100 (triggers on a normal stop) — the same rule, ten times stricter, depending
on a number unrelated to the strategy. Converted to **−2.5% of position**.

**Mechanics worth knowing:** `TRADE_AMOUNT_USD` is process identity, set on the
pm2 command line and preserved by `env_loader`, so editing `.env` alone changes
nothing. `restart_bots.py --set KEY=VALUE` now exists for exactly this.

**Cost:** both registrations voided and re-sealed as v3 / FCR-v2, starting
2026-09-04. Four days of the previous runs are discarded. Checkpoints move to
**2026-10-04** (stocks) and 2026-10-31 (FCR, unchanged).

---

## 2026-09-02 — Local candle store

**Why:** the exchange caps a response at ~2000 candles, and that cap flatters
results — six-hour gross edge read 1.71% over 83 days, 1.48% over 100, and
1.22% over 500 against a 1.206% fee. A store that appends each closed candle
accumulates history the API will never serve, and it compounds without further
work.

**Scope deliberately limited to collector, store and quality tests.** A larger
proposal included a feature pipeline and dataset builder for model training.
Declined for now: crypto's problem is a fee floor, which no model fixes, and
stocks have five completed trades — there is nothing to train on. Building the
consumer before the data exists is the pattern this project keeps repeating.

### Isolation

The store shares no code path with the bots. `bot.py` and `stock_bot.py` fetch
their own candles and do not import `market_data`; a test asserts it by parsing
their imports. The moment a bot reads from the store, a stale or gapped store
becomes a trading fault rather than a research inconvenience — and it would
void the pre-registered stock run, whose sealed config carries no data-source
field.

### Three invariants

| invariant | failure it prevents |
|---|---|
| only closed candles | a forming candle has a partial high/low/close revised later, so a backtest reads prices no trader could act on — and errs optimistically every time |
| no duplicates | a re-run or overlapping backfill double-counting a bar changes every average built on it |
| no silent revision | a venue returning different values for a stored timestamp is surfaced as a conflict, not absorbed |

Gaps are reported, never repaired. An outage and a venue halt need different
responses, and the store cannot tell them apart; inventing a bar to close a gap
puts fabricated prices into research.

### A bug worth recording

The first collector read the frame **index** instead of the `start` **column**,
producing timestamps of 0, 1, 2. **All 6318 were accepted** — because "has this
candle closed" is trivially true for 1970. An invariant checking only the near
edge of a range does not notice a value at the far end. Fixed, and a
plausibility floor added; the store now refuses anything before 2009 or more
than a day in the future.

### Result

498 days of six-hour history and 83 of hourly, across 9 pairs — 35,892 candles,
**zero gaps**. The unfinished-candle guard fires on every run, refusing exactly
one forming candle per series.

Collector runs hourly at :07 under pm2, `--no-autorestart`, writes only.

---

## 2026-09-01 — The short history window was flattering everything

Every crypto analysis in this project fetched **400 candles**. Coinbase serves
**2000**. That is 100 days at `SIX_HOUR` — and only **16.7 days** at
`ONE_HOUR`, which is what the timeframe comparison that parked crypto was
decided on.

### Re-run on the same 83-day window, both resolutions

| timeframe | candles | trades | net | gross/trade |
|---|---:|---:|---:|---:|
| `ONE_HOUR` | 2000 | 129 | −$16.55 | **1.07%** |
| `SIX_HOUR` | 333 | 13 | +$6.58 | **1.71%** |

The parking decision **holds** on 5× the data: hourly's average gross move
(1.07%) sits below the 1.206% round trip, six-hour's does not.

### But six-hour's margin shrinks as history grows

| window | candles | trades | net | gross/trade |
|---|---:|---:|---:|---:|
| 83 days | 333 | 13 | +$6.58 | 1.71% |
| 100 days | 400 | 19 | +$5.31 | 1.48% |
| **500 days** | **2000** | **116** | **+$2.84** | **1.22%** |

Against a 1.206% fee, the 500-day figure is a margin of **0.014%** — nothing.
Beat-hold is 3 of 9 coins.

**The earlier "six-hour clears the fee" result was a small-sample artifact.**
It did not overturn the decision to park crypto; it removed the last reason to
think crypto was close to working.

### Fixed

`MAX_CANDLES = 2000` in `auto_learner`, used by `walkforward.py`,
`analyze_correlation.py`, `add_crypto.py` and the learner (which was on 1500).
A test fails on any hardcoded candle count. `add_crypto --check` now backtests
16 out-of-sample trades where it saw 2.

Crypto correlation re-measured on the full window: **1.3 effective bets of 9**
(mean rho 0.769), down from 1.4.

**The general lesson, and it applies to the stock arm too:** the shorter the
history, the better a strategy looks. Any result should state its window.

---

## 2026-09-01 — A discretionary exit may not close at a loss

**Prompted by an intuition that turned out to be right:** "they enter fine and
exit too early." The mechanism was worse than early exit — part of the exit
logic was arithmetically incapable of profit.

### The trap

The trailing stop arms at `ACTIVATION` and exits `TRAIL` below the peak. On
crypto that is 1.5 / 1.5 against a **1.206% round trip**:

| peak reached | exits at | net after fee |
|---:|---:|---:|
| +1.5% | 0.0% | **−1.21%** |
| +2.0% | +0.5% | **−0.71%** |
| +2.7% | +1.2% | −0.01% |
| +4.0% | +2.5% | +1.29% |

**Break-even needs a peak above 2.71%, but the stop arms at 1.5%.** Every trade
peaking in between was a guaranteed net loss the moment it fired.

Stocks had the same shape at 1.0 / 1.5 on a **commission-free** venue: any peak
between +1.0% and +1.5% closed *below entry* — a losing exit where the venue
charges nothing.

Confirmed on 53 real trades: **22 cleared the fee at some point, only 10 closed
positive**, giving back **2.94%** from peak on average.

### The fix

An invariant, not retuned percentages:

> A discretionary exit — trailing stop or strategy signal — may not close a
> position at a net loss. Protective exits (stop-loss, ATR stop, break-even)
> are exempt; realising a loss is their purpose.

Retuning the two numbers would work until someone edits `.env`, and the trap
returns silently. The invariant holds for any values. Held-back exits are
logged, because a silently suppressed exit is indistinguishable from a bot that
stopped working.

### Walk-forward, 9 coins × 5 folds, 500 days

| exit rule | trades | net | beat hold |
|---|---:|---:|---:|
| current | 99 | −$2.64 | 17/45 |
| gate at break-even | 88 | −$2.42 | 17/45 |
| gate at 2× fee | 86 | −$1.52 | 17/45 |
| gate at 2.71% | 85 | −$1.13 | 17/45 |

Monotonic, which is the trustworthy shape — not a lucky cell. **It cuts the
loss by 57% and still does not cross zero, and beat-hold is unchanged at 17/45
in every variant.** Holding captures all of the underlying drift; gating
captures more of it, and still less than all.

### Two consequences to watch

- **Fewer trades, not more.** 99 → 85. Positions are held longer, so round
  trips are rarer. This slows evidence collection.
- **Losses get larger, not fewer.** Average loss moved −$0.409 → −$0.524.
  Small losses are converted into fewer, bigger ones capped by the 5% stop.
  If average loss grows faster than average win, this has become the
  loss-aversion trap and must be reverted. Recorded in the v2 registration.

### Also

Every six-hour analysis before today used 400 candles (100 days). Coinbase
serves **2000 (500 days)**. Prior six-hour conclusions rest on a quarter of the
available history.

The stock registration was **voided four days in** — it was measuring a
known-broken exit — and re-registered as `2026-09-stocks-mean-reversion-v2`,
stopping 2026-10-01.

---

## 2026-08-31 — Build the measurement layer

**Why.** Three months of tuning produced 56 paper trades and −$2.21, and no
statement about whether anything works. The bottleneck was never the strategy;
it was that the system could not tell truth from noise.

### What was added

| module | question it answers |
|---|---|
| `utils/benchmark.py` | Does this beat *not trading*? |
| `utils/evidence.py` | How much of this trade count is independent? |
| `utils/experiment.py` | Were the success criteria fixed before the result? |
| `walkforward.py` | Does it hold up across many windows, or one lucky split? |
| `analyze_correlation.py` | How many independent bets does the fleet hold? |

### What they immediately showed

**1. The strategy loses to simply holding.** Buy-and-hold is now a graduation
criterion, not a footnote — a strategy can pass every other check while being
worse than doing nothing.

```
crypto, strategy vs buy-and-hold over the same window
  LINK   +0.26%  vs  +44.15%     -43.90%   LOST
  SOL    +1.44%  vs  +36.23%     -34.78%   LOST
  XRP    -0.77%  vs  +29.24%     -30.01%   LOST
  ADA    -1.51%  vs  +24.37%     -25.88%   LOST
  beat buy-and-hold on 2 of 7 coins
```

SOL's +1.44% would have passed a positive-P&L check while holding made +36%.

**2. The trade counts overstate the evidence.** Correlation measured on price
returns, not trade outcomes — trades keyed by exit time rarely overlap, so a
trade-level correlation is undefined and reports perfect independence from
having no data.

```
stocks   12 symbols ->  4.9 effective (mean rho 0.13, max 0.90 SPY/QQQ)
crypto    9 symbols ->  1.4 effective (mean rho 0.70)
```

Crypto's nine bots are **one bet**. Its 53 trades are worth ~8.

**3. Walk-forward disagrees with the single split.** 12 tickers × 5 folds:

```
60 folds, 179 trades, net +2.68
profitable folds        36/60  (60%)
beat buy-and-hold       27/60  (45%)
mean excess vs holding  -0.95%
parameter churn         SL 0.52, TP 0.65  — unstable, fit is tracking noise
```

Marginally profitable, **loses to holding on average**, and its best parameters
change every window.

### Decisions

- Buy-and-hold added as a sixth graduation criterion. Weights rebalanced; an
  unmeasured benchmark **fails**, because "not measured" must never score the
  same as "beat the alternative".
- `sim_report` now grades on the same terms, so the emailed score cannot
  diverge from the enforced gate.
- The live stock run is pre-registered as `2026-09-stocks-mean-reversion` with
  sealed criteria and a stopping rule.
- `graduation.py` documents that it lacks the power to establish an edge.

---

## 2026-08-31 — Two overstated claims retracted

Both were mine, both were stated confidently, and neither survives its error bar.

**"Crypto has a positive gross edge."** 53 trades, mean gross +0.794%, but
σ = 4.258% and the 95% interval is **−0.353% to +1.940%** — it includes zero.
Confirming a 0.79% edge to ±0.20% needs ~435 trades, ~30 months at six-hour
rates.

**"Stocks have real out-of-sample support: +$3.65 over 127 trades."**
+$0.0333/trade, σ = $0.4487, 95% interval **−$0.045 to +$0.111**. Also includes
zero. Halving that bar needs ~2,789 trades.

**This indicts the gate.** Noise is 13× the effect:

| sample | 95% band | effect | |
|---:|---:|---:|---|
| 20 | ±$0.197 | $0.033 | invisible |
| 40 | ±$0.139 | $0.033 | invisible |
| 1000 | ±$0.028 | $0.033 | detectable |

A 20-trade win rate has a ±22 point interval: "55%" is indistinguishable from
anything between 33% and 77%. **Passing means "not visibly broken", not
"profitable".**

The useful formula: **n ≈ (1.96 / per-trade Sharpe)²**. At the gate's 0.35
that is ~31 trades. At the measured 0.074 it is ~700. *The goal is a higher
per-trade Sharpe, not more trades.*

---

## 2026-08-30 — Crypto parked (and an hourly detour reversed the same day)

**Moved to `ONE_HOUR`**, then **moved back**, inside a day. The reversal is the
useful part: the change was made to raise the evidence rate without first
checking whether hourly *could* clear costs.

```
timeframe   best gross/trade   fee round trip   net/trade
ONE_HOUR          1.17%            1.206%         -0.14%
SIX_HOUR          2.59%            1.206%         +1.39%
```

At hourly the average gross move is **smaller than the fee**. Removing the
early exit, widening stops and raising targets were all tested — selecting
in-sample, reporting out-of-sample — and every variant came out negative.

**The trap:** the timeframe fast enough to verify cannot clear the fee; the one
that clears the fee needs ~12 months to verify.

The fee lever is spent — the bot already sends `post_only` maker orders and
0.6%/side *is* the maker rate. Lower tiers need ~$10k monthly volume against
~$900 actual. Break-even needs **0.397%/side**, a 34% reduction.

**Parked**, not deleted: bots keep running on six-hour at no cost, nothing is
re-tuned. Re-open only if the economics change.

One hypothesis recorded untested: 41 of 53 trades exited on `SMA Bearish Cross`
averaging **−1.62%**, while 7 take-profit exits averaged **+9.07%**. The early
exit may be cutting off the winners that pay for the fee. Rests on 10–18
out-of-sample trades — too thin to act on.

---

## 2026-08-30 — Pooled graduation view

Per-symbol progress needed 20 trades on one ticker, so the number never moved.
The strategy is what is under test, so pooled is the honest unit. Markets stay
separate: crypto pays 1.2% and stocks pay nothing.

*Superseded next day:* pooled counts overstate evidence ~2.4× for stocks and
~6× for crypto. Effective counts now shown alongside.

---

## 2026-08-30 — A stock exit fix that was measured, then not made

Proposed per-ticker exits after computing that SPY's 8% take-profit was ~121
trading days away. **Both halves were wrong.**

The volatility math ignored the trailing stop and RSI exit, which close
positions far sooner — `SL5/TP8` produces 6–17 trades per ticker.

The first grid search *looked* excellent, because it searched 30 combinations
and reported the winner **on the data used to pick it**. Chosen honestly:

```
tuned per ticker   164 OOS trades   +$3.33
current SL5/TP8    127 OOS trades   +$3.65
```

Tuning was **worse**. No change made. The near-miss is why pre-registration and
walk-forward now exist.

---

## 2026-08-27 — All nine crypto bots were trading BTC

A `pm2 restart --update-env` from a shell holding `.env` replaced eight bots'
identities with `.env`'s single `TRADING_PAIR` in under a second. All wrote the
same state file. **The advice that caused it was documented in this repo.**

Nothing errored. The only symptom was per-coin trade files that stopped
growing — indistinguishable from a quiet market. Found only because an
unrelated script asked the registry which coins were running and got `BTC-USDC`
nine times. Caught after 36 minutes; no trades lost, which was luck.

Three layers: `utils/identity.py` (bots refuse to start on a name/symbol
mismatch), `registry.audit()` (drift in already-running bots, surfaced on the
dashboard and in the nightly email), and `restart_bots.py` (the only restart
path that restates identity and verifies it).

---

## Standing rules

1. **Never report a result on the data used to select it.** Choose in-sample,
   report out-of-sample, or walk forward.
2. **Beating zero is not the bar.** Beating buy-and-hold is.
3. **Count effective trades, not pooled trades.**
4. **Register criteria before the run.** A change mid-run voids it.
5. **"Not measured" never scores as "passed".**
6. **Optimise per-trade Sharpe**, not net P&L — it decides whether you can
   ever know.
7. **Restart with `restart_bots.py`.** Never `pm2 restart --update-env`.
