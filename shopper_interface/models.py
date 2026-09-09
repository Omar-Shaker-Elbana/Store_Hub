from django.conf import settings
from django.db import models

from merchant_interface.models import Store
from products.models import Product

# Create your models here.

User = settings.AUTH_USER_MODEL


class RecentlyViewed(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recently_viewed",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="viewed_by",
    )
    viewed_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        ordering = ["-viewed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                name="unique_recently_viewed_per_user_product",
            )
        ]

    def __str__(self):
        return f"{self.user} viewed {self.product}"


class StoreFollow(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="followed_stores",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="followers",
    )
    followed_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ["-followed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "store"], name="unique_follow_per_user_store"
            )
        ]

    def __str__(self):
        return f"{self.user} follows {self.store}"


class Interaction(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    action = models.CharField(
        choices=[
            ("view", "view"),
            ("cart", "cart"),
            ("wishlist", "wishlist"),
            ("purchase", "purchase"),
        ]
    )
    weight = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["product", "user"]),
            models.Index(fields=["user", "product"]),
        ]


class RecentSearch(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="recent_searches"
    )
    query_text = models.CharField(max_length=255)
    normalized_query = models.CharField(
        max_length=255, db_index=True
    )  # lowered + stripped, for dedup
    searched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-searched_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "normalized_query"],
                name="unique_recent_search_per_user",
            )
        ]


class SearchTrend(models.Model):
    normalized_query = models.CharField(max_length=255, unique=True)
    display_query = models.CharField(max_length=255)  # nicely-cased version to show
    hit_count = models.PositiveIntegerField(default=0)
    last_searched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-hit_count"]


class AbstractProductCollection(models.Model):
    """Shared shape for Cart and Wishlist: one per user."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.user}'s {self._meta.verbose_name}"


class AbstractCollectionItem(models.Model):
    """Shared shape for CartItem and WishlistItem: a product reference + timestamp."""

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, null=True, blank=True
    )
    added_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        abstract = True


class Cart(AbstractProductCollection):
    total_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00, null=True, blank=True
    )


class CartItem(AbstractCollectionItem):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product"], name="unique_product_per_cart"
            )
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product} ({self.cart.user})"


class Wishlist(AbstractProductCollection):
    pass


class WishlistItem(AbstractCollectionItem):
    wishlist = models.ForeignKey(
        Wishlist, on_delete=models.CASCADE, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["wishlist", "product"], name="unique_product_per_wishlist"
            )
        ]

    def __str__(self):
        return f"{self.product} ({self.wishlist.user})"
