# October 2026: automated crypto trading retired

Runtime files from the paper crypto bots, archived 2026-10-14 when the owner
retired automated crypto trading after four months (decision log entry of the
same date). Nothing reads these; they are the only record of that era.

- `state_*-usdc*.json` — each bot's last state. Six carry an open LONG at the
  time of the stop (ADA, BTC, ETH, NEAR, SOL, XRP), −$66.00 unrealised at
  market on 2026-10-14. Paper positions; never closed, because closing them
  would have recorded a trade nobody took.
- `completed_trades_*-usdc_dry.csv` — the Donchian era's closed round trips
  (2026-09-21 onward); `completed_trades_dry*.csv` — earlier eras.
- `trades_dry.csv`, `trades.csv` — raw fill logs, dry-run and the July live era.
- `logs/signal_log_*-usdc.csv` — per-candle decisions.

Registrations concluded: `experiments/2026-09-crypto-donchian-breakout.json`
(stopped by owner, not a result). The candle collector and the six-hour /
hourly store in `data/market_data.sqlite` are kept: data costs nothing and
any future look at crypto starts from a continuous series.
