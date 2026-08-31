"""
Compare a strategy against not trading.

Every gate in this project has asked "did it make money" — a bar that a
strategy can clear while being strictly worse than doing nothing. On the crypto
side buy-and-hold beat the strategy on nine coins out of nine, and the gate
would still have called a small positive P&L a success.

The alternative to running a bot is not cash under a mattress; it is holding
the same instrument over the same window at no cost per trade. That is the
honest null hypothesis, and it is the one used here.

Two things are deliberately not done. Returns are not annualised — the windows
are short and annualising a six-week result invents precision. And the
benchmark is not risk-adjusted against the strategy's exposure: a strategy that
is in the market a third of the time carries less risk than holding throughout,
so beating buy-and-hold on raw return while holding less is a stronger result
than the raw number shows. Time-in-market is reported so that is visible
rather than assumed either way.
"""


def buy_and_hold_return(prices):
    """Fractional return from holding across the whole window."""
    clean = [p for p in prices if p and p == p and p > 0]
    if len(clean) < 2:
        return None
    return (clean[-1] - clean[0]) / clean[0]


def strategy_return(trades, starting_capital=100.0):
    """
    Fractional return from a sequence of trades.

    Uses the P&L actually recorded rather than recomputing from prices, so it
    reflects the fills, fees and slippage that really happened.
    """
    if not trades or starting_capital <= 0:
        return None
    return sum(trades) / starting_capital


def time_in_market(hold_hours, window_hours):
    """Fraction of the window the strategy actually held a position."""
    if not window_hours or window_hours <= 0:
        return None
    return min(1.0, max(0.0, sum(hold_hours) / window_hours))


def compare(trades, prices, hold_hours=None, window_hours=None,
            starting_capital=100.0):
    """
    Strategy against buy-and-hold over one window.

    Returns None for `beat` when either side cannot be computed, so a missing
    price series reads as "unknown" rather than silently as "passed".
    """
    strat = strategy_return(trades, starting_capital)
    hold = buy_and_hold_return(prices)

    result = {
        "strategy_return": strat,
        "hold_return": hold,
        "excess": None,
        "beat": None,
        "trades": len(trades or []),
        "time_in_market": None,
    }
    if hold_hours is not None and window_hours:
        result["time_in_market"] = time_in_market(hold_hours, window_hours)
    if strat is None or hold is None:
        return result

    result["excess"] = strat - hold
    result["beat"] = strat > hold
    return result


def describe(result):
    """One line for a report or a dashboard tooltip."""
    if result.get("beat") is None:
        return "no benchmark available for this window"
    strat = result["strategy_return"] * 100
    hold = result["hold_return"] * 100
    excess = result["excess"] * 100
    verdict = "beat" if result["beat"] else "LOST TO"
    line = (f"strategy {strat:+.2f}% vs buy-and-hold {hold:+.2f}% "
            f"({excess:+.2f}% excess) — {verdict} holding")
    tim = result.get("time_in_market")
    if tim is not None:
        line += f", in market {tim*100:.0f}% of the window"
    return line


def from_trades(rows):
    """
    Buy-and-hold return inferred from the trade ledger alone.

    Every completed-trade row already carries entry_price and exit_price, so
    holding across the strategy's own active window — first entry to last exit
    on the same instrument — needs no price feed and no API call. That matters
    because a benchmark nothing can compute is a benchmark that silently never
    gets applied.

    It is an approximation in one direction worth naming: it measures holding
    only while the strategy was active, so it ignores the periods the strategy
    sat out. A strategy that avoids a crash by being flat is not credited for
    it here.

    Returns None when the rows lack usable prices, which the gate treats as a
    failed criterion rather than a passed one.
    """
    usable = []
    for row in rows or []:
        stamp = row.get("entry_time")
        try:
            entry = float(row.get("entry_price") or 0)
            exit_ = float(row.get("exit_price") or 0)
        except (TypeError, ValueError):
            continue
        if stamp and entry > 0 and exit_ > 0:
            usable.append((stamp, entry, exit_))
    if len(usable) < 1:
        return None

    usable.sort(key=lambda r: r[0])
    first_entry = usable[0][1]
    last_exit = usable[-1][2]
    if first_entry <= 0:
        return None
    return (last_exit - first_entry) / first_entry


def excess_from_trades(rows, pnls, starting_capital=100.0):
    """Strategy return minus buy-and-hold, both from the ledger. None if unknown."""
    hold = from_trades(rows)
    strat = strategy_return(pnls, starting_capital)
    if hold is None or strat is None:
        return None
    return strat - hold
