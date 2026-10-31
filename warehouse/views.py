from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from delivery import services as delivery_services
from delivery.models import DeliveryClaim

from . import services
from .models import WarehouseClaim


def warehouse_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not services.is_warehouse_staff(request.user):
            messages.error(request, "Only warehouse staff can use the packing pool.")
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapper


@login_required
def incoming(request):
    """Warehouse staff: what's on its way in (with the code to read out to
    the courier) and what is waiting for a courier to take out."""
    if not services.is_warehouse_staff(request.user):
        messages.error(request, "Only warehouse staff can view this page.")
        return redirect("home")

    delivery_services.release_expired_claims()
    arriving_claims = (
        DeliveryClaim.objects.filter(mission__kind="pickup", status="picked_up")
        .exclude(courier=request.user)
        .select_related("mission", "mission__order", "mission__store_order__store", "courier")
        .order_by("deadline")
    )
    awaiting_dropoff = (
        delivery_services.open_missions("dropoff")
        .select_related("order")
        .order_by("created_at")
    )
    context = {
        "arriving_claims": arriving_claims,
        "awaiting_dropoff": awaiting_dropoff,
        "now": timezone.now(),
    }
    return render(request, "warehouse/warehouse_incoming.html", context)


@warehouse_required
def pack_pool(request):
    """Packing pool: whole orders whose parts have all arrived, plus this
    worker's own active claims."""
    services.release_expired_claims()
    context = {
        "available_tasks": services.open_tasks()
        .select_related("order", "warehouse")
        .order_by("created_at"),
        "my_active_claims": WarehouseClaim.objects.filter(
            worker=request.user, status__in=WarehouseClaim.ACTIVE_STATUSES
        )
        .select_related("task", "task__order")
        .order_by("deadline"),
        "now": timezone.now(),
    }
    return render(request, "warehouse/pack_pool.html", context)


@warehouse_required
def claim_task(request, task_id):
    if request.method != "POST":
        return redirect("warehouse:pack_pool")
    claim, error = services.claim_task(task_id=task_id, user=request.user)
    if error:
        messages.error(request, error)
        return redirect("warehouse:pack_pool")
    messages.success(request, "Order claimed — you have 24 hours to pack it.")
    return redirect("warehouse:claim_detail", claim_id=claim.id)


@login_required
def claim_detail(request, claim_id):
    claim = get_object_or_404(
        WarehouseClaim.objects.select_related(
            "task", "task__order", "task__warehouse", "worker"
        ),
        id=claim_id,
    )
    if claim.worker_id != request.user.id and not request.user.is_staff:
        messages.error(request, "You don't have permission to view this task.")
        return redirect("home")
    if claim.is_expired:
        services.release_expired_claims()
        claim.refresh_from_db()
    context = {
        "claim": claim,
        "task": claim.task,
        "can_act": claim.worker_id == request.user.id and claim.is_active,
        "now": timezone.now(),
    }
    return render(request, "warehouse/pack_claim_detail.html", context)


@warehouse_required
def finish_claim(request, claim_id):
    if request.method != "POST":
        return redirect("warehouse:claim_detail", claim_id=claim_id)
    claim, error = services.finish_claim(claim_id=claim_id, user=request.user)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Order packed — it's now in the courier pool.")
    return redirect("warehouse:claim_detail", claim_id=claim_id)


@warehouse_required
def release_claim(request, claim_id):
    if request.method != "POST":
        return redirect("warehouse:claim_detail", claim_id=claim_id)
    claim, error = services.release_claim(claim_id=claim_id, user=request.user)
    if error:
        messages.error(request, error)
        return redirect("warehouse:claim_detail", claim_id=claim_id)
    messages.success(request, "Task released back to the pool.")
    return redirect("warehouse:pack_pool")