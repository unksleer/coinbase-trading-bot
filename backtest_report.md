# Coinbase Trading Bot Backtest & Optimization Report

This report summarizes the backtesting performance and parameter optimization for the Simple Moving Average (SMA) crossover strategy on **BTC-USD** using **1,000 hourly candles** (spanning from May 28, 2026, to July 8, 2026).

---

## 🛠️ Backtester Updates
We updated [backtest.py](file:///home/leeunks/coinbase-trading-bot/backtest.py) to support a configurable trade size parameter (`--trade-amount`). Setting `--trade-amount 0` configures the simulator to trade with **100% of available cash** at each signal, enabling realistic compounding portfolio backtesting.

---

## 📊 Backtest Performance Comparison (100% Cash Allocation)

Here is a side-by-side comparison of the **Baseline** configuration versus the **Optimized** configuration when allocating 100% of available cash to trades:

| Metric | Baseline Configuration | Optimized Configuration (Applied) |
| :--- | :---: | :---: |
| **Short Window** | 10 | **5** |
| **Long Window** | 20 | **20** |
| **Min Spread %** | 0.0% | **0.1%** |
| **Stop Loss %** | 1.0% | **1.0%** |
| **Take Profit %** | 5.0% | **5.0%** |
| **Completed Trades** | 3 | **2** |
| **Win Rate** | 33.33% (1 Win, 2 Losses) | **50.00%** (1 Win, 1 Loss) |
| **Profit Factor** | 1.54 | **3.45** |
| **Max Drawdown** | 3.4756% | **1.6821%** |
| **Average Win** | +$49.55 USD | **+$53.79 USD** |
| **Average Loss** | -$16.13 USD | **-$15.59 USD** |
| **Portfolio Net Profit** | **+$11.14 USD (+1.11%)** | **+$34.10 USD (+3.41%)** |

---

## 🔍 Key Findings & Applied Changes

1. **Parameter Optimization**: We applied the top-performing optimized parameters to [`.env`](file:///home/leeunks/coinbase-trading-bot/.env).
2. **Min Spread Filter**: Adding a `0.1%` minimum spread filtered out a low-quality losing crossover trade, increasing the **Profit Factor** from `1.54` to `3.45` and cutting the **Max Drawdown** in half (`1.68%`).
3. **Capital Allocation**: Compound trading using 100% of portfolio cash yields a much stronger overall return (**+3.41%** in ~41 days) compared to fixed-amount trading.
