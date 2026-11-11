from django.contrib import admin

from .models import OrderClaim


@admin.register(OrderClaim)
class OrderClaimAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "store_order",
        "claimant",
        "status",
        "claimed_at",
        "deadline",
        "items_processed",
        "wage_earned",
    )
    list_filter = ("status",)
    search_fields = ("claimant__email", "store_order__id")
