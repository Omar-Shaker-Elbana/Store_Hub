from django.conf import settings
from django.db import models

from merchant_interface.models import Store
from products.models import Product

User = settings.AUTH_USER_MODEL


class Order(models.Model):
    """One checkout transaction: single user, single shipping address, single payment.
    May fan out into several StoreOrders when the cart spans multiple merchants."""

    PAYMENT_CHOICES = (
        ("cash", "Cash"),
        # ('card', 'Card'),
    )

    STATUS_CHOICES = (
        ("Pending", "Pending"),
        ("Processing", "Processing"),
        ("Shipped", "Shipped"),
        ("Delivered", "Delivered"),
        ("Cancelled", "Cancelled"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    total_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    shipping_address = models.CharField(max_length=255, null=True, blank=True)
    payment_type = models.CharField(
        max_length=4, choices=PAYMENT_CHOICES, null=True, blank=True
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="Pending", null=True, blank=True
    )
    # card = models.ForeignKey("users.Card", on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.pk} ({self.user})"


class StoreOrder(models.Model):
    """A single merchant's slice of an Order. Status and delivery_person live
    here, not on Order, so Store A can mark their items Shipped without
    touching Store B's items in the same checkout."""

    STATUS_CHOICES = (
        ("Pending", "Pending"),
        ("Processing", "Processing"),
        ("Shipped", "Shipped"),
        ("Delivered", "Delivered"),
        ("Cancelled", "Cancelled"),
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="store_orders",
        null=True,
        blank=True,
    )
    store = models.ForeignKey(Store, on_delete=models.PROTECT, null=True, blank=True)
    delivery_person = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_store_orders",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="Pending", null=True, blank=True
    )
    subtotal = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    class Meta:
        ordering = ["-order__created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "store"], name="unique_store_per_order"
            )
        ]

    def __str__(self):
        return f"{self.store} portion of Order #{self.order_id} — {self.status}"


class OrderItem(models.Model):
    store_order = models.ForeignKey(
        StoreOrder,
        on_delete=models.CASCADE,
        related_name="items",
        null=True,
        blank=True,
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, null=True, blank=True
    )
    quantity = models.PositiveIntegerField(default=1, null=True, blank=True)
    price_at_purchase = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    def __str__(self):
        return f"{self.quantity} x {self.product}"
