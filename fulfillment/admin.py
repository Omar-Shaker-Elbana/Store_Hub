from django.contrib import admin

from .models import OrderClaim, WalletTransaction


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


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "membership", "amount", "created_at")
    search_fields = ("membership__user__email",)