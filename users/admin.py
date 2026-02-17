from django.contrib import admin
from .models import Profile, UserSettings

# Register your models here.

admin.site.register(Profile)
admin.site.register(UserSettings)