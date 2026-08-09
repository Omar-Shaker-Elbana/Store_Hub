from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from .models import Profile
from .forms import UpdateProfileForm, UpdateUserForm, UpdateSettingsForm

@login_required
def profile_view(request):
    
    profile = Profile.objects.filter(user=request.user).first()
    
    if request.method == "POST":
        if 'logout_btn' in request.POST:
            logout(request)
            messages.success(request, "Logout successful.")
            return redirect("/")
        else:
            update_settings = UpdateSettingsForm(request.POST, instance=request.user.usersettings)
            update_profile = UpdateProfileForm(request.POST, request.FILES, instance=profile)
            update_user = UpdateUserForm(request.POST, instance=request.user)
            
            if update_settings.is_valid() and update_profile.is_valid() and update_user.is_valid():
                update_settings.save()
                update_profile.save()
                update_user.save()
                messages.success(request, "Profile updated successfully.")
                return redirect("profile")
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
    
    return render(request, "users/new_profile.html", context)

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
