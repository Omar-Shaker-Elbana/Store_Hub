from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from merchant_interface.models import Membership, Store
from orders.models import StoreOrder
from payments import services as payments_services
from payments.models import Account, LedgerEntry, PayrollRun

from . import services
from .models import OrderClaim


@login_required
def work_pool(request, store_id):
    """The in-store work dashboard for one store: unclaimed orders anyone
    on staff can pick up, plus the current user's own active claims."""
    store = get_object_or_404(Store, id=store_id)
    membership = services.eligible_membership(request.user, store)
    if not membership:
        messages.error(
            request, "You don't have permission to work orders for this store."
        )
        return redirect("create_store")

    services.release_expired_claims(store=store)

    available_orders = (
        StoreOrder.objects.filter(
            store=store, status__in=services.CLAIMABLE_STORE_ORDER_STATUSES
        )
        .exclude(claims__status="claimed")
        .select_related("order", "order__user")
        .prefetch_related("items__product")
        .order_by("order__created_at")
    )

    my_active_claims = (
        OrderClaim.objects.filter(
            claimant=request.user, status="claimed", store_order__store=store
        )
        .select_related("store_order", "store_order__order")
        .order_by("deadline")
    )

    context = {
        "store": store,
        "membership": membership,
        "available_orders": available_orders,
        "my_active_claims": my_active_claims,
        "wallet_balance": payments_services.wallet_balance(request.user),
        "now": timezone.now(),
    }
    return render(request, "fulfillment/work_pool.html", context)


@login_required
def claim_order(request, store_id, store_order_id):
    store = get_object_or_404(Store, id=store_id)
    if request.method != "POST":
        return redirect("fulfillment:work_pool", store_id=store.id)

    claim, error = services.claim_store_order(
        store_order_id=store_order_id, store=store, user=request.user
    )
    if error:
        messages.error(request, error)
        return redirect("fulfillment:work_pool", store_id=store.id)

    messages.success(request, "Order claimed — you have 24 hours to finish it.")
    return redirect("fulfillment:claim_detail", claim_id=claim.id)


@login_required
def claim_detail(request, claim_id):
    claim = get_object_or_404(
        OrderClaim.objects.select_related(
            "store_order", "store_order__order", "store_order__store", "membership"
        ).prefetch_related("store_order__items__product"),
        id=claim_id,
    )
    store = claim.store_order.store
    membership = Membership.objects.filter(user=request.user, store=store).first()

    is_owner_or_manager = membership is not None and membership.role in (
        "owner",
        "manager",
    )
    if claim.claimant_id != request.user.id and not is_owner_or_manager:
        messages.error(request, "You don't have permission to view this order.")
        return redirect("create_store")

    if claim.status == "claimed" and claim.is_expired:
        services.release_expired_claims(store=store)
        claim.refresh_from_db()

    context = {
        "claim": claim,
        "store": store,
        "can_act": claim.claimant_id == request.user.id and claim.status == "claimed",
        "now": timezone.now(),
    }
    return render(request, "fulfillment/claim_detail.html", context)


@login_required
def finish_claim(request, claim_id):
    if request.method != "POST":
        return redirect("fulfillment:claim_detail", claim_id=claim_id)

    claim, error = services.finish_claim(claim_id=claim_id, user=request.user)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Nice work — order marked finished and wallet updated.")
    return redirect("fulfillment:claim_detail", claim_id=claim_id)


@login_required
def release_claim(request, claim_id):
    if request.method != "POST":
        return redirect("fulfillment:claim_detail", claim_id=claim_id)

    claim, error = services.release_claim(claim_id=claim_id, user=request.user)
    if error:
        messages.error(request, error)
        return redirect("fulfillment:claim_detail", claim_id=claim_id)

    messages.success(request, "Claim released back to the pool.")
    return redirect("fulfillment:work_pool", store_id=claim.store_order.store_id)


@login_required
def store_overview(request, store_id):
    """Owner/manager oversight: everyone's active + recent claims, what each
    staff member has earned at this store, and the store's treasury."""
    store = get_object_or_404(Store, id=store_id)
    membership = Membership.objects.filter(user=request.user, store=store).first()
    if not membership or membership.role not in ("owner", "manager"):
        messages.error(request, "You don't have permission to view this.")
        return redirect("create_store")

    services.release_expired_claims(store=store)
    payments_services.close_due_payrolls(stores=[store])

    active_claims = (
        OrderClaim.objects.filter(store_order__store=store, status="claimed")
        .select_related("claimant", "store_order", "store_order__order")
        .order_by("deadline")
    )
    recent_claims = (
        OrderClaim.objects.filter(store_order__store=store)
        .exclude(status="claimed")
        .select_related("claimant", "store_order", "store_order__order")
        .order_by("-claimed_at")[:25]
    )
    # What each helper/manager has earned *at this store* (commission and
    # salary), summed from the ledger. Their wallet itself is shared across
    # every job they hold, so it isn't shown here.
    earned_by_user = {
        row["account__user_id"]: row["total"]
        for row in LedgerEntry.objects.filter(
            store=store, account__kind=Account.USER, amount__gt=0
        )
        .values("account__user_id")
        .annotate(total=Sum("amount"))
    }
    staff_wallets = list(
        Membership.objects.filter(store=store, role__in=("helper", "manager")).select_related(
            "user"
        )
    )
    for member in staff_wallets:
        member.earned_here = earned_by_user.get(member.user_id, Decimal("0.00"))
    staff_wallets.sort(key=lambda member: member.earned_here, reverse=True)

    treasury = Account.objects.filter(kind=Account.STORE, store=store).first()

    context = {
        "store": store,
        "active_claims": active_claims,
        "recent_claims": recent_claims,
        "staff_wallets": staff_wallets,
        "treasury_balance": treasury.balance if treasury else Decimal("0.00"),
        "payroll_runs": PayrollRun.objects.filter(store=store)[:6],
        "now": timezone.now(),
    }
    return render(request, "fulfillment/store_overview.html", context)
