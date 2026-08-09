from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect

from .models import Membership, Store

User = settings.AUTH_USER_MODEL


def show_store_members(Store):
    members = Membership.objects.filter(store=Store)
    return members


def show_stores(request):
    memberships = Membership.objects.filter(user=request.user)
    stores = []
    for i in memberships:
        stores.append(i.store)
    return stores
