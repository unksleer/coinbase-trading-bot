import time
import uuid
from decimal import Decimal


TERMINAL_EXCHANGE_STATUSES = {"FILLED", "CANCELLED", "EXPIRED", "FAILED"}


def execute_journaled_market_sell(client, order_journal, product_id: str,
                                  sell_amount: float, price_hint: float,
                                  poll_attempts: int = 5,
                                  poll_interval_seconds: float = 1.0):
    """Submit a recoverable market sell and apply only confirmed Coinbase fills."""
    client_order_id = str(uuid.uuid4())
    size = Decimal(str(sell_amount))
    price = Decimal(str(price_hint))
    order_journal.log_intent(
        client_order_id, product_id, "SELL", size, price,
        reason="MANUAL_EXIT"
    )

    order = client.market_order_sell(
        client_order_id=client_order_id,
        product_id=product_id,
        base_size=str(size)
    )
    order_dict = order.to_dict() if hasattr(order, "to_dict") else (
        order if isinstance(order, dict) else {}
    )
    success_response = order_dict.get("success_response") or {}
    order_id = (
        order_dict.get("order_id") or success_response.get("order_id")
        or order_dict.get("id")
    )
    if not order_id:
        raise ValueError("Coinbase market sell submission returned no order ID.")
    order_journal.update_order_id(client_order_id, order_id)

    for _ in range(max(1, poll_attempts)):
        if poll_interval_seconds > 0:
            time.sleep(poll_interval_seconds)
        response = client.get_order(order_id=order_id)
        response_dict = response.to_dict() if hasattr(response, "to_dict") else response
        order_info = response_dict.get("order", {})
        order_journal.update_order_status(client_order_id, order_info)
        tracked = order_journal.get_order(client_order_id)
        if tracked.get("exchange_status") in TERMINAL_EXCHANGE_STATUSES:
            trade = order_journal.apply_fill_delta(client_order_id)
            return tracked, trade

    raise RuntimeError(
        f"Market sell {order_id} has no confirmed terminal status; "
        "local position was not changed and reconciliation is required."
    )
