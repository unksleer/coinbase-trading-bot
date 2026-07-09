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
            "cash_usd": 1000.0 if self.dry_run else 0.0,  # Starting cash for dry run
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

    def record_trade(self, trade_type: str, price: float, amount_usd: float, amount_base: float, fee_usd: float = 0.0):
        """
        Records a trade in the state and updates balances and position.
        
        Parameters:
        - trade_type: "BUY" or "SELL"
        - price: The execution price of the trade
        - amount_usd: The USD value involved (excluding/including fee depending on trade)
        - amount_base: The amount of base asset (e.g. BTC) traded
        - fee_usd: The trading fee paid in USD
        """
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        # Calculate realized PnL if selling
        pnl_usd = 0.0
        if trade_type == "SELL" and self.state["position"] == "LONG" and self.state["entry_price"] > 0:
            # PnL = (Sell Price - Buy Price) * Base Amount - Fees
            pnl_usd = (price - self.state["entry_price"]) * amount_base - fee_usd

        # Update balances
        if trade_type == "BUY":
            self.state["position"] = "LONG"
            self.state["entry_price"] = price
            self.state["cash_usd"] -= (amount_usd + fee_usd)
            self.state["holdings_base"] += amount_base
        elif trade_type == "SELL":
            self.state["position"] = "NONE"
            self.state["entry_price"] = 0.0
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
            "portfolio_value_usd": self.state["cash_usd"] + (self.state["holdings_base"] * price)
        }
        
        self.state["trades"].append(trade_record)
        self.save_state()
        return trade_record
