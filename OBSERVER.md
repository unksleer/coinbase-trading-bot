# The observer, and the agent it is not yet

Two phases. The first exists; the second is specified here so it can be built
against a record rather than an intention.

## Phase 1 — observe only (live from 2026-09-30)

`observer.py` runs hourly under pm2 (`observer`, cron `12 * * * *`). It reads
and reports. It cannot act, and a test proves it: the file imports nothing
that restarts a bot or writes configuration, and contains no call that could.

### What it checks, every hour

| check | source | fires as |
|:--|:--|:--|
| each registration's **stopping rule** | date and effective-trade count parsed from the sealed prose | `stop` |
| Donchian **watch-for (1)** — any take-profit or trailing exit | crypto trade ledgers | `stop` |
| Donchian **watch-for (2)** — average loss beyond 10% | crypto trade ledgers | `warn` |
| stocks **watch-for** — average loss exceeds average win | stock trade ledgers | `warn` |
| FCR **watch-for** — rejection mix drifts >20 points from the 501-session backtest | `logs/fcr_signals_spy.jsonl`, last decision per session | `warn` <20 sessions, `stop` after |
| fleet health | `registry.audit()` | `warn` |
| positions held past 2× the strategy's expected window | state files | `info` |
| progress toward each stopping rule | ledgers × correlation discount | `info` |

**Effective, not pooled.** Trade counts are discounted by the correlation
cache (`logs/correlations.json`): 12 stock tickers count as ~4.9, nine crypto
coins as ~1.3. A registration's "20 effective trades" means 20 after that.

### Where it reports

- **Dashboard** — the Observer panel, every run, sorted stop → warn → info,
  with the age of the last run. A panel that has not been written in three
  hours flags itself; "found nothing" and "did not run" must never look alike.
- **Email** — only a `stop`: a registration's own rule has fired. One email
  per stop per 24 hours. Nothing else emails; the operator reads the
  dashboard.
- **Audit** — `logs/observer.jsonl`, one row per run, whether or not anything
  fired.

### What it will never do

Write `.env`. Touch a state file. Restart, stop or reconfigure a bot. Change a
parameter. These are not policies; they are the absence of code paths, pinned
by `tests/test_observer.py`. Phase 2 capabilities must live in a different
file so this one stays provably inert.

## Phase 2 — act within a sealed registration (not built)

**Gate to begin building:** one registration has passed its own graduation
gate — all six criteria, including *beats buy-and-hold*, on effective trades.
Until then there is nothing for an agent to optimise, and an agent optimising
a strategy with no demonstrated edge will find noise and call it progress.

### What "act" would mean

A separate process, `agent.py`, that may **apply** a change — and only a
change that has already cleared every check the observer and the learner run
by hand today:

1. **Objective:** pooled expectancy after costs minus buy-and-hold, on
   effective trades. Never win rate. A rising win rate with falling net is the
   signature of tightened exits and is treated as a regression.
2. **Evidence floor:** no change is considered before the current
   registration reaches its effective-trade count. Calendar time is not
   evidence.
3. **Candidate validation:** walk-forward across ≥5 chronological folds, and
   neighbour agreement — adjacent parameter values must also improve. A
   single good cell is a lucky cell.
4. **Change budget:** one parameter change per registration period. Each
   change voids the running registration and seals a new one, automatically,
   with the reasoning recorded in `DECISIONS.md` by the agent.
5. **Kill switch:** drawdown beyond a sealed limit stops new entries. The
   agent may pull it; only a person may release it.
6. **Paper only:** `DRY_RUN` / `ALPACA_PAPER` remain required and are never
   written by the agent. Live is a human action through `trade.sh`.
7. **Autonomy is earned:** the first version proposes and waits for approval
   (`LEARNER_MODE=APPLY_APPROVED`, which exists). Unattended application is
   enabled only after a full registration cycle where every proposal would
   have been approved.

### What already exists toward it

`auto_learner.py` (schedule, grid search, walk-forward, profitability and
stability gates, `APPLY_APPROVED` mode), `utils/experiment.py` (sealing,
voiding), `utils/graduation.py` (the gate), `utils/benchmark.py`,
`utils/evidence.py`, `walkforward.py`. Phase 2 is mostly the loop that runs
them without a person, and the discipline of refusing to run them early.

### The honest reason to build it

Not speed. Consistency. A person checking in every few days applies these
rules unevenly; the last three months of this project are the record of that.
An agent applies them every time — which is only a virtue if the rules are
right, which is why Phase 1 comes first.
