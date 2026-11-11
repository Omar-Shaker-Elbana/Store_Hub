"""All money movement goes through this module.

Lock order is always platform -> store treasury -> user wallet, so two
payouts running at the same time can never deadlock each other.
"""

import calendar
from datetime import date
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from fulfillment.models import OrderClaim
from merchant_interface.models import Membership
from orders.models import Order, StoreOrder

from .models import Account, LedgerEntry, OrderSettlement, PayrollRun
from .rates import CENT, WAREHOUSE_PACK_FEE, money, platform_fee, shipping_fee

ZERO = Decimal("0.00")
# Only these roles can be on a monthly salary (owners are always on a profit
# percentage — see Membership.clean()).
SALARIED_ROLES = ("manager", "helper")


class WalletError(Exception):
    """Raised with a message that is safe to show to the user."""


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def _get_or_create(**lookup):
    account = Account.objects.filter(**lookup).first()
    if account:
        return account
    try:
        with transaction.atomic():
            return Account.objects.create(**lookup)
    except IntegrityError:  # someone else created it a moment ago
        return Account.objects.get(**lookup)


def platform_account():
    return _get_or_create(kind=Account.PLATFORM)


def user_account(user):
    return _get_or_create(kind=Account.USER, user=user)


def store_account(store):
    return _get_or_create(kind=Account.STORE, store=store)


def wallet_balance(user):
    """Read-only: never creates a wallet just because a page was opened."""
    balance = Account.objects.filter(kind=Account.USER, user=user).values_list(
        "balance", flat=True
    ).first()
    return balance if balance is not None else ZERO


def _lock(account):
    """Re-read an account under a row lock. Must be inside a transaction."""
    return Account.objects.select_for_update().get(pk=account.pk)


def _post(account, amount, kind, description, *, order_id=None, store_id=None, ref=""):
    """Add one signed entry to an already-locked account."""
    account.balance = account.balance + amount
    account.save(update_fields=["balance"])
    return LedgerEntry.objects.create(
        account=account,
        kind=kind,
        amount=amount,
        balance_after=account.balance,
        description=description[:255],
        order_id=order_id,
        store_id=store_id,
        ref=ref,
    )


def _already_paid(account, kind, ref):
    return LedgerEntry.objects.filter(account=account, kind=kind, ref=ref).exists()


# ---------------------------------------------------------------------------
# Courier and warehouse pay (paid by the platform the moment the work is done)
# ---------------------------------------------------------------------------


@transaction.atomic
def pay_courier(claim):
    """Credit a courier for one completed trip (flat COURIER_TRIP_FEE, taken
    from the platform account). Safe to call twice for the same claim."""
    amount = money(claim.fee_earned or ZERO)
    if amount <= 0:
        return None
    ref = f"delivery_claim:{claim.pk}"
    platform = _lock(platform_account())
    wallet = _lock(user_account(claim.courier))
    if _already_paid(wallet, "delivery_trip", ref):
        return None
    mission = claim.mission
    label = f"{mission.get_kind_display()} — order #{mission.order_id}"
    _post(
        platform,
        -amount,
        "delivery_trip",
        f"Courier pay to {claim.courier}: {label}",
        order_id=mission.order_id,
        ref=ref,
    )
    return _post(
        wallet,
        amount,
        "delivery_trip",
        label,
        order_id=mission.order_id,
        ref=ref,
    )


@transaction.atomic
def pay_packer(warehouse_claim):
    """Credit a warehouse worker for one packed package (WAREHOUSE_PACK_FEE,
    taken from the platform account). Safe to call twice for the same claim."""
    amount = WAREHOUSE_PACK_FEE
    ref = f"warehouse_claim:{warehouse_claim.pk}"
    order_id = warehouse_claim.task.order_id
    platform = _lock(platform_account())
    wallet = _lock(user_account(warehouse_claim.worker))
    if _already_paid(wallet, "packing", ref):
        return None
    _post(
        platform,
        -amount,
        "packing",
        f"Packing pay to {warehouse_claim.worker}: order #{order_id}",
        order_id=order_id,
        ref=ref,
    )
    return _post(
        wallet,
        amount,
        "packing",
        f"Packed order #{order_id}",
        order_id=order_id,
        ref=ref,
    )


# ---------------------------------------------------------------------------
# Order settlement (runs when the shopper's cash has been collected)
# ---------------------------------------------------------------------------


@transaction.atomic
def settle_order(order_id):
    """Split the shopper's cash for a delivered order.

    For every delivered store slice:
      * 1% goes to the platform account,
      * the rest goes to that store's treasury,
      * a percentage-wage helper/manager who finished the claim gets their
        commission out of that rest.
    The delivery & handling fee goes to the platform (it pays for the trips
    and the packing already advanced to couriers and warehouse workers).

    Idempotent: returns the existing OrderSettlement if already settled.
    """
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if order is None:
        return None
    existing = OrderSettlement.objects.filter(order=order).first()
    if existing:
        return existing

    store_orders = list(
        StoreOrder.objects.select_related("store")
        .filter(order=order, status="Delivered")
        .order_by("pk")
    )
    if not store_orders:
        return None

    platform = _lock(platform_account())
    gross_total = ZERO
    fee_total = ZERO

    for store_order in store_orders:
        gross = money(store_order.subtotal or ZERO)
        fee = platform_fee(gross)
        net = gross - fee
        store = store_order.store
        ref = f"store_order:{store_order.pk}"

        if fee > 0:
            _post(
                platform,
                fee,
                "platform_fee",
                f"1% of order #{order.pk} sales at {store}",
                order_id=order.pk,
                store_id=store.pk,
                ref=ref,
            )
        treasury = _lock(store_account(store))
        _post(
            treasury,
            net,
            "sale",
            f"Order #{order.pk} sales (after {fee} platform fee)",
            order_id=order.pk,
            store_id=store.pk,
            ref=ref,
        )

        claims = OrderClaim.objects.select_related("membership", "claimant").filter(
            store_order=store_order, status="finished"
        )
        for claim in claims:
            membership = claim.membership
            if membership.wage_type != "percentage" or not membership.wage:
                continue
            commission = min(money(net * membership.wage / Decimal("100")), treasury.balance)
            if commission <= 0:
                continue
            wallet = _lock(user_account(claim.claimant))
            claim_ref = f"claim:{claim.pk}"
            _post(
                treasury,
                -commission,
                "commission",
                f"Commission to {claim.claimant} on order #{order.pk}",
                order_id=order.pk,
                store_id=store.pk,
                ref=claim_ref,
            )
            _post(
                wallet,
                commission,
                "commission",
                f"{membership.wage}% commission on order #{order.pk} at {store}",
                order_id=order.pk,
                store_id=store.pk,
                ref=claim_ref,
            )
            claim.wage_earned = commission
            claim.save(update_fields=["wage_earned"])

        gross_total += gross
        fee_total += fee

    shipping = shipping_fee(len(store_orders))
    if shipping > 0:
        _post(
            platform,
            shipping,
            "shipping_fee",
            f"Delivery & handling fee — order #{order.pk}",
            order_id=order.pk,
            ref=f"order:{order.pk}",
        )
    return OrderSettlement.objects.create(
        order=order,
        gross_sales=gross_total,
        platform_fee=fee_total,
        shipping_fee=shipping,
    )


# ---------------------------------------------------------------------------
# Monthly payroll (salaries first, then the owners share what is left)
# ---------------------------------------------------------------------------


def next_month(day):
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, 1)


def _month_end(period):
    return period.replace(day=calendar.monthrange(period.year, period.month)[1])


def _salary_for_month(membership, period):
    """Full monthly salary, pro-rated by days if they joined mid-month.
    Returns None if they hadn't joined yet by the end of the month."""
    last_day = _month_end(period)
    joined = membership.join_date
    if joined and joined > last_day:
        return None
    days_in_month = last_day.day
    if joined and joined > period:
        days_worked = (last_day - joined).days + 1
        return money(membership.wage * days_worked / days_in_month)
    return money(membership.wage)


@transaction.atomic
def run_payroll(store, period):
    """Pay one store's month. Returns the PayrollRun, or None if that month
    was already paid.

    1. Salaried managers/helpers are paid from the store treasury. If the
       treasury can't cover everyone, each is paid the same fraction of what
       they are owed (the shortfall is recorded on the run, not carried).
    2. Whatever is left is shared among the owners by their profit-share
       percentage. Any unallocated remainder stays in the treasury.
    """
    period = period.replace(day=1)
    treasury = _lock(store_account(store))
    # Checked *after* taking the lock, so two payroll runs started at the
    # same moment can't both decide the month is still unpaid.
    if PayrollRun.objects.filter(store=store, period=period).exists():
        return None
    label = f"{period:%B %Y}"

    owed = []
    salaried = Membership.objects.select_related("user").filter(
        store=store, wage_type="salary", role__in=SALARIED_ROLES, wage__gt=0
    )
    for membership in salaried.order_by("join_date", "pk"):
        amount = _salary_for_month(membership, period)
        if amount:
            owed.append((membership, amount))
    due = sum((amount for _, amount in owed), ZERO)

    full = due <= treasury.balance
    ratio = Decimal("1") if full or due == 0 else treasury.balance / due
    paid = ZERO
    for membership, amount in owed:
        pay = amount if full else (amount * ratio).quantize(CENT, rounding=ROUND_DOWN)
        pay = min(pay, treasury.balance)
        if pay <= 0:
            continue
        wallet = _lock(user_account(membership.user))
        ref = f"payroll:{store.pk}:{period:%Y-%m}:{membership.pk}"
        _post(
            treasury,
            -pay,
            "salary",
            f"{label} salary to {membership.user}",
            store_id=store.pk,
            ref=ref,
        )
        _post(
            wallet,
            pay,
            "salary",
            f"{label} salary at {store}",
            store_id=store.pk,
            ref=ref,
        )
        paid += pay

    pool = treasury.balance
    distributed = ZERO
    owners = Membership.objects.select_related("user").filter(
        store=store, role="owner", wage_type="percentage", wage__gt=0
    )
    for membership in owners.order_by("pk"):
        share = (pool * membership.wage / Decimal("100")).quantize(CENT, rounding=ROUND_DOWN)
        share = min(share, treasury.balance)
        if share <= 0:
            continue
        wallet = _lock(user_account(membership.user))
        ref = f"payroll:{store.pk}:{period:%Y-%m}:{membership.pk}"
        _post(
            treasury,
            -share,
            "profit_share",
            f"{label} profit share to {membership.user} ({membership.wage}%)",
            store_id=store.pk,
            ref=ref,
        )
        _post(
            wallet,
            share,
            "profit_share",
            f"{label} profit share ({membership.wage}%) at {store}",
            store_id=store.pk,
            ref=ref,
        )
        distributed += share

    return PayrollRun.objects.create(
        store=store,
        period=period,
        salaries_due=due,
        salaries_paid=paid,
        profit_distributed=distributed,
    )


def close_due_payrolls(stores=None, now=None):
    """Pay every fully finished month that hasn't been paid yet.

    Nothing in this project runs a scheduler, so (like the claim-expiry
    helpers) this is called opportunistically when a wallet or the platform
    page is opened. `python manage.py run_payroll` does the same from cron.
    """
    today = (now or timezone.now()).date()
    this_month = today.replace(day=1)
    accounts = Account.objects.filter(kind=Account.STORE).select_related("store")
    if stores is not None:
        accounts = accounts.filter(store__in=stores)

    closed = 0
    for account in accounts:
        last = PayrollRun.objects.filter(store=account.store).order_by("-period").first()
        if last:
            month = next_month(last.period)
        else:
            first_entry = account.entries.order_by("created_at", "id").first()
            if first_entry is None:
                continue
            month = first_entry.created_at.date().replace(day=1)
        while month < this_month:
            if run_payroll(account.store, month):
                closed += 1
            month = next_month(month)
    return closed


def run_payroll_now(stores=None, now=None):
    """Early close: pay the *current* month right now (demos, mid-month
    payouts). Months that already have a run are skipped."""
    this_month = (now or timezone.now()).date().replace(day=1)
    accounts = Account.objects.filter(kind=Account.STORE).select_related("store")
    if stores is not None:
        accounts = accounts.filter(store__in=stores)
    close_due_payrolls(stores=stores, now=now)
    ran = 0
    for account in accounts:
        if run_payroll(account.store, this_month):
            ran += 1
    return ran


# ---------------------------------------------------------------------------
# Withdrawals
# ---------------------------------------------------------------------------


def parse_amount(raw):
    try:
        amount = Decimal(str(raw).strip())
        if not amount.is_finite():
            raise InvalidOperation
        # Always round *down* to cents: never take more than was asked for.
        amount = amount.quantize(CENT, rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError):
        raise WalletError("Enter a valid amount.")
    if amount <= 0:
        raise WalletError("Enter an amount greater than zero.")
    return amount


@transaction.atomic
def withdraw(user, raw_amount):
    """Take money out of a wallet. Only ever subtracts what was asked, and
    never more than the available balance. Returns the ledger entry."""
    amount = parse_amount(raw_amount)
    wallet = _lock(user_account(user))
    if amount > wallet.balance:
        raise WalletError(
            f"You can only withdraw up to your available balance (${wallet.balance})."
        )
    return _post(wallet, -amount, "withdrawal", "Withdrawal")


# ---------------------------------------------------------------------------
# Read models for the pages
# ---------------------------------------------------------------------------


def wallet_totals(account):
    entries = account.entries
    earned = entries.filter(amount__gt=0).aggregate(t=Sum("amount"))["t"] or ZERO
    withdrawn = (
        entries.filter(kind="withdrawal").aggregate(t=Sum("amount"))["t"] or ZERO
    )
    month_start = timezone.now().date().replace(day=1)
    earned_month = (
        entries.filter(amount__gt=0, created_at__date__gte=month_start).aggregate(
            t=Sum("amount")
        )["t"]
        or ZERO
    )
    return {
        "earned": earned,
        "withdrawn": -withdrawn,
        "earned_month": earned_month,
    }


def platform_summary():
    """Everything the superuser page needs about the website's own money."""
    platform = platform_account()
    entries = platform.entries
    month_start = timezone.now().date().replace(day=1)

    def total(kind, since=None, negate=False):
        qs = entries.filter(kind=kind)
        if since:
            qs = qs.filter(created_at__date__gte=since)
        value = qs.aggregate(t=Sum("amount"))["t"] or ZERO
        return -value if negate else value

    in_flight = (
        entries.filter(
            kind__in=("delivery_trip", "packing"), order__settlement__isnull=True
        ).aggregate(t=Sum("amount"))["t"]
        or ZERO
    )
    month_net = (
        entries.filter(created_at__date__gte=month_start).aggregate(t=Sum("amount"))["t"]
        or ZERO
    )
    return {
        "account": platform,
        "balance": platform.balance,
        "fees_total": total("platform_fee"),
        "fees_month": total("platform_fee", month_start),
        "shipping_total": total("shipping_fee"),
        "shipping_month": total("shipping_fee", month_start),
        "courier_total": total("delivery_trip", negate=True),
        "courier_month": total("delivery_trip", month_start, negate=True),
        "packing_total": total("packing", negate=True),
        "packing_month": total("packing", month_start, negate=True),
        # Courier/packing pay already advanced for orders whose cash hasn't
        # arrived yet; it comes back with the shipping fee on delivery.
        "in_flight": -in_flight,
        "balance_after_in_flight": platform.balance - in_flight,
        "month_net": month_net,
        "month_start": month_start,
        "orders_settled": OrderSettlement.objects.count(),
    }


def warehouse_worker_payouts(since=None):
    """Per warehouse worker: packages packed and what the platform paid them."""
    qs = LedgerEntry.objects.filter(kind="packing", account__kind=Account.USER)
    if since:
        qs = qs.filter(created_at__date__gte=since)
    return (
        qs.values("account__user__id", "account__user__username", "account__user__email")
        .annotate(packages=Count("id"), paid=Sum("amount"))
        .order_by("-paid", "account__user__username")
    )
