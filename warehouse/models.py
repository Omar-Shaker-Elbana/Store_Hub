from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL

PACK_CLAIM_DURATION = timedelta(hours=24)
MAX_ACTIVE_PACK_CLAIMS = 5  # packing tasks one worker can hold at once


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

class WarehouseTask(models.Model):
    """Consolidate + pack ONE whole order once every store's part has
    arrived at the warehouse. Finishing it opens the dropoff mission."""

    STATUS_CHOICES = (
        ("open", "Open"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )

    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="warehouse_tasks"
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="tasks"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open")
    items_snapshot = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["order"],
                condition=~models.Q(status="cancelled"),
                name="one_live_warehouse_task_per_order",
            )
        ]

    def __str__(self):
        return f"Pack order #{self.order_id} ({self.status})"

    @property
    def item_count(self):
        return sum(int(i.get("quantity") or 0) for i in self.items_snapshot)


class WarehouseClaim(models.Model):
    """One warehouse worker's exclusive 24h hold on a packing task."""

    ACTIVE_STATUSES = ("claimed",)
    STATUS_CHOICES = (
        ("claimed", "Claimed"),
        ("finished", "Finished"),
        ("expired", "Expired"),
        ("released", "Released"),
        ("cancelled", "Cancelled"),
    )

    task = models.ForeignKey(
        WarehouseTask, on_delete=models.CASCADE, related_name="claims"
    )
    worker = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="warehouse_claims"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="claimed")
    claimed_at = models.DateTimeField(auto_now_add=True)
    deadline = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-claimed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["task"],
                condition=models.Q(status="claimed"),
                name="one_active_claim_per_warehouse_task",
            )
        ]

    def __str__(self):
        return f"{self.worker} on packing task #{self.task_id} ({self.status})"

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