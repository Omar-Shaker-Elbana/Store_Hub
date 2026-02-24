from django.contrib import messages
from django.shortcuts import render, redirect
from .models import Store, Membership, Niche
from .forms import StoreForm, MembershipInvitationForm, SuggestNicheForm, MembershipForm
from django.contrib.auth.decorators import login_required

# Create your views here.

def show_store(request, store_id):
    store = Store.objects.get(id=store_id)
    return render(request, 'merchant_interface/stores.html', {'store': store})

@login_required
def create_store(request):
    if request.method == 'POST':
        create_store_form = StoreForm(request.POST, request.FILES)
        suggest_niche_form = SuggestNicheForm(request.POST)

        if "suggest_niche_btn" in request.POST:
            if suggest_niche_form.is_valid():
                suggest_niche_form.save()
                messages.success(request, 'Niche suggested successfully!')
                return redirect('create_store')
            else:
                messages.error(request, 'Error suggesting niche. Please try again.')

        if "create_store_btn" in request.POST and create_store_form.is_valid():
            if create_store_form.is_valid():
                store = create_store_form.save()
                Membership.objects.create(user=request.user, store=store, role='Owner')
                messages.success(request, 'Store created successfully!')
                return redirect(f'/add_members/{store.id}')
            else:
                messages.error(request, 'Error creating store. Please try again.')

    else:
        create_store_form = StoreForm()
        suggest_niche_form = SuggestNicheForm()
    
    context = {
        'create_store_form': create_store_form,
        'suggest_niche_form': suggest_niche_form
    }

    return render(request, 'merchant_interface/create_store.html', context)

@login_required
def add_members(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, 'Store not found.')
        return redirect('create_store')

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership or user_membership.role != 'Owner':
        messages.error(request, 'You do not have permission to add members to this store.')
        return redirect('create_store')
    
    owners = Membership.objects.filter(store=store, role='Owner')
    managers = Membership.objects.filter(store=store, role='Manager')
    helpers = Membership.objects.filter(store=store, role='Helper')

    if request.method == 'POST':
        invitation_form = MembershipInvitationForm(request.POST)

        if "send_invitation_btn" in request.POST:
            if invitation_form.is_valid():
                invitation = invitation_form.save(commit=False)
                invitation.inviter = request.user
                invitation.store = store
                invitation.save()
                messages.success(request, 'Invitation sent successfully!')
                return redirect(f'/add_members/{store.id}')
            else:
                messages.error(request, 'Error sending invitation. Please try again.')

        else:
            membership_id = request.POST.get('membership_id')
            membership = Membership.objects.filter(id=membership_id, store=store).first()
            if membership:
                membership_form = MembershipForm(request.POST, instance=membership)
                if membership_form.is_valid():
                    membership_form.save()
                    messages.success(request, 'Membership updated successfully!')
                    return redirect(f'/add_members/{store.id}')
                else:
                    messages.error(request, 'Error updating membership. Please try again.')
            else:
                messages.error(request, 'Membership not found.')

    else:
        invitation_form = MembershipInvitationForm()

    context = {
        'store': store,
        'invitation_form': invitation_form,
        'owners': owners,
        'managers': managers,
        'helpers': helpers
    }

    return render(request, 'merchant_interface/add_members.html', context)

@login_required
def edit_store(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, 'Store not found.')
        return redirect('create_store')

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership or user_membership.role != 'Owner':
        messages.error(request, 'You do not have permission to edit this store.')
        return redirect('create_store')

    if request.method == 'POST':
        form = StoreForm(request.POST, request.FILES, instance=store)
        if form.is_valid():
            form.save()
            messages.success(request, 'Store updated successfully!')
            return redirect(f'/add_members/{store.id}')
        else:
            messages.error(request, 'Error updating store. Please try again.')
    else:
        form = StoreForm(instance=store)

    context = {
        'form': form,
        'store': store
    }

    return render(request, 'merchant_interface/edit_store.html', context)

@login_required
def all_my_stores(request):
    memberships = Membership.objects.filter(user=request.user)
    if not memberships:
        messages.error(request, 'You are not a member of any stores.')
        return redirect('create_store')

    stores = [membership.store for membership in memberships]

    context = {
        'stores': stores
    }

    return render(request, 'merchant_interface/all_my_stores.html', context)

@login_required
def my_store(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, 'Store not found.')
        return redirect('create_store')

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership:
        messages.error(request, 'You do not have permission to view this store.')
        return redirect('create_store')
    
     # Placeholder for analytics data retrieval and processing

    context = {
        'store': store,
        'membership': user_membership
    }

    return render(request, 'merchant_interface/my_store.html', context)

def my_analytics(request, store_id):
    # Placeholder for analytics data retrieval and processing
    return render(request, 'merchant_interface/my_analytics.html',)

