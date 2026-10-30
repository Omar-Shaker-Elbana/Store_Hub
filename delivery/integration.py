from django.apps import apps

from . import conf


def order_model():
    return apps.get_model(conf.ORDER_MODEL)


def store_order_model():
    return apps.get_model(conf.STORE_ORDER_MODEL)


def _first(obj, names, default=""):
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return default


def _contact(name, phone):
    return " · ".join(str(p) for p in (name, phone) if p)


# ---- Snapshots used when a mission is created -----------------------------

def store_pickup_info(store_order):
    store = getattr(store_order, "store", None)
    return {
        "address": str(_first(store, ("address", "full_address", "location"))),
        "area": str(_first(store, ("city", "area", "governorate"))),
        "contact": _contact(
            _first(store, ("name",)),
            _first(store, ("phone", "phone_number", "contact_phone")),
        ),
    }


def shipping_info(order):
    address = _first(order, ("shipping_address", "delivery_address", "address"))
    source = address if address and not isinstance(address, str) else order
    owner = _first(order, ("user", "customer", "shopper"), None)
    name = _first(order, ("full_name", "shipping_name"))
    if not name and owner is not None and hasattr(owner, "get_full_name"):
        name = owner.get_full_name()
    phone = _first(order, ("phone", "shipping_phone", "phone_number")) or _first(
        owner, ("phone", "phone_number")
    )
    return {
        "address": str(address),
        "area": str(_first(source, ("city", "shipping_city", "area", "governorate"))),
        "contact": _contact(name, phone),
    }


def order_owner_id(order):
    for name in ("user_id", "customer_id", "shopper_id"):
        if hasattr(order, name):
            return getattr(order, name)
    return None


def _item_row(item):
    product = getattr(item, "product", None)
    name = getattr(item, "product_name", None) or getattr(product, "name", None) or str(item)
    return {
        "name": str(name),
        "quantity": int(getattr(item, "quantity", 1) or 1),
        "sku": str(getattr(product, "sku", "") or ""),
    }


def store_order_items(store_order):
    manager = getattr(store_order, "items", None) or getattr(store_order, "orderitem_set", None)
    return [_item_row(i) for i in manager.all()] if manager is not None else []


def order_items(order):
    rows = []
    store_orders = store_order_model().objects.filter(order=order).exclude(
        status__in=conf.EXCLUDED_STORE_ORDER_STATUSES
    )
    for so in store_orders:
        store_name = str(getattr(getattr(so, "store", None), "name", "") or "")
        for row in store_order_items(so):
            rows.append({**row, "store": store_name})
    return rows


# ---- Status updates --------------------------------------------------------

def _save_status(obj, status):
    if obj.status == status:
        return
    obj.status = status
    obj._delivery_internal = True  # tells our own signal handlers to ignore this save
    fields = ["status"] + (["updated_at"] if hasattr(obj, "updated_at") else [])
    try:
        obj.save(update_fields=fields)
    finally:
        obj._delivery_internal = False


def set_store_order_status(store_order, key):
    _save_status(store_order, conf.STATUSES[key])


def set_order_status(order, key):
    _save_status(order, conf.STATUSES[key])