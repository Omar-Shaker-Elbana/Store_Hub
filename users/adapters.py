import urllib.request

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.shortcuts import redirect


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            return  # already linked to a User, nothing to do — let allauth log them in normally

        email = sociallogin.user.email
        if email and User.objects.filter(email=email).exists():
            messages.error(
                request,
                "An account already exists with this email. Please log in with your email and password instead.",
            )
            raise ImmediateHttpResponse(redirect("account_login"))

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra_data = sociallogin.account.extra_data
        user.first_name = data.get("first_name") or extra_data.get("given_name", "")
        user.last_name = data.get("last_name") or extra_data.get("family_name", "")
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        extra_data = sociallogin.account.extra_data
        picture_url = extra_data.get("picture")
        if picture_url:
            profile = user.profile
            if not profile.picture:
                try:
                    img_data = urllib.request.urlopen(picture_url, timeout=5).read()
                    profile.picture.save(
                        f"{user.pk}_google.jpg", ContentFile(img_data), save=False
                    )
                    profile.save()
                except Exception:
                    pass
        return user
