from django.contrib import messages
from django.shortcuts import render, redirect
from Online_Store import settings
from orders.models import Order
from .models import MembershipInvitation, Store, Membership, Niche
from .forms import StoreForm, MembershipInvitationForm, SuggestNicheForm, MembershipForm
from products.models import Product
from django.contrib.auth.decorators import login_required
from notifications.models import Notification
from django.contrib.auth import get_user_model

# Create your views here.
User = get_user_model()

# User = settings.AUTH_USER_MODEL

def show_store(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, 'Store not found.')
        return redirect('shopper_interface/home')
    
    store_products = Product.objects.filter(store=store)
    context = {
        'store': store,
        'store_products': store_products
    }

    return render(request, 'merchant_interface/show_store.html', context)

@login_required
def create_store(request):
    if request.method == 'POST':
        create_store_form = StoreForm(request.POST, request.FILES)
        suggest_niche_form = SuggestNicheForm(request.POST)

        if "suggest_niche_btn" in request.POST:
            if suggest_niche_form.is_valid():
                suggested_niche = suggest_niche_form.save(commit=False)
                suggested_niche.suggested_by = request.user
                suggested_niche.save()
                messages.success(request, 'Niche suggested successfully!')
                return redirect('create_store')
            else:
                messages.error(request, 'Error suggesting niche. Please try again.')

        if "create_store_btn" in request.POST:
            if create_store_form.is_valid():
                store = create_store_form.save()
                Membership.objects.create(user=request.user, store=store, role='Owner')
                messages.success(request, 'Store created successfully!')
                return redirect('add_members', store_id=store.id)
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
                noti = Notification.objects.create(
    recipient = User.objects.filter(email=invitation.invitee_email).first(),
    sender=request.user,
    message=f"You have been invited to join {store.name} as a {invitation.role}."
)
                noti.save()
                messages.success(request, 'Invitation sent successfully!')
                return redirect('add_members', store_id=store.id)
            else:
                messages.error(request, 'Error sending invitation. Please try again.')

        else:
            membership_id = request.POST.get('membership_id')
            membership = Membership.objects.filter(id=membership_id, store=store).first()
            if membership:
                membership_data = request.POST.copy()
                membership_data.setdefault('wage_type', membership.wage_type)
                membership_form = MembershipForm(membership_data, instance=membership)
                if membership_form.is_valid():
                    membership_form.save()
                    messages.success(request, 'Membership updated successfully!')
                    return redirect('add_members', store_id=store.id)
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
            return redirect('add_members', store_id=store.id)
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

@login_required
def my_store_members(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, 'Store not found.')
        return redirect('create_store')

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership:
        messages.error(request, 'You do not have permission to view this store.')
        return redirect('create_store')

    owners = Membership.objects.filter(store=store, role='Owner')
    managers = Membership.objects.filter(store=store, role='Manager')
    helpers = Membership.objects.filter(store=store, role='Helper')

    context = {
        'store': store,
        'membership': user_membership,
        'owners': owners,
        'managers': managers,
        'helpers': helpers
    }

    return render(request, 'merchant_interface/my_store_members.html', context)

@login_required
def edit_membership(request, membership_id):
    membership = Membership.objects.filter(id=membership_id).first()
    if not membership:
        messages.error(request, 'Membership not found.')
        return redirect('all_my_stores')

    user_membership = Membership.objects.filter(user=request.user, store=membership.store).first()
    if not user_membership or user_membership.role != 'Owner':
        messages.error(request, 'You do not have permission to edit this membership.')
        return redirect('all_my_stores')

    if request.method == 'POST':
        form = MembershipForm(request.POST, instance=membership)
        if form.is_valid():
            form.save()
            messages.success(request, 'Membership updated successfully!')
            return redirect('my_store_members', store_id=membership.store.id)
        else:
            messages.error(request, 'Error updating membership. Please try again.')
    else:
        form = MembershipForm(instance=membership)

    context = {
        'form': form,
        'membership': membership
    }

    return render(request, 'merchant_interface/edit_membership.html', context)

@login_required
def my_analytics(request, store_id):
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

    return render(request, 'merchant_interface/my_analytics.html', context )

def my_job_invitations(request):
    recieved_invitations = MembershipInvitation.objects.filter(invitee_email=request.user.email, status='Pending')
    sent_invitations = MembershipInvitation.objects.filter(inviter_email=request.user.email, status='Pending')
    # invitations = recieved_invitations | sent_invitations
    context = {
        'received_invitations': recieved_invitations,
        'sent_invitations': sent_invitations
    }

    if request.method == 'POST':
        invitation_id = request.POST.get('invitation_id')
        action = request.POST.get('action')

        invitation = MembershipInvitation.objects.filter(id=invitation_id).first()
        if not invitation:
            messages.error(request, 'Invitation not found.')
            return redirect('my_job_invitations')

        if action == 'accept':
            invitation.status = 'Accepted'
            Membership.objects.create(user=request.user, store=invitation.store, role=invitation.role, wage_type=invitation.wage_type, wage=invitation.wage)
            messages.success(request, 'Invitation accepted successfully!')
        elif action == 'decline':
            invitation.status = 'Declined'
            messages.success(request, 'Invitation declined successfully!')
        else:
            messages.error(request, 'Invalid action.')
            return redirect('my_job_invitations')

        invitation.save()
        return redirect('my_job_invitations')

    return render(request, 'merchant_interface/my_job_invitations.html', context)

@login_required
def view_orders(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, 'Store not found.')
        return redirect('create_store')

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership:
        messages.error(request, 'You do not have permission to view this store.')
        return redirect('create_store')

    if request.method == 'POST':
        order_id = request.POST.get('order_id')
        action = request.POST.get('action')

        order = store.orders.filter(id=order_id).first()
        if not order:
            messages.error(request, 'Order not found.')
            return redirect('view_orders', store_id=store.id)

        if action == 'mark_as_shipped':
            order.status = 'Shipped'
            noti = Notification.objects.create(
                recipient=order.user,
                sender=request.user,
                message=f"Your order #{order.id} from {store.name} has been marked as shipped."
            )
            messages.success(request, 'Order marked as shipped successfully!')
        # elif action == 'mark_as_delivered':
        #     order.status = 'Delivered'
        #     noti = Notification.objects.create(
        #         recipient=order.user,
        #         sender=request.user,
        #         message=f"Your order #{order.id} from {store.name} has been marked as delivered."
        #     )
        #     messages.success(request, 'Order marked as delivered successfully!')
        else:
            messages.error(request, 'Invalid action.')
            return redirect('view_orders', store_id=store.id)

        order.save()
        return redirect('view_orders', store_id=store.id)

    context = {
        'store': store,
        'membership': user_membership
    }

    return render(request, 'merchant_interface/view_orders.html', context)

@login_required
def sign_as_delivery(request):
    
    if request.method == 'POST':
        profile = request.user.profile
        profile.is_delivery_person = True
        profile.save()
        messages.success(request, 'You have successfully signed up as a delivery person.')
        return redirect('home')  # Redirect to a relevant page after signing up

    return render(request, 'merchant_interface/sign_as_delievery.html')

def sign_as_merchant(request):
    if request.method == 'POST':
        profile = request.user.profile
        profile.is_merchant = True
        profile.save()
        messages.success(request, 'You have successfully signed up as a merchant.')
        return redirect('home')  # Redirect to a relevant page after signing up

    return render(request, 'merchant_interface/sign_as_merchant.html')

def view_available_parcels(request):
    # Fetch all orders that are not yet assigned to a delivery person
    available_orders = Order.objects.filter(delivery_person__isnull=True, status='Processing')

    if request.method == 'POST':
        order_id = request.POST.get('order_id')
        delivery_person_id = request.POST.get('delivery_person_id')

        order = Order.objects.filter(id=order_id).first()
        if not order:
            messages.error(request, 'Order not found.')
            return redirect('view_available_parcels')

        delivery_person = User.objects.filter(id=delivery_person_id).first()
        if not delivery_person:
            messages.error(request, 'Delivery person not found.')
            return redirect('view_available_parcels')

        order.delivery_person = delivery_person
        order.status = 'Assigned'
        order.save()
        messages.success(request, 'Order assigned successfully!')
        return redirect('view_available_parcels')

    context = {
        'available_orders': available_orders
    }
    return render(request, 'merchant_interface/view_packages.html', context)