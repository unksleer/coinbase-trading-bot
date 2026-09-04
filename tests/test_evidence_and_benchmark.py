"""
These two modules exist to stop the system flattering itself.

evidence.py answers "how much of this trade count is independent" — twelve
correlated tickers are not twelve bets, and crypto's nine coins measure as 1.4.
benchmark.py answers "is this better than not trading" — a bar the strategy
failed on 5 of 7 coins while passing a positive-P&L check.

The failure mode both must avoid is the same one: reporting the flattering
answer when the honest one is "cannot tell".
"""
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

from utils import benchmark, evidence, graduation  # noqa: E402


# ── evidence ────────────────────────────────────────────────────────────────

def _series(values_by_name):
    return {n: {str(i): v for i, v in enumerate(vals)}
            for n, vals in values_by_name.items()}


def test_identical_series_collapse_to_one_bet():
    base = [0.1, -0.2, 0.3, -0.1, 0.05] * 12
    names, matrix = evidence.correlation_matrix(_series({"A": base, "B": base}))
    assert evidence.effective_symbols(matrix) == pytest.approx(1.0, abs=0.05)


def test_opposing_series_are_not_counted_as_two_bets():
    base = [0.1, -0.2, 0.3, -0.1, 0.05] * 12
    inverse = [-v for v in base]
    _, matrix = evidence.correlation_matrix(_series({"A": base, "B": inverse}))
    # Perfectly anti-correlated is still one underlying factor, not two.
    assert evidence.effective_symbols(matrix) <= 2.0


def test_too_few_shared_points_reports_unknown_not_independent():
    """
    The bug this guards: trade outcomes keyed by exit time rarely overlap
    between symbols, and returning a zero matrix asserted PERFECT independence
    — the most flattering possible answer — purely from having no data.
    """
    names, matrix = evidence.correlation_matrix(
        {"A": {"t1": 0.1, "t2": 0.2}, "B": {"t3": 0.1, "t4": 0.2}})
    assert matrix is None
    assert evidence.effective_symbols(matrix) is None
    assert evidence.effective_trades(100, 2, None) is None


def test_single_symbol_is_unknown_not_independent():
    _, matrix = evidence.correlation_matrix({"A": {"t": 1.0}})
    assert matrix is None


def test_effective_trades_discounts_by_overlap():
    assert evidence.effective_trades(120, 12, 4.0) == 40
    assert evidence.effective_trades(120, 12, 12.0) == 120


def test_trades_needed_matches_the_gate_threshold():
    """
    n ~ (1.96/sharpe)^2. At the gate's 0.35 this is ~31 trades, which is what
    makes MIN_TRADES=20 nearly defensible; at the measured 0.074 it is ~700,
    which is what makes it not.
    """
    assert evidence.trades_needed(graduation.MIN_SHARPE) == 32
    assert evidence.trades_needed(0.074) > 600
    assert evidence.trades_needed(0) is None
    assert evidence.trades_needed(None) is None


# ── benchmark ───────────────────────────────────────────────────────────────

def test_hold_return_from_ledger_prices():
    rows = [{"entry_time": "2026-08-01", "entry_price": "100", "exit_price": "102"},
            {"entry_time": "2026-08-05", "entry_price": "103", "exit_price": "110"}]
    assert benchmark.from_trades(rows) == pytest.approx(0.10)


def test_hold_uses_chronological_order_not_file_order():
    rows = [{"entry_time": "2026-08-09", "entry_price": "150", "exit_price": "200"},
            {"entry_time": "2026-08-01", "entry_price": "100", "exit_price": "110"}]
    # first entry 100 -> last exit 200
    assert benchmark.from_trades(rows) == pytest.approx(1.0)


def test_missing_prices_report_unknown():
    assert benchmark.from_trades([{"entry_time": "x"}]) is None
    assert benchmark.from_trades([]) is None
    assert benchmark.excess_from_trades([{"entry_time": "x"}], [1.0]) is None


def test_excess_is_strategy_minus_hold():
    rows = [{"entry_time": "2026-08-01", "entry_price": "100", "exit_price": "110"}]
    # strategy made 5% on 100 capital; holding made 10%
    assert benchmark.excess_from_trades(rows, [5.0]) == pytest.approx(-0.05)


# ── the gate ────────────────────────────────────────────────────────────────

def test_unmeasured_benchmark_fails_rather_than_passes():
    """"Not measured" must never score the same as "beat the alternative"."""
    result = graduation.evaluate([1.0] * 25, benchmark_excess=None)
    check = next(c for c in result["criteria"] if c["key"] == "beats_hold")
    assert check["passed"] is False
    assert result["ready"] is False


def test_losing_to_hold_blocks_an_otherwise_perfect_record():
    """25 straight wins is still a failure if holding did better."""
    result = graduation.evaluate([1.0] * 25, benchmark_excess=-0.05)
    assert result["ready"] is False
    assert any("buy-and-hold" in b for b in result["blockers"])


def test_beating_hold_completes_the_gate():
    result = graduation.evaluate([1.0] * 25, benchmark_excess=0.05)
    assert result["ready"] is True
    assert result["score"] == graduation.MAX_SCORE
    assert result["total"] == 6


# ── history window ──────────────────────────────────────────────────────────
# Every crypto analysis used 400 candles until 2026-09-01 — 100 days at
# SIX_HOUR, and only 16.7 days at ONE_HOUR. The shorter window flattered the
# results materially: six-hour's gross edge measured 1.71% over 83 days, 1.48%
# over 100, and 1.22% over 500, against a 1.206% round trip. The apparent
# margin was a small-sample artifact that shrank to nothing as history grew.

def test_full_history_window_is_used_everywhere():
    import auto_learner
    assert auto_learner.MAX_CANDLES >= 2000

    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("walkforward.py", "analyze_correlation.py", "add_crypto.py",
                 "auto_learner.py"):
        with open(os.path.join(root, name)) as handle:
            body = handle.read()
        assert "fetch_candles(" not in body or "MAX_CANDLES" in body, \
            f"{name} fetches candles without using the full window"


def test_no_hardcoded_short_window_remains():
    import glob, os, re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for path in glob.glob(os.path.join(root, "*.py")):
        with open(path) as handle:
            for n, line in enumerate(handle, 1):
                if re.search(r"fetch_candles\([^)]*,\s*\d{1,4}\s*\)", line):
                    offenders.append(f"{os.path.basename(path)}:{n}")
    assert not offenders, f"hardcoded candle counts: {offenders}"


# ── the gate must not care what size a trade was ─────────────────────────────
# Position size went $10 -> $100 on 2026-09-04. The CSVs still hold the $10
# trades, so from the next exit one series mixes both. On a dollar basis that
# alone wrecks the per-trade Sharpe; on a percent basis it changes nothing.

def _same_pct_returns():
    return [0.8, -0.5, 1.2, -0.4, 0.9, -0.6] * 4            # 24 trades


def test_sharpe_is_identical_at_any_single_size():
    pcts = _same_pct_returns()
    at_10 = [p / 100 * 10 for p in pcts]
    at_100 = [p / 100 * 100 for p in pcts]
    assert graduation.per_trade_sharpe(at_10) == pytest.approx(
        graduation.per_trade_sharpe(at_100))


def test_mixed_sizes_corrupt_a_dollar_sharpe():
    """Documents the defect, so the reason for the percent basis survives."""
    pcts = _same_pct_returns()
    clean = graduation.per_trade_sharpe([p / 100 * 10 for p in pcts])
    mixed = [p / 100 * 10 for p in pcts[:12]] + [p / 100 * 100 for p in pcts[12:]]
    # How much it degrades depends on the sequence — 0.286 -> 0.016 on a 6-trade
    # demo, 0.307 -> 0.233 here. The guaranteed property is the direction: the
    # size change alone lowers the Sharpe of returns that did not change.
    assert graduation.per_trade_sharpe(mixed) < clean


def test_the_gate_on_a_percent_basis_ignores_size_mixing():
    pcts = _same_pct_returns()
    mixed_usd = [p / 100 * 10 for p in pcts[:12]] + [p / 100 * 100 for p in pcts[12:]]
    clean_usd = [p / 100 * 100 for p in pcts]
    a = graduation.evaluate(mixed_usd, benchmark_excess=0.01, pcts=pcts)
    b = graduation.evaluate(clean_usd, benchmark_excess=0.01, pcts=pcts)
    assert a["stats"]["sharpe"] == pytest.approx(b["stats"]["sharpe"])
    assert a["stats"]["basis"] == "percent"
    assert [c["passed"] for c in a["criteria"]] == [c["passed"] for c in b["criteria"]]


def test_worst_trade_floor_is_a_percentage_of_position():
    """-2.5% is -2.5% whether the trade was $10 or $100."""
    small = graduation.evaluate([-0.30] + [0.5] * 24, pcts=[-3.0] + [5.0] * 24)
    big = graduation.evaluate([-3.00] + [5.0] * 24, pcts=[-3.0] + [5.0] * 24)
    for result in (small, big):
        worst = next(c for c in result["criteria"] if c["key"] == "worst_trade")
        assert worst["passed"] is False, "a -3% trade breaches a -2.5% floor at any size"


def test_dollar_path_still_works_for_callers_without_percentages():
    result = graduation.evaluate([0.5] * 25, benchmark_excess=0.01)
    assert result["stats"]["basis"] == "dollars"
    assert result["ready"] is True
