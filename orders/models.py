from django.conf import settings
from django.db import models
from products.models import Product

# Create your models here.

User = settings.AUTH_USER_MODEL

class Cart(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

class Wishlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)

class WishlistItem(models.Model):
    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)

class Order(models.Model):
    PAYMENT_CHOICES = (
        ('cash', 'Cash'),
        ('card', 'Card'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_address = models.CharField(max_length=255)
    status = models.CharField(max_length=20, default='Pending')
    payment_type = models.CharField(max_length=4, choices=PAYMENT_CHOICES, null=True, blank=True)
    card = models.ForeignKey("users.Card", on_delete=models.CASCADE, null=True, blank=True)

class OrderItem(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)