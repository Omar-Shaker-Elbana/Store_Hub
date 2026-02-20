from django.contrib import admin
from .models import Store, Membership

# Register your models here.

admin.site.register(Membership)
admin.site.register(Store)