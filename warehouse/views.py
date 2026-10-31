from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from delivery import services
from delivery.models import DeliveryClaim


@login_required
def incoming(request):
    """Warehouse staff: what's on its way in (with the code to read out to
    the courier) and what is waiting for a courier to take out."""
    if not services.is_warehouse_staff(request.user):
        messages.error(request, "Only warehouse staff can view this page.")
        return redirect("home")

    services.release_expired_claims()
    arriving_claims = (
        DeliveryClaim.objects.filter(mission__kind="pickup", status="picked_up")
        .exclude(courier=request.user)
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
    return render(request, "warehouse/incoming.html", context)