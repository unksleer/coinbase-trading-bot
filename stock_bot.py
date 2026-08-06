#!/usr/bin/env python3
"""
stock_bot.py — SMA Crossover Stock Trading Bot (Alpaca)

Same strategy logic as the crypto bot (SMA crossover + RSI filter) but
runs against the Alpaca Markets API for US equities.

Key differences vs crypto bot:
  - Market hours gate: only trades 9:30am–4:00pm ET, Mon–Fri
  - No 24/7 market — bot sleeps outside market hours
  - Commission-free via Alpaca — fee hurdle is near zero
  - Paper trading via paper-api.alpaca.markets (set ALPACA_PAPER=True)
  - Fractional shares supported — $10/trade works fine

PM2 launch (one per ticker):
  STOCK_TICKER=SPY TRADE_AMOUNT_USD=10 ALPACA_PAPER=True \\
    pm2 start stock_bot.py --name spy-bot --interpreter .venv/bin/python --no-autorestart

  STOCK_TICKER=QQQ TRADE_AMOUNT_USD=10 ALPACA_PAPER=True \\
    pm2 start stock_bot.py --name qqq-bot --interpreter .venv/bin/python --no-autorestart

  STOCK_TICKER=LPL TRADE_AMOUNT_USD=10 ALPACA_PAPER=True \\
    pm2 start stock_bot.py --name lpl-bot --interpreter .venv/bin/python --no-autorestart
"""
import os
import sys
import csv
import json
import time
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd
from dotenv import load_dotenv

# ── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ── Configuration from environment ───────────────────────────────────────────
TICKER          = os.getenv("STOCK_TICKER", "SPY").upper()
TRADE_AMOUNT    = float(os.getenv("TRADE_AMOUNT_USD", 10))
IS_PAPER        = os.getenv("ALPACA_PAPER", "True").strip().lower() not in ("false", "0", "no")
API_KEY         = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY      = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL        = os.getenv("ALPACA_BASE_URL",
                            "https://paper-api.alpaca.markets" if IS_PAPER
                            else "https://api.alpaca.markets")

# SMA windows — stocks have smaller % moves than crypto, so tighter windows
SMA_SHORT       = int(os.getenv(f"{TICKER}_SMA_SHORT_WINDOW",  os.getenv("STOCK_SMA_SHORT_WINDOW",  "10")))
SMA_LONG        = int(os.getenv(f"{TICKER}_SMA_LONG_WINDOW",   os.getenv("STOCK_SMA_LONG_WINDOW",   "30")))
STOP_LOSS_PCT   = float(os.getenv(f"{TICKER}_STOP_LOSS_PERCENT",  os.getenv("STOCK_STOP_LOSS_PERCENT",  "3.0")))
TAKE_PROFIT_PCT = float(os.getenv(f"{TICKER}_TAKE_PROFIT_PERCENT", os.getenv("STOCK_TAKE_PROFIT_PERCENT", "5.0")))
RSI_MIN         = float(os.getenv("STOCK_RSI_BUY_MIN", "40"))
RSI_MAX         = float(os.getenv("STOCK_RSI_BUY_MAX", "70"))
LOOP_INTERVAL   = int(os.getenv("STOCK_LOOP_INTERVAL_SEC", "60"))
CANDLE_BARS     = int(os.getenv("STOCK_CANDLE_BARS", "100"))  # 1h bars to fetch

# ── Logging ───────────────────────────────────────────────────────────────────
DIR = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(DIR, "logs", f"stock_{TICKER.lower()}.log")
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [stock/{TICKER}] %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(f"stock_bot.{TICKER}")

MODE = "PAPER" if IS_PAPER else "LIVE"

# ── State file ────────────────────────────────────────────────────────────────
STATE_PATH  = os.path.join(DIR, f"state_stock_{TICKER.lower()}_{MODE.lower()}.json")
TRADES_PATH = os.path.join(DIR, f"completed_trades_stock_{TICKER.lower()}_{MODE.lower()}.csv")

TRADE_FIELDNAMES = [
    "ticker", "entry_time", "exit_time", "entry_price", "exit_price",
    "entry_reason", "exit_reason", "gross_pct", "net_pct",
    "pnl_usd", "shares", "mode",
]


# ─────────────────────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"position": "NONE", "entry_price": 0.0, "shares": 0.0,
            "entry_time": None, "ticker": TICKER, "mode": MODE}


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def record_trade(state: dict, exit_price: float, exit_reason: str):
    entry_price = state["entry_price"]
    shares      = state["shares"]
    gross_pct   = (exit_price - entry_price) / entry_price * 100
    pnl_usd     = (exit_price - entry_price) * shares

    row = {
        "ticker":       TICKER,
        "entry_time":   state.get("entry_time", ""),
        "exit_time":    datetime.now(timezone.utc).isoformat(),
        "entry_price":  round(entry_price, 4),
        "exit_price":   round(exit_price, 4),
        "entry_reason": state.get("entry_reason", "SMA Bullish Cross"),
        "exit_reason":  exit_reason,
        "gross_pct":    round(gross_pct, 4),
        "net_pct":      round(gross_pct, 4),  # no commission
        "pnl_usd":      round(pnl_usd, 4),
        "shares":       round(shares, 6),
        "mode":         MODE,
    }
    file_exists = os.path.exists(TRADES_PATH)
    with open(TRADES_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    log.info(f"Trade recorded: {exit_reason}  PnL=${pnl_usd:+.4f}  gross={gross_pct:+.3f}%")


# ─────────────────────────────────────────────────────────────────────────────
# Market hours check (Eastern Time)
# ─────────────────────────────────────────────────────────────────────────────
def is_market_open() -> bool:
    """Returns True if US stock market is currently open (9:30am–4pm ET, Mon–Fri)."""
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(API_KEY, SECRET_KEY, paper=IS_PAPER)
        clock = client.get_clock()
        return clock.is_open
    except Exception as e:
        log.warning(f"Could not check market clock via Alpaca: {e}. Falling back to time-based check.")
        # Fallback: manual time check (UTC offset for ET)
        now_utc = datetime.now(timezone.utc)
        # ET is UTC-4 (EDT) or UTC-5 (EST) — use UTC-4 as approximation
        now_et = now_utc - timedelta(hours=4)
        if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        market_open  = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= now_et < market_close


def minutes_until_open() -> int:
    """Returns minutes until next market open. Used for smart sleep."""
    now_utc = datetime.now(timezone.utc)
    now_et  = now_utc - timedelta(hours=4)
    # If market is open today, next open is tomorrow at 9:30
    if now_et.weekday() < 5:
        if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30):
            open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        else:
            # After close — next trading day
            open_et = (now_et + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
    else:
        # Weekend — find next Monday
        days_ahead = 7 - now_et.weekday()
        open_et = (now_et + timedelta(days=days_ahead)).replace(hour=9, minute=30, second=0, microsecond=0)
    delta = (open_et - now_et).total_seconds() / 60
    return max(1, int(delta))


# ─────────────────────────────────────────────────────────────────────────────
# Data fetching — 1-hour bars from Alpaca
# ─────────────────────────────────────────────────────────────────────────────
def fetch_bars(ticker: str, n_bars: int = 100) -> pd.DataFrame:
    """Fetch the last n_bars of 1-hour OHLCV bars for a stock ticker."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import Adjustment, DataFeed

        data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
        end   = datetime.now(timezone.utc)
        start = end - timedelta(hours=n_bars * 2)  # fetch extra to ensure enough bars

        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Hour,
            start=start,
            end=end,
            adjustment=Adjustment.ALL,
            feed=DataFeed.IEX,   # IEX = free tier; SIP requires paid subscription
        )
        bars = data_client.get_stock_bars(req)
        df = bars.df

        if df.empty:
            return pd.DataFrame()

        # Flatten multi-index if present
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(ticker, level=0)

        df = df.sort_index()
        df["close"] = pd.to_numeric(df["close"])
        return df.tail(n_bars).reset_index()

    except Exception as e:
        log.error(f"Failed to fetch bars for {ticker}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# Indicators
# ─────────────────────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> dict:
    closes = df["close"]
    sma_s  = closes.rolling(SMA_SHORT).mean()
    sma_l  = closes.rolling(SMA_LONG).mean()

    # RSI
    delta  = closes.diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_g  = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_l  = loss.ewm(alpha=1/14, adjust=False).mean()
    rs     = avg_g / avg_l.replace(0, float("nan"))
    rsi    = 100 - (100 / (1 + rs))

    return {
        "price":     float(closes.iloc[-1]),
        "sma_short": float(sma_s.iloc[-1]),
        "sma_long":  float(sma_l.iloc[-1]),
        "prev_sma_short": float(sma_s.iloc[-2]),
        "prev_sma_long":  float(sma_l.iloc[-2]),
        "rsi":       float(rsi.iloc[-1]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Order execution via Alpaca
# ─────────────────────────────────────────────────────────────────────────────
def place_buy(price: float) -> float:
    """Submit a fractional market buy for TRADE_AMOUNT USD. Returns shares bought."""
    shares = round(TRADE_AMOUNT / price, 6)
    if IS_PAPER:
        log.info(f"[{MODE}] BUY {shares:.6f} {TICKER} @ ~${price:.4f}  (${TRADE_AMOUNT:.2f})")
        return shares
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        client = TradingClient(API_KEY, SECRET_KEY, paper=IS_PAPER)
        req = MarketOrderRequest(
            symbol=TICKER,
            notional=TRADE_AMOUNT,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(req)
        log.info(f"[{MODE}] BUY order submitted: {order.id} — ${TRADE_AMOUNT:.2f} of {TICKER}")
        return shares
    except Exception as e:
        log.error(f"BUY order failed: {e}")
        return 0.0


def place_sell(shares: float, price: float, reason: str):
    """Submit a market sell for all held shares."""
    if IS_PAPER:
        log.info(f"[{MODE}] SELL {shares:.6f} {TICKER} @ ~${price:.4f}  reason={reason}")
        return
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        client = TradingClient(API_KEY, SECRET_KEY, paper=IS_PAPER)
        req = MarketOrderRequest(
            symbol=TICKER,
            qty=shares,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(req)
        log.info(f"[{MODE}] SELL order submitted: {order.id} — {shares:.6f} {TICKER}  reason={reason}")
    except Exception as e:
        log.error(f"SELL order failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main trading loop
# ─────────────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info(f"Stock Trading Bot — {TICKER} ({MODE})")
    log.info(f"  SMA {SMA_SHORT}/{SMA_LONG} | SL={STOP_LOSS_PCT}% | TP={TAKE_PROFIT_PCT}%")
    log.info(f"  RSI filter: [{RSI_MIN}–{RSI_MAX}] | Trade size: ${TRADE_AMOUNT}")
    log.info(f"  Base URL: {BASE_URL}")
    log.info("=" * 60)

    if not API_KEY or not SECRET_KEY:
        log.error("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env — exiting.")
        sys.exit(1)

    state = load_state()
    log.info(f"State loaded: position={state['position']}")

    while True:
        try:
            # ── Market hours gate ─────────────────────────────────────────────
            if not is_market_open():
                mins = minutes_until_open()
                sleep_mins = min(mins, 30)  # wake every 30m max to recheck
                log.info(f"Market closed. Next open in ~{mins}m. Sleeping {sleep_mins}m.")
                time.sleep(sleep_mins * 60)
                continue

            # ── Fetch data and compute indicators ─────────────────────────────
            log.info(f"--- {TICKER} market check ---")
            df = fetch_bars(TICKER, CANDLE_BARS + SMA_LONG + 10)
            if df.empty or len(df) < SMA_LONG + 5:
                log.warning(f"Not enough bars ({len(df)}). Waiting...")
                time.sleep(LOOP_INTERVAL)
                continue

            ind = compute_indicators(df)
            price     = ind["price"]
            sma_s     = ind["sma_short"]
            sma_l     = ind["sma_long"]
            prev_s    = ind["prev_sma_short"]
            prev_l    = ind["prev_sma_long"]
            rsi       = ind["rsi"]

            spread = (sma_s - sma_l) / sma_l * 100
            direction = "ABOVE" if sma_s > sma_l else "BELOW"

            log.info(
                f"Indicators | SMA_short={sma_s:.4f} | SMA_long={sma_l:.4f} | "
                f"short is {abs(spread):.3f}% {direction} long | spread={abs(spread):.3f}% | "
                f"RSI={rsi:.1f}[{RSI_MIN}-{RSI_MAX}] | "
                f"Position: {state['position']}"
            )

            # ── Strategy logic ────────────────────────────────────────────────
            if state["position"] == "NONE":
                # Bullish crossover: short was below long, now above
                bullish_cross = prev_s < prev_l and sma_s > sma_l
                rsi_ok = RSI_MIN <= rsi <= RSI_MAX

                if bullish_cross and rsi_ok:
                    log.info(f"🟢 BUY SIGNAL — Bullish crossover confirmed | RSI={rsi:.1f}")
                    shares = place_buy(price)
                    if shares > 0:
                        state.update({
                            "position":     "LONG",
                            "entry_price":  price,
                            "shares":       shares,
                            "entry_time":   datetime.now(timezone.utc).isoformat(),
                            "entry_reason": "SMA Bullish Cross",
                        })
                        save_state(state)
                elif bullish_cross and not rsi_ok:
                    log.info(f"⚠️  Crossover detected but RSI={rsi:.1f} outside [{RSI_MIN}–{RSI_MAX}] — skipping")

            elif state["position"] == "LONG":
                entry_price = state["entry_price"]
                chg_pct = (price - entry_price) / entry_price * 100

                exit_reason = None
                if chg_pct <= -STOP_LOSS_PCT:
                    exit_reason = "Stop Loss Triggered"
                elif chg_pct >= TAKE_PROFIT_PCT:
                    exit_reason = "Take Profit Triggered"
                elif prev_s > prev_l and sma_s < sma_l:
                    exit_reason = "SMA Bearish Cross"

                if exit_reason:
                    log.info(f"🔴 SELL SIGNAL — {exit_reason} | chg={chg_pct:+.3f}% | Price=${price:.4f}")
                    place_sell(state["shares"], price, exit_reason)
                    record_trade(state, price, exit_reason)
                    state.update({
                        "position": "NONE", "entry_price": 0.0,
                        "shares": 0.0, "entry_time": None,
                    })
                    save_state(state)
                else:
                    log.info(
                        f"Holding {state['shares']:.6f} {TICKER} | "
                        f"Entry=${entry_price:.4f} | Current=${price:.4f} | "
                        f"P&L: {chg_pct:+.3f}% (${(price - entry_price) * state['shares']:+.4f})"
                    )

        except KeyboardInterrupt:
            log.info("Bot interrupted by user. Exiting gracefully.")
            break
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)

        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
