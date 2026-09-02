"""
How much independent evidence a set of trades actually carries.

Pooling twelve tickers looked like twelve times the evidence. It is not. SPY
and QQQ correlate at 0.95 on hourly returns, PG and XLP at 0.70 — when one
signals, the other usually signals on the same market move. That is one bet
placed twice, not two observations.

Measured on the live watchlist: mean pairwise correlation 0.18, and an
effective 4.0 independent bets out of 12. A pooled trade count therefore
overstates the evidence by roughly threefold, which matters directly because
the sample size needed to confirm an edge goes as the square of the noise-to-
signal ratio. Overstating n by 3x understates the time to a verdict by 3x.

The correction is deliberately conservative and approximate. It is better to
know the number is inflated and by roughly how much than to keep reporting a
count that quietly means something other than it says.
"""
import math


def correlation_matrix(series_by_key):
    """
    Pairwise correlations, aligned on shared keys.

    series_by_key maps a name to a dict of {timestamp: value}. Alignment is on
    the timestamp, never on position: aligning by position silently compares
    different hours to each other and produced a mean correlation of 0.02 for a
    universe whose real figure is 0.18.
    """
    names = sorted(series_by_key)
    if len(names) < 2:
        # One symbol cannot be correlated with anything. Unknown, not
        # independent.
        return names, None

    shared = set.intersection(*(set(series_by_key[n]) for n in names))
    if len(shared) < MIN_SHARED_POINTS:
        # Not measurable. Returning a zero matrix here would assert perfect
        # independence — the most flattering possible answer — on the strength
        # of having no data. Trade outcomes keyed by exit time almost never
        # overlap across symbols, which is exactly how that happens.
        return names, None

    stamps = sorted(shared)
    cols = {n: [series_by_key[n][t] for t in stamps] for n in names}

    def corr(a, b):
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((x - mb) ** 2 for x in b)
        if va <= 0 or vb <= 0:
            return 0.0
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        return cov / math.sqrt(va * vb)

    matrix = [[1.0 if i == j else corr(cols[names[i]], cols[names[j]])
               for j in range(len(names))] for i in range(len(names))]
    return names, matrix


MIN_SHARED_POINTS = 30


def effective_symbols(matrix):
    """
    Effective number of independent symbols: k^2 / sum of the correlation matrix.

    Standard measure. Equals k when everything is uncorrelated and 1 when
    everything is perfectly correlated, which is the behaviour wanted.
    """
    if matrix is None:
        return None
    k = len(matrix)
    if k == 0:
        return 0.0
    total = sum(sum(row) for row in matrix)
    if total <= 0:
        return float(k)
    return min(float(k), (k * k) / total)


def effective_trades(pooled_trades, k_symbols, k_effective):
    """
    Trade count discounted for how much the symbols overlap.

    Approximate by construction: it scales the pooled count by the fraction of
    symbols that are genuinely independent. A full treatment would need the
    correlation between the trade P&L series themselves, which needs far more
    trades than exist. This errs toward reporting less evidence than claimed.
    """
    if k_effective is None:
        return None          # unknown, not "all of them"
    if k_symbols <= 0 or pooled_trades <= 0:
        return 0
    return max(1, int(round(pooled_trades * (k_effective / k_symbols))))


def trades_needed(per_trade_sharpe, confidence=1.96):
    """
    Independent trades needed for the mean to clear zero at 95%.

    n ~ (z / sharpe)^2. This is the number that decides whether a strategy is
    testable at all: at a per-trade Sharpe of 0.35 it is ~31 trades, at 0.074 —
    the figure actually measured here — it is ~700.
    """
    if not per_trade_sharpe or per_trade_sharpe <= 0:
        return None
    return int(math.ceil((confidence / per_trade_sharpe) ** 2))


def summarise(series_by_key, pooled_trades, per_trade_sharpe=None):
    """One dict describing how much this evidence is really worth."""
    names, matrix = correlation_matrix(series_by_key)
    k = len(names)
    k_eff = effective_symbols(matrix)
    if matrix is None:
        off = []
    else:
        off = [matrix[i][j] for i in range(k) for j in range(i + 1, k)]
    return {
        "symbols": k,
        "measured": matrix is not None,
        "effective_symbols": round(k_eff, 1) if k_eff is not None else None,
        "mean_correlation": round(sum(off) / len(off), 3) if off else None,
        "max_correlation": round(max(off), 3) if off else None,
        "pooled_trades": pooled_trades,
        "effective_trades": effective_trades(pooled_trades, k, k_eff),
        "per_trade_sharpe": (round(per_trade_sharpe, 3)
                             if per_trade_sharpe is not None else None),
        "trades_needed": trades_needed(per_trade_sharpe),
    }
