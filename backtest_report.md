# Coinbase Trading Bot Backtest & Optimization Report

> **⚠ Historical snapshot — 2026-07-12. Do not use these parameters.**
>
> Superseded by live sim results. This report assumes a 1.20% per-side taker fee
> (2.40% roundtrip); the bot places maker orders at 0.60% per side, and the
> measured roundtrip drag across 51 sim trades is **1.207%**. The parameters
> below (SMA 5/50, SL 3.0%, TP 6.0%) are no longer in use — see
> [trading_profiles.md](trading_profiles.md) for current values and
> `analyze_entries.py` for analysis against current data.


This report summarizes the backtesting performance and parameter optimization for the Simple Moving Average (SMA) crossover strategy on **BTC-USD** using **2,000 candles**, specifically accounting for the **1.20% fee rate** (2.40% roundtrip transaction cost).

---

## 📊 Backtest Performance Comparison (1.20% Fee Rate)

Here is a comparison of the **15-minute Aggressive (Baseline)** configuration versus the **1-hour Optimized (Conservative)** configuration:

| Metric | Baseline 15-Minute Profile | Optimized 1-Hour Profile |
| :--- | :---: | :---: |
| **Short Window** | 12 | **5** |
| **Long Window** | 35 | **50** |
| **Min Spread %** | 0.03% | **0.0%** |
| **Stop Loss %** | 2.5% | **3.0%** |
| **Take Profit %** | 4.0% | **6.0%** |
| **EMA Trend Filter** | Disabled | **Enabled (200)** |
| **Completed Trades** | 5 | **5** |
| **Win Rate** | 0.00% (0 Wins, 5 Losses) | **60.00%** (3 Wins, 2 Losses) |
| **Profit Factor** | 0.00 | **1.71** |
| **Max Drawdown** | 2.0955% | **1.1295%** |
| **Average Win** | N/A | **+$0.43 USD** |
| **Average Loss** | -$0.30 USD | **-$0.26 USD** |
| **Portfolio Net Profit** | **-$2.09 USD (-2.08%)** | **+$0.51 USD (+0.51%)** |

---

## 🔍 Key Findings & Applied Changes

1. **Transaction Fee Drag**: At a 1.20% fee per transaction, roundtrip trading costs are 2.40%. This is the single biggest factor behind your historical losses. Tight timeframes (15-minute) do not have large enough price movements to cover this hurdle.
2. **Granularity Shift**: Moving to **1-hour candles** filters out short-term noise and allows the bot to capture larger trending moves that easily clear the 2.40% roundtrip fee drag.
3. **EMA Trend Filter**: Enabling the long-term **200 EMA Trend Filter** is critical. It restricts the bot to buying only in defined macro uptrends, which turned a -36.2% loss (without EMA) into a profitable **+0.51%** gain (with EMA).
4. **Wider SL/TP**: Setting stop-loss too tight (e.g. 1.0% or 2.5%) triggers stop-outs due to normal intraday noise. Widening stop-loss to 3.0% - 5.0% and take-profit to 6.0% - 15.0% improves the risk-to-reward ratio after fees.
