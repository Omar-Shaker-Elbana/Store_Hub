from django.contrib import admin

from .models import (Membership, MembershipChangeRequest, MembershipInvitation,
                     Niche, Promotion, Store, SuggestedNiche)


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    search_fields = ("name",)


admin.site.register(Membership)
admin.site.register(Niche)
admin.site.register(MembershipChangeRequest)
admin.site.register(SuggestedNiche)
admin.site.register(MembershipInvitation)
admin.site.register(Promotion)