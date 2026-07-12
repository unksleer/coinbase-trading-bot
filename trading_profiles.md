# Bot Trading Profiles

This project supports switching between two preset configurations by editing the `ACTIVE_PROFILE` environment variable in the [.env](file:///home/leeunks/coinbase-trading-bot/.env) file. 

The configuration values for both profiles are detailed below:

---

## 1. Parameters Table

| Parameter Key | Conservative Profile | Aggressive Profile | Parameter Purpose & Behavior |
| :--- | :---: | :---: | :--- |
| **`SMA_SHORT_WINDOW`** | `5` | `8` | Short-term Simple Moving Average period length (in candles). |
| **`SMA_LONG_WINDOW`** | `50` | `35` | Long-term Simple Moving Average period length (in candles). |
| **`SMA_MIN_SPREAD_PERCENT`** | `0.0%` (`0.0`) | `0.0%` (`0.0`) | The minimum percentage difference required between the Short SMA and Long SMA on a crossover to trigger a BUY signal. |
| **`RSI_FILTER_ENABLED`** | `True` | `True` | Activates the Relative Strength Index filter to prevent buying when the asset is overbought. |
| **`RSI_PERIOD`** | `14` | `14` | The window length used to calculate the RSI. |
| **`RSI_BUY_MAX`** | `60` | `70` | Prevents a BUY signal if the RSI exceeds this value. (Aggressive allows higher-momentum entries up to `70`). |
| **`RSI_SELL_MIN`** | `40` | `40` | Prevents a SELL signal if the RSI falls below this value (stops selling if already oversold). |
| **`EMA_TREND_FILTER_ENABLED`**| `True` | `True` | When `True`, restricts the bot from buying unless the current price is above the long-term EMA. (Enabled for both to filter out whipsaws in choppy markets). |
| **`EMA_TREND_PERIOD`** | `200` | `200` | The window length for the Exponential Moving Average trend filter. |
| **`STOP_LOSS_PERCENT`** | `3.0%` (`3.0`) | `5.0%` (`5.0`) | Risk management threshold. Automatically triggers a market SELL if the price falls below this percentage of the entry price. |
| **`TAKE_PROFIT_PERCENT`** | `6.0%` (`6.0`) | `15.0%` (`15.0`) | Profit target threshold. Automatically triggers a market SELL if the price rises above this percentage of the entry price. |

---

## 2. Dynamic Profile Selection in Code
In the codebase, [config/settings.py](file:///home/leeunks/coinbase-trading-bot/config/settings.py) checks the `ACTIVE_PROFILE` env variable. It will attempt to load profile-specific keys (e.g. `AGGRESSIVE_SMA_SHORT_WINDOW`). If they are not defined, it defaults to the standard keys (e.g. `SMA_SHORT_WINDOW`).

---

## 3. How to Switch Profiles
1. Open the [.env](file:///home/leeunks/coinbase-trading-bot/.env) file.
2. Edit the active profile variable to your preferred mode:
   ```ini
   # Options: CONSERVATIVE, AGGRESSIVE
   ACTIVE_PROFILE=AGGRESSIVE
   ```
3. Restart your bot processes under PM2 to apply:
   ```bash
   # Restart all running bots
   pm2 restart all
   
   # Restart only the BTC bot
   pm2 restart btc-bot
   ```
