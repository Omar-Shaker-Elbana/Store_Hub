from django.contrib import admin

from .models import Warehouse, WarehouseClaim, WarehouseTask


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "is_default", "is_active")

@admin.register(WarehouseTask)
class WarehouseTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "status", "warehouse", "created_at", "completed_at")
    list_filter = ("status",)
    search_fields = ("order__id",)


@admin.register(WarehouseClaim)
class WarehouseClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "worker", "status", "claimed_at", "deadline")
    list_filter = ("status",)
    search_fields = ("worker__email", "worker__username", "task__order__id")