from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from merchant_interface.models import Membership
from notifications.models import Notification
from orders.models import StoreOrder

from .models import PER_ITEM_BASE_RATE, OrderClaim, WalletTransaction

# StoreOrders in these statuses still need in-store work done on them and
# can be claimed. Shipped/Delivered/Cancelled orders never show up in the
# pool.
CLAIMABLE_STORE_ORDER_STATUSES = ("Pending", "Processing")

# Only these Membership roles work the in-store fulfillment queue.
STAFF_ROLES = ("helper", "manager")


def release_expired_claims(store=None):
    """Flip any claim whose 24h window has passed to 'expired' so its
    StoreOrder falls back into the pool for someone else.

    Nothing in this project runs a background scheduler, so this is
    called opportunistically at the top of the views below instead of on
    a timer. A management command (release_expired_claims) is also
    provided for anyone who does want to run it on a cron/beat schedule.
    """
    qs = OrderClaim.objects.filter(status="claimed", deadline__lte=timezone.now())
    if store is not None:
        qs = qs.filter(store_order__store=store)
    qs.update(status="expired")


def get_active_claim(store_order):
    return store_order.claims.filter(status="claimed").first()


def eligible_membership(user, store):
    """Return the user's Membership at `store` if they're allowed to work
    the in-store fulfillment queue there (helper or manager), else None."""
    if not user.is_authenticated:
        return None
    return Membership.objects.filter(
        user=user, store=store, role__in=STAFF_ROLES
    ).first()


@transaction.atomic
def claim_store_order(*, store_order_id, store, user):
    """Attempt to claim a StoreOrder for in-store processing.

    Returns (claim, error_message) — exactly one of the two is None.
    """
    release_expired_claims(store=store)

    membership = eligible_membership(user, store)
    if not membership:
        return None, "You don't have permission to work orders for this store."

    store_order = (
        StoreOrder.objects.select_for_update()
        .filter(id=store_order_id, store=store)
        .first()
    )
    if not store_order:
        return None, "Order not found."

    if store_order.status not in CLAIMABLE_STORE_ORDER_STATUSES:
        return None, "This order is no longer available to claim."

    if get_active_claim(store_order):
        return None, "Someone else just claimed this order."

    claim = OrderClaim.objects.create(
        store_order=store_order,
        membership=membership,
        claimant=user,
        deadline=timezone.now() + timezone.timedelta(hours=24),
    )

    if store_order.status == "Pending":
        store_order.status = "Processing"
        store_order.save(update_fields=["status"])
        if store_order.order.user_id:
            Notification.objects.create(
                recipient=store_order.order.user,
                sender=user,
                message=(
                    f"Your order from {store_order.store.name} is now being processed."
                ),
            )

    return claim, None


@transaction.atomic
def finish_claim(*, claim_id, user):
    """Mark a claim finished, tally items processed, and — for
    percentage-wage staff only — credit an arbitrary per-item wage to
    their wallet.

    Returns (claim, error_message) — exactly one of the two is None.
    """
    claim = (
        OrderClaim.objects.select_for_update()
        .select_related("store_order", "store_order__order", "store_order__store", "membership")
        .filter(id=claim_id)
        .first()
    )
    if not claim:
        return None, "Claim not found."
    if claim.claimant_id != user.id:
        return None, "Only the person who claimed this order can finish it."
    if claim.status != "claimed":
        return None, "This claim is no longer active."
    if claim.is_expired:
        claim.status = "expired"
        claim.save(update_fields=["status"])
        return None, "Your 24-hour window on this order expired — it's back in the pool."

    items_processed = (
        claim.store_order.items.aggregate(total=Sum("quantity"))["total"] or 0
    )

    claim.status = "finished"
    claim.finished_at = timezone.now()
    claim.items_processed = items_processed

    membership = claim.membership
    if membership.wage_type == "percentage" and membership.wage:
        wage_earned = (
            Decimal(items_processed) * PER_ITEM_BASE_RATE * (membership.wage / Decimal("100"))
        ).quantize(Decimal("0.01"))
        claim.wage_earned = wage_earned
        membership.wallet_balance = (
            membership.wallet_balance or Decimal("0.00")
        ) + wage_earned
        membership.save(update_fields=["wallet_balance"])
        WalletTransaction.objects.create(
            membership=membership,
            claim=claim,
            amount=wage_earned,
            description=(
                f"Processed {items_processed} item(s) on order "
                f"#{claim.store_order.order_id} (StoreOrder #{claim.store_order_id})"
            ),
        )

    claim.save(
        update_fields=["status", "finished_at", "items_processed", "wage_earned"]
    )

    store_order = claim.store_order
    if store_order.status == "Processing":
        store_order.status = "Shipped"
        store_order.save(update_fields=["status"])
        if store_order.order.user_id:
            Notification.objects.create(
                recipient=store_order.order.user,
                sender=user,
                message=f"Your order from {store_order.store.name} is now Shipped.",
            )

    return claim, None


@transaction.atomic
def release_claim(*, claim_id, user):
    """Let a claimant voluntarily give back an order before the 24h
    deadline, instead of leaving it dangling until it auto-expires."""
    claim = OrderClaim.objects.select_for_update().filter(id=claim_id).first()
    if not claim:
        return None, "Claim not found."
    if claim.claimant_id != user.id:
        return None, "Only the person who claimed this order can release it."
    if claim.status != "claimed":
        return None, "This claim is no longer active."

    claim.status = "released"
    claim.save(update_fields=["status"])
    return claim, None