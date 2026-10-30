from datetime import timedelta
from decimal import Decimal

from django.conf import settings


def _s(name, default):
    return getattr(settings, name, default)


ORDER_MODEL = _s("DELIVERY_ORDER_MODEL", "orders.Order")
STORE_ORDER_MODEL = _s("DELIVERY_STORE_ORDER_MODEL", "orders.StoreOrder")

CLAIM_WINDOW = timedelta(hours=_s("DELIVERY_CLAIM_HOURS", 24))
MAX_ACTIVE_MISSIONS = _s("DELIVERY_MAX_ACTIVE_MISSIONS", 5)
MAX_CODE_ATTEMPTS = _s("DELIVERY_MAX_CODE_ATTEMPTS", 5)
CODE_REQUIRED_FOR = tuple(_s("DELIVERY_CODE_REQUIRED_FOR", ("dropoff",)))

FEES = {
    "pickup": Decimal(str(_s("DELIVERY_PICKUP_FEE", "25.00"))),
    "dropoff": Decimal(str(_s("DELIVERY_DROPOFF_FEE", "40.00"))),
}

# Keys are what the delivery app means; values are what YOUR models store.
_DEFAULT_STATUSES = {
    # StoreOrder: fulfillment finished -> triggers the pickup mission
    "store_order_ready": "ready_for_pickup",
    "store_order_in_transit": "in_transit_to_warehouse",
    "store_order_at_warehouse": "at_warehouse",
    # Order
    "order_ready_for_delivery": "ready_for_delivery",
    "order_out_for_delivery": "out_for_delivery",
    "order_delivered": "delivered",
    # Both models
    "cancelled": "cancelled",
}
STATUSES = {**_DEFAULT_STATUSES, **_s("DELIVERY_STATUS_MAP", {})}

# Store orders that don't need to reach the warehouse for the order to move on.
EXCLUDED_STORE_ORDER_STATUSES = tuple(
    _s("DELIVERY_EXCLUDED_STORE_ORDER_STATUSES", (STATUSES["cancelled"],))
)