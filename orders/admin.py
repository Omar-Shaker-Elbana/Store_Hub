from django.contrib import admin

from .models import Cart, CartItem, Order, OrderItem, Wishlist, WishlistItem

admin.site.register([Cart, CartItem, Wishlist, WishlistItem, Order, OrderItem])
