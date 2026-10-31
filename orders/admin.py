from django.contrib import admin

from .models import Order, OrderItem, StoreOrder


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ["product", "quantity", "price_at_purchase"]
    autocomplete_fields = ["product"]


class StoreOrderInline(admin.TabularInline):
    model = StoreOrder
    extra = 0
    fields = ["store", "status", "delivery_person", "subtotal"]
    autocomplete_fields = ["store", "delivery_person"]
    show_change_link = True


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "status",
        "total_price",
        "payment_type",
        "created_at",
    ]
    list_filter = ["status", "payment_type", "created_at"]
    search_fields = ["id", "user__username", "user__email", "shipping_address"]
    autocomplete_fields = ["user"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at"]
    inlines = [StoreOrderInline]


@admin.register(StoreOrder)
class StoreOrderAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "order",
        "store",
        "status",
        "delivery_person",
        "subtotal",
    ]
    list_filter = ["status", "store"]
    search_fields = [
        "id",
        "order__id",
        "store__name",
        "delivery_person__username",
    ]
    autocomplete_fields = ["order", "store", "delivery_person"]
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ["id", "store_order", "product", "quantity", "price_at_purchase"]
    search_fields = ["store_order__id", "product__name"]
    autocomplete_fields = ["store_order", "product"]