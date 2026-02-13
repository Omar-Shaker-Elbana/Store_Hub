from django.shortcuts import render, redirect
from django.contrib.auth import login

from users.models import Profile
from .forms import UserForm, ProfileForm

def register_view(request):
    if request.method == "POST":
        print("Received POST data:", request.POST)
        user_form = UserForm(request.POST)
        profile_form = ProfileForm(request.POST, request.FILES)
        
        if user_form.is_valid() and profile_form.is_valid():
            # Save user first
            print("User form is valid. Saving user...")
            new_user = user_form.save(commit=False)
            new_user.set_password(user_form.cleaned_data['password'])  # hash password
            new_user.save()
            
            # Save profile
            new_profile = Profile.objects.get(user=new_user)  # Get the profile created by the signal
            new_profile.birthday = profile_form.cleaned_data['birthday']
            new_profile.picture = profile_form.cleaned_data['picture']
            new_profile.gender = profile_form.cleaned_data['gender']
            new_profile.country = profile_form.cleaned_data['country']
            new_profile.user = new_user
            new_profile.save()
            
            print("User and profile created successfully!")
            # Auto-login
            login(request, new_user)
            return redirect("/")
    else:
        # print(user_form.errors)
        # print(profile_form.errors)
        user_form = UserForm()
        profile_form = ProfileForm()
    
    context = {
        'user_form': user_form,
        'profile_form': profile_form
    }
    return render(request, "users/register.html", context)