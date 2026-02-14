from django.contrib.auth.forms import UserCreationForm
from django import forms
from django.contrib.auth.models import User
from .models import Profile
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
        fields = ['birthday', 'picture', 'gender', 'country']

class UpdateUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email']


