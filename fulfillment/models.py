from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from merchant_interface.models import Membership
from orders.models import StoreOrder

User = settings.AUTH_USER_MODEL

CLAIM_DURATION = timezone.timedelta(hours=24)

# Arbitrary placeholder pay rate, per the spec ("wallet arbitrary for now").
# A percentage-wage staffer earns (their wage %) of this amount per item
# they processed. Swap this out for real payroll logic later.
PER_ITEM_BASE_RATE = Decimal("5.00")


class OrderClaim(models.Model):
    """One helper/manager's exclusive claim on a StoreOrder's in-store
    processing (picking/packing).

    Only one claim can be "claimed" (active) at a time for a given
    StoreOrder — enforced by the partial unique constraint below — but
    past claims (expired/released/finished) are kept around for history
    and wage auditing rather than deleted or overwritten.
    """

    STATUS_CHOICES = (
        ("claimed", "Claimed"),
        ("finished", "Finished"),
        ("expired", "Expired"),
        ("released", "Released"),
    )

    store_order = models.ForeignKey(
        StoreOrder, on_delete=models.CASCADE, related_name="claims"
    )
    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="claims"
    )
    claimant = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="fulfillment_claims"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="claimed")
    claimed_at = models.DateTimeField(auto_now_add=True)
    deadline = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    items_processed = models.PositiveIntegerField(null=True, blank=True)
    wage_earned = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    class Meta:
        ordering = ["-claimed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store_order"],
                condition=models.Q(status="claimed"),
                name="one_active_claim_per_store_order",
            )
        ]

    def __str__(self):
        return (
            f"{self.claimant} claim on StoreOrder #{self.store_order_id} "
            f"({self.status})"
        )

    @property
    def is_expired(self):
        return self.status == "claimed" and timezone.now() >= self.deadline

    @property
    def time_left(self):
        if self.status != "claimed":
            return timezone.timedelta(0)
        return max(self.deadline - timezone.now(), timezone.timedelta(0))

    def clean(self):
        super().clean()
        if (
            self.membership_id
            and self.store_order_id
            and self.membership.store_id != self.store_order.store_id
        ):
            raise ValidationError(
                "Membership must belong to the same store as the order being claimed."
            )


class WalletTransaction(models.Model):
    """Ledger entry crediting a staff member's in-app wallet for a store.

    Kept as an append-only log (rather than just mutating a single
    balance field) so the balance is always reconstructable and each
    payout is traceable back to the claim that earned it.
    """

    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="wallet_transactions"
    )
    claim = models.ForeignKey(
        OrderClaim,
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
        return f"{self.amount} -> {self.membership} ({self.created_at:%Y-%m-%d})"