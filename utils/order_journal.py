import os
import json
import logging
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from datetime import datetime

logger = logging.getLogger("coinbase_bot.order_journal")

# Explicit Order States
STATE_SUBMITTING = "SUBMITTING"
STATE_OPEN = "OPEN"
STATE_PARTIALLY_FILLED = "PARTIALLY_FILLED"
STATE_FILLED = "FILLED"
STATE_CANCEL_PENDING = "CANCEL_PENDING"
STATE_CANCELLED = "CANCELLED"
STATE_FAILED = "FAILED"
STATE_UNKNOWN = "UNKNOWN"

VALID_STATES = {
    STATE_SUBMITTING,
    STATE_OPEN,
    STATE_PARTIALLY_FILLED,
    STATE_FILLED,
    STATE_CANCEL_PENDING,
    STATE_CANCELLED,
    STATE_FAILED,
    STATE_UNKNOWN,
}

def quantize_value(val, increment_str: str, rounding_mode=ROUND_DOWN) -> Decimal:
    """
    Quantizes a value (float, str, Decimal) to the precision specified by increment_str.
    Returns a Decimal.
    """
    if val is None:
        return Decimal("0")
    try:
        d_val = Decimal(str(val))
        d_inc = Decimal(str(increment_str))
        return d_val.quantize(d_inc, rounding=rounding_mode)
    except (InvalidOperation, ValueError, TypeError) as e:
        logger.error(f"Quantization failed for value {val} with increment {increment_str}: {e}")
        raise ValueError(f"Invalid decimal value or increment: {val}, {increment_str}")

class OrderJournal:
    def __init__(self, state_manager):
        self.state_manager = state_manager
        if "orders" not in self.state_manager.state:
            self.state_manager.state["orders"] = {}
            self.state_manager.save_state()

    def get_order(self, client_order_id: str) -> dict:
        return self.state_manager.state["orders"].get(client_order_id)

    def log_intent(self, client_order_id: str, product_id: str, side: str,
                   size: Decimal, price: Decimal, reason: str = None,
                   indicators: dict = None):
        """
        Persists order intent and state to SUBMITTING before making the API call.
        """
        timestamp = datetime.utcnow().isoformat() + "Z"
        order_record = {
            "client_order_id": client_order_id,
            "order_id": None,
            "product_id": product_id,
            "side": side.upper(),
            "status": STATE_SUBMITTING,
            "exchange_status": None,
            "size": str(size),
            "price": str(price),
            "filled_size": "0",
            "filled_value": "0",
            "fee_usd": "0",
            "applied_filled_size": "0",
            "applied_filled_value": "0",
            "applied_fee_usd": "0",
            "reason": reason,
            "indicators": indicators or {},
            "created_at": timestamp,
            "updated_at": timestamp
        }
        self.state_manager.state["orders"][client_order_id] = order_record
        self.state_manager.save_state()
        logger.info(f"Order intent persisted: {client_order_id} ({side} {size} {product_id} @ {price})")

    def update_order_id(self, client_order_id: str, order_id: str):
        """
        Links the exchange-assigned order_id to the client_order_id.
        """
        if client_order_id in self.state_manager.state["orders"]:
            self.state_manager.state["orders"][client_order_id]["order_id"] = order_id
            self.state_manager.state["orders"][client_order_id]["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self.state_manager.save_state()
            logger.info(f"Order ID {order_id} linked to client ID {client_order_id}")

    def parse_coinbase_response(self, order_info: dict) -> dict:
        """
        Defensively parses order info dict returned from Coinbase API.
        Raises ValueError if critical fields are missing or malformed.
        Never defaults missing fill fields to requested amounts.
        """
        parsed = {}
        
        # 1. Verify existence of critical status fields
        status = order_info.get("status")
        if not status:
            raise ValueError("Missing 'status' field in Coinbase order response")
            
        order_id = order_info.get("order_id") or order_info.get("id")
        if not order_id:
            raise ValueError("Missing order ID field in Coinbase order response")
            
        parsed["order_id"] = order_id
        parsed["status"] = status
        
        # 2. Extract filled size and filled value with strict validation
        filled_size_str = order_info.get("filled_size")
        filled_value_str = order_info.get("filled_value")
        total_fees_str = order_info.get("total_fees")
        
        if filled_size_str is None or filled_value_str is None:
            raise ValueError("Missing filled_size or filled_value in Coinbase order response")
            
        try:
            parsed["filled_size"] = Decimal(str(filled_size_str))
            parsed["filled_value"] = Decimal(str(filled_value_str))
            parsed["fee_usd"] = Decimal(str(total_fees_str)) if total_fees_str is not None else Decimal("0")
        except (InvalidOperation, ValueError, TypeError) as e:
            raise ValueError(f"Non-numeric fields in Coinbase fill details: {e}")
            
        return parsed

    def update_order_status(self, client_order_id: str, response_data: dict):
        """
        Updates the order status and values defensively.
        If response_data indicates unknown or error status, marks state as STATE_UNKNOWN.
        """
        if client_order_id not in self.state_manager.state["orders"]:
            logger.warning(f"Attempted to update untracked order: {client_order_id}")
            return
            
        order = self.state_manager.state["orders"][client_order_id]
        
        try:
            parsed = self.parse_coinbase_response(response_data)
            
            # Map Coinbase status to our explicit states
            cb_status = parsed["status"].upper()
            filled_size = parsed["filled_size"]
            target_size = Decimal(order["size"])
            
            if cb_status == "FILLED":
                new_state = STATE_FILLED
            elif cb_status == "OPEN":
                if 0 < filled_size < target_size:
                    new_state = STATE_PARTIALLY_FILLED
                else:
                    new_state = STATE_OPEN
            elif cb_status in ("CANCELLED", "EXPIRED"):
                if filled_size > 0:
                    new_state = STATE_PARTIALLY_FILLED
                else:
                    new_state = STATE_CANCELLED
            elif cb_status == "FAILED":
                new_state = STATE_FAILED
            else:
                new_state = STATE_UNKNOWN
                    
            order["order_id"] = parsed["order_id"]
            order["status"] = new_state
            order["exchange_status"] = cb_status
            order["filled_size"] = str(parsed["filled_size"])
            order["filled_value"] = str(parsed["filled_value"])
            order["fee_usd"] = str(parsed["fee_usd"])
            
        except ValueError as e:
            logger.error(f"Defensive parse failed for order {client_order_id}: {e}")
            order["status"] = STATE_UNKNOWN
            
        order["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.state_manager.save_state()
        logger.info(f"Order status updated: {client_order_id} -> {order['status']} (filled: {order['filled_size']})")

    def cancel_and_confirm(self, client, client_order_id: str, poll_attempts: int = 3,
                           poll_interval_seconds: float = 1.0) -> bool:
        """Cancel an order and verify Coinbase reports a terminal state.

        A false result is fail-closed: callers must not submit a replacement or
        market fallback because the original order may still be executable.
        """
        order = self.get_order(client_order_id)
        if not order or not order.get("order_id"):
            logger.error(f"Cannot cancel untracked order {client_order_id}.")
            return False

        order["status"] = STATE_CANCEL_PENDING
        order["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.state_manager.save_state()

        try:
            client.cancel_orders(order_ids=[order["order_id"]])
        except Exception as exc:
            logger.error(f"Cancellation request failed for {order['order_id']}: {exc}")
            order["status"] = STATE_UNKNOWN
            self.state_manager.save_state()
            return False

        for _ in range(max(1, poll_attempts)):
            if poll_interval_seconds > 0:
                import time
                time.sleep(poll_interval_seconds)
            try:
                response = client.get_order(order_id=order["order_id"])
                response_dict = response.to_dict() if hasattr(response, "to_dict") else response
                self.update_order_status(client_order_id, response_dict.get("order", {}))
                tracked = self.get_order(client_order_id)
                if tracked.get("exchange_status") in ("CANCELLED", "EXPIRED", "FAILED", "FILLED"):
                    return True
            except Exception as exc:
                logger.error(f"Cancellation confirmation failed for {order['order_id']}: {exc}")

        order = self.get_order(client_order_id)
        order["status"] = STATE_UNKNOWN
        order["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.state_manager.save_state()
        logger.critical(
            f"Cancellation was not confirmed for {order['order_id']}; replacement execution is blocked."
        )
        return False

    def has_unapplied_fill(self, order: dict) -> bool:
        return Decimal(str(order.get("filled_size", "0"))) > Decimal(
            str(order.get("applied_filled_size", "0"))
        )

    def apply_fill_delta(self, client_order_id: str):
        """Atomically apply only the previously unseen cumulative fill delta."""
        order = self.get_order(client_order_id)
        if not order:
            raise ValueError(f"Cannot apply fill for unknown order {client_order_id}.")

        filled_size = Decimal(str(order.get("filled_size", "0")))
        filled_value = Decimal(str(order.get("filled_value", "0")))
        fee_usd = Decimal(str(order.get("fee_usd", "0")))
        applied_size = Decimal(str(order.get("applied_filled_size", "0")))
        applied_value = Decimal(str(order.get("applied_filled_value", "0")))
        applied_fee = Decimal(str(order.get("applied_fee_usd", "0")))

        delta_size = filled_size - applied_size
        delta_value = filled_value - applied_value
        delta_fee = fee_usd - applied_fee
        if delta_size == 0 and delta_value == 0 and delta_fee == 0:
            return None
        if delta_size <= 0 or delta_value <= 0 or delta_fee < 0:
            raise ValueError(
                f"Non-monotonic cumulative fill for {client_order_id}: "
                f"size={filled_size}/{applied_size}, value={filled_value}/{applied_value}, "
                f"fee={fee_usd}/{applied_fee}"
            )

        average_price = delta_value / delta_size
        return self.state_manager.record_trade(
            trade_type=order["side"],
            price=float(average_price),
            amount_usd=float(delta_value),
            amount_base=float(delta_size),
            fee_usd=float(delta_fee),
            reason=order.get("reason") or "RECOVERED_ORDER_FILL",
            indicators=order.get("indicators") or {},
            order_application={
                "client_order_id": client_order_id,
                "filled_size": filled_size,
                "filled_value": filled_value,
                "fee_usd": fee_usd,
            },
        )

    def reconcile_unresolved_orders(self, client):
        """
        Reconciles all open/submitting/unresolved orders against Coinbase.
        Must be run on startup before any trading loop executes.
        """
        terminal_exchange_states = {"CANCELLED", "EXPIRED", "FAILED", "FILLED"}
        unresolved_ids = [
            cid for cid, o in self.state_manager.state["orders"].items()
            if o["status"] in (STATE_SUBMITTING, STATE_OPEN, STATE_PARTIALLY_FILLED, STATE_CANCEL_PENDING, STATE_UNKNOWN)
            and o.get("exchange_status") not in terminal_exchange_states
        ]
        
        if not unresolved_ids:
            logger.info("No unresolved orders to reconcile.")
        else:
            logger.info(f"Found {len(unresolved_ids)} unresolved orders to reconcile: {unresolved_ids}")
        
        success = True
        for cid in unresolved_ids:
            order = self.state_manager.state["orders"][cid]
            order_id = order.get("order_id")
            
            if not client:
                # If client is None (dry-run mode), we mock resolve
                logger.info(f"[Dry Run] Mock reconciling unresolved order: {cid}")
                order["status"] = STATE_CANCELLED
                order["exchange_status"] = "CANCELLED"
                order["updated_at"] = datetime.utcnow().isoformat() + "Z"
                self.state_manager.save_state()
                continue
                
            try:
                if order_id:
                    # Fetch by order_id
                    fill_details = client.get_order(order_id=order_id)
                    fill_dict = fill_details.to_dict() if hasattr(fill_details, 'to_dict') else fill_details
                    order_info = fill_dict.get('order', {})
                    if order_info:
                        self.update_order_status(cid, order_info)
                        self.apply_fill_delta(cid)
                    else:
                        logger.error(f"Order {order_id} not found on exchange. Marking UNKNOWN.")
                        order["status"] = STATE_UNKNOWN
                        self.state_manager.save_state()
                else:
                    # If we don't have order_id (bot crashed *during* submission),
                    # we must look it up on the exchange using client_order_id.
                    logger.info(f"Order {cid} has no order_id. Searching list_orders by client_order_id...")
                    # Query recent orders
                    response = client.list_orders(status=["OPEN", "FILLED", "CANCELLED", "EXPIRED", "FAILED"])
                    resp_dict = response.to_dict() if hasattr(response, 'to_dict') else response
                    orders_list = resp_dict.get('orders', [])
                    
                    found = False
                    for o in orders_list:
                        if o.get("client_order_id") == cid:
                            logger.info(f"Found match: client_order_id {cid} matches order_id {o.get('order_id')}")
                            self.update_order_status(cid, o)
                            self.apply_fill_delta(cid)
                            found = True
                            break
                            
                    if not found:
                        # If not found at all, and it was in SUBMITTING/UNKNOWN, it was likely never accepted.
                        logger.warning(f"Unresolved order {cid} not found on exchange. Marking FAILED.")
                        order["status"] = STATE_FAILED
                        order["updated_at"] = datetime.utcnow().isoformat() + "Z"
                        self.state_manager.save_state()
                        
            except Exception as e:
                logger.error(f"Failed to reconcile order {cid}: {e}")
                order["status"] = STATE_UNKNOWN
                self.state_manager.save_state()
                success = False

        # Terminal orders can still contain a fill that was persisted by the
        # exchange immediately before this process crashed.
        for cid, order in self.state_manager.state["orders"].items():
            if self.has_unapplied_fill(order):
                try:
                    self.apply_fill_delta(cid)
                except Exception as exc:
                    logger.critical(f"Failed to apply recovered fill for {cid}: {exc}")
                    success = False
                
        unsafe_orders = []
        for cid, order in self.state_manager.state["orders"].items():
            is_exchange_unresolved = (
                order.get("status") in (
                    STATE_SUBMITTING, STATE_OPEN, STATE_PARTIALLY_FILLED,
                    STATE_CANCEL_PENDING, STATE_UNKNOWN,
                )
                and order.get("exchange_status") not in terminal_exchange_states
            )
            if is_exchange_unresolved or self.has_unapplied_fill(order):
                unsafe_orders.append(cid)

        if unsafe_orders:
            logger.critical(
                "Trading blocked: unresolved orders or unapplied fills remain: "
                f"{unsafe_orders}"
            )
            return False
        return success
