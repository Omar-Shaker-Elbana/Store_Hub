from django.contrib import admin, messages

from . import services
from .models import (
    CourierWallet,
    CourierWalletTransaction,
    DeliveryClaim,
    DeliveryMission,
    StorePickupLocation,
)


@admin.register(StorePickupLocation)
class StorePickupLocationAdmin(admin.ModelAdmin):
    list_display = ("store", "address", "phone")
    search_fields = ("store__name", "address")


class DeliveryClaimInline(admin.TabularInline):
    model = DeliveryClaim
    extra = 0
    can_delete = False
    fields = ("courier", "status", "claimed_at", "deadline", "picked_up_at", "finished_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DeliveryMission)
class DeliveryMissionAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "status", "order", "store_order", "fee", "cod_amount", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("order__id",)
    inlines = [DeliveryClaimInline]


@admin.action(description="Return selected claims to the work pool")
def return_to_pool(modeladmin, request, queryset):
    released = 0
    for claim_id in queryset.values_list("id", flat=True):
        claim, _ = services.force_release_claim(claim_id=claim_id, staff_user=request.user)
        released += 1 if claim else 0
    modeladmin.message_user(request, f"{released} claim(s) returned to the pool.", messages.SUCCESS)


@admin.register(DeliveryClaim)
class DeliveryClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "mission", "courier", "status", "claimed_at", "deadline", "fee_earned")
    list_filter = ("status", "mission__kind")
    search_fields = ("courier__email", "courier__username", "mission__order__id")
    actions = [return_to_pool]


@admin.register(CourierWallet)
class CourierWalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance")
    search_fields = ("user__email", "user__username")


@admin.register(CourierWalletTransaction)
class CourierWalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "amount", "created_at")