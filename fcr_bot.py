#!/usr/bin/env python3
"""
First Candle Rule — SPY paper bot. EXPERIMENTAL.

Backtested over 501 sessions before this existed: 25 trades, 20% win rate,
net -$0.27, profit factor 0.30, against +$3.99 for simply holding. It is
expected to lose slowly. It runs to check that live behaviour matches the
backtest — a validation of the pipeline, not of the strategy — and to give a
worked example of adding an isolated experiment.

SAFETY. This process contains no order-submission code of any kind. Fills are
simulated against closed candles, the way the crypto bot's dry-run path works.
There is no live branch to enable, no TradingClient constructed for trading,
and no credential used for anything but reading bars. That is what "paper
enforced in code, not just documented" means here: not a flag that gates a live
path, but the absence of the path.

ALPACA_PAPER must still parse to exactly true. Absent, malformed or false all
refuse to start — belt and braces over a path that does not exist.

ISOLATION. Its own state file, its own trade CSV, its own signal log, its own
pm2 process. It imports nothing from bot.py or stock_bot.py and writes nothing
they read.

    ALPACA_PAPER=True .venv/bin/python fcr_bot.py
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

from utils import env_loader                                    # noqa: E402
env_loader.load()

from strategies import first_candle_rule as fcr                 # noqa: E402
from market_data.alpaca_candle_collector import in_regular_session  # noqa: E402

TICKER = os.getenv("FCR_TICKER", "SPY").upper()
GRANULARITY = "Min15"
LOOP_SECONDS = int(os.getenv("FCR_LOOP_SECONDS", "60"))

STATE_PATH = os.path.join(DIR, f"state_fcr_{TICKER.lower()}_paper.json")
TRADES_PATH = os.path.join(DIR, f"completed_trades_{TICKER.lower()}_fcr_paper.csv")
SIGNAL_PATH = os.path.join(DIR, "logs", f"fcr_signals_{TICKER.lower()}.jsonl")

TRADE_FIELDS = [
    "ticker", "strategy", "session", "entry_time", "exit_time", "entry_price",
    "exit_price", "stop_price", "target_price", "open_high", "open_low",
    "open_mid", "shares", "gross_pnl_usd", "fees_usd", "slippage_usd",
    "pnl_usd", "gross_pct", "net_pct", "exit_reason", "mfe_pct", "mae_pct",
    "holding_time_hours", "mode",
]


def log(message):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z [fcr/{TICKER}] {message}",
          flush=True)


def require_paper():
    """
    Refuse to run unless paper mode is explicitly, unambiguously on.

    Fails closed on absent and on malformed, not just on false. A typo in an
    env var must never be read as permission.
    """
    raw = (os.getenv("ALPACA_PAPER") or "").strip().lower()
    if raw not in ("true", "1", "yes"):
        sys.exit(f"REFUSING TO START: ALPACA_PAPER={raw!r}. This is an "
                 f"experimental strategy and runs in simulation only.")
    if (os.getenv("FCR_ALLOW_LIVE") or "").strip():
        sys.exit("REFUSING TO START: FCR_ALLOW_LIVE is set. There is no live "
                 "path in this file and there must not be one.")


def entries_blocked():
    """Kill switch. Blocks NEW entries; protective exits always continue."""
    return (os.getenv("FCR_KILL_SWITCH") or "").strip().lower() in ("true", "1", "yes")


def load_state():
    try:
        with open(STATE_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"position": "NONE", "session": None, "entered_sessions": []}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(state, handle, indent=2)
    os.replace(tmp, STATE_PATH)          # atomic; a crash cannot half-write it


def record_signal(payload):
    os.makedirs(os.path.dirname(SIGNAL_PATH), exist_ok=True)
    payload["logged_at"] = datetime.now(timezone.utc).isoformat()
    with open(SIGNAL_PATH, "a") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def record_trade(row):
    exists = os.path.exists(TRADES_PATH)
    with open(TRADES_PATH, "a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in TRADE_FIELDS})


def session_candles(now_et):
    """Today's closed regular-hours bars, oldest first, plus prior history."""
    import pandas as pd
    from utils import stock_data

    frame = stock_data.fetch_bars(TICKER, GRANULARITY, 30)
    if frame is None or frame.empty:
        return None, None, None
    frame = frame.reset_index()
    stamps = pd.to_datetime(frame["timestamp"], utc=True)
    et = stamps.dt.tz_convert("America/New_York")

    candles, today = [], []
    for i in range(len(frame)):
        if not in_regular_session(et.iloc[i]):
            continue
        row = {"ts": int(stamps.iloc[i].timestamp()),
               "open": float(frame["open"].iloc[i]), "high": float(frame["high"].iloc[i]),
               "low": float(frame["low"].iloc[i]), "close": float(frame["close"].iloc[i]),
               "volume": float(frame["volume"].iloc[i])}
        candles.append(row)
        if et.iloc[i].date() == now_et.date():
            today.append(row)
    return candles, today, str(now_et.date())


def market_open():
    from utils import venue
    v = venue.AlpacaVenue(api_key=os.getenv("ALPACA_API_KEY", ""),
                          secret_key=os.getenv("ALPACA_SECRET_KEY", ""),
                          paper=True)
    return v.market_status() == "open"


def main():
    require_paper()
    log(f"EXPERIMENTAL First Candle Rule · {TICKER} · SIMULATION ONLY")
    log(f"Backtest over 501 sessions: 25 trades, 20% win, net -$0.27. "
        f"Expected to lose slowly.")
    log(f"state={os.path.basename(STATE_PATH)} trades={os.path.basename(TRADES_PATH)}")

    config = fcr.Config(fee_per_side=0.0, slippage_per_side=0.0005,
                        position_usd=float(os.getenv("FCR_POSITION_USD", "10")))

    while True:
        try:
            import pandas as pd
            now_et = pd.Timestamp.now(tz="America/New_York")
            state = load_state()

            if not market_open():
                if state.get("position") == "LONG":
                    log("Market closed with a position open — session exit.")
                    close_position(state, config, reason="End of session")
                else:
                    log("Market closed.")
                time.sleep(LOOP_SECONDS)
                continue

            history, today, session = session_candles(now_et)
            if not today:
                log("No closed regular-hours candles yet today.")
                time.sleep(LOOP_SECONDS)
                continue

            if state.get("position") == "LONG":
                check_exit(state, today, config)
            elif session in state.get("entered_sessions", []):
                log(f"Already attempted an entry this session ({session}).")
            elif entries_blocked():
                log("FCR_KILL_SWITCH is on — no new entries. Exits still run.")
            else:
                consider_entry(state, history, today, session, config)

        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}")
        time.sleep(LOOP_SECONDS)


def consider_entry(state, history, today, session, config):
    prior = [c for c in history if c not in today]
    decision = fcr.evaluate_session(today, config, session, TICKER, prior)
    payload = decision.as_row()
    payload["kill_switch"] = entries_blocked()
    record_signal(payload)

    if not decision.accepted:
        log(f"No entry — {decision.reason}")
        return

    state.update({
        "position": "LONG", "session": session,
        "entry_price": decision.entry_price, "entry_ts": decision.entry_ts,
        "stop_price": decision.stop_price, "target_price": decision.target_price,
        "open_high": decision.open_high, "open_low": decision.open_low,
        "open_mid": decision.open_mid,
        "shares": config.position_usd / decision.entry_price,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "mfe_pct": 0.0, "mae_pct": 0.0,
    })
    state.setdefault("entered_sessions", []).append(session)
    save_state(state)
    log(f"PAPER ENTRY {decision.entry_price:.4f}  stop {decision.stop_price:.4f} "
        f"target {decision.target_price:.4f}  (range {decision.open_low:.2f}"
        f"-{decision.open_high:.2f})")


def check_exit(state, today, config):
    last = today[-1]
    price, high, low = last["close"], last["high"], last["low"]
    entry = state["entry_price"]
    state["mfe_pct"] = max(state.get("mfe_pct", 0.0), (high - entry) / entry * 100)
    state["mae_pct"] = min(state.get("mae_pct", 0.0), (low - entry) / entry * 100)

    reason = None
    if low <= state["stop_price"]:
        reason, price = "Stop Loss", state["stop_price"]
    elif high >= state["target_price"]:
        reason, price = "Take Profit", state["target_price"]
    elif price < state["open_high"]:
        reason = "Closed back inside opening range"

    if reason:
        close_position(state, config, reason, price)
    else:
        save_state(state)
        log(f"Holding {entry:.4f} -> {price:.4f} "
            f"({(price-entry)/entry*100:+.2f}%)  stop {state['stop_price']:.4f}")


def close_position(state, config, reason, price=None):
    entry = state["entry_price"]
    shares = state["shares"]
    if price is None:
        price = entry
    exit_price = price * (1.0 - config.slippage_per_side)
    gross = (exit_price - entry) * shares
    fees = config.position_usd * config.fee_per_side * 2

    record_trade({
        "ticker": TICKER, "strategy": "FIRST_CANDLE_RULE",
        "session": state.get("session"), "entry_time": state.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "entry_price": entry, "exit_price": exit_price,
        "stop_price": state.get("stop_price"), "target_price": state.get("target_price"),
        "open_high": state.get("open_high"), "open_low": state.get("open_low"),
        "open_mid": state.get("open_mid"), "shares": shares,
        "gross_pnl_usd": gross, "fees_usd": fees,
        "slippage_usd": config.position_usd * config.slippage_per_side * 2,
        "pnl_usd": gross - fees,
        "gross_pct": (exit_price - entry) / entry * 100,
        "net_pct": (gross - fees) / config.position_usd * 100,
        "exit_reason": reason,
        "mfe_pct": state.get("mfe_pct"), "mae_pct": state.get("mae_pct"),
        "holding_time_hours": "", "mode": "PAPER",
    })
    log(f"PAPER EXIT {exit_price:.4f} — {reason}  net ${gross-fees:+.4f}")
    state.update({"position": "NONE", "entry_price": 0.0, "shares": 0.0,
                  "stop_price": None, "target_price": None})
    save_state(state)


if __name__ == "__main__":
    main()
