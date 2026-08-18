from allauth.account.forms import SignupForm
from django import forms
from django.contrib.auth.models import User

from .models import Profile, UserSettings


class CustomSignupForm(SignupForm):
    first_name = forms.CharField(max_length=30, required=False)
    last_name = forms.CharField(max_length=30, required=False)
    birthday = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    picture = forms.ImageField(required=False)
    gender = forms.ChoiceField(
        choices=[("", "— Select —")] + list(Profile.GENDER_CHOICES),
        required=False,
    )
    country = forms.CharField(max_length=100, required=False)

    def save(self, request):
        user = super().save(request)  # allauth handles user + EmailAddress creation

        profile = user.profile  # created by the post_save signal
        profile.birthday = self.cleaned_data.get("birthday")
        profile.picture = self.cleaned_data.get("picture")
        profile.gender = self.cleaned_data.get("gender")
        profile.country = self.cleaned_data.get("country")
        profile.save()

        return user


class UpdateProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = [
            "birthday",
            "picture",
            "gender",
            "country",
            "address1",
            "address2",
            "address3",
        ]


class UpdateUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name"]


class UpdateSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = [
            "theme",
        ]
