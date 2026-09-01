from django.contrib import admin

from .models import (Membership, MembershipChangeRequest, MembershipInvitation,
                     Niche, Promotion, Store, SuggestedNiche)

# Register your models here.

admin.site.register(Membership)
admin.site.register(Store)
admin.site.register(Niche)
admin.site.register(MembershipChangeRequest)
admin.site.register(SuggestedNiche)
admin.site.register(MembershipInvitation)
admin.site.register(Promotion)
