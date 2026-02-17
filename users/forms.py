from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from django import forms
from django.contrib.auth.models import User
from .models import Profile, UserSettings
from django.contrib.auth import update_session_auth_hash

# from django.contrib.auth.forms import AuthenticationForm

# class UserRegisterForm(UserCreationForm):
#     class Meta:
#         model = User
#         fields = ['username', 'email']


class UserForm(UserCreationForm):
    # password = forms.CharField(widget=forms.PasswordInput)
    
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['birthday', 'picture', 'gender', 'country']

# class EmailAuthenticationForm(AuthenticationForm):
    # username = forms.EmailField(label="Email")

class UpdateProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['birthday', 'picture', 'gender', 'country', 'address1', 'address2', 'address3']

class UpdateUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name']


class UpdateSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = ['theme', 'language']

# class UpdatePasswordForm(forms.Form):
#     current_password = forms.CharField(widget=forms.PasswordInput, label="Current Password")
#     new_password = forms.CharField(widget=forms.PasswordInput, label="New Password")
#     confirm_password = forms.CharField(widget=forms.PasswordInput, label="Confirm New Password")