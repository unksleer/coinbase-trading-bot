import pytest
import os
import sys
from unittest.mock import patch
import importlib

# Add parent directory to path to import local modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

@pytest.fixture
def mock_env():
    with patch.dict(os.environ, {}, clear=True):
        yield

def test_settings_validation_live_mode_fails_empty(mock_env):
    os.environ["DRY_RUN"] = "False"
    os.environ["COINBASE_API_KEY"] = "fake"
    os.environ["COINBASE_API_SECRET"] = "fake"
    
    import config.settings
    importlib.reload(config.settings)
    from config.settings import Settings
    
    errors = Settings.validate()
    # It should have errors because safety settings are missing/0
    assert any("PRODUCT_ALLOWLIST is required" in e for e in errors)
    assert any("MAX_ORDER_VALUE_USD must be > 0" in e for e in errors)
    assert any("MAX_EXPOSURE_PER_PRODUCT_USD must be > 0" in e for e in errors)
    assert any("MAX_TRADES_PER_DAY must be > 0" in e for e in errors)
    assert any("STALE_DATA_CUTOFF_MINUTES must be > 0" in e for e in errors)
    assert any("API_ERROR_LIMIT must be > 0" in e for e in errors)
    assert any("MAX_DAILY_LOSS_USD must be > 0" in e for e in errors)

def test_settings_validation_live_mode_passes_valid(mock_env):
    os.environ["DRY_RUN"] = "False"
    os.environ["COINBASE_API_KEY"] = "fake"
    os.environ["COINBASE_API_SECRET"] = "fake"
    os.environ["TRADING_PAIR"] = "BTC-USD"
    os.environ["PRODUCT_ALLOWLIST"] = "BTC-USD,ETH-USD"
    os.environ["MAX_ORDER_VALUE_USD"] = "100"
    os.environ["TRADE_AMOUNT_USD"] = "50"
    os.environ["MAX_EXPOSURE_PER_PRODUCT_USD"] = "200"
    os.environ["MAX_TRADES_PER_DAY"] = "10"
    os.environ["STALE_DATA_CUTOFF_MINUTES"] = "5"
    os.environ["API_ERROR_LIMIT"] = "3"
    os.environ["MAX_DAILY_LOSS_USD"] = "50"
    
    import config.settings
    importlib.reload(config.settings)
    from config.settings import Settings
    
    errors = Settings.validate()
    # Filter out API key errors if any, but focus on safety errors
    safety_errors = [e for e in errors if any(k in e for k in ["ALLOWLIST", "MAX_", "MIN_", "STALE", "API_ERROR"])]
    assert len(safety_errors) == 0

def test_stock_bot_validation_live_mode_fails_empty(mock_env):
    os.environ["ALPACA_PAPER"] = "False"
    
    import stock_bot
    importlib.reload(stock_bot)
    
    with pytest.raises(SystemExit) as exc:
        stock_bot.validate_safety()
    assert exc.value.code == 1

def test_stock_bot_validation_live_mode_passes_valid(mock_env, capsys):
    os.environ["ALPACA_PAPER"] = "False"
    os.environ["STOCK_TICKER"] = "SPY"
    os.environ["PRODUCT_ALLOWLIST"] = "SPY,QQQ"
    os.environ["MAX_ORDER_VALUE_USD"] = "100"
    os.environ["TRADE_AMOUNT_USD"] = "50"
    os.environ["MAX_EXPOSURE_PER_PRODUCT_USD"] = "200"
    os.environ["MAX_TRADES_PER_DAY"] = "10"
    os.environ["STALE_DATA_CUTOFF_MINUTES"] = "5"
    os.environ["API_ERROR_LIMIT"] = "3"
    os.environ["MAX_DAILY_LOSS_USD"] = "50"
    
    import stock_bot
    importlib.reload(stock_bot)
    
    # Should not sys.exit
    stock_bot.validate_safety()
    out, err = capsys.readouterr()
    assert "CRITICAL ERROR" not in out
