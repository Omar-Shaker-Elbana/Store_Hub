from django.db.models.signals import post_save
from django.dispatch import receiver

from orders.models import CartItem, WishlistItem, OrderItem
from .models import Interaction, RecentlyViewed

VIEW_WEIGHT = 1
WISHLIST_WEIGHT = 3
CART_WEIGHT = 5
PURCHASE_WEIGHT = 10


@receiver(post_save, sender=RecentlyViewed)
def log_view(sender, instance, **kwargs):
    if instance.user_id and instance.product_id:
        Interaction.objects.create(
            user=instance.user, product=instance.product,
            action='view', weight=VIEW_WEIGHT,
        )


@receiver(post_save, sender=CartItem)
def log_cart_add(sender, instance, created, **kwargs):
    if created:
        Interaction.objects.create(
            user=instance.cart.user, product=instance.product,
            action='cart', weight=CART_WEIGHT,
        )


@receiver(post_save, sender=WishlistItem)
def log_wishlist_add(sender, instance, created, **kwargs):
    if created:
        Interaction.objects.create(
            user=instance.wishlist.user, product=instance.product,
            action='wishlist', weight=WISHLIST_WEIGHT,
        )


@receiver(post_save, sender=OrderItem)
def log_purchase(sender, instance, created, **kwargs):
    if created:
        Interaction.objects.create(
            user=instance.order.user, product=instance.product,
            action='purchase', weight=PURCHASE_WEIGHT * instance.quantity,
        )