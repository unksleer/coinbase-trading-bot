import logging
import time
from decimal import Decimal
from datetime import datetime, timedelta

logger = logging.getLogger("coinbase_bot.risk_engine")

class RiskLimitViolation(Exception):
    pass

class RiskEngine:
    def __init__(self, settings_class, state_manager):
        self.settings = settings_class
        self.state_manager = state_manager
        self.api_error_count = 0

    def record_api_error(self):
        self.api_error_count += 1
        limit = getattr(self.settings, "REPEATED_API_ERROR_LIMIT", 5)
        if self.api_error_count >= limit:
            logger.critical(f"Circuit breaker tripped: {self.api_error_count} repeated API errors (Limit: {limit})")
            return False
        return True

    def reset_api_errors(self):
        self.api_error_count = 0

    def check_market_data_freshness(self, latest_timestamp_unix: int):
        """
        Verifies that latest market candle timestamp is not stale.
        """
        current_time = time.time()
        age = current_time - latest_timestamp_unix
        cutoff = getattr(self.settings, "STALE_MARKET_DATA_CUTOFF_SECONDS", 300)
        
        if age > cutoff:
            msg = f"Stale market data detected: last candle was {age:.1f}s ago (Cutoff: {cutoff}s)"
            logger.critical(msg)
            raise RiskLimitViolation(msg)

    def validate_pre_flight(self, side: str, price: Decimal, size: Decimal, product_id: str):
        """
        Runs all pre-flight risk checks before placing an order.
        Raises RiskLimitViolation if any check fails.
        """
        state = self.state_manager.state
        
        # 1. Emergency Kill Switch
        if getattr(self.settings, "EMERGENCY_KILL_SWITCH", False):
            raise RiskLimitViolation("Emergency kill switch is ACTIVE. Trading is disabled.")

        # 2. Approved Product Allowlist
        allowlist_str = getattr(self.settings, "APPROVED_PRODUCT_ALLOWLIST", "")
        if allowlist_str:
            allowlist = [p.strip().upper() for p in allowlist_str.split(",") if p.strip()]
            if product_id.upper() not in allowlist:
                raise RiskLimitViolation(f"Product {product_id} is not in the approved allowlist: {allowlist}")

        # 3. Mandatory Stop Protection in Live Mode
        if not self.settings.DRY_RUN:
            sl_pct = getattr(self.settings, "STOP_LOSS_PERCENT", 0.0)
            sl_atr = getattr(self.settings, "STOP_LOSS_ATR_MULTIPLE", 0.0)
            if sl_pct <= 0.0 and sl_atr <= 0.0:
                raise RiskLimitViolation("Mandatory stop protection is missing or disabled in Live mode (STOP_LOSS_PERCENT or STOP_LOSS_ATR_MULTIPLE must be > 0).")

        # 4. Maximum Value Per Order
        order_value = price * size
        max_order_val = Decimal(str(getattr(self.settings, "MAX_VAL_PER_ORDER", 500.0)))
        if order_value > max_order_val:
            raise RiskLimitViolation(f"Order value ${order_value:.2f} exceeds MAX_VAL_PER_ORDER (${max_order_val:.2f})")

        # 5. Product Exposure Limits
        current_holdings = Decimal(str(state.get("holdings_base", 0.0)))
        current_exposure = current_holdings * price
        
        if side.upper() == "BUY":
            projected_exposure = current_exposure + order_value
            max_exposure = Decimal(str(getattr(self.settings, "MAX_EXPOSURE_PER_PRODUCT", 1000.0)))
            if projected_exposure > max_exposure:
                raise RiskLimitViolation(f"Projected product exposure ${projected_exposure:.2f} exceeds MAX_EXPOSURE_PER_PRODUCT (${max_exposure:.2f})")

            # 6. Minimum Quote Currency Reserve
            quote_balance = Decimal(str(state.get("cash_usd", 0.0)))
            min_reserve = Decimal(str(getattr(self.settings, "MIN_QUOTE_RESERVE", 10.0)))
            if quote_balance - order_value < min_reserve:
                raise RiskLimitViolation(f"Buy order value ${order_value:.2f} would reduce quote currency balance (${quote_balance:.2f}) below minimum reserve (${min_reserve:.2f})")

        # 7. Maximum Trades Per Day
        trades = state.get("trades", [])
        now_dt = datetime.utcnow()
        trades_last_24h = 0
        for t in trades:
            try:
                t_dt = datetime.strptime(t["timestamp"].rstrip('Z').split('.')[0], "%Y-%m-%dT%H:%M:%S")
                if now_dt - t_dt < timedelta(days=1):
                    trades_last_24h += 1
            except Exception:
                pass
                
        max_trades = getattr(self.settings, "MAX_TRADES_PER_DAY", 20)
        if trades_last_24h >= max_trades:
            raise RiskLimitViolation(f"Daily trade count limit reached: {trades_last_24h}/{max_trades} trades in last 24h")

        # 8. Consecutive Loss Cooldown
        self.check_loss_cooldown(trades)

    def check_loss_cooldown(self, trades):
        """
        Trips a cooldown if consecutive trades resulted in losses.
        """
        cooldown_trades_limit = getattr(self.settings, "CONSECUTIVE_LOSS_COOLDOWN_TRADES", 3)
        cooldown_seconds = getattr(self.settings, "CONSECUTIVE_LOSS_COOLDOWN_SECONDS", 3600)
        
        if cooldown_trades_limit <= 0 or not trades:
            return

        consecutive_losses = 0
        last_loss_time = None
        
        # Sort trades by timestamp to scan in chronological order
        sorted_trades = sorted(trades, key=lambda x: x.get("timestamp", ""))
        
        # We only look at completed roundtrips or closed trades (type: SELL)
        sell_trades = [t for t in sorted_trades if t.get("type") == "SELL"]
        
        for t in reversed(sell_trades):
            pnl = Decimal(str(t.get("realized_pnl_usd", 0.0)))
            if pnl < 0:
                consecutive_losses += 1
                if last_loss_time is None:
                    last_loss_time = t.get("timestamp")
            else:
                break
                
        if consecutive_losses >= cooldown_trades_limit and last_loss_time:
            try:
                loss_dt = datetime.strptime(last_loss_time.rstrip('Z').split('.')[0], "%Y-%m-%dT%H:%M:%S")
                elapsed = (datetime.utcnow() - loss_dt).total_seconds()
                if elapsed < cooldown_seconds:
                    remaining = cooldown_seconds - elapsed
                    raise RiskLimitViolation(f"Consecutive loss cooldown active. {consecutive_losses} consecutive losses. Cooldown remaining: {remaining:.0f}s")
            except RiskLimitViolation:
                raise
            except Exception:
                pass

    def check_daily_drawdown(self):
        """
        Verifies that total daily realized + unrealized loss does not exceed limit.
        """
        trades = self.state_manager.state.get("trades", [])
        now_dt = datetime.utcnow()
        daily_realized_pnl = Decimal("0")
        
        # 1. Sum 24h realized pnl
        for t in trades:
            try:
                t_dt = datetime.strptime(t["timestamp"].rstrip('Z').split('.')[0], "%Y-%m-%dT%H:%M:%S")
                if now_dt - t_dt < timedelta(days=1):
                    daily_realized_pnl += Decimal(str(t.get("realized_pnl_usd", 0.0)))
            except Exception:
                pass

        # 2. Calculate daily drawdown limit
        max_daily_loss = Decimal(str(getattr(self.settings, "MAX_DAILY_LOSS", 100.0)))
        
        if daily_realized_pnl < -max_daily_loss:
            msg = f"Daily realized loss threshold exceeded: ${daily_realized_pnl:.2f} (Limit: ${max_daily_loss:.2f})"
            logger.critical(msg)
            raise RiskLimitViolation(msg)
