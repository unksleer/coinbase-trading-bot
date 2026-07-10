import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Determine the active profile (e.g. CONSERVATIVE, AGGRESSIVE)
ACTIVE_PROFILE = os.getenv("ACTIVE_PROFILE", "CONSERVATIVE").strip().upper()

def get_env_with_profile(key: str, default: str) -> str:
    """
    Returns environment variable value. If ACTIVE_PROFILE is set,
    first looks for f"{ACTIVE_PROFILE}_{key}". If not found, falls back
    to the base key, and finally to the default.
    """
    if ACTIVE_PROFILE:
        profile_key = f"{ACTIVE_PROFILE}_{key}"
        if profile_key in os.environ:
            return os.environ[profile_key]
    return os.getenv(key, default)

class Settings:
    # Coinbase Developer Platform (CDP) API Credentials
    COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
    # Handle newline escaping in secret key
    COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET", "").replace("\\n", "\n")

    # Active Profile Name
    PROFILE = ACTIVE_PROFILE

    # Trading Settings
    # Default to True if the setting is missing, empty, or set to True/1/t/yes
    _dry_run_val = get_env_with_profile("DRY_RUN", "True").strip().lower()
    DRY_RUN = _dry_run_val in ("", "true", "1", "t", "yes")
    TRADING_PAIR = get_env_with_profile("TRADING_PAIR", "BTC-USD")
    GRANULARITY = get_env_with_profile("GRANULARITY", "ONE_MINUTE")
    
    try:
        TRADE_AMOUNT_USD = float(get_env_with_profile("TRADE_AMOUNT_USD", "10.0"))
    except ValueError:
        TRADE_AMOUNT_USD = 10.0


    try:
        LOOP_INTERVAL_SECONDS = int(get_env_with_profile("LOOP_INTERVAL_SECONDS", "60"))
    except ValueError:
        LOOP_INTERVAL_SECONDS = 60

    try:
        SMA_SHORT_WINDOW = int(get_env_with_profile("SMA_SHORT_WINDOW", "10"))
    except ValueError:
        SMA_SHORT_WINDOW = 10

    try:
        SMA_LONG_WINDOW = int(get_env_with_profile("SMA_LONG_WINDOW", "30"))
    except ValueError:
        SMA_LONG_WINDOW = 30

    try:
        SMA_MIN_SPREAD_PERCENT = float(get_env_with_profile("SMA_MIN_SPREAD_PERCENT", "0.0"))
    except ValueError:
        SMA_MIN_SPREAD_PERCENT = 0.0

    try:
        STOP_LOSS_PERCENT = float(get_env_with_profile("STOP_LOSS_PERCENT", "0.0"))
    except ValueError:
        STOP_LOSS_PERCENT = 0.0

    try:
        TAKE_PROFIT_PERCENT = float(get_env_with_profile("TAKE_PROFIT_PERCENT", "0.0"))
    except ValueError:
        TAKE_PROFIT_PERCENT = 0.0

    RSI_FILTER_ENABLED = get_env_with_profile("RSI_FILTER_ENABLED", "False").lower() in ("true", "1", "t", "yes")
    
    try:
        RSI_PERIOD = int(get_env_with_profile("RSI_PERIOD", "14"))
    except ValueError:
        RSI_PERIOD = 14

    try:
        RSI_BUY_MAX = float(get_env_with_profile("RSI_BUY_MAX", "60.0"))
    except ValueError:
        RSI_BUY_MAX = 60.0

    try:
        RSI_SELL_MIN = float(get_env_with_profile("RSI_SELL_MIN", "40.0"))
    except ValueError:
        RSI_SELL_MIN = 40.0

    EMA_TREND_FILTER_ENABLED = get_env_with_profile("EMA_TREND_FILTER_ENABLED", "False").lower() in ("true", "1", "t", "yes")

    try:
        EMA_TREND_PERIOD = int(get_env_with_profile("EMA_TREND_PERIOD", "200"))
    except ValueError:
        EMA_TREND_PERIOD = 200

    @classmethod
    def validate(cls):
        """
        Validates that necessary configuration parameters are present.
        If DRY_RUN is False, API credentials must be set.
        """
        errors = []
        if not cls.DRY_RUN:
            if not cls.COINBASE_API_KEY:
                errors.append("COINBASE_API_KEY is required when DRY_RUN is False.")
            if not cls.COINBASE_API_SECRET:
                errors.append("COINBASE_API_SECRET is required when DRY_RUN is False.")
        
        if cls.TRADE_AMOUNT_USD <= 0:
            errors.append("TRADE_AMOUNT_USD must be greater than 0.")
            
        if cls.LOOP_INTERVAL_SECONDS < 5:
            errors.append("LOOP_INTERVAL_SECONDS must be at least 5 seconds to avoid API rate limits.")
            
        if cls.SMA_SHORT_WINDOW <= 0:
            errors.append("SMA_SHORT_WINDOW must be greater than 0.")
            
        if cls.SMA_LONG_WINDOW <= cls.SMA_SHORT_WINDOW:
            errors.append("SMA_LONG_WINDOW must be greater than SMA_SHORT_WINDOW.")
            
        if cls.SMA_MIN_SPREAD_PERCENT < 0:
            errors.append("SMA_MIN_SPREAD_PERCENT must be greater than or equal to 0.")

        if cls.STOP_LOSS_PERCENT < 0:
            errors.append("STOP_LOSS_PERCENT must be greater than or equal to 0.")

        if cls.TAKE_PROFIT_PERCENT < 0:
            errors.append("TAKE_PROFIT_PERCENT must be greater than or equal to 0.")

        if cls.RSI_FILTER_ENABLED:
            if cls.RSI_PERIOD <= 0:
                errors.append("RSI_PERIOD must be greater than 0 when RSI filter is enabled.")
            if not (0 <= cls.RSI_BUY_MAX <= 100):
                errors.append("RSI_BUY_MAX must be between 0 and 100.")
            if not (0 <= cls.RSI_SELL_MIN <= 100):
                errors.append("RSI_SELL_MIN must be between 0 and 100.")

        if cls.EMA_TREND_FILTER_ENABLED:
            if cls.EMA_TREND_PERIOD <= 0:
                errors.append("EMA_TREND_PERIOD must be greater than 0 when EMA filter is enabled.")
            
        valid_granularities = {
            "ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE", "THIRTY_MINUTE",
            "ONE_HOUR", "TWO_HOUR", "SIX_HOUR", "ONE_DAY"
        }
        if cls.GRANULARITY not in valid_granularities:
            errors.append(f"GRANULARITY must be one of: {', '.join(sorted(valid_granularities))}")
            
        return errors
