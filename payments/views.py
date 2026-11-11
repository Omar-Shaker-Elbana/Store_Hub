from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone

from merchant_interface.models import Store

from . import services
from .models import OrderSettlement, PayrollRun


def superuser_required(view):
    """Website staff only. Anonymous visitors are sent to log in; anyone
    else who is not a superuser gets a 403."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def _profile_flag(user, flag):
    profile = getattr(user, "profile", None)
    return bool(profile and getattr(profile, flag, False))


@login_required
def wallet(request):
    """Every member's wallet: available balance, what they have earned and
    withdrawn, a withdraw form, and the full money history."""
    my_stores = Store.objects.filter(membership__user=request.user)
    services.close_due_payrolls(stores=my_stores)

    account = services.user_account(request.user)
    totals = services.wallet_totals(account)
    entries = account.entries.select_related("order", "store")
    page = Paginator(entries, 20).get_page(request.GET.get("page"))

    context = {
        "account": account,
        "totals": totals,
        "page_obj": page,
        "is_courier": _profile_flag(request.user, "is_delivery_person"),
        "is_warehouse": _profile_flag(request.user, "is_warehouse_staff"),
    }
    return render(request, "payments/wallet.html", context)


@login_required
def withdraw(request):
    if request.method != "POST":
        return redirect("payments:wallet")
    try:
        entry = services.withdraw(request.user, request.POST.get("amount", ""))
    except services.WalletError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Withdrew ${-entry.amount:.2f} from your wallet.")
    return redirect("payments:wallet")


@superuser_required
def platform(request):
    """The website's own account: the 1% it earns, the delivery & handling
    fees, what it pays couriers and warehouse workers, and its balance."""
    services.close_due_payrolls()
    summary = services.platform_summary()
    month_start = summary["month_start"]
    context = {
        "summary": summary,
        "settlements": OrderSettlement.objects.select_related("order", "order__user")[:25],
        "entries": summary["account"].entries.select_related("order", "store")[:30],
        "warehouse_month": services.warehouse_worker_payouts(since=month_start),
        "warehouse_total": services.warehouse_worker_payouts(),
        "recent_payrolls": PayrollRun.objects.select_related("store")[:10],
        "now": timezone.now(),
    }
    return render(request, "payments/platform.html", context)


@superuser_required
def run_payroll_now(request):
    if request.method != "POST":
        return redirect("payments:platform")
    ran = services.run_payroll_now()
    if ran:
        messages.success(request, f"Payroll paid for {ran} store(s) for the current month.")
    else:
        messages.info(request, "Nothing to pay: every store's current month is already paid.")
    return redirect("payments:platform")
