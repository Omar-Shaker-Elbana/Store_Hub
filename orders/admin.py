from django.contrib import admin
from .models import Cart, CartItem, Wishlist, WishlistItem, Order, OrderItem

admin.site.register([Cart, CartItem, Wishlist, WishlistItem, Order, OrderItem])