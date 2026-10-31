import logging
import secrets
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from notifications.models import Notification
from orders.models import Order, StoreOrder

from .models import (
    CLAIM_DURATION,
    DROPOFF_FEE,
    MAX_ACTIVE_CLAIMS,
    MAX_CODE_ATTEMPTS,
    PICKUP_FEE,
    CourierWallet,
    CourierWalletTransaction,
    DeliveryClaim,
    DeliveryMission,
    Warehouse,
)

User = get_user_model()
logger = logging.getLogger(__name__)

ACTIVE = DeliveryClaim.ACTIVE_STATUSES

# Order of a StoreOrder's life. An Order (the checkout) has no status of its
# own that anything maintained, so it is derived: the status of its
# furthest-behind, non-cancelled StoreOrder.
STATUS_RANK = {
    "Pending": 0,
    "Processing": 1,
    "Shipped": 2,
    "At Warehouse": 3,
    "Out for Delivery": 4,
    "Delivered": 5,
}
ORDER_TIMESTAMP_FIELD = {
    "Processing": "processed_at",
    "Shipped": "shipped_at",
    "Delivered": "delivered_at",
    "Cancelled": "cancelled_at",
}


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def _has_flag(user, flag):
    if not user.is_authenticated:
        return False
    profile = getattr(user, "profile", None)
    return bool(profile and getattr(profile, flag))


def is_courier(user):
    return _has_flag(user, "is_delivery_person")


def is_warehouse_staff(user):
    return _has_flag(user, "is_warehouse_staff")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _notify(recipient, message, sender=None):
    if recipient is not None:
        Notification.objects.create(recipient=recipient, sender=sender, message=message)


def get_active_claim(mission):
    return mission.claims.filter(status__in=ACTIVE).first()


def open_missions(kind=None):
    """The work pool: open missions nobody currently holds."""
    qs = DeliveryMission.objects.filter(status="open").exclude(
        claims__status__in=ACTIVE
    )
    if kind in ("pickup", "dropoff"):
        qs = qs.filter(kind=kind)
    return qs


def _locked_claim(claim_id):
    return DeliveryClaim.objects.select_for_update().filter(id=claim_id).first()


def _claim_problem(claim, user):
    """Shared guard for acting on a claim. Returns an error string or None."""
    if not claim:
        return "Claim not found."
    if claim.courier_id != user.id:
        return "Only the courier who claimed this mission can do that."
    if claim.status == "expired":
        return "Your 24-hour window on this mission expired — it's back in the pool."
    if not claim.is_active:
        return "This claim is no longer active."
    return None


def _live_store_orders(order_id):
    return StoreOrder.objects.filter(order_id=order_id).exclude(status="Cancelled")


def _assign_delivery_person(mission, user):
    """Keep StoreOrder.delivery_person (shown to the shopper) pointing at
    whoever currently holds the mission."""
    if mission.kind == "pickup":
        qs = StoreOrder.objects.filter(pk=mission.store_order_id)
    else:
        qs = _live_store_orders(mission.order_id)
    qs.update(delivery_person=user)


def _unassign_delivery_person(mission, courier_id):
    if mission.kind == "pickup":
        qs = StoreOrder.objects.filter(pk=mission.store_order_id)
    else:
        qs = StoreOrder.objects.filter(order_id=mission.order_id)
    qs.filter(delivery_person_id=courier_id).update(delivery_person=None)


def _revert_out_for_delivery(order_id):
    for store_order in StoreOrder.objects.select_for_update().filter(
        order_id=order_id, status="Out for Delivery"
    ):
        store_order.status = "At Warehouse"
        store_order.save(update_fields=["status"])


def _end_claim(claim, new_status, *, revert=True):
    """Close an active claim without delivering. If the courier already had
    the goods (picked_up) on a dropoff, the order goes back to 'At Warehouse'
    so the next courier can take it from there."""
    was_picked_up = claim.status == "picked_up"
    claim.status = new_status
    claim.save(update_fields=["status"])
    mission = claim.mission
    _unassign_delivery_person(mission, claim.courier_id)
    if revert and was_picked_up and mission.kind == "dropoff":
        _revert_out_for_delivery(mission.order_id)


# ---------------------------------------------------------------------------
# Order status roll-up
# ---------------------------------------------------------------------------


def sync_order_status(order_id):
    """Recompute Order.status from its StoreOrders (and stamp the matching
    timestamp). Safe to call any number of times."""
    order = Order.objects.filter(pk=order_id).first()
    if order is None:
        return None
    statuses = list(
        StoreOrder.objects.filter(order_id=order_id).values_list("status", flat=True)
    )
    if not statuses:
        return order.status
    live = [s or "Pending" for s in statuses if s != "Cancelled"]
    new_status = (
        min(live, key=lambda s: STATUS_RANK.get(s, 0)) if live else "Cancelled"
    )
    if order.status == new_status:
        return new_status

    order.status = new_status
    fields = ["status"]
    stamp = ORDER_TIMESTAMP_FIELD.get(new_status)
    if stamp and getattr(order, stamp) is None:
        setattr(order, stamp, timezone.now())
        fields.append(stamp)
    order.save(update_fields=fields)
    return new_status


# ---------------------------------------------------------------------------
# Mission creation
# ---------------------------------------------------------------------------


def _items_snapshot(store_orders):
    rows = []
    for store_order in store_orders:
        store_name = store_order.store.name if store_order.store_id else ""
        for item in store_order.items.select_related("product"):
            rows.append(
                {
                    "product": item.product.name if item.product_id else "Item",
                    "quantity": item.quantity or 0,
                    "store": store_name,
                }
            )
    return rows


def _warehouse_contact(warehouse):
    return " · ".join(p for p in (warehouse.name, warehouse.phone) if p)


def _cod_total(order, store_orders):
    if order.payment_type != "cash":
        return Decimal("0.00")
    return sum((so.subtotal or Decimal("0.00") for so in store_orders), Decimal("0.00"))


def create_pickup_mission(store_order):
    """Merchant -> warehouse, carrying only this store's items. Idempotent.
    Returns (mission, error)."""
    live = (
        DeliveryMission.objects.filter(store_order=store_order, kind="pickup")
        .exclude(status="cancelled")
        .first()
    )
    if live:
        return live, None

    warehouse = Warehouse.get_default()
    if warehouse is None:
        return None, "No active warehouse is configured."

    store = store_order.store
    location = getattr(store, "pickup_location", None) if store else None
    if location:
        pickup_address = location.address
        pickup_contact = " · ".join(p for p in (store.name, location.phone) if p)
    else:
        name = store.name if store else "the store"
        pickup_address = f"{name} — no pickup address on file, contact the store"
        pickup_contact = name or ""

    try:
        with transaction.atomic():
            mission = DeliveryMission.objects.create(
                kind="pickup",
                order_id=store_order.order_id,
                store_order=store_order,
                warehouse=warehouse,
                pickup_address=pickup_address,
                pickup_contact=pickup_contact,
                dropoff_address=warehouse.address,
                dropoff_contact=_warehouse_contact(warehouse),
                items_snapshot=_items_snapshot([store_order]),
                fee=PICKUP_FEE,
            )
    except IntegrityError:  # a concurrent creator won the race
        return (
            DeliveryMission.objects.filter(store_order=store_order, kind="pickup")
            .exclude(status="cancelled")
            .first(),
            None,
        )
    return mission, None


def create_dropoff_mission(order):
    """Warehouse -> shopper, carrying the whole order. Idempotent.
    Returns (mission, error)."""
    live = (
        DeliveryMission.objects.filter(order=order, kind="dropoff")
        .exclude(status="cancelled")
        .first()
    )
    if live:
        return live, None

    warehouse = Warehouse.get_default()
    if warehouse is None:
        return None, "No active warehouse is configured."

    store_orders = list(_live_store_orders(order.pk).select_related("store"))
    cod = _cod_total(order, store_orders)
    shopper = order.user
    contact = (shopper.get_full_name() or shopper.get_username()) if shopper else ""

    try:
        with transaction.atomic():
            mission = DeliveryMission.objects.create(
                kind="dropoff",
                order=order,
                warehouse=warehouse,
                pickup_address=warehouse.address,
                pickup_contact=_warehouse_contact(warehouse),
                dropoff_address=order.shipping_address or "",
                dropoff_contact=contact,
                items_snapshot=_items_snapshot(store_orders),
                fee=DROPOFF_FEE,
                cod_amount=cod,
            )
    except IntegrityError:
        return (
            DeliveryMission.objects.filter(order=order, kind="dropoff")
            .exclude(status="cancelled")
            .first(),
            None,
        )
    return mission, None


def advance_order_if_ready(order):
    """Once every non-cancelled StoreOrder is 'At Warehouse', open a
    warehouse packing task. The warehouse -> shopper mission is created
    only when a warehouse worker finishes that task. Returns (task, error);
    task is None if it wasn't time yet or one already exists."""
    from warehouse import services as warehouse_services  # lazy: import cycle

    live = _live_store_orders(order.pk)
    if not live.exists() or live.exclude(status="At Warehouse").exists():
        return None, None
    if (
        DeliveryMission.objects.filter(order=order, kind="dropoff")
        .exclude(status="cancelled")
        .exists()
    ):
        return None, None
    if warehouse_services.get_live_task(order):
        return None, None
    task, error = warehouse_services.create_task(order)
    if task:
        _notify(
            order.user,
            f"All items of order #{order.pk} are at our warehouse — "
            "we're packing them now and a courier will deliver them soon.",
        )
    return task, error


def _refresh_dropoff_after_cancel(order):
    """A store order was cancelled after the order's dropoff mission already
    existed. Re-snapshot its items and COD total from the store orders that
    are still live, or close the mission if nothing is left to deliver."""
    mission = (
        DeliveryMission.objects.select_for_update()
        .filter(order=order, kind="dropoff", status="open")
        .first()
    )
    if mission is None:
        return
    live = list(_live_store_orders(order.pk).select_related("store"))
    if live:
        mission.items_snapshot = _items_snapshot(live)
        mission.cod_amount = _cod_total(order, live)
        mission.save(update_fields=["items_snapshot", "cod_amount"])
        return
    for claim in mission.claims.select_for_update().filter(status__in=ACTIVE):
        _end_claim(claim, "cancelled", revert=False)
        _notify(
            claim.courier,
            f"Delivery mission #{mission.pk} was cancelled: every store order "
            "in the order was cancelled.",
        )
    mission.status = "cancelled"
    mission.save(update_fields=["status"])


def handle_store_order_cancelled(store_order):
    """Cancel the store's pickup mission (telling any courier who held it),
    then re-check whether the rest of the order can move on."""
    with transaction.atomic():
        missions = DeliveryMission.objects.select_for_update().filter(
            store_order=store_order, status="open"
        )
        for mission in missions:
            for claim in mission.claims.select_for_update().filter(status__in=ACTIVE):
                _end_claim(claim, "cancelled", revert=False)
                _notify(
                    claim.courier,
                    f"Pickup mission #{mission.pk} was cancelled: the store order "
                    "was cancelled.",
                )
            mission.status = "cancelled"
            mission.save(update_fields=["status"])
        if store_order.order_id:
            from warehouse import services as warehouse_services  # lazy: import cycle

            warehouse_services.refresh_task_after_cancel(store_order.order)
            _refresh_dropoff_after_cancel(store_order.order)
            advance_order_if_ready(store_order.order)


# ---------------------------------------------------------------------------
# Work-pool actions (all return (result, error_message))
# ---------------------------------------------------------------------------


def release_expired_claims():
    """Send every claim past its 24h deadline back to the pool. Called at the
    top of each action/view (no scheduler in this project) and by the
    release_expired_delivery_claims command."""
    now = timezone.now()
    ids = list(
        DeliveryClaim.objects.filter(status__in=ACTIVE, deadline__lte=now).values_list(
            "id", flat=True
        )
    )
    released = 0
    for claim_id in ids:
        with transaction.atomic():
            claim = (
                DeliveryClaim.objects.select_for_update()
                .filter(id=claim_id, status__in=ACTIVE, deadline__lte=now)
                .first()
            )
            if claim is None:
                continue
            _end_claim(claim, "expired")
            _notify(
                claim.courier,
                f"Your 24-hour window on delivery mission #{claim.mission_id} "
                "expired — it went back to the work pool.",
            )
            released += 1
    return released


@transaction.atomic
def claim_mission(*, mission_id, user):
    release_expired_claims()

    if not is_courier(user):
        return None, "Only registered delivery partners can claim missions."

    # Serialise a courier's own claims so the active-claim cap can't be raced.
    User.objects.select_for_update().filter(pk=user.pk).first()

    mission = (
        DeliveryMission.objects.select_for_update()
        .select_related("order")
        .filter(id=mission_id)
        .first()
    )
    if not mission:
        return None, "Mission not found."
    if mission.status != "open":
        return None, "This mission is no longer available."
    if mission.order.user_id == user.id:
        return None, "You can't take a delivery mission for your own order."
    if get_active_claim(mission):
        return None, "Someone else just claimed this mission."

    active = DeliveryClaim.objects.filter(courier=user, status__in=ACTIVE).count()
    if active >= MAX_ACTIVE_CLAIMS:
        return None, f"You can hold at most {MAX_ACTIVE_CLAIMS} missions at a time."

    claim = DeliveryClaim.objects.create(
        mission=mission, courier=user, deadline=timezone.now() + CLAIM_DURATION
    )
    _assign_delivery_person(mission, user)
    return claim, None


@transaction.atomic
def release_claim(*, claim_id, user):
    """Courier hands a not-yet-picked-up mission back before the deadline."""
    release_expired_claims()
    claim = _locked_claim(claim_id)
    error = _claim_problem(claim, user)
    if error:
        return None, error
    if claim.status != "claimed":
        return None, "You already have the goods — contact support to hand this back."
    _end_claim(claim, "released")
    return claim, None


@transaction.atomic
def force_release_claim(*, claim_id, staff_user=None):
    """Staff tool (admin action): put a mission back in the pool no matter
    what state the claim is in, and tell the courier."""
    claim = _locked_claim(claim_id)
    if claim is None or not claim.is_active:
        return None, "That claim is not active."
    _end_claim(claim, "released")
    _notify(
        claim.courier,
        f"Delivery mission #{claim.mission_id} was returned to the work pool by staff.",
        sender=staff_user,
    )
    return claim, None


@transaction.atomic
def mark_picked_up(*, claim_id, user):
    release_expired_claims()
    claim = _locked_claim(claim_id)
    error = _claim_problem(claim, user)
    if error:
        return None, error
    if claim.status != "claimed":
        return None, "This mission is already picked up."

    mission = claim.mission
    claim.status = "picked_up"
    claim.picked_up_at = timezone.now()
    claim.save(update_fields=["status", "picked_up_at"])

    if mission.kind == "dropoff":
        moved = 0
        for store_order in StoreOrder.objects.select_for_update().filter(
            order_id=mission.order_id, status="At Warehouse"
        ):
            store_order.status = "Out for Delivery"
            store_order.save(update_fields=["status"])
            moved += 1
        if moved:
            _notify(
                mission.order.user,
                f"Order #{mission.order_id} is out for delivery. When the courier "
                f"arrives, give them this confirmation code: {mission.confirmation_code}",
                sender=user,
            )
    return claim, None


@transaction.atomic
def complete_claim(*, claim_id, user, code="", cash_collected=False):
    """Final handover. The courier must type the code the RECEIVER holds
    (warehouse staff for a pickup mission, the shopper for a dropoff)."""
    release_expired_claims()
    claim = _locked_claim(claim_id)
    error = _claim_problem(claim, user)
    if error:
        return None, error
    if claim.status != "picked_up":
        return None, "Pick the goods up before completing the handover."
    if claim.code_attempts >= MAX_CODE_ATTEMPTS:
        return None, "Too many wrong codes — contact support."

    mission = claim.mission
    supplied = str(code or "").strip().encode()
    if not secrets.compare_digest(supplied, mission.confirmation_code.encode()):
        claim.code_attempts += 1
        claim.save(update_fields=["code_attempts"])
        left = MAX_CODE_ATTEMPTS - claim.code_attempts
        return None, f"That confirmation code is incorrect ({left} attempt(s) left)."

    collects_cash = mission.kind == "dropoff" and mission.cod_amount > 0
    if collects_cash and not cash_collected:
        return None, f"Confirm you collected {mission.cod_amount} in cash first."

    now = timezone.now()
    claim.status = "delivered"
    claim.finished_at = now
    claim.cash_collected = collects_cash
    claim.fee_earned = mission.fee
    claim.save(update_fields=["status", "finished_at", "cash_collected", "fee_earned"])
    mission.status = "completed"
    mission.completed_at = now
    mission.save(update_fields=["status", "completed_at"])
    _credit_wallet(claim)

    if mission.kind == "pickup":
        _finish_pickup(mission, user)
    else:
        _finish_dropoff(mission, user)
    return claim, None


def _credit_wallet(claim):
    CourierWallet.objects.get_or_create(user=claim.courier)
    wallet = CourierWallet.objects.select_for_update().get(user=claim.courier)
    wallet.balance += claim.fee_earned
    wallet.save(update_fields=["balance"])
    CourierWalletTransaction.objects.create(
        wallet=wallet,
        claim=claim,
        amount=claim.fee_earned,
        description=(
            f"{claim.mission.get_kind_display()} — order #{claim.mission.order_id} "
            f"(mission #{claim.mission_id})"
        ),
    )


def _finish_pickup(mission, user):
    # Lock the order so two stores finishing at the same moment can't both
    # conclude "someone else hasn't arrived yet".
    order = Order.objects.select_for_update().get(pk=mission.order_id)
    store_order = StoreOrder.objects.select_for_update().get(pk=mission.store_order_id)
    if store_order.status == "Shipped":
        store_order.status = "At Warehouse"
        store_order.save(update_fields=["status"])
        _notify(
            order.user,
            f"Your items from {store_order.store.name} reached our warehouse.",
            sender=user,
        )
    advance_order_if_ready(order)


def _finish_dropoff(mission, user):
    delivered = 0
    for store_order in StoreOrder.objects.select_for_update().filter(
        order_id=mission.order_id, status="Out for Delivery"
    ):
        store_order.status = "Delivered"
        store_order.save(update_fields=["status"])
        delivered += 1
    if delivered:
        _notify(
            mission.order.user,
            f"Order #{mission.order_id} was delivered. Enjoy!",
            sender=user,
        )