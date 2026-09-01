from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from notifications.models import Notification
from products.models import Product

from .forms import (MembershipForm, MembershipInvitationForm, StoreForm,
                    SuggestNicheForm)
from .models import (Membership, MembershipChangeRequest, MembershipInvitation,
                     Promotion, Store, SuggestedNiche)

# Create your views here.

User = get_user_model()


def _create_membership_change_request(request, user_membership, membership, form):
    """
    Shared logic for proposing a membership change, used by edit_membership.
    Returns True if a change request was created (success message already
    added), False otherwise (error message already added).
    """
    new_role = form.cleaned_data["role"]
    if new_role == "owner" and user_membership.role != "owner":
        messages.error(request, "Only an owner can promote someone to owner.")
        return False

    if (
        membership.role == "owner"
        and new_role != "owner"
        and Membership.objects.filter(store=membership.store, role="owner").count() <= 1
    ):
        messages.error(request, "This store must have at least one owner.")
        return False

    if MembershipChangeRequest.objects.filter(
        membership=membership, status="pending"
    ).exists():
        messages.error(
            request,
            "There is already a pending change request for this membership.",
        )
        return False

    MembershipChangeRequest.objects.create(
        membership=membership,
        requested_by=request.user,
        new_role=new_role,
        new_wage_type=form.cleaned_data["wage_type"],
        new_wage=form.cleaned_data["wage"],
    )
    Notification.objects.create(
        recipient=membership.user,
        sender=request.user,
        message=(
            f"{request.user} proposed changes to your membership at "
            f"{membership.store.name}. Review and respond."
        ),
    )
    messages.success(request, "Change request sent for approval!")
    return True


def show_store(request, store_id):
    store = Store.objects.filter(id=store_id).select_related("niche").first()
    if not store:
        messages.error(request, "Store not found.")
        return redirect("create_store")

    if not store.enabled:
        is_member = (
            request.user.is_authenticated
            and Membership.objects.filter(user=request.user, store=store).exists()
        )
        if not is_member:
            messages.error(request, "This store is currently closed.")
            return redirect("home")

    product_list = Product.objects.filter(store=store)
    paginator = Paginator(product_list, 12)
    page_number = request.GET.get("page", 1)
    store_products = paginator.get_page(page_number)

    context = {"store": store, "store_products": store_products}

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, "merchant_interface/_product_cards.html", context)

    return render(request, "merchant_interface/show_store.html", context)


@login_required
def create_store(request):
    create_store_form = StoreForm(prefix="store", require_name=True)
    suggest_niche_form = SuggestNicheForm(prefix="niche")

    if request.method == "POST":
        if "suggest_niche_btn" in request.POST:
            suggest_niche_form = SuggestNicheForm(request.POST, prefix="niche")
            if suggest_niche_form.is_valid():
                suggested_niche = suggest_niche_form.save(commit=False)
                suggested_niche.suggested_by = request.user
                suggested_niche.save()
                messages.success(request, "Niche suggested successfully!")
                return redirect("create_store")
            else:
                messages.error(request, "Error suggesting niche. Please try again.")

        elif "create_store_btn" in request.POST:
            create_store_form = StoreForm(
                request.POST, request.FILES, prefix="store", require_name=True
            )
            if create_store_form.is_valid():
                with transaction.atomic():
                    store = create_store_form.save()
                    Membership.objects.create(
                        user=request.user,
                        store=store,
                        role="owner",
                        join_date=timezone.now().date(),
                    )
                messages.success(request, "Store created successfully!")
                return redirect("manage_store_invitations", store_id=store.id)
            else:
                messages.error(request, "Error creating store. Please try again.")

    context = {
        "create_store_form": create_store_form,
        "suggest_niche_form": suggest_niche_form,
    }
    return render(request, "merchant_interface/create_store.html", context)


@login_required
def manage_store_invitations(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, "Store not found.")
        return redirect("create_store")

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership:
        messages.error(request, "You do not have permission to view this store.")
        return redirect("create_store")

    can_manage = user_membership.role in ("owner", "manager")

    owners = Membership.objects.filter(store=store, role="owner")
    managers = Membership.objects.filter(store=store, role="manager")
    helpers = Membership.objects.filter(store=store, role="helper")
    pending_invitations = MembershipInvitation.objects.filter(
        store=store, status="pending"
    )

    if request.method == "POST":
        if not can_manage:
            messages.error(
                request, "You do not have permission to manage members of this store."
            )
            return redirect("manage_store_invitations", store_id=store.id)

        invitation_form = MembershipInvitationForm(request.POST)

        if "send_invitation_btn" in request.POST:
            if invitation_form.is_valid():
                if (
                    request.user.email
                    and invitation_form.cleaned_data["invitee_email"].lower()
                    == request.user.email.lower()
                ):
                    messages.error(request, "You cannot invite yourself.")
                    return redirect("manage_store_invitations", store_id=store.id)

                if (
                    invitation_form.cleaned_data["role"] == "owner"
                    and user_membership.role != "owner"
                ):
                    messages.error(
                        request, "Only an owner can invite someone as owner."
                    )
                    return redirect("manage_store_invitations", store_id=store.id)

                invitee_user = User.objects.filter(
                    email=invitation_form.cleaned_data["invitee_email"]
                ).first()

                if (
                    invitee_user
                    and Membership.objects.filter(
                        user=invitee_user, store=store
                    ).exists()
                ):
                    messages.error(
                        request, "This person is already a member of this store."
                    )
                    return redirect("manage_store_invitations", store_id=store.id)

                if MembershipInvitation.objects.filter(
                    store=store,
                    invitee_email=invitation_form.cleaned_data["invitee_email"],
                    status="pending",
                ).exists():
                    messages.error(
                        request, "An invitation is already pending for this email."
                    )
                    return redirect("manage_store_invitations", store_id=store.id)

                invitation = invitation_form.save(commit=False)
                invitation.inviter = request.user
                invitation.store = store
                invitation.invitee = invitee_user
                invitation.save()
                if invitee_user:
                    Notification.objects.create(
                        recipient=invitee_user,
                        sender=request.user,
                        message=f"You have been invited to join {store.name} as a {invitation.role}.",
                    )
                messages.success(request, "Invitation sent successfully!")
                return redirect("manage_store_invitations", store_id=store.id)
            else:
                messages.error(request, "Error sending invitation. Please try again.")

        elif "cancel_invitation_btn" in request.POST:
            invitation_id = request.POST.get("invitation_id")
            invitation = MembershipInvitation.objects.filter(
                id=invitation_id, store=store, status="pending"
            ).first()
            if invitation:
                invitation.status = "rejected"
                invitation.save()
                messages.success(request, "Invitation canceled successfully!")
            else:
                messages.error(request, "Invitation not found.")
            return redirect("manage_store_invitations", store_id=store.id)

        else:
            messages.error(request, "Invalid request.")
            return redirect("manage_store_invitations", store_id=store.id)

    else:
        invitation_form = MembershipInvitationForm()

    context = {
        "store": store,
        "user_membership": user_membership,
        "invitation_form": invitation_form,
        "owners": owners,
        "managers": managers,
        "helpers": helpers,
        "pending_invitations": pending_invitations,
        "can_manage": can_manage,
    }

    return render(request, "merchant_interface/manage_members.html", context)


@login_required
def edit_store(request, store_id):
    store = Store.objects.filter(id=store_id).first()
    if not store:
        messages.error(request, "Store not found.")
        return redirect("create_store")

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership or user_membership.role != "owner":
        messages.error(request, "You do not have permission to edit this store.")
        return redirect("create_store")

    if request.method == "POST":
        store_status_action = request.POST.get("store_status_action")
        if store_status_action == "close":
            store.enabled = False
            store.save(update_fields=["enabled"])
            messages.success(request, f"{store.name or 'Your store'} has been closed.")
            return redirect("edit_store", store_id=store.id)

        if store_status_action == "reopen":
            store.enabled = True
            store.save(update_fields=["enabled"])
            messages.success(
                request, f"{store.name or 'Your store'} has been reopened."
            )
            return redirect("edit_store", store_id=store.id)

        form = StoreForm(request.POST, request.FILES, instance=store)
        if form.is_valid():
            form.save()
            messages.success(request, "Store updated successfully!")
            return redirect("manage_store_invitations", store_id=store.id)
        else:
            messages.error(request, "Error updating store. Please try again.")
    else:
        form = StoreForm(instance=store)

    context = {"form": form, "store": store}

    return render(request, "merchant_interface/edit_store.html", context)


@login_required
def all_my_stores(request):
    memberships = Membership.objects.filter(user=request.user).select_related(
        "store", "store__niche"
    )
    if not memberships:
        messages.error(request, "You are not a member of any stores.")
        return redirect("create_store")

    context = {"memberships": memberships}

    return render(request, "merchant_interface/all_my_stores.html", context)


@login_required
def edit_membership(request, membership_id):
    membership = (
        Membership.objects.filter(id=membership_id)
        .select_related("user", "store")
        .first()
    )
    if not membership:
        messages.error(request, "Membership not found.")
        return redirect("all_my_stores")

    user_membership = Membership.objects.filter(
        user=request.user, store=membership.store
    ).first()

    if not user_membership:
        messages.error(request, "You do not have permission to edit this membership.")
        return redirect("all_my_stores")

    if membership.user_id == request.user.id:
        messages.error(request, "You cannot edit your own membership.")
        return redirect("manage_store_invitations", store_id=membership.store.id)

    if user_membership.role == "owner":
        can_edit = True
    elif user_membership.role == "manager":
        can_edit = membership.role == "helper"
    else:
        can_edit = False

    if not can_edit:
        messages.error(request, "You do not have permission to edit this membership.")
        return redirect("manage_store_invitations", store_id=membership.store.id)

    can_remove = user_membership.role == "owner"

    # Placeholder for member analytics, to be added later

    def _restrict_role_choices(bound_form):
        # Managers can hand out manager/helper roles only — never owner.
        if user_membership.role == "manager":
            bound_form.fields["role"].choices = [
                choice for choice in Membership.ROLE_CHOICES if choice[0] != "owner"
            ]

    if request.method == "POST":
        form = MembershipForm(request.POST)
        _restrict_role_choices(form)
        if form.is_valid():
            _create_membership_change_request(
                request, user_membership, membership, form
            )
            return redirect("manage_store_invitations", store_id=membership.store.id)
        else:
            messages.error(request, "Error submitting changes. Please try again.")
    else:
        form = MembershipForm(instance=membership)
        _restrict_role_choices(form)

    context = {
        "form": form,
        "membership": membership,
        "user_membership": user_membership,
        "can_remove": can_remove,
    }

    return render(request, "merchant_interface/edit_membership.html", context)


@login_required
def remove_membership(request, membership_id):
    membership = (
        Membership.objects.filter(id=membership_id)
        .select_related("user", "store")
        .first()
    )
    if not membership:
        messages.error(request, "Membership not found.")
        return redirect("all_my_stores")

    store = membership.store
    is_self = membership.user_id == request.user.id

    if is_self:
        can_remove = True
    else:
        user_membership = Membership.objects.filter(
            user=request.user, store=store
        ).first()
        can_remove = user_membership is not None and user_membership.role == "owner"

    if not can_remove:
        messages.error(request, "You do not have permission to remove this membership.")
        if is_self:
            return redirect("all_my_stores")
        return redirect("manage_store_invitations", store_id=store.id)

    is_last_owner = (
        membership.role == "owner"
        and Membership.objects.filter(store=store, role="owner").count() <= 1
    )
    if is_last_owner:
        messages.error(
            request,
            "This store must have at least one owner. "
            "Promote another member to owner first.",
        )
        if is_self:
            return redirect("all_my_stores")
        return redirect("manage_store_invitations", store_id=store.id)

    if request.method != "POST":
        messages.error(request, "Invalid request.")
        return redirect("all_my_stores")

    member_name = str(membership.user)
    store_name = store.name
    removed_user = membership.user
    membership.delete()

    if is_self:
        messages.success(request, f"You have left {store_name}.")
        return redirect("all_my_stores")

    Notification.objects.create(
        recipient=removed_user,
        sender=request.user,
        message=f"You have been removed from {store_name}.",
    )
    messages.success(request, f"{member_name} has been removed from {store_name}.")
    return redirect("manage_store_invitations", store_id=store.id)


@login_required
def my_job_invitations(request):
    received_invitations = MembershipInvitation.objects.filter(
        Q(invitee=request.user) | Q(invitee_email=request.user.email),
        status="pending",
    ).select_related("store", "inviter")
    sent_invitations = MembershipInvitation.objects.filter(
        inviter=request.user, status="pending"
    ).select_related("store")
    change_requests = MembershipChangeRequest.objects.filter(
        membership__user=request.user, status="pending"
    ).select_related("membership__store", "requested_by")

    context = {
        "received_invitations": received_invitations,
        "sent_invitations": sent_invitations,
        "change_requests": change_requests,
    }

    if request.method == "POST":
        item_type = request.POST.get("item_type")
        action = request.POST.get("action")

        if item_type == "invitation":
            invitation_id = request.POST.get("invitation_id")
            invitation = MembershipInvitation.objects.filter(
                Q(invitee=request.user) | Q(invitee_email=request.user.email),
                id=invitation_id,
                status="pending",
            ).first()
            if not invitation:
                messages.error(request, "Invitation not found.")
                return redirect("my_job_invitations")

            if action == "accept":
                if Membership.objects.filter(
                    user=request.user, store=invitation.store
                ).exists():
                    messages.error(request, "You are already a member of this store.")
                    return redirect("my_job_invitations")
                if not invitation.store.enabled:
                    messages.error(
                        request,
                        f"{invitation.store.name} is currently closed and "
                        "not accepting new members.",
                    )
                    return redirect("my_job_invitations")
                invitation.status = "accepted"
                Membership.objects.create(
                    user=request.user,
                    store=invitation.store,
                    role=invitation.role,
                    wage_type=invitation.wage_type,
                    wage=invitation.wage,
                    join_date=timezone.now().date(),
                )
                Notification.objects.create(
                    recipient=invitation.inviter,
                    sender=request.user,
                    message=(
                        f"{request.user} accepted your invitation to join "
                        f"{invitation.store.name}."
                    ),
                )
                messages.success(request, "Invitation accepted successfully!")
            elif action == "decline":
                invitation.status = "rejected"
                Notification.objects.create(
                    recipient=invitation.inviter,
                    sender=request.user,
                    message=(
                        f"{request.user} declined your invitation to join "
                        f"{invitation.store.name}."
                    ),
                )
                messages.success(request, "Invitation declined successfully!")
            else:
                messages.error(request, "Invalid action.")
                return redirect("my_job_invitations")

            invitation.save()

        elif item_type == "change_request":
            change_request_id = request.POST.get("change_request_id")
            change_request = (
                MembershipChangeRequest.objects.filter(
                    id=change_request_id,
                    membership__user=request.user,
                    status="pending",
                )
                .select_related("membership")
                .first()
            )
            if not change_request:
                messages.error(request, "Change request not found.")
                return redirect("my_job_invitations")

            if action == "accept":
                membership = change_request.membership
                old_role = membership.role
                old_wage_type = membership.wage_type
                old_wage = membership.wage

                membership.role = change_request.new_role
                membership.wage_type = change_request.new_wage_type
                membership.wage = change_request.new_wage
                membership.save()

                role_changed = old_role != change_request.new_role
                wage_type_changed = old_wage_type != change_request.new_wage_type
                wage_changed = old_wage != change_request.new_wage

                if role_changed or wage_type_changed or wage_changed:
                    role_labels = dict(Membership.ROLE_CHOICES)
                    wage_labels = dict(Membership.WAGE_CHOICES)
                    Promotion.objects.create(
                        store=membership.store,
                        date=timezone.now().date(),
                        old_position=(
                            role_labels.get(old_role, old_role)
                            if role_changed
                            else None
                        ),
                        new_position=(
                            role_labels.get(
                                change_request.new_role, change_request.new_role
                            )
                            if role_changed
                            else None
                        ),
                        old_wage_type=(
                            wage_labels.get(old_wage_type, old_wage_type)
                            if wage_type_changed
                            else None
                        ),
                        new_wage_type=(
                            wage_labels.get(
                                change_request.new_wage_type,
                                change_request.new_wage_type,
                            )
                            if wage_type_changed
                            else None
                        ),
                        old_wage=old_wage if wage_changed else None,
                        new_wage=change_request.new_wage if wage_changed else None,
                        Giver=change_request.requested_by,
                        Receiver=membership.user,
                    )

                change_request.status = "accepted"
                Notification.objects.create(
                    recipient=change_request.requested_by,
                    sender=request.user,
                    message=(
                        f"{request.user} accepted the membership changes you "
                        f"proposed at {membership.store.name}."
                    ),
                )
                messages.success(request, "Membership changes accepted!")
            elif action == "reject":
                change_request.status = "rejected"
                Notification.objects.create(
                    recipient=change_request.requested_by,
                    sender=request.user,
                    message=(
                        f"{request.user} rejected the membership changes you "
                        f"proposed at {change_request.membership.store.name}."
                    ),
                )
                messages.success(request, "Membership changes rejected.")
            else:
                messages.error(request, "Invalid action.")
                return redirect("my_job_invitations")

            change_request.save()

        else:
            messages.error(request, "Invalid request.")

        return redirect("my_job_invitations")

    return render(request, "merchant_interface/my_job_invitations.html", context)


@login_required
def my_promotion_history(request):
    if request.method == "POST":
        promotion_id = request.POST.get("promotion_id")
        promotion = Promotion.objects.filter(id=promotion_id).first()
        if not promotion:
            messages.error(request, "Promotion record not found.")
        elif promotion.Receiver_id != request.user.id:
            messages.error(request, "You do not have permission to change this.")
        else:
            promotion.enabled = not promotion.enabled
            promotion.save()
            messages.success(request, "Visibility updated.")
        return redirect("my_promotion_history")

    memberships = Membership.objects.filter(user=request.user).select_related("store")
    promotions = Promotion.objects.filter(Receiver=request.user).select_related(
        "store", "Giver"
    )

    role_labels = dict(Membership.ROLE_CHOICES)

    earliest_role_by_store = {}
    for promo in promotions.order_by("date"):
        if promo.old_position and promo.store_id not in earliest_role_by_store:
            earliest_role_by_store[promo.store_id] = promo.old_position

    timeline_events = []

    for membership in memberships:
        if membership.join_date:
            starting_role = earliest_role_by_store.get(
                membership.store_id, role_labels.get(membership.role, membership.role)
            )
            timeline_events.append(
                {
                    "type": "joined",
                    "date": membership.join_date,
                    "store": membership.store,
                    "role": starting_role,
                }
            )

    for promo in promotions:
        timeline_events.append(
            {
                "type": "promotion",
                "date": promo.date,
                "promotion": promo,
            }
        )

    timeline_events.sort(key=lambda e: e["date"], reverse=True)

    context = {"timeline_events": timeline_events}
    return render(request, "merchant_interface/my_promotion_history.html", context)


@login_required
def view_user_promotion_history(request, user_id):
    history_user = User.objects.filter(id=user_id).first()
    if not history_user:
        messages.error(request, "User not found.")
        return redirect("create_store")

    memberships = Membership.objects.filter(user=history_user).select_related("store")
    promotions = Promotion.objects.filter(
        Receiver=history_user, enabled=True
    ).select_related("store", "Giver")

    role_labels = dict(Membership.ROLE_CHOICES)

    # For each store, find the earliest recorded "old_position" from that
    # store's promotions, so the join event can show what role they
    # actually started at rather than their current role.
    earliest_role_by_store = {}
    for promo in promotions.order_by("date"):
        if promo.old_position and promo.store_id not in earliest_role_by_store:
            earliest_role_by_store[promo.store_id] = promo.old_position

    timeline_events = []

    for membership in memberships:
        if membership.join_date:
            starting_role = earliest_role_by_store.get(
                membership.store_id, role_labels.get(membership.role, membership.role)
            )
            timeline_events.append(
                {
                    "type": "joined",
                    "date": membership.join_date,
                    "store": membership.store,
                    "role": starting_role,
                }
            )

    for promo in promotions:
        # Recruiters only ever see role changes here — wage/salary
        # details never leave the owner's own timeline view.
        if promo.old_position or promo.new_position:
            timeline_events.append(
                {
                    "type": "promotion",
                    "date": promo.date,
                    "promotion": promo,
                }
            )

    timeline_events.sort(key=lambda e: e["date"], reverse=True)

    context = {"history_user": history_user, "timeline_events": timeline_events}
    return render(request, "merchant_interface/user_promotion_history.html", context)


@login_required
def manage_store_inventory(request, store_id):
    store = Store.objects.filter(id=store_id).select_related("niche").first()
    if not store:
        messages.error(request, "Store not found.")
        return redirect("create_store")

    user_membership = Membership.objects.filter(user=request.user, store=store).first()
    if not user_membership:
        messages.error(request, "You do not have permission to view this store.")
        return redirect("create_store")

    product_list = (
        Product.objects.filter(store=store)
        .select_related("category")
        .prefetch_related("images")
        .order_by("name")
    )
    paginator = Paginator(product_list, 12)
    page_number = request.GET.get("page", 1)
    store_products = paginator.get_page(page_number)

    context = {
        "store": store,
        "user_membership": user_membership,
        "store_products": store_products,
    }
    return render(request, "merchant_interface/manage_store_inventory.html", context)


# @login_required
# def store_analytics(request, store_id):
#     store = Store.objects.filter(id=store_id).first()
#     if not store:
#         messages.error(request, "Store not found.")
#         return redirect("create_store")

#     user_membership = Membership.objects.filter(user=request.user, store=store).first()
#     if not user_membership:
#         messages.error(request, "You do not have permission to view this store.")
#         return redirect("create_store")

#     # Placeholder for analytics data retrieval and processing

#     context = {"store": store, "membership": user_membership}

#     return render(request, "merchant_interface/my_store.html", context)

# @login_required
# def user_analytics(request, store_id):
#     store = Store.objects.filter(id=store_id).first()
#     if not store:
#         messages.error(request, "Store not found.")
#         return redirect("create_store")

#     user_membership = Membership.objects.filter(user=request.user, store=store).first()
#     if not user_membership:
#         messages.error(request, "You do not have permission to view this store.")
#         return redirect("create_store")

#     # Placeholder for analytics data retrieval and processing

#     context = {"store": store, "membership": user_membership}

#     return render(request, "merchant_interface/my_analytics.html", context)
