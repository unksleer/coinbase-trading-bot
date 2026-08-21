import json
import os
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from utils.order_journal import OrderJournal, STATE_UNKNOWN
from utils.risk_engine import RiskEngine, RiskLimitViolation


ROOT = Path(__file__).resolve().parents[1]


class MemoryStateManager:
    def __init__(self, state=None):
        self.state = state or {
            "orders": {},
            "holdings_base": 1.0,
            "cash_usd": 1000.0,
            "trades": [],
        }
        self.save_count = 0

    def save_state(self):
        self.save_count += 1


class SafetySettings:
    DRY_RUN = False
    DISABLE_STRATEGY_EXITS = False
    STOP_LOSS_PERCENT = 5.0
    STOP_LOSS_ATR_MULTIPLE = 0.0
    EMERGENCY_KILL_SWITCH = True
    APPROVED_PRODUCT_ALLOWLIST = "BTC-USDC"
    MAX_VAL_PER_ORDER = 10.0
    MAX_EXPOSURE_PER_PRODUCT = 10.0
    MIN_QUOTE_RESERVE = 999.0
    MAX_TRADES_PER_DAY = 1


class CancelNeverConfirmsClient:
    def cancel_orders(self, order_ids):
        return {"success": True}

    def get_order(self, order_id):
        return {
            "order": {
                "order_id": order_id,
                "status": "OPEN",
                "filled_size": "0",
                "filled_value": "0",
                "total_fees": "0",
            }
        }


class ExecutionSafetyTests(unittest.TestCase):
    def test_documented_risk_environment_names_are_loaded(self):
        env = os.environ.copy()
        env.update({
            "EMERGENCY_KILL_SWITCH": "true",
            "MAX_VAL_PER_ORDER": "12.5",
            "MAX_DAILY_LOSS": "7.5",
            "REPEATED_API_ERROR_LIMIT": "9",
        })
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json,sys,types; "
                    "dotenv=types.ModuleType('dotenv'); dotenv.load_dotenv=lambda: None; "
                    "sys.modules['dotenv']=dotenv; from config.settings import Settings; "
                    "print(json.dumps([Settings.EMERGENCY_KILL_SWITCH, "
                    "Settings.MAX_VAL_PER_ORDER, Settings.MAX_DAILY_LOSS, "
                    "Settings.REPEATED_API_ERROR_LIMIT]))"
                ),
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), [True, 12.5, 7.5, 9])

    def test_sell_can_reduce_exposure_during_kill_switch(self):
        manager = MemoryStateManager()
        engine = RiskEngine(SafetySettings, manager)
        engine.validate_pre_flight("SELL", Decimal("100"), Decimal("1"), "OTHER-USDC")

    def test_sell_cannot_exceed_tracked_holdings(self):
        manager = MemoryStateManager()
        engine = RiskEngine(SafetySettings, manager)
        with self.assertRaisesRegex(RiskLimitViolation, "exceeds tracked holdings"):
            engine.validate_pre_flight("SELL", Decimal("100"), Decimal("1.1"), "BTC-USDC")

    def test_unconfirmed_cancel_fails_closed(self):
        manager = MemoryStateManager()
        journal = OrderJournal(manager)
        journal.log_intent("cid", "BTC-USDC", "BUY", Decimal("0.1"), Decimal("100"))
        journal.update_order_id("cid", "oid")

        confirmed = journal.cancel_and_confirm(
            CancelNeverConfirmsClient(), "cid", poll_attempts=2, poll_interval_seconds=0
        )

        self.assertFalse(confirmed)
        self.assertEqual(journal.get_order("cid")["status"], STATE_UNKNOWN)

    def test_reconciliation_blocks_unapplied_fill(self):
        manager = MemoryStateManager()
        journal = OrderJournal(manager)
        journal.log_intent("cid", "BTC-USDC", "BUY", Decimal("0.1"), Decimal("100"))
        journal.update_order_status("cid", {
            "order_id": "oid",
            "status": "FILLED",
            "filled_size": "0.1",
            "filled_value": "10",
            "total_fees": "0.06",
        })

        self.assertFalse(journal.reconcile_unresolved_orders(client=None))
        journal.mark_fill_applied("cid")
        self.assertTrue(journal.reconcile_unresolved_orders(client=None))

    def test_disable_buys_does_not_guard_stop_losses(self):
        source = (ROOT / "bot.py").read_text()
        self.assertNotIn("not Settings.DISABLE_BUYS and Settings.STOP_LOSS_PERCENT", source)
        self.assertNotIn("not Settings.DISABLE_BUYS and Settings.STOP_LOSS_ATR_MULTIPLE", source)


if __name__ == "__main__":
    unittest.main()
