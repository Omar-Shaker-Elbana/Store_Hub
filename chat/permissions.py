from functools import wraps

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render

from merchant_interface.models import Membership, MembershipInvitation, Store

from .models import DirectConversation

# Store roles allowed to post announcements. Values mirror
# Membership.ROLE_CHOICES.
ANNOUNCER_ROLES = frozenset({"owner", "manager"})


def wants_json(request):
    return (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )


def forbidden(request, message):
    """One error convention for the whole app: JSON for fetch/XHR callers,
    a plain 403 for ordinary page loads."""
    if wants_json(request):
        return JsonResponse({"error": message}, status=403)
    return HttpResponseForbidden(message)


def is_merchant(user):
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_merchant)


ACTIVE_INVITATION_STATUSES = ("pending", "accepted")


def eligible_chat_contacts(user):
    """Users `user` may open a direct conversation with: anyone sharing a
    store membership with them, plus anyone connected through a pending or
    accepted job invitation, in either direction."""
    store_ids = Membership.objects.filter(user=user).values_list(
        "store_id", flat=True
    )
    coworker_ids = (
        Membership.objects.filter(store_id__in=store_ids)
        .exclude(user=user)
        .values_list("user_id", flat=True)
    )
    sent_ids = MembershipInvitation.objects.filter(
        inviter=user,
        status__in=ACTIVE_INVITATION_STATUSES,
        invitee__isnull=False,
    ).values_list("invitee_id", flat=True)
    received_ids = MembershipInvitation.objects.filter(
        invitee=user, status__in=ACTIVE_INVITATION_STATUSES
    ).values_list("inviter_id", flat=True)

    return (
        get_user_model()
        .objects.filter(
            Q(pk__in=coworker_ids) | Q(pk__in=sent_ids) | Q(pk__in=received_ids)
        )
        .exclude(pk=user.pk)
        .distinct()
    )


def can_chat_with(user, other_user):
    """Cheaper than filtering eligible_chat_contacts(user): checks the one
    pair directly with EXISTS-style queries instead of building the full
    contact list, which matters here since this runs on every websocket
    connect."""
    if user.pk == other_user.pk:
        return False

    shares_store = Membership.objects.filter(
        user=user,
        store_id__in=Membership.objects.filter(user=other_user).values_list(
            "store_id", flat=True
        ),
    ).exists()
    if shares_store:
        return True

    return MembershipInvitation.objects.filter(
        Q(inviter=user, invitee=other_user) | Q(inviter=other_user, invitee=user),
        status__in=ACTIVE_INVITATION_STATUSES,
    ).exists()


def merchant_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_merchant(request.user):
            if wants_json(request):
                return forbidden(request, "Only merchants can access the chat.")
            return render(request, "chat/not_merchant.html", status=403)
        return view(request, *args, **kwargs)

    return wrapper


def conversation_participant_required(view):
    """Turns the `conversation_id` URL kwarg into a `conversation` argument and
    refuses anyone who isn't one of the two participants."""

    @wraps(view)
    def wrapper(request, *args, conversation_id, **kwargs):
        conversation = get_object_or_404(DirectConversation, pk=conversation_id)
        if not conversation.has_participant(request.user):
            return forbidden(request, "You do not have access to this conversation.")
        return view(request, *args, conversation=conversation, **kwargs)

    return wrapper


def store_member_required(view):
    """Turns the `store_id` URL kwarg into `store` + `membership` arguments."""

    @wraps(view)
    def wrapper(request, *args, store_id, **kwargs):
        store = get_object_or_404(Store, pk=store_id)
        membership = Membership.objects.filter(user=request.user, store=store).first()
        if membership is None:
            return forbidden(request, "You are not a member of this store.")
        return view(request, *args, store=store, membership=membership, **kwargs)

    return wrapper


def store_announcer_required(view):
    @store_member_required
    @wraps(view)
    def wrapper(request, *args, store, membership, **kwargs):
        if membership.role not in ANNOUNCER_ROLES:
            return forbidden(
                request, "Only owners and managers can post announcements."
            )
        return view(request, *args, store=store, membership=membership, **kwargs)

    return wrapper