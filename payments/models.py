from decimal import Decimal

from django.conf import settings
from django.db import models

from merchant_interface.models import Store

User = settings.AUTH_USER_MODEL


class Account(models.Model):
    """Somewhere money can sit.

    * platform: the website's own account (the 1% + delivery & handling fees,
      minus what it pays couriers and warehouse workers). Exactly one exists.
    * user: a person's wallet. One per user, shared by every role they hold
      (owner, manager, helper, courier, warehouse worker).
    * store: a store's treasury. Sales land here (after the platform's 1% and
      any staff commission); monthly payroll then pays salaries and shares the
      profit out to the owners.

    Only the platform account may dip below zero (it advances courier and
    packing pay before the shopper's cash arrives); the database refuses a
    negative wallet or treasury.
    """

    PLATFORM = "platform"
    USER = "user"
    STORE = "store"
    KIND_CHOICES = [
        (PLATFORM, "Platform"),
        (USER, "User wallet"),
        (STORE, "Store treasury"),
    ]

    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name="wallet"
    )
    store = models.OneToOneField(
        Store,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="treasury",
    )
    balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind"],
                condition=models.Q(kind="platform"),
                name="payments_single_platform_account",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="platform", user__isnull=True, store__isnull=True)
                    | models.Q(kind="user", user__isnull=False, store__isnull=True)
                    | models.Q(kind="store", store__isnull=False, user__isnull=True)
                ),
                name="payments_account_owner_matches_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(kind="platform") | models.Q(balance__gte=0),
                name="payments_only_platform_may_be_negative",
            ),
        ]

    def __str__(self):
        if self.kind == self.PLATFORM:
            return "Platform account"
        if self.kind == self.STORE:
            return f"{self.store} treasury"
        return f"{self.user} wallet"


class LedgerEntry(models.Model):
    """One line of an account's history. Append-only: the balance is always
    the sum of an account's entries, and every payout traces back to the
    claim / order / payroll run that caused it via `ref`."""

    KIND_CHOICES = [
        ("delivery_trip", "Delivery trip"),
        ("packing", "Package packed"),
        ("commission", "Order commission"),
        ("salary", "Monthly salary"),
        ("profit_share", "Profit share"),
        ("withdrawal", "Withdrawal"),
        ("sale", "Store sales (after platform fee)"),
        ("platform_fee", "Platform fee (1%)"),
        ("shipping_fee", "Delivery & handling fee"),
    ]

    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="entries"
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    # Signed: positive = money in, negative = money out.
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    # Idempotency key, e.g. "delivery_claim:12". The unique constraint below
    # makes paying the same thing twice impossible, even under a race.
    ref = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["account", "-created_at"]),
            models.Index(fields=["kind", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "kind", "ref"],
                condition=~models.Q(ref=""),
                name="payments_one_entry_per_account_kind_ref",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} -> {self.account}"


class OrderSettlement(models.Model):
    """Written once per order, when its drop-off is delivered and the courier
    hands over the shopper's cash. This row is what the superuser page lists
    as "1% earned on order #N", and it makes settling idempotent."""

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="settlement"
    )
    gross_sales = models.DecimalField(max_digits=12, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=12, decimal_places=2)
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2)
    settled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-settled_at", "-id"]

    def __str__(self):
        return f"Settlement of order #{self.order_id}"


class PayrollRun(models.Model):
    """One store's payroll for one calendar month (`period` = its first day).
    Unique per store+month so a month can never be paid twice."""

    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name="payroll_runs"
    )
    period = models.DateField()
    salaries_due = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    salaries_paid = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    profit_distributed = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    run_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "period"], name="payments_one_payroll_per_store_month"
            ),
        ]

    def __str__(self):
        return f"{self.store} payroll {self.period:%Y-%m}"

    @property
    def salary_shortfall(self):
        return self.salaries_due - self.salaries_paid
