import urllib.request
from django.core.files.base import ContentFile
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra_data = sociallogin.account.extra_data
        user.first_name = data.get('first_name') or extra_data.get('given_name', '')
        user.last_name = data.get('last_name') or extra_data.get('family_name', '')
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)  # user now has a pk; Profile exists via signal
        extra_data = sociallogin.account.extra_data

        picture_url = extra_data.get('picture')
        if picture_url:
            profile = user.profile
            if not profile.picture:
                try:
                    img_data = urllib.request.urlopen(picture_url, timeout=5).read()
                    profile.picture.save(f"{user.pk}_google.jpg", ContentFile(img_data), save=False)
                    profile.save()
                except Exception:
                    pass  # don't block signup if the avatar fetch fails

        return user