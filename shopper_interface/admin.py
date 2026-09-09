from django.contrib import admin

from .models import (Cart, CartItem, Interaction, RecentlyViewed,
                      RecentSearch, SearchTrend, StoreFollow, Wishlist,
                      WishlistItem)


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    fields = ("product", "quantity", "added_at")
    readonly_fields = ("added_at",)
    autocomplete_fields = ("product",)


class WishlistItemInline(admin.TabularInline):
    model = WishlistItem
    extra = 0
    fields = ("product", "added_at")
    readonly_fields = ("added_at",)
    autocomplete_fields = ("product",)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("user", "total_price", "item_count")
    search_fields = ("user__username", "user__email")
    inlines = [CartItemInline]

    def item_count(self, obj):
        return obj.cartitem_set.count()
    item_count.short_description = "Items"


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("cart", "product", "quantity", "added_at")
    list_filter = ("added_at",)
    search_fields = ("cart__user__username", "product__name")
    autocomplete_fields = ("cart", "product")


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ("user", "item_count")
    search_fields = ("user__username", "user__email")
    inlines = [WishlistItemInline]

    def item_count(self, obj):
        return obj.wishlistitem_set.count()
    item_count.short_description = "Items"


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("wishlist", "product", "added_at")
    list_filter = ("added_at",)
    search_fields = ("wishlist__user__username", "product__name")
    autocomplete_fields = ("wishlist", "product")


@admin.register(RecentlyViewed)
class RecentlyViewedAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "viewed_at")
    list_filter = ("viewed_at",)
    search_fields = ("user__username", "product__name")
    autocomplete_fields = ("user", "product")


@admin.register(StoreFollow)
class StoreFollowAdmin(admin.ModelAdmin):
    list_display = ("user", "store", "followed_at")
    list_filter = ("followed_at", "store")
    search_fields = ("user__username", "store__name")
    autocomplete_fields = ("user", "store")


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "action", "weight", "timestamp")
    list_filter = ("action", "timestamp")
    search_fields = ("user__username", "product__name")
    autocomplete_fields = ("user", "product")


@admin.register(RecentSearch)
class RecentSearchAdmin(admin.ModelAdmin):
    list_display = ("user", "query_text", "normalized_query", "searched_at")
    list_filter = ("searched_at",)
    search_fields = ("user__username", "query_text", "normalized_query")
    autocomplete_fields = ("user",)


@admin.register(SearchTrend)
class SearchTrendAdmin(admin.ModelAdmin):
    list_display = ("display_query", "normalized_query", "hit_count", "last_searched_at")
    list_filter = ("last_searched_at",)
    search_fields = ("display_query", "normalized_query")
    ordering = ("-hit_count",)