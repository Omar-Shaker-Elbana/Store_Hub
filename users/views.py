from django.shortcuts import render, redirect
from django.contrib.auth import login, update_session_auth_hash
from users.models import Profile
from .forms import UserForm, ProfileForm, UpdateProfileForm, UpdateUserForm, UpdateSettingsForm
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages

def register_view(request):
    if request.method == "POST":
        
        user_form = UserForm(request.POST)
        profile_form = ProfileForm(request.POST, request.FILES)
        login_form = AuthenticationForm(request, data=request.POST)  # Initialize login form for validation
        
        if 'register_btn' in request.POST:
           
            if user_form.is_valid() and profile_form.is_valid():
                # Save user first
                new_user = user_form.save(commit=False)
                new_user.username = user_form.cleaned_data['email']  # Set username to email
                # new_user.set_password(user_form.cleaned_data['password'])  # hash password
                new_user.save()
                
                # Save profile
                new_profile = Profile.objects.get(user=new_user)  # Get the profile created by the signal
                new_profile.birthday = profile_form.cleaned_data['birthday']
                new_profile.picture = profile_form.cleaned_data['picture']
                new_profile.gender = profile_form.cleaned_data['gender']
                new_profile.country = profile_form.cleaned_data['country']
                new_profile.user = new_user
                new_profile.save()
                
                login(request, new_user)
                messages.success(request, "Registration successful. You are now logged in.")
                return redirect("/")
            
            else:
                messages.error(request, "Registration failed. Please check the form.")
                # return redirect("/users/register/")
            
        elif 'login_btn' in request.POST:
            login_form = AuthenticationForm(request, data=request.POST)
            if login_form.is_valid():
                user = login_form.get_user()
                login(request, user)
                messages.success(request, "Login successful.")
                return redirect("/")
            else:
                messages.error(request, "Login failed. Please check the form.")
                # return redirect("/users/register/")

    else:
        user_form = UserForm()
        profile_form = ProfileForm()
        login_form = AuthenticationForm()
    
    context = {
        'user_form': user_form,
        'profile_form': profile_form,
        'login_form': login_form
    }

    return render(request, "users/register.html", context)

@login_required
def profile_view(request):
    
    profile = Profile.objects.get(user=request.user)
    
    if request.method == "POST":
        update_settings = UpdateSettingsForm(request.POST, instance=request.user.usersettings)
        update_profile = UpdateProfileForm(request.POST, request.FILES, instance=profile)
        update_user = UpdateUserForm(request.POST, instance=request.user)
        
        if update_settings.is_valid() and update_profile.is_valid() and update_user.is_valid():
            update_settings.save()
            update_profile.save()
            update_user.save()
            return redirect("/")
        else:
            messages.error(request, "Update failed. Please check the form.")
            # return redirect("profile")
    
    else:
        update_settings = UpdateSettingsForm(instance=request.user.usersettings)
        update_profile = UpdateProfileForm(instance=profile)
        update_user = UpdateUserForm(instance=request.user)
    
    context = {
        'profile': profile,
        'update_settings': update_settings,
        'update_profile': update_profile,
        'update_user': update_user
    }
    
    return render(request, "users/profile.html", context)

@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # Keeps user logged in
            messages.success(request, "Password changed successfully.")
            return redirect('profile')
        else:
            messages.error(request, "Password change failed. Please check the form.")
            # return redirect('/users/change-password/')
    else:
        form = PasswordChangeForm(request.user)

    return render(request, 'users/change_password.html', {'form': form})
