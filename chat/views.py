from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import AnnouncementForm, DirectMessageForm
from .models import (Announcement, AnnouncementAttachment, DirectConversation,
                     DirectMessage, DirectMessageAttachment)
from .permissions import (ANNOUNCER_ROLES, can_chat_with,
                          conversation_participant_required,
                          eligible_chat_contacts, merchant_required,
                          store_announcer_required, store_member_required)
from .utils import broadcast_direct_message, serialize_direct_message

User = get_user_model()

MESSAGES_PER_PAGE = 50
ANNOUNCEMENTS_PER_PAGE = 20
MERCHANTS_PER_PAGE = 25


# ---------------------------------------------------------------------------
# 1-to-1 direct messaging
# ---------------------------------------------------------------------------


@login_required
@merchant_required
def inbox(request):
    """List every conversation the current merchant is part of, plus a
    searchable page of other merchants they could start a new one with."""
    latest = DirectMessage.objects.filter(conversation=OuterRef("pk")).order_by(
        "-created_at"
    )
    conversations = (
        DirectConversation.objects.for_user(request.user)
        .select_related("participant_one", "participant_two")
        .annotate(
            last_message_content=Subquery(latest.values("content")[:1]),
            last_message_at=Subquery(latest.values("created_at")[:1]),
            unread_count=Count(
                "messages",
                filter=Q(messages__is_read=False) & ~Q(messages__sender=request.user),
            ),
        )
    )

    # Templates can't call other_participant(user) with an argument, so
    # resolve it here and hand the template plain data instead.
    conversation_rows = [
        {
            "conversation": conversation,
            "other_user": conversation.other_participant(request.user),
            "last_message_content": conversation.last_message_content,
            "last_message_at": conversation.last_message_at,
            "unread_count": conversation.unread_count,
        }
        for conversation in conversations
    ]

    query = (request.GET.get("q") or "").strip()
    merchants = eligible_chat_contacts(request.user).select_related(
        "profile"
    ).order_by("username")
    if query:
        merchants = merchants.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    merchant_page = Paginator(merchants, MERCHANTS_PER_PAGE).get_page(
        request.GET.get("merchant_page")
    )

    context = {
        "conversation_rows": conversation_rows,
        "merchant_page": merchant_page,
        "merchants": merchant_page.object_list,
        "query": query,
    }
    return render(request, "chat/inbox.html", context)


@login_required
@require_POST
@merchant_required
def start_conversation(request, user_id):
    other_user = get_object_or_404(User.objects.select_related("profile"), pk=user_id)

    if other_user.pk == request.user.pk:
        messages.error(request, "You can't start a conversation with yourself.")
        return redirect("chat:inbox")

    if not can_chat_with(request.user, other_user):
        messages.error(
            request,
            "You can only chat with coworkers or merchants you have an "
            "active job invitation with.",
        )
        return redirect("chat:inbox")

    conversation, _created = DirectConversation.objects.get_or_create_between(
        request.user, other_user
    )
    return redirect("chat:conversation_detail", conversation_id=conversation.id)


@login_required
@conversation_participant_required
def conversation_detail(request, conversation):
    # Opening the thread is what marks it read. The update is idempotent and
    # only ever touches rows addressed to us, so it is safe on a GET.
    conversation.messages.mark_read_for(request.user)

    message_qs = conversation.messages.select_related("sender").prefetch_related(
        "attachments"
    )
    paginator = Paginator(message_qs, MESSAGES_PER_PAGE)
    # Page 1 is the oldest slice, so default to the newest one.
    message_page = paginator.get_page(request.GET.get("page") or paginator.num_pages)

    context = {
        "conversation": conversation,
        "other_user": conversation.other_participant(request.user),
        "message_page": message_page,
        "message_list": message_page.object_list,
        "form": DirectMessageForm(),
    }
    return render(request, "chat/conversation_detail.html", context)


@login_required
@require_POST
@conversation_participant_required
def send_direct_attachment(request, conversation):
    """HTTP path for a direct message. Plain text normally travels over the
    websocket; this endpoint carries files and doubles as a no-JS fallback."""
    form = DirectMessageForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    message = DirectMessage.objects.create(
        conversation=conversation,
        sender=request.user,
        content=form.cleaned_data["content"],
    )

    uploaded_file = form.cleaned_data.get("file")
    if uploaded_file:
        DirectMessageAttachment.objects.create(
            message=message,
            file=uploaded_file,
            original_filename=uploaded_file.name,
            content_type=uploaded_file.content_type or "",
            size=uploaded_file.size,
        )

    conversation.touch()
    broadcast_direct_message(message)
    return JsonResponse({"status": "ok", "message": serialize_direct_message(message)})


# ---------------------------------------------------------------------------
# Store announcements (owners/managers post, all store members read)
# ---------------------------------------------------------------------------


@login_required
@store_member_required
def store_announcements(request, store, membership):
    announcement_list = (
        Announcement.objects.filter(store=store)
        .select_related("author")
        .prefetch_related("attachments")
    )
    announcement_page = Paginator(announcement_list, ANNOUNCEMENTS_PER_PAGE).get_page(
        request.GET.get("page")
    )

    context = {
        "store": store,
        "announcement_page": announcement_page,
        "announcement_list": announcement_page.object_list,
        "form": AnnouncementForm(),
        "can_post": membership.role in ANNOUNCER_ROLES,
    }
    return render(request, "chat/announcements.html", context)


@login_required
@require_POST
@store_announcer_required
def post_announcement(request, store, membership):
    form = AnnouncementForm(request.POST, request.FILES)
    if not form.is_valid():
        for error_list in form.errors.values():
            for error in error_list:
                messages.error(request, error)
        return redirect("chat:store_announcements", store_id=store.id)

    announcement = Announcement.objects.create(
        store=store, author=request.user, content=form.cleaned_data["content"]
    )
    for uploaded_file in form.cleaned_data["files"]:
        AnnouncementAttachment.objects.create(
            announcement=announcement,
            file=uploaded_file,
            original_filename=uploaded_file.name,
            content_type=uploaded_file.content_type or "",
            size=uploaded_file.size,
        )

    messages.success(request, "Announcement posted.")
    return redirect("chat:store_announcements", store_id=store.id)
