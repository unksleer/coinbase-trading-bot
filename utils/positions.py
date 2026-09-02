"""
One reader for open positions, across both markets.

This logic existed twice and was wrong twice, in the same two ways. Crypto
state files record `holdings_base` and are named state_<pair>_dry.json; stock
files record `shares` and are named state_stock_<ticker>_paper.json. Code
written for the crypto shape read a quantity of 0 for every equity position and
derived a symbol of STOCK_WMT_PAPER, which no price feed resolves.

Both failures produce a P&L of exactly $0.00 — a missing number rendering as a
real one. The dashboard showed six positions flat while they were collectively
down $0.78; the emailed P&L statement showed the same zeros.

So the parsing lives here now and both read it. A third consumer gets it right
for free, which is the point.
"""
import glob
import json
import os

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse(path, state):
    stem = os.path.basename(path).replace("state_", "").replace(".json", "")

    if stem.startswith("stock_"):
        symbol = (stem[len("stock_"):]
                  .replace("_paper", "").replace("_live", "").upper())
        return {
            "symbol": symbol,
            "market": "stocks",
            "quantity": float(state.get("shares", 0.0) or 0.0),
            "dry_run": stem.endswith("_paper") or bool(state.get("dry_run", True)),
        }

    return {
        "symbol": stem.replace("_dry", "").upper(),
        "market": "crypto",
        "quantity": float(state.get("holdings_base", 0.0) or 0.0),
        "dry_run": stem.endswith("_dry") or bool(state.get("dry_run", True)),
    }


def open_positions(root=None):
    """
    Every state file currently holding a position, normalised.

    Each entry has: symbol, market, quantity, entry_price, entry_time,
    dry_run, entry_fee_usd, state_file.
    """
    root = root or DIR
    out = []
    for path in sorted(glob.glob(os.path.join(root, "state_*.json"))):
        try:
            with open(path) as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            continue
        if state.get("position") != "LONG":
            continue

        entry = _parse(path, state)
        entry.update({
            "entry_price": float(state.get("entry_price", 0.0) or 0.0),
            "entry_time": state.get("entry_time") or "",
            "entry_fee_usd": float(state.get("entry_fee_usd", 0.0) or 0.0),
            "state_file": os.path.basename(path),
        })
        out.append(entry)
    return out


def unrealised(position, current_price):
    """
    Mark-to-market P&L, or None when the price is unknown.

    None rather than 0.0 deliberately: a position with no available price is
    not flat, and reporting it as flat is how six underwater positions read as
    breaking even.
    """
    if current_price is None or current_price <= 0:
        return None
    if position["entry_price"] <= 0 or position["quantity"] <= 0:
        return None
    gross = (current_price - position["entry_price"]) * position["quantity"]
    return gross - position["entry_fee_usd"]


def price(symbol, market, timeout=6):
    """
    Last price for a symbol, or None. Venue-appropriate, never raises.

    Uncached — callers that poll should cache. Returning None on failure is
    the contract that matters: it must never be confused with a price of zero.
    """
    try:
        if market == "stocks":
            from utils import stock_data
            closes = stock_data.closes(symbol, "Hour", 5)
            return float(closes[-1]) if len(closes) else None

        import requests
        for product in (symbol, symbol.replace("-USDC", "-USD")):
            response = requests.get(
                f"https://api.exchange.coinbase.com/products/{product}/ticker",
                timeout=timeout)
            if response.status_code == 200:
                return float(response.json()["price"])
    except Exception:
        return None
    return None
