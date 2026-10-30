from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from orders.models import Order

from . import services
from .models import CourierWallet, DeliveryClaim, DeliveryMission


def courier_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not services.is_courier(request.user):
            messages.error(
                request, "Only registered delivery partners can use the work pool."
            )
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapper


@courier_required
def work_pool(request):
    """Courier dashboard: unclaimed missions anyone on the delivery team can
    take, plus the current user's own active claims."""
    services.release_expired_claims()

    kind = request.GET.get("kind")
    available_missions = (
        services.open_missions(kind)
        .select_related("order", "warehouse", "store_order__store")
        .order_by("created_at")
    )
    my_active_claims = (
        DeliveryClaim.objects.filter(
            courier=request.user, status__in=DeliveryClaim.ACTIVE_STATUSES
        )
        .select_related("mission", "mission__order")
        .order_by("deadline")
    )
    context = {
        "available_missions": available_missions,
        "my_active_claims": my_active_claims,
        "kind": kind,
        "now": timezone.now(),
    }
    return render(request, "delivery/work_pool.html", context)


@courier_required
def claim_mission(request, mission_id):
    if request.method != "POST":
        return redirect("delivery:work_pool")

    claim, error = services.claim_mission(mission_id=mission_id, user=request.user)
    if error:
        messages.error(request, error)
        return redirect("delivery:work_pool")

    messages.success(request, "Mission claimed — you have 24 hours to finish it.")
    return redirect("delivery:claim_detail", claim_id=claim.id)


@login_required
def claim_detail(request, claim_id):
    claim = get_object_or_404(
        DeliveryClaim.objects.select_related(
            "mission", "mission__order", "mission__warehouse", "courier"
        ),
        id=claim_id,
    )
    if claim.courier_id != request.user.id and not request.user.is_staff:
        messages.error(request, "You don't have permission to view this mission.")
        return redirect("home")

    if claim.is_expired:
        services.release_expired_claims()
        claim.refresh_from_db()

    context = {
        "claim": claim,
        "mission": claim.mission,
        "can_act": claim.courier_id == request.user.id and claim.is_active,
        "needs_cash": claim.mission.kind == "dropoff" and claim.mission.cod_amount > 0,
        "now": timezone.now(),
    }
    return render(request, "delivery/claim_detail.html", context)


@courier_required
def pick_up_claim(request, claim_id):
    if request.method != "POST":
        return redirect("delivery:claim_detail", claim_id=claim_id)
    claim, error = services.mark_picked_up(claim_id=claim_id, user=request.user)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Marked as picked up — the clock keeps running.")
    return redirect("delivery:claim_detail", claim_id=claim_id)


@courier_required
def complete_claim(request, claim_id):
    if request.method != "POST":
        return redirect("delivery:claim_detail", claim_id=claim_id)
    claim, error = services.complete_claim(
        claim_id=claim_id,
        user=request.user,
        code=request.POST.get("code", ""),
        cash_collected=request.POST.get("cash_collected") == "on",
    )
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Handover confirmed — your wallet was credited.")
    return redirect("delivery:claim_detail", claim_id=claim_id)


@courier_required
def release_claim(request, claim_id):
    if request.method != "POST":
        return redirect("delivery:claim_detail", claim_id=claim_id)
    claim, error = services.release_claim(claim_id=claim_id, user=request.user)
    if error:
        messages.error(request, error)
        return redirect("delivery:claim_detail", claim_id=claim_id)
    messages.success(request, "Mission released back to the pool.")
    return redirect("delivery:work_pool")


@courier_required
def my_wallet(request):
    wallet, _ = CourierWallet.objects.get_or_create(user=request.user)
    context = {
        "wallet": wallet,
        "transactions": wallet.transactions.select_related("claim__mission"),
        "history": DeliveryClaim.objects.filter(courier=request.user)
        .exclude(status__in=DeliveryClaim.ACTIVE_STATUSES)
        .select_related("mission")[:25],
    }
    return render(request, "delivery/wallet.html", context)


@login_required
def warehouse_incoming(request):
    """Warehouse staff: what's on its way in (with the code to read out to
    the courier) and what is waiting for a courier to take out."""
    if not services.is_warehouse_staff(request.user):
        messages.error(request, "Only warehouse staff can view this page.")
        return redirect("home")

    services.release_expired_claims()
    arriving_claims = (
        DeliveryClaim.objects.filter(mission__kind="pickup", status="picked_up")
        .select_related("mission", "mission__order", "mission__store_order__store", "courier")
        .order_by("deadline")
    )
    awaiting_dropoff = (
        services.open_missions("dropoff")
        .select_related("order")
        .order_by("created_at")
    )
    context = {
        "arriving_claims": arriving_claims,
        "awaiting_dropoff": awaiting_dropoff,
        "now": timezone.now(),
    }
    return render(request, "delivery/warehouse_incoming.html", context)


@login_required
def order_tracking(request, order_id):
    """Shopper-facing progress for one of their own orders. The dropoff
    confirmation code is only revealed once the courier is on the way."""
    order = get_object_or_404(
        Order.objects.prefetch_related("store_orders__store", "store_orders__delivery_person"),
        pk=order_id,
        user=request.user,
    )
    missions = DeliveryMission.objects.filter(order=order).order_by("created_at")
    out_claim = (
        DeliveryClaim.objects.filter(
            mission__order=order, mission__kind="dropoff", status="picked_up"
        )
        .select_related("mission", "courier")
        .first()
    )
    context = {
        "order": order,
        "missions": missions,
        "dropoff_claim": out_claim,
        "confirmation_code": out_claim.mission.confirmation_code if out_claim else None,
    }
    return render(request, "delivery/order_tracking.html", context)