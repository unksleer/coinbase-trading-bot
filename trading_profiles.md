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
| **`STOP_LOSS_ATR_MULTIPLE`** | `0.0` (Disabled) | `0.0` (Disabled) | Dynamic ATR-based Stop Loss multiple. If set > 0, sets a stop loss at `Entry Price - (Multiple * Entry ATR)`. |
| **`TAKE_PROFIT_ATR_MULTIPLE`**| `0.0` (Disabled) | `0.0` (Disabled) | Dynamic ATR-based Take Profit multiple. If set > 0, sets a take profit at `Entry Price + (Multiple * Entry ATR)`. |

---

## 2. Coin-Specific Overrides
To allow fine-tuning for individual assets based on their volatility and behaviors, the bot supports **coin-prefixed overrides** in the [.env](file:///home/leeunks/coinbase-trading-bot/.env) file. 

The bot will automatically extract the base asset prefix from the active `TRADING_PAIR` (e.g., `SOL` from `SOL-USDC`) and check for overrides in the following order of precedence:
1. `<COIN_PREFIX>_<KEY>` (e.g., `SOL_SMA_SHORT_WINDOW`)
2. `<ACTIVE_PROFILE>_<KEY>` (e.g., `AGGRESSIVE_SMA_SHORT_WINDOW`)
3. `<KEY>` (e.g., `SMA_SHORT_WINDOW`)

### Example overrides in `.env`:
```ini
# Custom overrides for SOL-USDC
SOL_SMA_SHORT_WINDOW=12
SOL_SMA_LONG_WINDOW=30
SOL_STOP_LOSS_PERCENT=3.0
SOL_TAKE_PROFIT_PERCENT=12.0

# Custom overrides for BTC-USDC
BTC_SMA_SHORT_WINDOW=5
BTC_SMA_LONG_WINDOW=50
BTC_STOP_LOSS_PERCENT=3.0
BTC_TAKE_PROFIT_PERCENT=10.0
```

---

## 3. ATR-Based Risk Management
Instead of static percentages, the bot supports dynamic stop-loss and take-profit thresholds scaled by volatility via the **Average True Range (ATR)**.

* **ATR Stop Loss:** Triggers when price falls below `Entry Price - (STOP_LOSS_ATR_MULTIPLE * Entry ATR)`.
* **ATR Take Profit:** Triggers when price rises above `Entry Price + (TAKE_PROFIT_ATR_MULTIPLE * Entry ATR)`.
* **State Preservation:** The ATR value at the time of entry is locked into `entry_indicators` inside the state file (e.g., [state_sol-usdc-atr.json](file:///home/leeunks/coinbase-trading-bot/state_sol-usdc-atr.json)) to maintain fixed price thresholds throughout the duration of the trade.

---

## 4. Dynamic Profile Selection in Code
In the codebase, [config/settings.py](file:///home/leeunks/coinbase-trading-bot/config/settings.py) checks the `ACTIVE_PROFILE` env variable. It will attempt to load profile-specific keys (e.g. `AGGRESSIVE_SMA_SHORT_WINDOW`). If they are not defined, it defaults to the standard keys (e.g. `SMA_SHORT_WINDOW`).

---

## 5. How to Switch Profiles
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
