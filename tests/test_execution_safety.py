import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from utils.order_journal import OrderJournal, STATE_UNKNOWN
from utils.risk_engine import RiskEngine, RiskLimitViolation
from utils.state import StateManager


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


class OpenPartialFillClient:
    def get_order(self, order_id):
        return {
            "order": {
                "order_id": order_id,
                "status": "OPEN",
                "filled_size": "0.2",
                "filled_value": "20",
                "total_fees": "0.12",
            }
        }


class ExecutionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_state_manager(self, name="state.json"):
        return StateManager(
            state_file=str(Path(self.temp_dir.name) / name),
            dry_run=True,
            persist_trade_csv=False,
        )

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

    def test_reconciliation_recovers_fill_once(self):
        manager = self.make_state_manager()
        journal = OrderJournal(manager)
        journal.log_intent("cid", "BTC-USDC", "BUY", Decimal("0.1"), Decimal("100"))
        journal.update_order_status("cid", {
            "order_id": "oid",
            "status": "FILLED",
            "filled_size": "0.1",
            "filled_value": "10",
            "total_fees": "0.06",
        })

        self.assertTrue(journal.reconcile_unresolved_orders(client=None))
        self.assertAlmostEqual(manager.state["holdings_base"], 0.1)
        self.assertAlmostEqual(manager.state["cash_usd"], 89.94)
        self.assertEqual(len(manager.state["trades"]), 1)

        self.assertTrue(journal.reconcile_unresolved_orders(client=None))
        self.assertAlmostEqual(manager.state["holdings_base"], 0.1)
        self.assertEqual(len(manager.state["trades"]), 1)

    def test_partial_fill_deltas_are_applied_cumulatively(self):
        manager = self.make_state_manager()
        journal = OrderJournal(manager)
        journal.log_intent("cid", "BTC-USDC", "BUY", Decimal("1"), Decimal("100"))

        journal.update_order_status("cid", {
            "order_id": "oid", "status": "OPEN", "filled_size": "0.2",
            "filled_value": "20", "total_fees": "0.12",
        })
        journal.apply_fill_delta("cid")
        journal.update_order_status("cid", {
            "order_id": "oid", "status": "OPEN", "filled_size": "0.5",
            "filled_value": "50", "total_fees": "0.30",
        })
        journal.apply_fill_delta("cid")
        self.assertIsNone(journal.apply_fill_delta("cid"))

        self.assertAlmostEqual(manager.state["holdings_base"], 0.5)
        self.assertAlmostEqual(manager.state["entry_price"], 100.0)
        self.assertAlmostEqual(manager.state["entry_fee_usd"], 0.30)
        self.assertAlmostEqual(manager.state["cash_usd"], 49.70)
        self.assertEqual(len(manager.state["trades"]), 2)

    def test_restart_recovers_persisted_exchange_fill_without_duplication(self):
        state_path = "restart.json"
        manager = self.make_state_manager(state_path)
        journal = OrderJournal(manager)
        journal.log_intent("cid", "BTC-USDC", "BUY", Decimal("0.1"), Decimal("100"))
        journal.update_order_status("cid", {
            "order_id": "oid", "status": "FILLED", "filled_size": "0.1",
            "filled_value": "10", "total_fees": "0.06",
        })

        restarted = self.make_state_manager(state_path)
        self.assertTrue(OrderJournal(restarted).reconcile_unresolved_orders(client=None))
        self.assertEqual(len(restarted.state["trades"]), 1)

        restarted_again = self.make_state_manager(state_path)
        self.assertTrue(OrderJournal(restarted_again).reconcile_unresolved_orders(client=None))
        self.assertEqual(len(restarted_again.state["trades"]), 1)

    def test_partial_sell_allocates_entry_fee_proportionally(self):
        manager = self.make_state_manager()
        manager.state.update({
            "position": "LONG", "entry_price": 100.0, "entry_time": "2026-01-01T00:00:00Z",
            "entry_fee_usd": 1.0, "holdings_base": 1.0, "cash_usd": 0.0,
            "highest_price_since_entry": 120.0, "lowest_price_since_entry": 95.0,
            "entry_reason": "TEST", "entry_indicators": {},
        })
        manager.log_completed_roundtrip = lambda **kwargs: None
        manager.save_state()
        journal = OrderJournal(manager)
        journal.log_intent("sell", "BTC-USDC", "SELL", Decimal("1"), Decimal("120"))
        journal.update_order_status("sell", {
            "order_id": "oid", "status": "OPEN", "filled_size": "0.4",
            "filled_value": "48", "total_fees": "0.10",
        })
        trade = journal.apply_fill_delta("sell")

        self.assertAlmostEqual(manager.state["holdings_base"], 0.6)
        self.assertAlmostEqual(manager.state["entry_fee_usd"], 0.6)
        self.assertAlmostEqual(trade["realized_pnl_usd"], 7.5)

    def test_non_monotonic_fill_is_rejected(self):
        manager = self.make_state_manager()
        journal = OrderJournal(manager)
        journal.log_intent("cid", "BTC-USDC", "BUY", Decimal("1"), Decimal("100"))
        journal.update_order_status("cid", {
            "order_id": "oid", "status": "OPEN", "filled_size": "0.5",
            "filled_value": "50", "total_fees": "0.30",
        })
        journal.apply_fill_delta("cid")
        journal.update_order_status("cid", {
            "order_id": "oid", "status": "OPEN", "filled_size": "0.4",
            "filled_value": "40", "total_fees": "0.24",
        })
        with self.assertRaisesRegex(ValueError, "Non-monotonic cumulative fill"):
            journal.apply_fill_delta("cid")

    def test_open_partial_fill_is_applied_but_blocks_new_orders(self):
        manager = self.make_state_manager()
        journal = OrderJournal(manager)
        journal.log_intent("cid", "BTC-USDC", "BUY", Decimal("1"), Decimal("100"))
        journal.update_order_id("cid", "oid")

        self.assertFalse(journal.reconcile_unresolved_orders(OpenPartialFillClient()))
        self.assertAlmostEqual(manager.state["holdings_base"], 0.2)
        self.assertEqual(len(manager.state["trades"]), 1)

        # Repeated OPEN response is idempotent and remains fail-closed.
        self.assertFalse(journal.reconcile_unresolved_orders(OpenPartialFillClient()))
        self.assertAlmostEqual(manager.state["holdings_base"], 0.2)
        self.assertEqual(len(manager.state["trades"]), 1)

    def test_unconfirmed_sell_cancel_cannot_reach_market_fallback(self):
        source = (ROOT / "bot.py").read_text()
        self.assertGreaterEqual(source.count("if isinstance(inner_e, UnconfirmedCancellationError):"), 2)
        self.assertGreaterEqual(source.count("raise UnconfirmedCancellationError("), 2)

    def test_disable_buys_does_not_guard_stop_losses(self):
        source = (ROOT / "bot.py").read_text()
        self.assertNotIn("not Settings.DISABLE_BUYS and Settings.STOP_LOSS_PERCENT", source)
        self.assertNotIn("not Settings.DISABLE_BUYS and Settings.STOP_LOSS_ATR_MULTIPLE", source)


if __name__ == "__main__":
    unittest.main()
