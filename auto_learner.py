#!/usr/bin/env python3
"""
auto_learner.py — Data-Driven Strategy Learning Loop
Runs nightly (via PM2 cron). For each active sim coin:

  PHASE 1 — DIAGNOSE (from real sim trade history):
    - Load completed_trades_*_dry.csv for this coin
    - Compute: win rate, avg hold time, avg MFE/MAE, dominant exit reason
    - Classify a failure mode: WHIPSAW | SL_TOO_TIGHT | FEE_DRAG | TREND_MISSED | OK

  PHASE 2 — ADAPT (search space driven by failure mode):
    - Cross-reference logs/param_history.json to avoid retrying failed combos
    - Adjust SMA windows, SL%, TP% based on what the failure mode tells us
    - Run grid backtest on 1,500 real price candles

  PHASE 3 — DECIDE (only update if genuinely better):
    - New params must beat current by ≥5% AND have positive backtest profit
    - Write to .env, restart bot, record trial in param_history.json

  PHASE 4 — GRADUATE (real sim performance):
    - If a coin has ≥20 sim trades, win rate ≥55%, net PnL > $0 → promote to live

PM2 registration (run once):
  pm2 start auto_learner.py --name learner --interpreter .venv/bin/python \\
      --cron "0 4 * * *" --no-autorestart
"""
import os
import sys
import csv
import time
import json
import glob
import subprocess
import logging
from datetime import datetime, timezone
from collections import Counter
import pandas as pd
import requests

# ─────────────────────────────────────────────────────────────────────────────
DIR           = os.path.dirname(os.path.abspath(__file__))
LOG_PATH      = os.path.join(DIR, "logs", "auto_learner.log")
BEST_PATH     = os.path.join(DIR, "logs", "best_params.json")
HISTORY_PATH  = os.path.join(DIR, "logs", "param_history.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [learner] %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("auto_learner")

# ── Coins and their PM2 bot names ────────────────────────────────────────────
COINS = {
    "BTC-USDC":   {"bot": "btc-bot",    "gran": "ONE_HOUR", "prefix": "BTC",    "amount": 10},
    "ETH-USDC":   {"bot": "eth-bot",    "gran": "ONE_HOUR", "prefix": "ETH",    "amount": 10},
    "SOL-USDC":   {"bot": "sol-bot",    "gran": "ONE_HOUR", "prefix": "SOL",    "amount": 10},
    "ADA-USDC":   {"bot": "ada-bot",    "gran": "ONE_HOUR", "prefix": "ADA",    "amount": 10},
    "LINK-USDC":  {"bot": "link-bot",   "gran": "ONE_HOUR", "prefix": "LINK",   "amount": 10},
    "NEAR-USDC":  {"bot": "near-bot",   "gran": "ONE_HOUR", "prefix": "NEAR",   "amount": 10},
    "SUI-USDC":   {"bot": "sui-bot",    "gran": "ONE_HOUR", "prefix": "SUI",    "amount": 10},
    "XRP-USDC":   {"bot": "xrp-bot",    "gran": "ONE_HOUR", "prefix": "XRP",    "amount": 10},
    "RENDER-USDC":{"bot": "render-bot", "gran": "ONE_HOUR", "prefix": "RENDER", "amount": 10},
}

MIN_IMPROVEMENT_PCT = 5.0   # New params must beat old by ≥5% net profit
MIN_TRADES          = 5     # Optimizer result must have ≥5 backtest trades to count
REAL_FEE_RATE       = 0.006 # Actual Coinbase Advanced Trade taker fee (0.60%)

GRANULARITY_MAP = {
    "ONE_MINUTE": 60, "FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900,
    "ONE_HOUR": 3600, "ONE_DAY": 86400
}

# Failure mode constants
FM_WHIPSAW      = "WHIPSAW"       # SMA too short: price gyrates through crossover repeatedly
FM_SL_TOO_TIGHT = "SL_TOO_TIGHT"  # Stops firing on normal noise before trend develops
FM_FEE_DRAG     = "FEE_DRAG"      # Avg gross move < roundtrip fee (wrong timeframe)
FM_TP_TOO_LOOSE = "TP_TOO_LOOSE"  # MFE shows price hit profitable level but we missed it
FM_OK           = "OK"            # Strategy working; minor refinements only
FM_NO_DATA      = "NO_DATA"       # Not enough real trades yet to diagnose


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1: DIAGNOSE — load real sim trades and classify failure mode
# ─────────────────────────────────────────────────────────────────────────────

def load_real_trades(pair: str) -> list:
    """
    Load all completed sim trades for this pair from CSV.
    Only loads current era trades to avoid poisoning the learner
    with trades from old broken timeframes (like ONE_MINUTE).
    Returns list of dicts with parsed numeric fields.
    """
    coin_slug = pair.lower().replace("-", "-")
    csv_path  = os.path.join(DIR, f"completed_trades_{coin_slug}_dry.csv")

    all_files = [csv_path] if os.path.exists(csv_path) else []

    trades = []
    seen = set()  # deduplicate by (entry_time, exit_time)
    for fpath in all_files:
        try:
            with open(fpath, errors="replace") as f:
                for row in csv.DictReader(f):
                    key = (row.get("entry_time", ""), row.get("exit_time", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        trades.append({
                            "pnl_usd":          float(row.get("pnl_usd") or 0),
                            "fees_usd":          float(row.get("fees_usd") or 0),
                            "gross_pct":         float(row.get("gross_pct") or 0),
                            "mfe_pct":           float(row.get("mfe_pct") or 0),
                            "mae_pct":           float(row.get("mae_pct") or 0),
                            "holding_time_hours":float(row.get("holding_time_hours") or 0),
                            "exit_reason":       row.get("exit_reason", ""),
                        })
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            log.warning(f"  Could not read {fpath}: {e}")

    return trades


def analyze_failure_mode(trades: list, fee_rate: float = REAL_FEE_RATE) -> dict:
    """
    Given a list of real completed trades, classify the dominant failure mode
    and return a rich diagnostic dict the adaptive search can act on.
    """
    if len(trades) < 5:
        return {"mode": FM_NO_DATA, "n": len(trades)}

    n         = len(trades)
    wins      = [t for t in trades if t["pnl_usd"] > 0]
    losses    = [t for t in trades if t["pnl_usd"] <= 0]
    win_rate  = len(wins) / n * 100

    avg_gross     = sum(t["gross_pct"] for t in trades) / n
    avg_mfe       = sum(t["mfe_pct"] for t in trades) / n
    avg_mae       = sum(abs(t["mae_pct"]) for t in trades) / n
    avg_hold      = sum(t["holding_time_hours"] for t in trades) / n
    total_fees    = sum(t["fees_usd"] for t in trades)
    total_gross_move = sum(abs(t["gross_pct"]) for t in trades)
    roundtrip_fee_pct = fee_rate * 2 * 100   # e.g. 1.2%

    exit_counts = Counter(t["exit_reason"] for t in trades)
    dominant_exit, dominant_count = exit_counts.most_common(1)[0]
    dominant_pct = dominant_count / n * 100

    diag = {
        "n":               n,
        "win_rate":        round(win_rate, 1),
        "avg_gross_pct":   round(avg_gross, 3),
        "avg_mfe_pct":     round(avg_mfe, 3),
        "avg_mae_pct":     round(avg_mae, 3),
        "avg_hold_h":      round(avg_hold, 2),
        "dominant_exit":   dominant_exit,
        "dominant_exit_pct": round(dominant_pct, 1),
        "total_fees":      round(total_fees, 4),
        "roundtrip_fee_pct": roundtrip_fee_pct,
        "fee_move_ratio":  round(roundtrip_fee_pct / max(avg_gross, 0.01), 2),
    }

    # ── Classify failure mode ─────────────────────────────────────────────────
    # Priority order matters — most specific condition checked first.

    # Whipsaw: almost all exits are crossover reversals with short hold times.
    # This is the most common failure and is very specific — check FIRST.
    if (dominant_exit in ("SMA Bearish Cross", "SMA Bullish Cross")
            and dominant_pct >= 60 and avg_hold < 6):
        diag["mode"] = FM_WHIPSAW
        diag["action"] = (
            f"{dominant_pct:.0f}% of exits are crossover reversals with avg hold {avg_hold:.1f}h. "
            "SMA windows are too short — price crosses and re-crosses before trend confirms."
        )

    # Stop loss too tight: SL fires repeatedly on normal noise before trend develops.
    elif dominant_exit == "Stop Loss Triggered" and dominant_pct >= 50 and avg_mae < 5.0:
        diag["mode"] = FM_SL_TOO_TIGHT
        diag["action"] = (
            f"{dominant_pct:.0f}% of exits are stop losses with avg adverse excursion {avg_mae:.2f}%. "
            "Normal price noise is triggering stops. Widen SL or wait for stronger entry signals."
        )

    # Fee drag: avg gross move is smaller than the roundtrip fee cost.
    # Happens when on too-short a timeframe regardless of SMA settings.
    elif avg_gross < roundtrip_fee_pct * 0.8 and avg_hold < 3:
        diag["mode"] = FM_FEE_DRAG
        diag["action"] = (
            f"Avg gross {avg_gross:.3f}% is < roundtrip fee {roundtrip_fee_pct:.2f}%. "
            "Switch to longer timeframe or wait for larger swings."
        )

    # TP too loose: MFE shows price hit good levels but reversed before TP triggered.
    elif avg_mfe > roundtrip_fee_pct * 2 and win_rate < 30 and dominant_exit != "Stop Loss Triggered":
        diag["mode"] = FM_TP_TOO_LOOSE
        diag["action"] = (
            f"Avg max favorable excursion is {avg_mfe:.2f}% but win rate is {win_rate:.1f}%. "
            "Price is reaching profitable levels but reversing before TP is hit. Tighten TP."
        )

    # Strategy working — minor refinements only
    elif win_rate >= 40:
        diag["mode"] = FM_OK
        diag["action"] = "Strategy performing adequately. Minor refinements only."

    else:
        # Generic: low win rate, mixed exits
        diag["mode"] = FM_WHIPSAW  # default — try widening windows
        diag["action"] = (
            f"Win rate {win_rate:.1f}% with mixed exit reasons. "
            "Default: try wider SMA windows to reduce false signals."
        )

    return diag


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2: ADAPT — param history + failure-mode-driven search bounds
# ─────────────────────────────────────────────────────────────────────────────

def load_param_history() -> dict:
    """Load persistent record of every parameter combination tried across all runs."""
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_param_history(history: dict):
    """Write param history to disk."""
    import numpy as np
    class NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, np.bool_):
                return bool(obj)
            return super(NpEncoder, self).default(obj)
            
    try:
        with open(HISTORY_PATH, "w") as f:
            json.dump(history, f, indent=2, cls=NpEncoder)
    except Exception as e:
        log.warning(f"Failed to save param history: {e}")


def record_param_trial(history: dict, pair: str, params: dict,
                       backtest: dict, diag: dict, improved: bool):
    """
    Record one parameter trial to the history ledger.
    This is the core 'memory' — prevents re-testing what has already failed.
    """
    if pair not in history:
        history[pair] = []
    history[pair].append({
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "params":     params,
        "backtest":   {k: round(v, 4) if isinstance(v, float) else v
                       for k, v in backtest.items()},
        "real_diag":  {k: v for k, v in diag.items() if k != "action"},
        "improved":   improved,
    })
    # Keep only the last 60 trials per coin to avoid unbounded growth
    history[pair] = history[pair][-60:]


def get_tried_combos(history: dict, pair: str) -> set:
    """
    Return a set of (short, long, sl, tp) tuples already tried for this pair.
    Any combo that was tried and showed negative backtest profit is blacklisted.
    """
    tried = set()
    for trial in history.get(pair, []):
        p = trial.get("params", {})
        bt = trial.get("backtest", {})
        if bt.get("net_profit", 0) <= 0:
            tried.add((p.get("short"), p.get("long"),
                       p.get("stop_loss"), p.get("take_profit")))
    return tried


def get_adaptive_search_bounds(diag: dict, history: dict, pair: str) -> dict:
    """
    Based on the failure mode diagnosis and historical trials, return
    adjusted search parameter bounds for the grid search.

    Returns: {"shorts": [...], "longs": [...], "sls": [...], "tps": [...]}
    """
    mode = diag.get("mode", FM_NO_DATA)

    # Baseline 1-hour bounds — always the floor
    shorts = [5, 8, 10, 15]
    longs  = [20, 25, 30, 40, 50, 60]
    sls    = [3.0, 4.0, 5.0, 6.0]
    tps    = [6.0, 8.0, 10.0, 12.0, 15.0]

    if mode == FM_WHIPSAW:
        # SMA windows are too short — push toward longer windows
        # Crossovers happen too fast; we need the baseline to be slower
        shorts = [8, 10, 15, 20]
        longs  = [30, 40, 50, 60, 80]
        # SL/TP stay wide since we want to hold through the real trend
        sls    = [4.0, 5.0, 6.0, 7.0]
        tps    = [8.0, 10.0, 12.0, 15.0]
        log.info(f"  Adaptive: WHIPSAW → searching longer windows (short 8-20, long 30-80)")

    elif mode == FM_SL_TOO_TIGHT:
        # Stop losses firing on normal noise — need wider SL
        # Keep same SMA windows, push SL up
        shorts = [5, 8, 10, 15]
        longs  = [25, 30, 40, 50]
        sls    = [5.0, 6.0, 7.0, 8.0]
        tps    = [8.0, 10.0, 12.0, 15.0, 18.0]
        log.info(f"  Adaptive: SL_TOO_TIGHT → searching wider stop-losses (5-8%)")

    elif mode == FM_TP_TOO_LOOSE:
        # Price reaching good levels but reversing — tighten TP to harvest gains
        avg_mfe = diag.get("avg_mfe_pct", 4.0)
        # Set TP options around and below the avg MFE
        tp_anchor = max(4.0, avg_mfe * 0.6)
        tps = sorted(set([round(tp_anchor, 1),
                          round(tp_anchor * 0.8, 1),
                          round(tp_anchor * 1.0, 1),
                          round(tp_anchor * 1.2, 1),
                          round(tp_anchor * 1.5, 1),
                          6.0, 8.0]))
        sls = [3.0, 4.0, 5.0, 6.0]
        log.info(f"  Adaptive: TP_TOO_LOOSE → tightening TP range around MFE ({avg_mfe:.2f}%)")

    elif mode == FM_FEE_DRAG:
        # Fee is larger than move — need much longer windows to ride bigger trends
        shorts = [10, 15, 20, 25]
        longs  = [40, 50, 60, 80, 100]
        sls    = [5.0, 6.0, 7.0, 8.0]
        tps    = [10.0, 12.0, 15.0, 18.0, 20.0]
        log.info(f"  Adaptive: FEE_DRAG → searching very long windows for large trend moves")

    elif mode == FM_OK:
        # Strategy working — fine-tune around current params
        avg_mfe = diag.get("avg_mfe_pct", 8.0)
        avg_mae = diag.get("avg_mae_pct", 4.0)
        tps = sorted(set([round(avg_mfe * 0.7, 1), round(avg_mfe * 0.9, 1),
                          round(avg_mfe * 1.1, 1), 8.0, 10.0]))
        sls = sorted(set([round(avg_mae * 1.1, 1), round(avg_mae * 1.3, 1),
                          5.0, 6.0]))
        log.info(f"  Adaptive: OK → fine-tuning around MFE={avg_mfe:.2f}% / MAE={avg_mae:.2f}%")

    # else NO_DATA → use baseline bounds as-is

    # ── Remove already-failed combos from the search space ───────────────────
    blacklist = get_tried_combos(history, pair)
    if blacklist:
        log.info(f"  Skipping {len(blacklist)} previously failed combo(s)")

    return {"shorts": shorts, "longs": longs, "sls": sls, "tps": tps,
            "blacklist": blacklist}


# ─────────────────────────────────────────────────────────────────────────────
# Data fetching
# ─────────────────────────────────────────────────────────────────────────────
def fetch_candles(pair: str, granularity: str, total: int = 1500) -> pd.DataFrame:
    gran_sec = GRANULARITY_MAP.get(granularity, 3600)
    all_candles, end_time = [], datetime.utcnow()
    pid = pair
    while len(all_candles) < total:
        limit = min(300, total - len(all_candles))
        start = end_time - pd.Timedelta(seconds=limit * gran_sec)
        url = f"https://api.exchange.coinbase.com/products/{pid}/candles"
        try:
            r = requests.get(url, params={"granularity": gran_sec,
                                           "start": start.isoformat(),
                                           "end": end_time.isoformat()}, timeout=10)
            if r.status_code in (400, 404) and pid.endswith("-USDC"):
                pid = pid[:-4] + "USD"
                continue
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            all_candles.extend(data)
            end_time = datetime.utcfromtimestamp(min(c[0] for c in data))
            time.sleep(0.25)
        except Exception as e:
            log.warning(f"Candle fetch error for {pid}: {e}")
            break
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=["start","low","high","open","close","volume"])
    df["start"] = pd.to_numeric(df["start"])
    return df.sort_values("start").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Fast backtest
# ─────────────────────────────────────────────────────────────────────────────
def quick_backtest(closes, short_sma, long_sma, stop_loss_pct, take_profit_pct,
                   fee_rate=REAL_FEE_RATE, trade_usd=10.0, start_cash=100.0,
                   trailing_stop_pct=3.0, trailing_activation_pct=2.0):
    cash, holdings, entry_price = start_cash, 0.0, 0.0
    position = "NONE"
    wins, losses, trades = 0, 0, 0
    gain_sum, loss_sum = 0.0, 0.0
    highest_price = 0.0

    for i in range(1, len(closes)):
        p = closes[i]
        ps = short_sma[i]; pl = long_sma[i]
        pps = short_sma[i-1]; ppl = long_sma[i-1]
        if any(v != v for v in [ps, pl, pps, ppl]):  # NaN check
            continue

        if position == "NONE":
            if pps < ppl and ps > pl:       # Bullish crossover → BUY
                if cash >= trade_usd:
                    fee = trade_usd * fee_rate
                    holdings = (trade_usd - fee) / p
                    entry_price = p
                    highest_price = p
                    cash -= trade_usd
                    position = "LONG"
        elif position == "LONG":
            if p > highest_price:
                highest_price = p
            chg = (p - entry_price) / entry_price * 100
            exit_now = False
            if stop_loss_pct > 0 and chg <= -stop_loss_pct:
                exit_now = True
            elif take_profit_pct > 0 and chg >= take_profit_pct:
                exit_now = True
            # Trailing stop: activates once price rises enough, sells if it drops back
            elif trailing_stop_pct > 0 and trailing_activation_pct > 0 and chg >= trailing_activation_pct:
                trail_price = highest_price * (1.0 - trailing_stop_pct / 100.0)
                if p <= trail_price:
                    exit_now = True
            elif pps > ppl and ps < pl:     # Bearish crossover → SELL
                exit_now = True
            if exit_now:
                proceeds = holdings * p
                fee = proceeds * fee_rate
                pnl = proceeds - fee - (entry_price * holdings + entry_price * holdings * fee_rate)
                cash += proceeds - fee
                if pnl > 0:
                    wins += 1; gain_sum += pnl
                else:
                    losses += 1; loss_sum += abs(pnl)
                trades += 1
                holdings, entry_price, position, highest_price = 0.0, 0.0, "NONE", 0.0

    # Close open position at last price
    if position == "LONG" and holdings > 0:
        p = closes[-1]
        proceeds = holdings * p
        fee = proceeds * fee_rate
        cash += proceeds - fee

    win_rate = wins / trades * 100 if trades > 0 else 0.0
    pf = gain_sum / loss_sum if loss_sum > 0 else (999.0 if gain_sum > 0 else 1.0)
    return {"net_profit": cash - start_cash, "trades": trades,
            "win_rate": win_rate, "profit_factor": pf, "wins": wins, "losses": losses}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2/3: Adaptive optimizer
# ─────────────────────────────────────────────────────────────────────────────
def optimize_coin(pair: str, granularity: str, diag: dict, history: dict) -> dict:
    """
    Run grid search with walk-forward validation.
    Train on first 67% of candles, validate on remaining 33%.
    Only accepts parameters that perform well on both sets.
    Returns best parameter set found, or {} if no valid result.
    """
    log.info(f"Optimizing {pair} ({granularity})...")
    df = fetch_candles(pair, granularity, 1500)
    if df.empty or len(df) < 100:
        log.warning(f"  Not enough candle data for {pair}")
        return {}

    closes_full = pd.to_numeric(df["close"])

    # Walk-forward split: 67% train, 33% validate
    split_idx = int(len(closes_full) * 0.67)
    closes_train = closes_full.iloc[:split_idx]
    closes_val   = closes_full.iloc[split_idx:]

    log.info(f"  Walk-forward split: {len(closes_train)} train / {len(closes_val)} validate candles")

    if len(closes_val) < 50:
        log.warning(f"  Validation set too small ({len(closes_val)} candles), using full dataset")
        closes_train = closes_full
        closes_val = None

    bounds    = get_adaptive_search_bounds(diag, history, pair)
    SHORTS    = bounds["shorts"]
    LONGS     = bounds["longs"]
    SLS       = bounds["sls"]
    TPS       = bounds["tps"]
    blacklist = bounds["blacklist"]

    # Pre-compute SMAs for train set
    sma_cache_train = {}
    for w in set(SHORTS + LONGS):
        sma_cache_train[w] = closes_train.rolling(w).mean().to_numpy()
    closes_train_np = closes_train.to_numpy()

    # Pre-compute SMAs for validation set (if available)
    sma_cache_val = {}
    closes_val_np = None
    if closes_val is not None:
        for w in set(SHORTS + LONGS):
            sma_cache_val[w] = closes_val.rolling(w).mean().to_numpy()
        closes_val_np = closes_val.to_numpy()

    best, best_score = {}, -9999
    tested, skipped = 0, 0

    for s in SHORTS:
        for l in LONGS:
            if l <= s:
                continue
            for sl in SLS:
                for tp in TPS:
                    if (s, l, sl, tp) in blacklist:
                        skipped += 1
                        continue
                    tested += 1
                    r_train = quick_backtest(closes_train_np, sma_cache_train[s], sma_cache_train[l], sl, tp)
                    if r_train["trades"] < MIN_TRADES:
                        continue

                    # ── Composite score: quality > quantity ───────────────────
                    pf_capped = min(r_train["profit_factor"], 5.0)
                    train_score = (r_train["net_profit"] * 0.5
                                   + pf_capped * 10 * 0.3
                                   + r_train["win_rate"] * 0.2)

                    # Walk-forward validation: check on unseen data
                    if closes_val_np is not None and len(closes_val_np) >= l + 2:
                        r_val = quick_backtest(closes_val_np, sma_cache_val[s], sma_cache_val[l], sl, tp)
                        # Penalize if validation shows a loss
                        if r_val["trades"] > 0 and r_val["net_profit"] < 0:
                            train_score *= 0.5  # Heavy penalty for out-of-sample loss
                        elif r_val["trades"] > 0 and r_val["net_profit"] > 0:
                            train_score *= 1.2  # Bonus for out-of-sample profit

                    if train_score > best_score:
                        best_score = train_score
                        best = {"short": s, "long": l, "stop_loss": sl,
                                "take_profit": tp, **r_train}
                        # Add validation results if available
                        if closes_val_np is not None and len(closes_val_np) >= l + 2:
                            r_val = quick_backtest(closes_val_np, sma_cache_val[s], sma_cache_val[l], sl, tp)
                            best["val_net_profit"] = r_val["net_profit"]
                            best["val_win_rate"] = r_val["win_rate"]
                            best["val_trades"] = r_val["trades"]

    log.info(f"  Grid search: {tested} combos tested, {skipped} skipped (previously failed)")
    if best:
        val_info = ""
        if "val_net_profit" in best:
            val_info = f"  VAL: profit=${best['val_net_profit']:.2f} WR={best['val_win_rate']:.1f}% trades={best['val_trades']}"
        log.info(f"  Best: SMA {best['short']}/{best['long']}  SL={best['stop_loss']}%"
                 f"  TP={best['take_profit']}%  → profit=${best['net_profit']:.2f}"
                 f"  WR={best['win_rate']:.1f}%  PF={best['profit_factor']:.2f}  trades={best['trades']}"
                 f"{val_info}")
    return best


# ─────────────────────────────────────────────────────────────────────────────
# .env reader / writer
# ─────────────────────────────────────────────────────────────────────────────
def read_env() -> dict:
    env_path = os.path.join(DIR, ".env")
    data = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip()
    return data


def write_env_keys(updates: dict):
    """Update specific keys in .env in-place, preserving all comments."""
    env_path = os.path.join(DIR, ".env")
    with open(env_path) as f:
        lines = f.readlines()
    new_lines = []
    updated = set()
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}\n")
                updated.add(k)
                continue
        new_lines.append(line)
    for k, v in updates.items():
        if k not in updated:
            new_lines.append(f"{k}={v}\n")
    with open(env_path, "w") as f:
        f.writelines(new_lines)
    log.info(f"  Updated .env: {updates}")


def get_current_params(prefix: str) -> dict:
    env = read_env()
    return {
        "short":       int(env.get(f"{prefix}_SMA_SHORT_WINDOW",  env.get("CONSERVATIVE_SMA_SHORT_WINDOW", 5))),
        "long":        int(env.get(f"{prefix}_SMA_LONG_WINDOW",   env.get("CONSERVATIVE_SMA_LONG_WINDOW", 30))),
        "stop_loss":   float(env.get(f"{prefix}_STOP_LOSS_PERCENT",   "5.0")),
        "take_profit": float(env.get(f"{prefix}_TAKE_PROFIT_PERCENT", "8.0")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PM2 bot restart
# ─────────────────────────────────────────────────────────────────────────────
def restart_bot(bot_name: str, pair: str, amount: int, dry_run: bool = True):
    dr = "True" if dry_run else "False"
    cmd = (f"TRADING_PAIR={pair} TRADE_AMOUNT_USD={amount} DRY_RUN={dr} "
           f"pm2 restart {bot_name} --update-env")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        log.info(f"  ✅ Restarted {bot_name}")
    else:
        log.warning(f"  ⚠ Restart failed for {bot_name}: {result.stderr.strip()}")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4: Graduation check
# ─────────────────────────────────────────────────────────────────────────────
def check_graduation() -> list:
    ready = []
    for fpath in glob.glob(os.path.join(DIR, "completed_trades_*_dry.csv")):
        trades = []
        try:
            with open(fpath) as f:
                for row in csv.DictReader(f):
                    try:
                        trades.append(float(row["pnl_usd"]))
                    except (KeyError, ValueError):
                        pass
        except Exception:
            continue
        n = len(trades)
        if n < 20:
            continue
        wins     = sum(1 for p in trades if p > 0)
        win_rate = wins / n * 100
        net_pnl  = sum(trades)
        worst    = min(trades)
        if win_rate >= 55 and net_pnl > 0 and worst >= -2.50:
            coin = (os.path.basename(fpath)
                    .replace("completed_trades_", "")
                    .replace("_dry.csv", ""))
            ready.append({"coin": coin, "trades": n,
                          "win_rate": win_rate, "net_pnl": net_pnl})
    return ready


# ─────────────────────────────────────────────────────────────────────────────
# Notification
# ─────────────────────────────────────────────────────────────────────────────
def send_notification(subject: str, body: str):
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(DIR, ".env"))
        import smtplib
        from email.mime.text import MIMEText
        smtp_host = os.getenv("SMTP_HOST", "")
        smtp_port = int(os.getenv("SMTP_PORT", 465))
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASS", "")
        to_email  = os.getenv("NOTIFICATION_EMAIL", smtp_user)
        if not smtp_host or not smtp_user:
            return
        msg = MIMEText(body)
        msg["Subject"] = f"[TradingBot] {subject}"
        msg["From"]    = smtp_user
        msg["To"]      = to_email
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as srv:
            srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_user, to_email, msg.as_string())
        log.info(f"  Email sent: {subject}")
    except Exception as e:
        log.warning(f"  Email failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("AUTO LEARNER starting")
    log.info("=" * 60)

    history       = load_param_history()
    best_params_all = {}
    param_updates   = {}
    bots_to_restart = []
    diag_summary    = {}

    for pair, cfg in COINS.items():
        prefix = cfg["prefix"]
        log.info(f"\n{'─'*60}")
        log.info(f"  {pair}")
        log.info(f"{'─'*60}")

        # ── PHASE 1: Diagnose from real trade history ─────────────────────────
        real_trades = load_real_trades(pair)
        diag = analyze_failure_mode(real_trades)
        diag_summary[pair] = diag

        log.info(f"  Real trades loaded: {diag['n']}")
        if diag["n"] >= 5:
            log.info(f"  Failure mode:    {diag['mode']}")
            log.info(f"  Win rate:        {diag['win_rate']}%")
            log.info(f"  Avg gross:       {diag['avg_gross_pct']}%")
            log.info(f"  Avg MFE/MAE:     +{diag['avg_mfe_pct']}% / -{diag['avg_mae_pct']}%")
            log.info(f"  Avg hold:        {diag['avg_hold_h']}h")
            log.info(f"  Dominant exit:   {diag['dominant_exit']} ({diag['dominant_exit_pct']}%)")
            log.info(f"  Action:          {diag.get('action', 'N/A')}")
        else:
            log.info(f"  Not enough real trades to diagnose — using baseline search bounds")

        # ── Get current .env params ───────────────────────────────────────────
        current = get_current_params(prefix)
        log.info(f"  Current params:  SMA {current['short']}/{current['long']}"
                 f"  SL={current['stop_loss']}%  TP={current['take_profit']}%")

        # ── PHASE 2/3: Adaptive grid search ──────────────────────────────────
        best = optimize_coin(pair, cfg["gran"], diag, history)
        if not best:
            log.warning(f"  No valid optimizer result for {pair} — keeping current params")
            continue

        best_params_all[pair] = best

        # ── Run current params on same candle data for comparison ─────────────
        df = fetch_candles(pair, cfg["gran"], 1500)
        if df.empty:
            continue
        closes   = pd.to_numeric(df["close"]).to_numpy()
        closes_s = pd.to_numeric(df["close"])
        sma_s    = closes_s.rolling(current["short"]).mean().to_numpy()
        sma_l    = closes_s.rolling(current["long"]).mean().to_numpy()
        baseline = quick_backtest(closes, sma_s, sma_l,
                                  current["stop_loss"], current["take_profit"])

        log.info(f"  Baseline backtest: profit=${baseline['net_profit']:.2f}"
                 f"  WR={baseline['win_rate']:.1f}%  trades={baseline['trades']}")

        # ── Decide whether to update ──────────────────────────────────────────
        improvement    = best["net_profit"] - baseline["net_profit"]
        pct_improve    = (improvement / max(abs(baseline["net_profit"]), 0.5)) * 100

        params_changed = (best["short"] != current["short"] or
                          best["long"]  != current["long"] or
                          abs(best["stop_loss"]   - current["stop_loss"])   > 0.01 or
                          abs(best["take_profit"] - current["take_profit"]) > 0.01)

        # Require both: params changed AND improvement threshold AND positive backtest profit
        should_update = (params_changed and
                         pct_improve >= MIN_IMPROVEMENT_PCT and
                         best["net_profit"] > 0)

        # Record this trial in history (before deciding)
        record_param_trial(history, pair,
                           {"short": best["short"], "long": best["long"],
                            "stop_loss": best["stop_loss"], "take_profit": best["take_profit"]},
                           best, diag, should_update)

        if should_update:
            log.info(f"  🔄 IMPROVEMENT +{pct_improve:.1f}% with positive profit → updating params")
            param_updates[f"{prefix}_SMA_SHORT_WINDOW"]   = best["short"]
            param_updates[f"{prefix}_SMA_LONG_WINDOW"]    = best["long"]
            param_updates[f"{prefix}_STOP_LOSS_PERCENT"]  = best["stop_loss"]
            param_updates[f"{prefix}_TAKE_PROFIT_PERCENT"]= best["take_profit"]
            bots_to_restart.append((cfg["bot"], pair, cfg["amount"]))
        else:
            reason = []
            if not params_changed:
                reason.append("same params")
            if pct_improve < MIN_IMPROVEMENT_PCT:
                reason.append(f"improvement {pct_improve:.1f}% < {MIN_IMPROVEMENT_PCT}% threshold")
            if best["net_profit"] <= 0:
                reason.append("backtest still negative — no update until profitable")
            log.info(f"  ✓ Keeping current params ({'; '.join(reason)})")

        time.sleep(1)

    # ── Apply .env updates ────────────────────────────────────────────────────
    if param_updates:
        write_env_keys({k: str(v) for k, v in param_updates.items()})
        log.info(f"\n✅ Updated .env with {len(param_updates)} param changes")
    else:
        log.info(f"\n✓ No .env changes — all coins keeping current params")

    # ── Save history and best params ──────────────────────────────────────────
    save_param_history(history)
    log.info(f"📚 Param history saved to {HISTORY_PATH}")

    try:
        with open(BEST_PATH, "w") as f:
            json.dump({
                "timestamp":   datetime.now(timezone.utc).isoformat(),
                "best_params": best_params_all,
            }, f, indent=2)
        log.info(f"📁 Best params saved to {BEST_PATH}")
    except Exception as e:
        log.warning(f"Failed to save best params: {e}")

    # ── Restart affected bots ─────────────────────────────────────────────────
    for bot_name, pair, amount in bots_to_restart:
        restart_bot(bot_name, pair, amount, dry_run=True)

    # ── Run sim report ────────────────────────────────────────────────────────
    log.info("\nRunning sim_report...")
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(DIR, "sim_report.py")],
            capture_output=True, text=True, cwd=DIR
        )
        report_text = result.stdout
        log.info(report_text)
    except Exception as e:
        report_text = f"sim_report failed: {e}"
        log.warning(report_text)

    # ── Graduation check ──────────────────────────────────────────────────────
    ready_coins = check_graduation()
    if ready_coins:
        alert = "\n🚀 GRADUATION ALERT 🚀\nSim coins meeting live trading criteria:\n\n"
        for c in ready_coins:
            alert += (f"  {c['coin'].upper()}: {c['trades']} trades | "
                      f"WR={c['win_rate']:.1f}% | PnL=${c['net_pnl']:+.2f}\n")
        alert += "\nRun: python3 sim_report.py to see full details.\n"
        log.info(alert)
        send_notification("SIM BOT READY FOR LIVE TRADING", alert)
    else:
        log.info("No coins ready for graduation yet.")

    # ── Daily digest email ────────────────────────────────────────────────────
    digest = f"Auto-Learner Digest — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
    digest += f"Param updates applied: {len(param_updates) // 4} coin(s)\n"
    digest += f"Bots restarted: {len(bots_to_restart)}\n\n"
    digest += "─── Failure Mode Summary ───\n"
    for pair, d in diag_summary.items():
        mode = d.get("mode", "N/A")
        wr   = d.get("win_rate", "N/A")
        n    = d.get("n", 0)
        digest += f"  {pair:<14}: {mode:<16} WR={wr}%  ({n} real trades)\n"
    digest += "\n" + report_text[:2500]
    send_notification("Daily Learning Report", digest)

    log.info("\n" + "=" * 60)
    log.info("AUTO LEARNER complete")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
