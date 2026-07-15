import os
import json
from datetime import datetime

class StateManager:
    def __init__(self, state_file="state.json", dry_run=True):
        self.state_file = state_file
        self.dry_run = dry_run
        self.state = self.load_state()

    def load_state(self):
        """
        Loads state from a JSON file. If the file does not exist,
        initializes a default state.
        """
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                # If reading fails, we will re-initialize
                pass

        # Default state
        default_state = {
            "dry_run": self.dry_run,
            "position": "NONE",  # "NONE" (no active trade) or "LONG" (holding the asset)
            "entry_price": 0.0,
            "entry_time": None,
            "highest_price_since_entry": 0.0,
            "lowest_price_since_entry": 0.0,
            "max_drawdown_since_entry": 0.0,
            "cash_usd": 100.0 if self.dry_run else 0.0,  # Starting cash for dry run
            "holdings_base": 0.0,
            "trades": []
        }
        
        self.save_state(default_state)
        return default_state

    def save_state(self, state=None):
        """
        Saves the current state to the JSON file.
        """
        if state is not None:
            self.state = state
        
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            # We will rely on logging from the caller if needed
            pass

    def reload_state(self):
        """
        Reloads state from the JSON file to ensure we have the latest
        on-disk changes (e.g. from manual sells).
        """
        self.state = self.load_state()
        return self.state

    def record_trade(self, trade_type: str, price: float, amount_usd: float, amount_base: float, fee_usd: float = 0.0, reason: str = None, indicators: dict = None):
        """
        Records a trade in the state and updates balances and position.
        """
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        # Get entry time of position before updating it
        pos_entry_time = self.state.get("entry_time", None)
        if pos_entry_time is None and self.state.get("position") == "LONG":
            pos_entry_time = self.get_entry_time()
            
        entry_time_record = pos_entry_time if trade_type == "SELL" else timestamp
        
        # Calculate realized PnL if selling
        pnl_usd = 0.0
        if trade_type == "SELL" and self.state["position"] == "LONG" and self.state["entry_price"] > 0:
            # PnL = (Sell Price - Buy Price) * Base Amount - Fees
            # We subtract both the Buy fee (entry_fee_usd) and the Sell fee (fee_usd)
            entry_fee = self.state.get("entry_fee_usd", 0.0)
            pnl_usd = (price - self.state["entry_price"]) * amount_base - (entry_fee + fee_usd)

        # Update balances
        if trade_type == "BUY":
            self.state["position"] = "LONG"
            self.state["entry_price"] = price
            self.state["entry_time"] = timestamp
            self.state["entry_fee_usd"] = fee_usd
            self.state["highest_price_since_entry"] = price
            self.state["lowest_price_since_entry"] = price
            self.state["max_drawdown_since_entry"] = 0.0
            self.state["cash_usd"] -= (amount_usd + fee_usd)
            self.state["holdings_base"] += amount_base
            self.state["entry_reason"] = reason or "SMA Bullish Cross"
            self.state["entry_indicators"] = indicators or {}
        elif trade_type == "SELL":
            # Log completed roundtrip before clearing state
            try:
                self.log_completed_roundtrip(
                    exit_time=timestamp,
                    entry_time=entry_time_record,
                    entry_price=self.state["entry_price"],
                    exit_price=price,
                    entry_reason=self.state.get("entry_reason", "SMA Bullish Cross"),
                    exit_reason=reason or "SMA Bearish Cross",
                    amount_base=amount_base,
                    buy_fee=self.state.get("entry_fee_usd", 0.0),
                    sell_fee=fee_usd,
                    pnl_usd=pnl_usd,
                    highest_price=self.state.get("highest_price_since_entry", self.state["entry_price"]),
                    lowest_price=self.state.get("lowest_price_since_entry", self.state["entry_price"]),
                    entry_indicators=self.state.get("entry_indicators", {})
                )
            except Exception as e:
                pass

            self.state["position"] = "NONE"
            self.state["entry_price"] = 0.0
            self.state["entry_time"] = None
            self.state["entry_fee_usd"] = 0.0
            self.state["highest_price_since_entry"] = 0.0
            self.state["lowest_price_since_entry"] = 0.0
            self.state["max_drawdown_since_entry"] = 0.0
            self.state["entry_reason"] = None
            self.state["entry_indicators"] = {}
            self.state["cash_usd"] += (amount_usd - fee_usd)
            self.state["holdings_base"] -= amount_base
            if self.state["holdings_base"] < 1e-8:  # Clean up floating point dust
                self.state["holdings_base"] = 0.0

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
        self.save_state()

        # Real-time append to trades.csv
        try:
            csv_path = "/home/leeunks/coinbase-trading-bot/trades.csv"
            file_exists = os.path.exists(csv_path)
            
            # Extract pair name from state_file
            basename = os.path.basename(self.state_file)
            pair_name = "UNKNOWN"
            if basename.startswith("state_") and basename.endswith(".json"):
                pair_name = basename.replace("state_", "").replace(".json", "").upper()
            
            headers = [
                "timestamp", 
                "pair", 
                "type", 
                "price", 
                "amount_usd", 
                "amount_base", 
                "fee_usd", 
                "realized_pnl_usd", 
                "portfolio_value_usd",
                "dry_run",
                "entry_time"
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
            pass

        return trade_record

    def update_position_metrics(self, current_price: float):
        """
        Updates highest price, lowest price, and max drawdown since entry when in a LONG position.
        """
        if self.state.get("position") != "LONG":
            return

        # Initialize if not present or default
        if "highest_price_since_entry" not in self.state or self.state["highest_price_since_entry"] == 0.0:
            self.state["highest_price_since_entry"] = self.state.get("entry_price", current_price)
        if "lowest_price_since_entry" not in self.state or self.state["lowest_price_since_entry"] == 0.0:
            self.state["lowest_price_since_entry"] = self.state.get("entry_price", current_price)
        if "max_drawdown_since_entry" not in self.state:
            self.state["max_drawdown_since_entry"] = 0.0

        # Update peak price
        if current_price > self.state["highest_price_since_entry"]:
            self.state["highest_price_since_entry"] = current_price

        # Update lowest price
        if current_price < self.state["lowest_price_since_entry"] or self.state["lowest_price_since_entry"] == 0.0:
            self.state["lowest_price_since_entry"] = current_price

        # Calculate current drawdown from peak
        peak = self.state["highest_price_since_entry"]
        current_drawdown = 0.0
        if peak > 0.0:
            current_drawdown = ((current_price - peak) / peak) * 100

        # Update max drawdown (more negative is lower/larger drawdown)
        if current_drawdown < self.state["max_drawdown_since_entry"]:
            self.state["max_drawdown_since_entry"] = current_drawdown

        self.save_state()

    def get_entry_time(self):
        """
        Returns the entry time of the current position. 
        If entry_time is not set in the state, scans trades history for the latest BUY.
        """
        entry_time_str = self.state.get("entry_time")
        if entry_time_str:
            return entry_time_str
        
        # Fallback: scan trades history from newest to oldest
        trades = self.state.get("trades", [])
        for trade in reversed(trades):
            if trade.get("type") == "BUY":
                return trade.get("timestamp")
        return None

    def log_completed_roundtrip(self, exit_time, entry_time, entry_price, exit_price, entry_reason, exit_reason,
                                amount_base, buy_fee, sell_fee, pnl_usd, highest_price, lowest_price, entry_indicators):
        """
        Logs a completed roundtrip trade with all the requested details in completed_trades.csv.
        """
        csv_path = "/home/leeunks/coinbase-trading-bot/completed_trades.csv"
        file_exists = os.path.exists(csv_path)
        
        # Calculate gross and net percentage returns
        gross_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price > 0.0 else 0.0
        
        # Net Return % = Net PnL / Total capital invested (including buy fee)
        capital_invested = entry_price * amount_base + buy_fee
        net_pct = (pnl_usd / capital_invested) * 100.0 if capital_invested > 0.0 else 0.0
        
        total_fees = buy_fee + sell_fee
        
        # Calculate MFE & MAE relative to entry price
        mfe_pct = ((highest_price - entry_price) / entry_price) * 100.0 if entry_price > 0.0 else 0.0
        mae_pct = ((lowest_price - entry_price) / entry_price) * 100.0 if entry_price > 0.0 else 0.0
        
        # Calculate Holding Time in hours
        holding_time_hours = 0.0
        try:
            et_str = entry_time.rstrip('Z')
            xt_str = exit_time.rstrip('Z')
            if '.' in et_str:
                et_fmt = "%Y-%m-%dT%H:%M:%S.%f"
            else:
                et_fmt = "%Y-%m-%dT%H:%M:%S"
            if '.' in xt_str:
                xt_fmt = "%Y-%m-%dT%H:%M:%S.%f"
            else:
                xt_fmt = "%Y-%m-%dT%H:%M:%S"
                
            et_dt = datetime.strptime(et_str, et_fmt)
            xt_dt = datetime.strptime(xt_str, xt_fmt)
            holding_time_hours = (xt_dt - et_dt).total_seconds() / 3600.0
        except Exception:
            pass
            
        # Extract pair name from state_file
        basename = os.path.basename(self.state_file)
        pair_name = "UNKNOWN"
        if basename.startswith("state_") and basename.endswith(".json"):
            pair_name = basename.replace("state_", "").replace(".json", "").upper()
            
        # Parse friendly entry/exit reasons
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
        elif exit_reason == "STRATEGY":
            exit_reason_friendly = "SMA Bearish Cross"
            
        headers = [
            "pair",
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
            "entry_reason",
            "exit_reason",
            "gross_pct",
            "net_pct",
            "fees_usd",
            "mfe_pct",
            "mae_pct",
            "holding_time_hours",
            "entry_sma_short",
            "entry_sma_long",
            "entry_ema",
            "entry_rsi",
            "entry_spread"
        ]
        
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
