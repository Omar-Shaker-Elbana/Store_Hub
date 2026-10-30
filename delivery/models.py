import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from merchant_interface.models import Store
from orders.models import Order, StoreOrder

User = settings.AUTH_USER_MODEL

CLAIM_DURATION = timedelta(hours=24)
MAX_ACTIVE_CLAIMS = 5  # missions one courier can hold at the same time
MAX_CODE_ATTEMPTS = 5  # wrong confirmation codes before a claim locks

# Arbitrary placeholder flat fees, same spirit as fulfillment's
# PER_ITEM_BASE_RATE. Swap for real pay logic later.
PICKUP_FEE = Decimal("25.00")
DROPOFF_FEE = Decimal("40.00")


def new_confirmation_code():
    return f"{secrets.randbelow(10**6):06d}"


def area_hint(address):
    """Coarse location (last two comma-separated parts) shown in the work
    pool, so couriers can choose by area without seeing a shopper's full
    address before they claim."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    return ", ".join(parts[-2:])


class Warehouse(models.Model):
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=32, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Warehouse.objects.exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_active=True).order_by("-is_default", "pk").first()


class StorePickupLocation(models.Model):
    """Where couriers collect a store's goods. Store has no address field of
    its own, so it lives here (editable in admin) until the merchant
    interface grows a form for it."""

    store = models.OneToOneField(
        Store, on_delete=models.CASCADE, related_name="pickup_location"
    )
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=32, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.store} pickup point"


class DeliveryMission(models.Model):
    """One leg of a delivery, waiting in (or already out of) the work pool.

    PICKUP  = merchant location -> warehouse, carrying ONLY that store's
              StoreOrder items.
    DROPOFF = warehouse -> shopper, carrying the whole order.

    Who holds a mission is tracked by DeliveryClaim rows (same pattern as
    fulfillment.OrderClaim), so every attempt stays in history.
    """

    KIND_CHOICES = (
        ("pickup", "Merchant → Warehouse"),
        ("dropoff", "Warehouse → Shopper"),
    )
    STATUS_CHOICES = (
        ("open", "Open"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )

    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="delivery_missions"
    )
    store_order = models.ForeignKey(
        StoreOrder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="delivery_missions",
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="missions"
    )

    # Snapshots, so later edits don't change a mission that's already live.
    pickup_address = models.CharField(max_length=255)
    pickup_contact = models.CharField(max_length=160, blank=True)
    dropoff_address = models.CharField(max_length=255)
    dropoff_contact = models.CharField(max_length=160, blank=True)
    items_snapshot = models.JSONField(default=list)

    fee = models.DecimalField(max_digits=8, decimal_places=2)
    # Cash the courier must collect from the shopper (checkout is cash-only).
    cod_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Read out by the RECEIVER (warehouse staff / shopper) and typed in by
    # the courier to prove the handover happened.
    confirmation_code = models.CharField(max_length=6, default=new_confirmation_code)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "kind", "created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["store_order"],
                condition=models.Q(kind="pickup") & ~models.Q(status="cancelled"),
                name="one_live_pickup_per_store_order",
            ),
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(kind="dropoff") & ~models.Q(status="cancelled"),
                name="one_live_dropoff_per_order",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} for Order #{self.order_id} ({self.status})"

    @property
    def item_count(self):
        return sum(int(i.get("quantity") or 0) for i in self.items_snapshot)

    @property
    def pickup_area_hint(self):
        return area_hint(self.pickup_address)

    @property
    def dropoff_area_hint(self):
        return area_hint(self.dropoff_address)


class DeliveryClaim(models.Model):
    """One courier's exclusive 24h hold on a mission."""

    ACTIVE_STATUSES = ("claimed", "picked_up")
    STATUS_CHOICES = (
        ("claimed", "Claimed"),
        ("picked_up", "Picked up"),
        ("delivered", "Delivered"),
        ("expired", "Expired"),
        ("released", "Released"),
        ("cancelled", "Cancelled"),
    )

    mission = models.ForeignKey(
        DeliveryMission, on_delete=models.CASCADE, related_name="claims"
    )
    courier = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="delivery_claims"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="claimed")
    claimed_at = models.DateTimeField(auto_now_add=True)
    deadline = models.DateTimeField()
    picked_up_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    code_attempts = models.PositiveSmallIntegerField(default=0)
    cash_collected = models.BooleanField(default=False)
    fee_earned = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )

    class Meta:
        ordering = ["-claimed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["mission"],
                condition=models.Q(status__in=("claimed", "picked_up")),
                name="one_active_claim_per_delivery_mission",
            )
        ]

    def __str__(self):
        return f"{self.courier} on mission #{self.mission_id} ({self.status})"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def is_expired(self):
        return self.is_active and timezone.now() >= self.deadline

    @property
    def time_left(self):
        if not self.is_active:
            return timedelta(0)
        return max(self.deadline - timezone.now(), timedelta(0))


class CourierWallet(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="courier_wallet"
    )
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return f"{self.user} wallet ({self.balance})"


class CourierWalletTransaction(models.Model):
    """Append-only ledger. OneToOne to the claim makes payout idempotent."""

    wallet = models.ForeignKey(
        CourierWallet, on_delete=models.CASCADE, related_name="transactions"
    )
    claim = models.OneToOneField(
        DeliveryClaim,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transaction",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.amount} -> {self.wallet.user} ({self.created_at:%Y-%m-%d})"