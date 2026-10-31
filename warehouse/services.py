from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from delivery import services as delivery_services
from notifications.models import Notification
from orders.models import Order

from .models import (
    MAX_ACTIVE_PACK_CLAIMS,
    PACK_CLAIM_DURATION,
    Warehouse,
    WarehouseClaim,
    WarehouseTask,
)

User = get_user_model()
ACTIVE = WarehouseClaim.ACTIVE_STATUSES


def is_warehouse_staff(user):
    return delivery_services.is_warehouse_staff(user)


def _notify(recipient, message, sender=None):
    if recipient is not None:
        Notification.objects.create(recipient=recipient, sender=sender, message=message)


def get_active_claim(task):
    return task.claims.filter(status__in=ACTIVE).first()


def get_live_task(order):
    return WarehouseTask.objects.filter(order=order).exclude(status="cancelled").first()


def open_tasks():
    """The packing pool: open tasks nobody currently holds."""
    return WarehouseTask.objects.filter(status="open").exclude(claims__status__in=ACTIVE)


def _all_parts_at_warehouse(order_id):
    live = delivery_services._live_store_orders(order_id)
    return live.exists() and not live.exclude(status="At Warehouse").exists()


def _locked_claim(claim_id):
    return (
        WarehouseClaim.objects.select_for_update()
        .select_related("task", "task__order")
        .filter(id=claim_id)
        .first()
    )


def _claim_problem(claim, user):
    if not claim:
        return "Claim not found."
    if claim.worker_id != user.id:
        return "Only the worker who claimed this task can do that."
    if claim.status == "expired":
        return "Your 24-hour window on this task expired — it's back in the pool."
    if not claim.is_active:
        return "This claim is no longer active."
    return None


def create_task(order):
    """Idempotent. Returns (task, error)."""
    live = get_live_task(order)
    if live:
        return live, None
    warehouse = Warehouse.get_default()
    if warehouse is None:
        return None, "No active warehouse is configured."
    store_orders = list(
        delivery_services._live_store_orders(order.pk).select_related("store")
    )
    try:
        with transaction.atomic():
            task = WarehouseTask.objects.create(
                order=order,
                warehouse=warehouse,
                items_snapshot=delivery_services._items_snapshot(store_orders),
            )
    except IntegrityError:  # a concurrent creator won the race
        return get_live_task(order), None
    return task, None


def refresh_task_after_cancel(order):
    """A store order was cancelled while the packing task is still open:
    re-snapshot the items, or close the task if nothing is left."""
    task = (
        WarehouseTask.objects.select_for_update()
        .filter(order=order, status="open")
        .first()
    )
    if task is None:
        return
    live = list(delivery_services._live_store_orders(order.pk).select_related("store"))
    if live:
        task.items_snapshot = delivery_services._items_snapshot(live)
        task.save(update_fields=["items_snapshot"])
        return
    for claim in task.claims.select_for_update().filter(status__in=ACTIVE):
        claim.status = "cancelled"
        claim.save(update_fields=["status"])
        _notify(
            claim.worker,
            f"Packing task #{task.pk} was cancelled: every store order in the order was cancelled.",
        )
    task.status = "cancelled"
    task.save(update_fields=["status"])


def release_expired_claims():
    now = timezone.now()
    ids = list(
        WarehouseClaim.objects.filter(status__in=ACTIVE, deadline__lte=now).values_list(
            "id", flat=True
        )
    )
    released = 0
    for claim_id in ids:
        with transaction.atomic():
            claim = (
                WarehouseClaim.objects.select_for_update()
                .filter(id=claim_id, status__in=ACTIVE, deadline__lte=now)
                .first()
            )
            if claim is None:
                continue
            claim.status = "expired"
            claim.save(update_fields=["status"])
            _notify(
                claim.worker,
                f"Your 24-hour window on packing task #{claim.task_id} expired — "
                "it went back to the warehouse pool.",
            )
            released += 1
    return released


@transaction.atomic
def claim_task(*, task_id, user):
    release_expired_claims()
    if not is_warehouse_staff(user):
        return None, "Only warehouse staff can claim packing tasks."

    # Serialise this worker's own claims so the cap can't be raced.
    User.objects.select_for_update().filter(pk=user.pk).first()

    task = (
        WarehouseTask.objects.select_for_update()
        .select_related("order")
        .filter(id=task_id)
        .first()
    )
    if not task:
        return None, "Task not found."
    if task.status != "open":
        return None, "This task is no longer available."
    if task.order.user_id == user.id:
        return None, "You can't pack your own order."
    if get_active_claim(task):
        return None, "Someone else just claimed this task."
    if not _all_parts_at_warehouse(task.order_id):
        return None, "Not every part of this order is at the warehouse yet."

    active = WarehouseClaim.objects.filter(worker=user, status__in=ACTIVE).count()
    if active >= MAX_ACTIVE_PACK_CLAIMS:
        return None, f"You can hold at most {MAX_ACTIVE_PACK_CLAIMS} tasks at a time."

    claim = WarehouseClaim.objects.create(
        task=task, worker=user, deadline=timezone.now() + PACK_CLAIM_DURATION
    )
    return claim, None


@transaction.atomic
def release_claim(*, claim_id, user):
    release_expired_claims()
    claim = _locked_claim(claim_id)
    error = _claim_problem(claim, user)
    if error:
        return None, error
    claim.status = "released"
    claim.save(update_fields=["status"])
    return claim, None


@transaction.atomic
def finish_claim(*, claim_id, user):
    """Order is packed: close the task and open the dropoff mission, which
    is the only thing that lets a courier claim it."""
    release_expired_claims()
    claim = _locked_claim(claim_id)
    error = _claim_problem(claim, user)
    if error:
        return None, error

    task = claim.task
    order = Order.objects.select_for_update().get(pk=task.order_id)
    if not _all_parts_at_warehouse(order.pk):
        return None, "Not every part of this order is at the warehouse any more."

    # Create the mission first so a failure (e.g. no active warehouse)
    # leaves the claim and task untouched.
    mission, error = delivery_services.create_dropoff_mission(order)
    if error:
        return None, error

    now = timezone.now()
    claim.status = "finished"
    claim.finished_at = now
    claim.save(update_fields=["status", "finished_at"])
    task.status = "completed"
    task.completed_at = now
    task.save(update_fields=["status", "completed_at"])

    _notify(
        order.user,
        f"Order #{order.pk} is packed and ready — a courier will deliver it soon.",
        sender=user,
    )
    return claim, None