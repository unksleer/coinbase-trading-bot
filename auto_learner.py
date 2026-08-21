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

  PHASE 3 — DECIDE (advisory by default):
    - Require positive results in two unseen folds, cost-adjusted benchmark
      outperformance, parameter-neighbor stability, and ≥20 paper trades
    - Record an evidence-backed recommendation; only an exact manual approval
      plus LEARNER_MODE=APPLY_APPROVED may update paper-bot settings

  PHASE 4 — GRADUATE (real sim performance):
    - If a coin has ≥20 sim trades, win rate ≥55%, net PnL > $0 → promote to live

PM2 registration (run once):
  pm2 start auto_learner.py --name learner --interpreter .venv/bin/python \\
      --cron "0 4 * * *" --no-autorestart
"""
import os
import stat
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
from utils.learner_policy import (
    MODE_ADVISORY,
    MODE_APPLY_APPROVED,
    assess_candidate,
    atomic_write_json,
    load_approved_ids,
    normalize_mode,
    recommendation_id,
    should_apply_recommendation,
)

# ─────────────────────────────────────────────────────────────────────────────
DIR           = os.path.dirname(os.path.abspath(__file__))
LOG_PATH      = os.path.join(DIR, "logs", "auto_learner.log")
BEST_PATH     = os.path.join(DIR, "logs", "best_params.json")
HISTORY_PATH  = os.path.join(DIR, "logs", "param_history.json")
RECOMMENDATIONS_PATH = os.path.join(DIR, "logs", "learner_recommendations.json")
APPROVALS_PATH = os.path.join(DIR, "logs", "learner_approvals.json")

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
DEFAULT_SLIPPAGE_BPS = 10.0 # Conservative 0.10% adverse execution per side
MIN_VALIDATION_TRADES = 4
MIN_STABILITY_PASS_RATE = 0.60
MIN_REAL_TRADES_FOR_RECOMMENDATION = 20

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
                   trailing_stop_pct=3.0, trailing_activation_pct=2.0,
                   slippage_bps=DEFAULT_SLIPPAGE_BPS):
    cash, holdings, entry_price = start_cash, 0.0, 0.0
    position = "NONE"
    wins, losses, trades = 0, 0, 0
    gain_sum, loss_sum = 0.0, 0.0
    highest_price = 0.0
    entry_cost = 0.0
    peak_equity = start_cash
    max_drawdown_pct = 0.0
    slippage = slippage_bps / 10000.0

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
                    execution_price = p * (1.0 + slippage)
                    holdings = (trade_usd - fee) / execution_price
                    entry_price = execution_price
                    entry_cost = trade_usd
                    highest_price = execution_price
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
                execution_price = p * (1.0 - slippage)
                proceeds = holdings * execution_price
                fee = proceeds * fee_rate
                pnl = proceeds - fee - entry_cost
                cash += proceeds - fee
                if pnl > 0:
                    wins += 1; gain_sum += pnl
                else:
                    losses += 1; loss_sum += abs(pnl)
                trades += 1
                holdings, entry_price, position, highest_price, entry_cost = 0.0, 0.0, "NONE", 0.0, 0.0

        liquidation_value = holdings * p * (1.0 - slippage) * (1.0 - fee_rate)
        equity = cash + liquidation_value
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown_pct = min(max_drawdown_pct, (equity - peak_equity) / peak_equity * 100.0)

    # Close open position at last price
    if position == "LONG" and holdings > 0:
        p = closes[-1]
        execution_price = p * (1.0 - slippage)
        proceeds = holdings * execution_price
        fee = proceeds * fee_rate
        pnl = proceeds - fee - entry_cost
        cash += proceeds - fee
        if pnl > 0:
            wins += 1; gain_sum += pnl
        else:
            losses += 1; loss_sum += abs(pnl)
        trades += 1

    win_rate = wins / trades * 100 if trades > 0 else 0.0
    pf = gain_sum / loss_sum if loss_sum > 0 else (999.0 if gain_sum > 0 else 1.0)
    return {"net_profit": cash - start_cash, "trades": trades,
            "win_rate": win_rate, "profit_factor": pf, "wins": wins, "losses": losses,
            "gross_profit": gain_sum, "gross_loss": loss_sum,
            "max_drawdown_pct": max_drawdown_pct}


def buy_and_hold_backtest(closes, fee_rate=REAL_FEE_RATE, trade_usd=10.0,
                          slippage_bps=DEFAULT_SLIPPAGE_BPS):
    """Cost-adjusted benchmark using the same capital as one strategy trade."""
    if len(closes) < 2:
        return {"net_profit": 0.0}
    slippage = slippage_bps / 10000.0
    buy_price = float(closes[0]) * (1.0 + slippage)
    sell_price = float(closes[-1]) * (1.0 - slippage)
    buy_fee = trade_usd * fee_rate
    holdings = (trade_usd - buy_fee) / buy_price
    proceeds = holdings * sell_price
    sell_fee = proceeds * fee_rate
    return {"net_profit": proceeds - sell_fee - trade_usd}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2/3: Adaptive optimizer
# ─────────────────────────────────────────────────────────────────────────────
def _run_segment(closes, short_window, long_window, stop_loss, take_profit):
    series = pd.Series(closes)
    return quick_backtest(
        series.to_numpy(),
        series.rolling(short_window).mean().to_numpy(),
        series.rolling(long_window).mean().to_numpy(),
        stop_loss, take_profit,
    )


def _aggregate_results(results):
    trades = sum(r["trades"] for r in results)
    wins = sum(r["wins"] for r in results)
    losses = sum(r["losses"] for r in results)
    gross_profit = sum(r["gross_profit"] for r in results)
    gross_loss = sum(r["gross_loss"] for r in results)
    return {
        "net_profit": sum(r["net_profit"] for r in results),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / trades * 100 if trades else 0.0,
        "profit_factor": (
            gross_profit / gross_loss if gross_loss > 0
            else (999.0 if gross_profit > 0 else 1.0)
        ),
        "max_drawdown_pct": min((r["max_drawdown_pct"] for r in results), default=0.0),
    }


def _stability_results(validation_segments, params):
    neighbors = [
        (max(2, params["short"] - 1), params["long"], params["stop_loss"], params["take_profit"]),
        (params["short"] + 1, params["long"], params["stop_loss"], params["take_profit"]),
        (params["short"], max(params["short"] + 2, params["long"] - 2), params["stop_loss"], params["take_profit"]),
        (params["short"], params["long"] + 2, params["stop_loss"], params["take_profit"]),
        (params["short"], params["long"], max(1.0, params["stop_loss"] - 0.5), params["take_profit"]),
        (params["short"], params["long"], params["stop_loss"] + 0.5, params["take_profit"]),
        (params["short"], params["long"], params["stop_loss"], max(2.0, params["take_profit"] - 1.0)),
        (params["short"], params["long"], params["stop_loss"], params["take_profit"] + 1.0),
    ]
    results = []
    for short, long, stop_loss, take_profit in neighbors:
        if long <= short:
            continue
        aggregate = _aggregate_results([
            _run_segment(segment, short, long, stop_loss, take_profit)
            for segment in validation_segments
        ])
        results.append(aggregate)
    return results


def optimize_coin(pair: str, granularity: str, diag: dict, history: dict,
                  current: dict) -> dict:
    """
    Run grid search with two chronological out-of-sample validation folds.
    Candidates must pass profitability, benchmark, and stability gates.
    Returns best parameter set found, or {} if no valid result.
    """
    log.info(f"Optimizing {pair} ({granularity})...")
    df = fetch_candles(pair, granularity, 1500)
    if df.empty or len(df) < 100:
        log.warning(f"  Not enough candle data for {pair}")
        return {}

    closes_full = pd.to_numeric(df["close"]).to_numpy()
    split_1 = int(len(closes_full) * 0.60)
    split_2 = int(len(closes_full) * 0.80)
    closes_train = closes_full[:split_1]
    validation_segments = [closes_full[split_1:split_2], closes_full[split_2:]]
    log.info(
        f"  Walk-forward windows: {len(closes_train)} train / "
        f"{len(validation_segments[0])} validation-1 / "
        f"{len(validation_segments[1])} validation-2 candles"
    )

    bounds    = get_adaptive_search_bounds(diag, history, pair)
    SHORTS    = bounds["shorts"]
    LONGS     = bounds["longs"]
    SLS       = bounds["sls"]
    TPS       = bounds["tps"]
    blacklist = bounds["blacklist"]

    train_series = pd.Series(closes_train)
    sma_cache_train = {
        w: train_series.rolling(w).mean().to_numpy()
        for w in set(SHORTS + LONGS)
    }

    baseline_folds = [
        _run_segment(segment, current["short"], current["long"],
                     current["stop_loss"], current["take_profit"])
        for segment in validation_segments
    ]
    baseline_train = _run_segment(
        closes_train, current["short"], current["long"],
        current["stop_loss"], current["take_profit"]
    )
    baseline_validation = _aggregate_results(baseline_folds)
    benchmark_profit = sum(
        buy_and_hold_backtest(segment)["net_profit"]
        for segment in validation_segments
    )

    candidates = []
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
                    r_train = quick_backtest(closes_train, sma_cache_train[s], sma_cache_train[l], sl, tp)
                    if r_train["trades"] < MIN_TRADES:
                        continue
                    fold_results = [
                        _run_segment(segment, s, l, sl, tp)
                        for segment in validation_segments
                    ]
                    validation = _aggregate_results(fold_results)
                    candidates.append({
                        "short": s, "long": l, "stop_loss": sl,
                        "take_profit": tp, **r_train,
                        "val_net_profit": validation["net_profit"],
                        "val_win_rate": validation["win_rate"],
                        "val_profit_factor": validation["profit_factor"],
                        "val_trades": validation["trades"],
                        "val_max_drawdown_pct": validation["max_drawdown_pct"],
                        "profitable_folds": sum(r["net_profit"] > 0 for r in fold_results),
                        "validation_folds": len(fold_results),
                    })

    log.info(f"  Grid search: {tested} combos tested, {skipped} skipped (previously failed)")
    candidates.sort(
        key=lambda item: (item["val_net_profit"], item["net_profit"], item["val_profit_factor"]),
        reverse=True,
    )
    best = {}
    for candidate in candidates[:25]:
        neighbors = _stability_results(validation_segments, candidate)
        candidate["stability_pass_rate"] = (
            sum(r["net_profit"] >= 0 for r in neighbors) / len(neighbors)
            if neighbors else 0.0
        )
        decision = assess_candidate(
            candidate, {"val_net_profit": baseline_validation["net_profit"]},
            benchmark_profit,
            min_validation_trades=MIN_VALIDATION_TRADES,
            min_stability_pass_rate=MIN_STABILITY_PASS_RATE,
        )
        candidate["eligible"] = decision["eligible"]
        candidate["rejection_reasons"] = decision["reasons"]
        if decision["eligible"]:
            best = candidate
            break

    if best:
        log.info(f"  Best: SMA {best['short']}/{best['long']}  SL={best['stop_loss']}%"
                 f"  TP={best['take_profit']}%  → profit=${best['net_profit']:.2f}"
                 f"  WR={best['win_rate']:.1f}%  PF={best['profit_factor']:.2f}  trades={best['trades']}"
                 f"  VAL profit=${best['val_net_profit']:.2f} WR={best['val_win_rate']:.1f}%"
                 f" trades={best['val_trades']} stability={best['stability_pass_rate']:.0%}")
        best["baseline_val_net_profit"] = baseline_validation["net_profit"]
        best["baseline_train_net_profit"] = baseline_train["net_profit"]
        best["benchmark_net_profit"] = benchmark_profit
    elif candidates:
        top = candidates[0]
        log.info(
            "  No candidate passed Learner V2 hard gates. Top rejection: "
            + "; ".join(top.get("rejection_reasons", ["not in stable top candidates"]))
        )
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
    """Atomically update specific keys in .env, preserving comments and mode."""
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
    env_mode = stat.S_IMODE(os.stat(env_path).st_mode)
    temp_path = f"{env_path}.learner-{os.getpid()}.tmp"
    try:
        with open(temp_path, "w") as f:
            f.writelines(new_lines)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, env_mode)
        os.replace(temp_path, env_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
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

    runtime_env = read_env()
    learner_mode = normalize_mode(runtime_env.get("LEARNER_MODE", MODE_ADVISORY))
    recommendations_path = os.path.join(
        DIR, runtime_env.get("LEARNER_RECOMMENDATIONS_PATH", "logs/learner_recommendations.json")
    )
    approvals_path = os.path.join(
        DIR, runtime_env.get("LEARNER_APPROVALS_PATH", "logs/learner_approvals.json")
    )
    approved_ids = load_approved_ids(approvals_path)
    log.info(f"Learner mode: {learner_mode}")
    if learner_mode == MODE_ADVISORY:
        log.info("Advisory-only safety is active: .env and PM2 will not be changed.")

    history       = load_param_history()
    best_params_all = {}
    param_updates   = {}
    bots_to_restart = []
    diag_summary    = {}
    recommendations = []

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
        best = optimize_coin(pair, cfg["gran"], diag, history, current)
        if not best:
            log.warning(f"  No valid optimizer result for {pair} — keeping current params")
            continue

        best_params_all[pair] = best

        # Compare on the exact same training window used by the optimizer.
        baseline = {"net_profit": best["baseline_train_net_profit"]}

        log.info(
            f"  Baseline training profit=${baseline['net_profit']:.2f}; "
            f"validation profit=${best['baseline_val_net_profit']:.2f}; "
            f"buy-and-hold validation=${best['benchmark_net_profit']:.2f}"
        )

        # ── Decide whether to update ──────────────────────────────────────────
        improvement    = best["net_profit"] - baseline["net_profit"]
        pct_improve    = (improvement / max(abs(baseline["net_profit"]), 0.5)) * 100

        params_changed = (best["short"] != current["short"] or
                          best["long"]  != current["long"] or
                          abs(best["stop_loss"]   - current["stop_loss"])   > 0.01 or
                          abs(best["take_profit"] - current["take_profit"]) > 0.01)

        # The optimizer already enforced hard out-of-sample, benchmark, and
        # stability gates. This final gate requires a meaningful train-period gain.
        should_recommend = (params_changed and
                            pct_improve >= MIN_IMPROVEMENT_PCT and
                            best["net_profit"] > 0 and
                            best.get("eligible", False) and
                            diag.get("n", 0) >= MIN_REAL_TRADES_FOR_RECOMMENDATION)

        # Record this trial in history (before deciding)
        record_param_trial(history, pair,
                           {"short": best["short"], "long": best["long"],
                            "stop_loss": best["stop_loss"], "take_profit": best["take_profit"]},
                           best, diag, should_recommend)

        if should_recommend:
            proposed = {
                "short": best["short"], "long": best["long"],
                "stop_loss": best["stop_loss"], "take_profit": best["take_profit"],
            }
            rec_id = recommendation_id(pair, proposed)
            recommendation = {
                "id": rec_id,
                "pair": pair,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "current": current,
                "proposed": proposed,
                "evidence": {
                    "train_net_profit": best["net_profit"],
                    "validation_net_profit": best["val_net_profit"],
                    "validation_trades": best["val_trades"],
                    "validation_profit_factor": best["val_profit_factor"],
                    "validation_max_drawdown_pct": best["val_max_drawdown_pct"],
                    "profitable_folds": best["profitable_folds"],
                    "validation_folds": best["validation_folds"],
                    "stability_pass_rate": best["stability_pass_rate"],
                    "baseline_validation_net_profit": best["baseline_val_net_profit"],
                    "benchmark_net_profit": best["benchmark_net_profit"],
                    "training_improvement_pct": pct_improve,
                    "real_trade_diagnosis": diag,
                },
                "approved": rec_id in approved_ids,
                "applied": False,
            }
            recommendations.append(recommendation)

            if should_apply_recommendation(learner_mode, rec_id, approved_ids):
                log.info(f"  ✅ Approved recommendation {rec_id} → staging paper parameter update")
                param_updates[f"{prefix}_SMA_SHORT_WINDOW"] = best["short"]
                param_updates[f"{prefix}_SMA_LONG_WINDOW"] = best["long"]
                param_updates[f"{prefix}_STOP_LOSS_PERCENT"] = best["stop_loss"]
                param_updates[f"{prefix}_TAKE_PROFIT_PERCENT"] = best["take_profit"]
                bots_to_restart.append((cfg["bot"], pair, cfg["amount"]))
                recommendation["applied"] = True
            else:
                log.info(
                    f"  💡 Recommendation {rec_id} recorded for review; "
                    "no settings changed."
                )
        else:
            reason = []
            if not params_changed:
                reason.append("same params")
            if pct_improve < MIN_IMPROVEMENT_PCT:
                reason.append(f"improvement {pct_improve:.1f}% < {MIN_IMPROVEMENT_PCT}% threshold")
            if best["net_profit"] <= 0:
                reason.append("backtest still negative — no update until profitable")
            if diag.get("n", 0) < MIN_REAL_TRADES_FOR_RECOMMENDATION:
                reason.append(
                    f"real paper trades {diag.get('n', 0)} < "
                    f"{MIN_REAL_TRADES_FOR_RECOMMENDATION} minimum"
                )
            log.info(f"  ✓ Keeping current params ({'; '.join(reason)})")

        time.sleep(1)

    # ── Apply .env updates ────────────────────────────────────────────────────
    if param_updates and learner_mode == MODE_APPLY_APPROVED:
        write_env_keys({k: str(v) for k, v in param_updates.items()})
        log.info(f"\n✅ Applied approved paper-only changes: {len(param_updates)} settings")
    else:
        log.info(f"\n✓ No .env changes — learner remained advisory or had no approved recommendation")

    try:
        atomic_write_json(recommendations_path, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": learner_mode,
            "recommendations": recommendations,
        })
        log.info(f"📋 Recommendations saved to {recommendations_path}")
    except Exception as e:
        log.warning(f"Failed to save learner recommendations: {e}")

    # ── Save history and best params ──────────────────────────────────────────
    save_param_history(history)
    log.info(f"📚 Param history saved to {HISTORY_PATH}")

    try:
        atomic_write_json(BEST_PATH, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "best_params": best_params_all,
        })
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
    digest += f"Learner mode: {learner_mode}\n"
    digest += f"Recommendations generated: {len(recommendations)}\n"
    digest += f"Approved paper updates applied: {len(param_updates) // 4} coin(s)\n"
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
