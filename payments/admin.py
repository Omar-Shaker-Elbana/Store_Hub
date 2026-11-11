from django.contrib import admin

from .models import Account, LedgerEntry, OrderSettlement, PayrollRun


class ReadOnlyAdmin(admin.ModelAdmin):
    """Money tables are append-only: browse them, never edit them by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Account)
class AccountAdmin(ReadOnlyAdmin):
    list_display = ("id", "kind", "user", "store", "balance")
    list_filter = ("kind",)
    search_fields = ("user__email", "user__username", "store__name")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(ReadOnlyAdmin):
    list_display = ("id", "account", "kind", "amount", "balance_after", "order", "created_at")
    list_filter = ("kind",)
    search_fields = ("account__user__email", "account__user__username", "ref", "description")


@admin.register(OrderSettlement)
class OrderSettlementAdmin(ReadOnlyAdmin):
    list_display = ("order", "gross_sales", "platform_fee", "shipping_fee", "settled_at")


@admin.register(PayrollRun)
class PayrollRunAdmin(ReadOnlyAdmin):
    list_display = ("store", "period", "salaries_due", "salaries_paid", "profit_distributed", "run_at")
    list_filter = ("period",)
