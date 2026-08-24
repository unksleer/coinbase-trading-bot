import os
import json
import fcntl
import logging
import shutil
import threading
from datetime import datetime

logger = logging.getLogger("coinbase_bot.state")

# Version 2 schema: added "orders" dictionary
STATE_SCHEMA_VERSION = 2

class CorruptStateError(Exception):
    pass

class StateLockError(Exception):
    pass

class StateLock:
    _thread_locks = {}
    _thread_locks_lock = threading.Lock()

    def __init__(self, lock_file_path):
        self.lock_file_path = os.path.abspath(lock_file_path)
        self.lock_fd = None
        self._count = 0
        
        with self._thread_locks_lock:
            if self.lock_file_path not in self._thread_locks:
                self._thread_locks[self.lock_file_path] = threading.RLock()
            self._thread_lock = self._thread_locks[self.lock_file_path]

    def acquire(self):
        self._thread_lock.acquire()
        if self._count == 0:
            try:
                self.lock_fd = os.open(self.lock_file_path, os.O_CREAT | os.O_WRONLY, 0o600)
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
            except Exception as e:
                self._thread_lock.release()
                logger.error(f"Failed to acquire file lock on {self.lock_file_path}: {e}")
                raise StateLockError(f"Lock acquisition failed: {e}")
        self._count += 1

    def release(self):
        if self._count > 0:
            self._count -= 1
            if self._count == 0 and self.lock_fd is not None:
                try:
                    fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                    os.close(self.lock_fd)
                except Exception as e:
                    logger.error(f"Failed to release file lock: {e}")
                finally:
                    self.lock_fd = None
            self._thread_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

def validate_state_schema(state: dict) -> bool:
    if not isinstance(state, dict):
        return False
    required_keys = {
        "version": int,
        "dry_run": bool,
        "position": str,
        "entry_price": (int, float),
        "holdings_base": (int, float),
        "cash_usd": (int, float),
        "trades": list,
        "orders": dict
    }
    for key, expected_type in required_keys.items():
        if key not in state:
            logger.error(f"State schema missing required key: '{key}'")
            return False
        if not isinstance(state[key], expected_type):
            logger.error(f"State schema key '{key}' has invalid type: expected {expected_type}, got {type(state[key])}")
            return False
    return True

def migrate_state(state: dict) -> dict:
    version = state.get("version", 1)
    if version == 1:
        state["version"] = 2
        if "orders" not in state:
            state["orders"] = {}
        if "entry_price" not in state:
            state["entry_price"] = 0.0
        if "holdings_base" not in state:
            state["holdings_base"] = 0.0
        if "cash_usd" not in state:
            state["cash_usd"] = 100.0 if state.get("dry_run", True) else 0.0
        if "trades" not in state:
            state["trades"] = []
    return state

class StateManager:
    def __init__(self, state_file="state.json", dry_run=True, persist_trade_csv=True):
        self.state_file = state_file
        self.dry_run = dry_run
        self.persist_trade_csv = persist_trade_csv
        self._lock = StateLock(self.state_file + ".lock")
        self.state = self.load_state()

    def lock(self):
        return self._lock

    def load_state(self):
        with self._lock:
            temp_file = self.state_file + ".tmp"
            bak_file = self.state_file + ".bak"
            
            def try_load(path):
                if not os.path.exists(path):
                    return None
                try:
                    with open(path, 'r') as f:
                        state = json.load(f)
                    state = migrate_state(state)
                    if validate_state_schema(state):
                        return state
                except Exception as e:
                    logger.error(f"Error loading state from {path}: {e}")
                return None

            main_exists = os.path.exists(self.state_file)
            bak_exists = os.path.exists(bak_file)
            
            if not main_exists and not bak_exists:
                # First run: Initialize default state safely
                logger.info(f"State database {self.state_file} not found. Creating default state...")
                default_state = {
                    "version": STATE_SCHEMA_VERSION,
                    "dry_run": self.dry_run,
                    "position": "NONE",
                    "entry_price": 0.0,
                    "entry_time": None,
                    "highest_price_since_entry": 0.0,
                    "lowest_price_since_entry": 0.0,
                    "max_drawdown_since_entry": 0.0,
                    "cash_usd": 100.0 if self.dry_run else 0.0,
                    "holdings_base": 0.0,
                    "trades": [],
                    "orders": {}
                }
                self.save_state(default_state)
                return default_state

            state = try_load(self.state_file)
            if state is not None:
                return state
                
            # If main state fails, try backup
            if main_exists:
                logger.warning(f"Main state file {self.state_file} failed to load. Trying backup...")
                state = try_load(bak_file)
                if state is not None:
                    logger.info(f"Successfully recovered state from backup file: {bak_file}")
                    try:
                        shutil.copy2(bak_file, self.state_file)
                    except Exception as e:
                        logger.error(f"Failed to restore main state file from backup: {e}")
                    return state

            # Both failed (or main failed and backup is missing)
            if not self.dry_run:
                logger.critical(f"FATAL: Live state file {self.state_file} is corrupt and backup recovery failed. Failing closed!")
                raise CorruptStateError(f"Live state file {self.state_file} is corrupt/unreadable.")
                
            logger.warning("Dry run state and backup missing/corrupt. Re-initializing default state.")
            default_state = {
                "version": STATE_SCHEMA_VERSION,
                "dry_run": self.dry_run,
                "position": "NONE",
                "entry_price": 0.0,
                "entry_time": None,
                "highest_price_since_entry": 0.0,
                "lowest_price_since_entry": 0.0,
                "max_drawdown_since_entry": 0.0,
                "cash_usd": 100.0,
                "holdings_base": 0.0,
                "trades": [],
                "orders": {}
            }
            self.save_state(default_state)
            return default_state

    def save_state(self, state=None):
        if state is not None:
            self.state = state
            
        with self._lock:
            temp_file = self.state_file + ".tmp"
            bak_file = self.state_file + ".bak"
            
            try:
                with open(temp_file, 'w') as f:
                    json.dump(self.state, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                    
                if os.path.exists(self.state_file):
                    try:
                        shutil.copy2(self.state_file, bak_file)
                    except Exception as e:
                        logger.warning(f"Could not create backup of state file: {e}")
                        
                os.replace(temp_file, self.state_file)
            except Exception as e:
                logger.error(f"Failed to save state atomically: {e}")
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass
                raise

    def reload_state(self):
        with self._lock:
            self.state = self.load_state()
            return self.state

    def record_trade(self, trade_type: str, price: float, amount_usd: float,
                     amount_base: float, fee_usd: float = 0.0,
                     reason: str = None, indicators: dict = None,
                     order_application: dict = None):
        """
        Records a trade and correctly handles partial fills for buys and sells.
        """
        with self._lock:
            timestamp = datetime.utcnow().isoformat() + "Z"
            
            pos_entry_time = self.state.get("entry_time", None)
            if pos_entry_time is None and self.state.get("position") == "LONG":
                pos_entry_time = self.get_entry_time()
                
            entry_time_record = pos_entry_time if trade_type == "SELL" else timestamp
            
            if amount_base <= 0 or amount_usd < 0 or fee_usd < 0 or price <= 0:
                raise ValueError("Trade price/base must be positive and quote/fee must be non-negative.")

            pnl_usd = 0.0
            allocated_entry_fee = 0.0
            if trade_type == "SELL" and self.state["position"] == "LONG" and self.state["entry_price"] > 0:
                holdings_before = self.state.get("holdings_base", 0.0)
                if amount_base > holdings_before + 1e-8:
                    raise ValueError(
                        f"Sell amount {amount_base} exceeds holdings {holdings_before}."
                    )
                total_entry_fee = self.state.get("entry_fee_usd", 0.0)
                allocated_entry_fee = total_entry_fee * min(amount_base / holdings_before, 1.0)
                pnl_usd = (price - self.state["entry_price"]) * amount_base - (allocated_entry_fee + fee_usd)

            if trade_type == "BUY":
                holdings_before = self.state.get("holdings_base", 0.0)
                if self.state.get("position") == "LONG" and holdings_before > 0:
                    total_holdings = holdings_before + amount_base
                    self.state["entry_price"] = (
                        (self.state["entry_price"] * holdings_before) + (price * amount_base)
                    ) / total_holdings
                    self.state["entry_fee_usd"] = self.state.get("entry_fee_usd", 0.0) + fee_usd
                    self.state["highest_price_since_entry"] = max(
                        self.state.get("highest_price_since_entry", price), price
                    )
                    self.state["lowest_price_since_entry"] = min(
                        self.state.get("lowest_price_since_entry", price), price
                    )
                else:
                    self.state["position"] = "LONG"
                    self.state["entry_price"] = price
                    self.state["entry_time"] = timestamp
                    self.state["entry_fee_usd"] = fee_usd
                    self.state["highest_price_since_entry"] = price
                    self.state["lowest_price_since_entry"] = price
                    self.state["max_drawdown_since_entry"] = 0.0
                    self.state["entry_reason"] = reason or "SMA Bullish Cross"
                    self.state["entry_indicators"] = indicators or {}
                self.state["cash_usd"] -= (amount_usd + fee_usd)
                self.state["holdings_base"] += amount_base
            elif trade_type == "SELL":
                try:
                    self.log_completed_roundtrip(
                        exit_time=timestamp,
                        entry_time=entry_time_record,
                        entry_price=self.state["entry_price"],
                        exit_price=price,
                        entry_reason=self.state.get("entry_reason", "SMA Bullish Cross"),
                        exit_reason=reason or "SMA Bearish Cross",
                        amount_base=amount_base,
                        buy_fee=allocated_entry_fee,
                        sell_fee=fee_usd,
                        pnl_usd=pnl_usd,
                        highest_price=self.state.get("highest_price_since_entry", self.state["entry_price"]),
                        lowest_price=self.state.get("lowest_price_since_entry", self.state["entry_price"]),
                        entry_indicators=self.state.get("entry_indicators", {})
                    )
                except Exception as e:
                    logger.error(f"Failed to log completed roundtrip: {e}")
                    raise

                remaining = self.state["holdings_base"] - amount_base
                if remaining < 1e-8:
                    self.state["position"] = "NONE"
                    self.state["entry_price"] = 0.0
                    self.state["entry_time"] = None
                    self.state["entry_fee_usd"] = 0.0
                    self.state["highest_price_since_entry"] = 0.0
                    self.state["lowest_price_since_entry"] = 0.0
                    self.state["max_drawdown_since_entry"] = 0.0
                    self.state["entry_reason"] = None
                    self.state["entry_indicators"] = {}
                    self.state["holdings_base"] = 0.0
                else:
                    self.state["holdings_base"] = remaining
                    self.state["entry_fee_usd"] = max(
                        0.0, self.state.get("entry_fee_usd", 0.0) - allocated_entry_fee
                    )
                    
                self.state["cash_usd"] += (amount_usd - fee_usd)

            trade_record = {
                "timestamp": timestamp,
                "type": trade_type,
                "price": price,
                "amount_usd": amount_usd,
                "amount_base": amount_base,
                "fee_usd": fee_usd,
                "realized_pnl_usd": pnl_usd,
                "portfolio_value_usd": self.state["cash_usd"] + (self.state["holdings_base"] * price),
                "entry_time": entry_time_record
            }
            
            self.state["trades"].append(trade_record)

            # Persist portfolio mutation and cumulative-fill checkpoint together.
            # This makes replay after a crash idempotent.
            if order_application:
                client_order_id = order_application["client_order_id"]
                order = self.state.get("orders", {}).get(client_order_id)
                if not order:
                    raise ValueError(f"Cannot apply fill for unknown order {client_order_id}.")
                order["applied_filled_size"] = str(order_application["filled_size"])
                order["applied_filled_value"] = str(order_application["filled_value"])
                order["applied_fee_usd"] = str(order_application["fee_usd"])
                order["updated_at"] = timestamp
            self.save_state()

            if not self.persist_trade_csv:
                return trade_record

            try:
                # Dynamic root and mode suffix isolation
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                suffix = "_dry" if self.dry_run else ""
                csv_path = os.path.join(project_root, f"trades{suffix}.csv")
                
                file_exists = os.path.exists(csv_path)
                basename = os.path.basename(self.state_file)
                pair_name = "UNKNOWN"
                if basename.startswith("state_") and basename.endswith(".json"):
                    pair_name = basename.replace("state_", "").replace(".json", "").upper()
                    # Strip suffix tags for reporting pair identification
                    pair_name = pair_name.replace("_DRY", "")
                
                headers = [
                    "timestamp", "pair", "type", "price", "amount_usd", 
                    "amount_base", "fee_usd", "realized_pnl_usd", 
                    "portfolio_value_usd", "dry_run", "entry_time"
                ]
                
                import csv
                with open(csv_path, 'a', newline='') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=headers)
                    if not file_exists:
                        writer.writeheader()
                    writer.writerow({
                        "timestamp": timestamp,
                        "pair": pair_name,
                        "type": trade_type,
                        "price": price,
                        "amount_usd": amount_usd,
                        "amount_base": amount_base,
                        "fee_usd": fee_usd,
                        "realized_pnl_usd": pnl_usd,
                        "portfolio_value_usd": trade_record["portfolio_value_usd"],
                        "dry_run": self.dry_run,
                        "entry_time": entry_time_record
                    })
            except Exception as e:
                logger.error(f"Failed to write to trades.csv: {e}")
                raise

            return trade_record

    def update_position_metrics(self, current_price: float):
        with self._lock:
            if self.state.get("position") != "LONG":
                return

            if "highest_price_since_entry" not in self.state or self.state["highest_price_since_entry"] == 0.0:
                self.state["highest_price_since_entry"] = self.state.get("entry_price", current_price)
            if "lowest_price_since_entry" not in self.state or self.state["lowest_price_since_entry"] == 0.0:
                self.state["lowest_price_since_entry"] = self.state.get("entry_price", current_price)
            if "max_drawdown_since_entry" not in self.state:
                self.state["max_drawdown_since_entry"] = 0.0

            if current_price > self.state["highest_price_since_entry"]:
                self.state["highest_price_since_entry"] = current_price

            if current_price < self.state["lowest_price_since_entry"] or self.state["lowest_price_since_entry"] == 0.0:
                self.state["lowest_price_since_entry"] = current_price

            peak = self.state["highest_price_since_entry"]
            current_drawdown = 0.0
            if peak > 0.0:
                current_drawdown = ((current_price - peak) / peak) * 100

            if current_drawdown < self.state["max_drawdown_since_entry"]:
                self.state["max_drawdown_since_entry"] = current_drawdown

            self.save_state()

    def get_entry_time(self):
        entry_time_str = self.state.get("entry_time")
        if entry_time_str:
            return entry_time_str
        
        trades = self.state.get("trades", [])
        for trade in reversed(trades):
            if trade.get("type") == "BUY":
                return trade.get("timestamp")
        return None

    def log_completed_roundtrip(self, exit_time, entry_time, entry_price, exit_price, entry_reason, exit_reason,
                                amount_base, buy_fee, sell_fee, pnl_usd, highest_price, lowest_price, entry_indicators):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        suffix = "_dry" if self.dry_run else ""
        # Per-coin file: COMPLETED_TRADES_FILE env var > auto per-coin name > shared fallback
        custom_path = os.getenv("COMPLETED_TRADES_FILE")
        if custom_path:
            csv_path = custom_path if os.path.isabs(custom_path) else os.path.join(project_root, custom_path)
        else:
            # Auto-derive coin name from state file for per-coin tracking
            basename = os.path.basename(self.state_file)
            coin_name = basename.replace("state_", "").replace("_dry.json", "").replace(".json", "").lower()
            csv_path = os.path.join(project_root, f"completed_trades_{coin_name}{suffix}.csv")
        file_exists = os.path.exists(csv_path)
        
        gross_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price > 0.0 else 0.0
        capital_invested = entry_price * amount_base + buy_fee
        net_pct = (pnl_usd / capital_invested) * 100.0 if capital_invested > 0.0 else 0.0
        total_fees = buy_fee + sell_fee
        
        mfe_pct = ((highest_price - entry_price) / entry_price) * 100.0 if entry_price > 0.0 else 0.0
        mae_pct = ((lowest_price - entry_price) / entry_price) * 100.0 if entry_price > 0.0 else 0.0
        
        holding_time_hours = 0.0
        try:
            et_str = entry_time.rstrip('Z')
            xt_str = exit_time.rstrip('Z')
            et_fmt = "%Y-%m-%dT%H:%M:%S.%f" if '.' in et_str else "%Y-%m-%dT%H:%M:%S"
            xt_fmt = "%Y-%m-%dT%H:%M:%S.%f" if '.' in xt_str else "%Y-%m-%dT%H:%M:%S"
            et_dt = datetime.strptime(et_str, et_fmt)
            xt_dt = datetime.strptime(xt_str, xt_fmt)
            holding_time_hours = (xt_dt - et_dt).total_seconds() / 3600.0
        except Exception:
            pass
            
        basename = os.path.basename(self.state_file)
        pair_name = "UNKNOWN"
        if basename.startswith("state_") and basename.endswith(".json"):
            pair_name = basename.replace("state_", "").replace(".json", "").upper()
            pair_name = pair_name.replace("_DRY", "")
            
        entry_reason_friendly = entry_reason
        exit_reason_friendly = exit_reason
        if exit_reason == "STOP_LOSS":
            exit_reason_friendly = "Stop Loss Triggered"
        elif exit_reason == "TAKE_PROFIT":
            exit_reason_friendly = "Take Profit Triggered"
        elif "STOP_LOSS" in exit_reason:
            exit_reason_friendly = "Stop Loss Triggered"
        elif "TAKE_PROFIT" in exit_reason:
            exit_reason_friendly = "Take Profit Triggered"
        elif "BREAK_EVEN" in exit_reason:
            exit_reason_friendly = "Break-even Stop Triggered"
        elif "TRAILING_STOP" in exit_reason:
            exit_reason_friendly = "Trailing Stop Triggered"
        elif exit_reason == "STRATEGY":
            from config.settings import Settings
            if Settings.STRATEGY_TYPE == "RSI_MEAN_REVERSION":
                exit_reason_friendly = "RSI Fell back from Overbought"
            else:
                exit_reason_friendly = "SMA Bearish Cross"
            
        headers = [
            "pair", "entry_time", "exit_time", "entry_price", "exit_price",
            "entry_reason", "exit_reason", "gross_pct", "net_pct", "pnl_usd",
            "fees_usd", "mfe_pct", "mae_pct", "holding_time_hours",
            "entry_sma_short", "entry_sma_long", "entry_ema", "entry_rsi", "entry_spread"
        ]
        
        try:
            import csv
            with open(csv_path, 'a', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=headers)
                if not file_exists:
                    writer.writeheader()
                writer.writerow({
                    "pair": pair_name,
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(exit_price, 4),
                    "entry_reason": entry_reason_friendly,
                    "exit_reason": exit_reason_friendly,
                    "gross_pct": round(gross_pct, 4),
                    "net_pct": round(net_pct, 4),
                    "pnl_usd": round(pnl_usd, 4),
                    "fees_usd": round(total_fees, 6),
                    "mfe_pct": round(mfe_pct, 4),
                    "mae_pct": round(mae_pct, 4),
                    "holding_time_hours": round(holding_time_hours, 4),
                    "entry_sma_short": round(entry_indicators.get("sma_short", 0.0), 4) if entry_indicators.get("sma_short") is not None else "",
                    "entry_sma_long": round(entry_indicators.get("sma_long", 0.0), 4) if entry_indicators.get("sma_long") is not None else "",
                    "entry_ema": round(entry_indicators.get("ema", 0.0), 4) if entry_indicators.get("ema") is not None else "",
                    "entry_rsi": round(entry_indicators.get("rsi", 0.0), 2) if entry_indicators.get("rsi") is not None else "",
                    "entry_spread": round(entry_indicators.get("spread", 0.0), 4) if entry_indicators.get("spread") is not None else ""
                })
        except Exception as e:
            logger.error(f"Failed to write to completed_trades.csv: {e}")
            raise
