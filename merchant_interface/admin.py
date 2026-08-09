from django.contrib import admin

from .models import Membership, Niche, Store

# Register your models here.

admin.site.register(Membership)
admin.site.register(Store)
admin.site.register(Niche)
