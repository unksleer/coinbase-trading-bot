"""
The signal table's strategy label must come from configuration, not a log.

The crypto signal log carries no `strategy` column. Reading it gave None and
the fallback labelled all nine Donchian bots SMA_CROSSOVER — invisible while
every one was IN TRADE, and wrong the moment one went flat, when a breakout
bot would be described in crossover vocabulary. A label a bot never wrote is
not a fact about the bot; its configuration is.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)

import dashboard  # noqa: E402

R = dashboard.DashboardHandler._strategy_for

BREAKOUT_ENV = {
    "ACTIVE_PROFILE": "CONSERVATIVE",
    "NEAR_ACTIVE_PROFILE": "BREAKOUT",
    "BREAKOUT_STRATEGY_TYPE": "DONCHIAN_BREAKOUT",
    "STRATEGY_TYPE": "SMA_CROSSOVER",
}


def test_crypto_resolves_through_the_active_profile():
    """The live case: no coin key, profile says BREAKOUT, base says SMA."""
    assert R("NEAR-USDC", BREAKOUT_ENV) == "DONCHIAN_BREAKOUT"


def test_a_coin_override_beats_the_profile():
    env = dict(BREAKOUT_ENV, NEAR_STRATEGY_TYPE="RSI_MEAN_REVERSION")
    assert R("NEAR-USDC", env) == "RSI_MEAN_REVERSION"


def test_a_coin_without_a_profile_falls_to_base():
    assert R("BTC-USDC", {"STRATEGY_TYPE": "SMA_CROSSOVER"}) == "SMA_CROSSOVER"


def test_the_log_column_is_never_preferred_over_config():
    """A stale or wrong column must not override what the bot is running."""
    assert R("NEAR-USDC", BREAKOUT_ENV, log_value="SMA_CROSSOVER") == "DONCHIAN_BREAKOUT"


def test_the_log_column_is_only_a_fallback_for_an_empty_env():
    """Keeps the synthetic-env tests honest without letting the column win."""
    assert R("ADA-USDC", {}, log_value="SMA_CROSSOVER") == "SMA_CROSSOVER"
    assert R("ADA-USDC", {}, log_value="RSI_MEAN_REVERSION") == "RSI_MEAN_REVERSION"


def test_nothing_at_all_matches_the_bots_own_default():
    """config/settings.py defaults STRATEGY_TYPE to SMA_CROSSOVER."""
    assert R("ADA-USDC", {}) == "SMA_CROSSOVER"


def test_stocks_use_the_stock_bots_naming():
    env = {"STOCK_STRATEGY": "RSI_MEAN_REVERSION", "STRATEGY_TYPE": "DONCHIAN_BREAKOUT"}
    assert R("stock-SPY", env) == "RSI_MEAN_REVERSION", "must not read the crypto key"


def test_a_ticker_override_beats_stock_strategy():
    env = {"STOCK_STRATEGY": "RSI_MEAN_REVERSION", "SPY_STRATEGY": "SMA_CROSSOVER"}
    assert R("stock-SPY", env) == "SMA_CROSSOVER"


def test_both_dashboard_paths_use_the_one_resolver():
    src = open(os.path.join(DIR, "dashboard.py")).read()
    assert src.count("_strategy_for(") >= 3          # def + two call sites
    assert '"strategy": strategy or "SMA_CROSSOVER"' not in src
    assert 'bot_strategy = self._strategy_for(pair, env)' in src
