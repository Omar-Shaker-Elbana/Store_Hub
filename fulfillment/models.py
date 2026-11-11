from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from merchant_interface.models import Membership
from orders.models import StoreOrder

User = settings.AUTH_USER_MODEL

CLAIM_DURATION = timezone.timedelta(hours=24)


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
    # Commission this claim earned. Filled in by payments.settle_order once
    # the order is delivered and the shopper's cash has been collected.
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
