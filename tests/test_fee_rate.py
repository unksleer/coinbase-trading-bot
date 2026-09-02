"""
The fee must be the one the bot actually pays.

bot.py places post_only limit orders — maker. The configuration charged 0.60%,
which is Coinbase's TAKER rate, so every backtest and every simulated trade was
over-charged by 0.40% per round trip. Two files disagreed in writing about
which rate 0.006 even was:

    auto_learner.py   "Actual Coinbase Advanced Trade taker fee (0.60%)"
    utils/venue.py    "the tier-1 MAKER rate"

The venue's own schedule settles it: 0.40% maker, 0.60% taker at base tier.

The error mattered. Measured gross edge is +0.794%/trade, so at the simulated
1.20% round trip the strategy read -0.406%/trade; at the real 0.80% it reads
-0.006%. That is the difference between "clearly losing" and "at break-even".
"""
import os
import sys

import pytest

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

MAKER_PER_SIDE = 0.004


def test_learner_uses_the_maker_rate():
    import auto_learner
    assert auto_learner.REAL_FEE_RATE == pytest.approx(MAKER_PER_SIDE)


def test_venue_matches_the_learner():
    """One venue, one fee. They disagreed for weeks."""
    import auto_learner
    from utils import venue
    assert venue.CoinbaseVenue.fee_rate_per_side == pytest.approx(
        auto_learner.REAL_FEE_RATE)


def test_round_trip_is_reported_consistently():
    from utils import venue
    assert venue.CoinbaseVenue().round_trip_cost_pct() == pytest.approx(0.80)


def test_backtest_default_matches():
    import backtest
    import inspect
    sig = inspect.signature(backtest.run_backtest if hasattr(backtest, "run_backtest")
                            else backtest.backtest)
    fee = sig.parameters.get("fee_rate")
    if fee is not None:
        assert fee.default == pytest.approx(MAKER_PER_SIDE)


def test_settings_default_is_per_side_not_round_trip():
    """
    The fallback was 1.20 — a round-trip figure in a per-side field. Had .env
    ever gone missing it would have charged 2.40% a round trip and made every
    strategy look unviable.
    """
    src = open(os.path.join(DIR, "config", "settings.py")).read()
    assert 'get_env_with_profile("FEE_RATE_PERCENT", "0.40")' in src
    assert "FEE_RATE_PERCENT = 1.20" not in src


def test_stocks_remain_commission_free():
    """Alpaca charges nothing; a crypto fee leaking in would be worse than both."""
    from utils import venue
    assert venue.AlpacaVenue.fee_rate_per_side == 0.0
    import compare_strategies
    assert compare_strategies.STOCK_FEE == 0.0


def test_env_carries_the_maker_rate():
    with open(os.path.join(DIR, ".env")) as handle:
        lines = [l.strip() for l in handle if l.strip().startswith("FEE_RATE_PERCENT=")]
    assert lines == ["FEE_RATE_PERCENT=0.40"], lines


def test_the_correction_is_worth_what_was_claimed():
    """Arithmetic behind the change, pinned so the reasoning survives."""
    gross = 0.794
    assert gross - (0.60 * 2) == pytest.approx(-0.406, abs=0.001)   # as simulated
    assert gross - (0.40 * 2) == pytest.approx(-0.006, abs=0.001)   # as charged
