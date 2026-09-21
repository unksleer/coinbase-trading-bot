#!/usr/bin/env python3
"""
Stock exit structures, swept with the entry rule held fixed.

Asks one question about the live RSI 35/65 mean-reversion rule: is "small
wins, occasional large loss" a defect in the exits or the shape of the rule?
The entry never changes; only the stop, target, trailing stop and RSI exit
do. Costs and the break-even gate are the live bot's.

Result on 2026-09-21, 12 tickers, 400 days of hourly bars, $100/trade: every
tighter exit lowered both the win rate and the net, monotonically. Live 5%
stop: 72% win, +$64. 3% stop: 58%, +$9. 2%: 49%, -$2. 1.5%: 43%, -$22.
Symmetric 1%/1%: 50%, -$76. The asymmetry is how mean reversion pays, not a
bug in it. Fetches from Alpaca, so unlike the stored-candle backtests a run
is not byte-for-byte reproducible; the shape of the result is.

    .venv/bin/python backtest_stock_exits.py
"""
import os
import sys
import statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import env_loader; env_loader.load()
from utils import stock_data
import compare_strategies as cs

TICKERS = ["DIS","JNJ","LPL","META","PG","QQQ","SLV","SPY","TSLA","WMT","XLP","XLU"]
SLIP = cs.SLIPPAGE
USD = 100.0

def simulate(closes, sl, tp, trail, trail_act, rsi_exit, gate=True):
    rsi = cs.rsi_series(closes, 14)
    pos = False; entry = peak = 0.0; pnls = []; reasons = {}
    for i in range(1, len(closes)):
        p = closes[i]
        if p != p or rsi[i] != rsi[i] or rsi[i-1] != rsi[i-1]:
            continue
        if not pos:
            if rsi[i-1] < 35.0 <= rsi[i]:
                entry = p * (1 + SLIP); peak = p; pos = True
            continue
        peak = max(peak, p)
        chg = (p - entry) / entry * 100.0
        covers = chg >= 0.0 if gate else True
        why = None
        if sl > 0 and chg <= -sl: why = "stop"
        elif tp > 0 and chg >= tp: why = "target"
        elif trail > 0 and chg >= trail_act and p <= peak * (1 - trail/100.0) and covers: why = "trail"
        elif rsi_exit and rsi[i-1] > 65.0 >= rsi[i] and covers: why = "rsi"
        if why:
            fill = p * (1 - SLIP)
            pnls.append((fill - entry) / entry * USD); reasons[why] = reasons.get(why, 0) + 1
            pos = False
    return pnls, reasons

VARIANTS = [
    ("LIVE: SL5 TP8 trail1.5@1 + RSI exit",   dict(sl=5, tp=8, trail=1.5, trail_act=1.0, rsi_exit=True)),
    ("SL3, otherwise live",                   dict(sl=3, tp=8, trail=1.5, trail_act=1.0, rsi_exit=True)),
    ("SL2, otherwise live",                   dict(sl=2, tp=8, trail=1.5, trail_act=1.0, rsi_exit=True)),
    ("SL1.5, otherwise live",                 dict(sl=1.5, tp=8, trail=1.5, trail_act=1.0, rsi_exit=True)),
    ("SL5 TP8 trail, NO rsi exit",            dict(sl=5, tp=8, trail=1.5, trail_act=1.0, rsi_exit=False)),
    ("SL2 TP8 trail, NO rsi exit",            dict(sl=2, tp=8, trail=1.5, trail_act=1.0, rsi_exit=False)),
    ("symmetric SL1.5 TP1.5, nothing else",   dict(sl=1.5, tp=1.5, trail=0, trail_act=0, rsi_exit=False)),
    ("symmetric SL2 TP2, nothing else",       dict(sl=2, tp=2, trail=0, trail_act=0, rsi_exit=False)),
    ("SL2 TP3 trail1@1, NO rsi exit",         dict(sl=2, tp=3, trail=1.0, trail_act=1.0, rsi_exit=False)),
    ("SL1 TP1 scalp",                         dict(sl=1, tp=1, trail=0, trail_act=0, rsi_exit=False)),
]

series = {}
for t in TICKERS:
    c = stock_data.closes(t, "Hour", 400)
    if len(c) > 200: series[t] = c
    else: print(f"  {t}: only {len(c)} bars, skipped")
print(f"  {len(series)} tickers, {sum(len(c) for c in series.values())} hourly bars, slippage {SLIP*100:.2f}%/side, ${USD:.0f}/trade\n")

def hold(seg): return (seg[-1] - seg[0]) / seg[0] * USD
full_hold = sum(hold(c) for c in series.values())
oos_hold = sum(hold(c[int(len(c)*0.6):]) for c in series.values())
print(f"  buy-and-hold: full ${full_hold:+.2f}   last-40% ${oos_hold:+.2f}   ({len(series)} x ${USD:.0f})\n")
print(f"  {'variant':40} {'n':>4} {'win%':>5} {'avgW':>7} {'avgL':>7} {'W/L':>5} {'PF':>5} {'net':>9} {'OOS net':>9} {'sym+':>5}  exits")
print("  " + "-"*130)
for name, kw in VARIANTS:
    allp, oosp, symp, reasons = [], [], 0, {}
    for t, c in series.items():
        p, r = simulate(c, **kw); allp += p
        for k,v in r.items(): reasons[k] = reasons.get(k,0)+v
        if sum(p) > 0: symp += 1
        po, _ = simulate(c[int(len(c)*0.6):], **kw); oosp += po
    w = [x for x in allp if x > 0]; l = [x for x in allp if x <= 0]
    aw = st.mean(w) if w else 0; al_ = st.mean(l) if l else 0
    pf = (sum(w)/abs(sum(l))) if l and sum(l) else float('inf')
    print(f"  {name:40} {len(allp):>4} {100*len(w)/max(1,len(allp)):>5.0f} {aw:>+7.2f} {al_:>+7.2f} {aw/abs(al_) if al_ else 0:>5.2f} {pf:>5.2f} {sum(allp):>+9.2f} {sum(oosp):>+9.2f} {symp:>3}/{len(series)}  {reasons}")
