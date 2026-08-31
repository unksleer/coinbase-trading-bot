# Decision log

Every change, the reason for it, and what the numbers actually said — including
where earlier entries in this file were wrong. Newest first.

The purpose is not a changelog. It is to stop the same ground being re-covered:
several decisions here reversed earlier ones, and in at least two cases the
reversal happened because a claim was checked rather than repeated.

**Conventions.** A claim with no interval attached is a point estimate and
should be read as one. "Measured" means from live trades; "backtested" means
from historical simulation, which is weaker. Effective trades ≠ pooled trades.

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
