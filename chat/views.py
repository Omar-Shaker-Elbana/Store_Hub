from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from merchant_interface.models import Membership, Store

from .models import (
    Announcement,
    AnnouncementAttachment,
    DirectConversation,
    DirectMessage,
    DirectMessageAttachment,
)
from .utils import broadcast_direct_message

User = get_user_model()

MAX_ATTACHMENT_SIZE = 15 * 1024 * 1024  # 15 MB per file


def _is_merchant(user):
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.is_merchant)


def _membership_for(user, store):
    return Membership.objects.filter(user=user, store=store).first()


# ---------------------------------------------------------------------------
# 1-to-1 direct messaging
# ---------------------------------------------------------------------------

@login_required
def inbox(request):
    """List every conversation the current merchant is part of, plus the
    pool of other merchants they could start a new one with."""
    if not _is_merchant(request.user):
        return HttpResponseForbidden("Only merchants can access the chat.")

    conversations = (
        DirectConversation.objects.for_user(request.user)
        .select_related('participant_one', 'participant_two')
        .prefetch_related('messages')
    )

    # Templates can't call other_participant(user) with an argument, so
    # resolve it here and hand the template plain data instead.
    conversation_rows = [
        {
            'conversation': conversation,
            'other_user': conversation.other_participant(request.user),
            'last_message': conversation.messages.last(),
            'unread_count': conversation.messages.filter(is_read=False).exclude(sender=request.user).count(),
        }
        for conversation in conversations
    ]

    merchants = User.objects.filter(profile__is_merchant=True).exclude(pk=request.user.pk)

    context = {
        'conversation_rows': conversation_rows,
        'merchants': merchants,
    }
    return render(request, 'chat/inbox.html', context)


@login_required
def start_conversation(request, user_id):
    if not _is_merchant(request.user):
        return HttpResponseForbidden("Only merchants can start a chat.")

    other_user = get_object_or_404(User, pk=user_id)

    if other_user.pk == request.user.pk:
        messages.error(request, "You can't start a conversation with yourself.")
        return redirect('chat:inbox')

    if not _is_merchant(other_user):
        messages.error(request, "You can only chat with other merchants.")
        return redirect('chat:inbox')

    conversation, _created = DirectConversation.objects.get_or_create_between(request.user, other_user)
    return redirect('chat:conversation_detail', conversation_id=conversation.id)


@login_required
def conversation_detail(request, conversation_id):
    conversation = get_object_or_404(DirectConversation, pk=conversation_id)
    if not conversation.has_participant(request.user):
        return HttpResponseForbidden("You do not have access to this conversation.")

    # Mark anything sent to us as read as soon as we open the thread.
    (conversation.messages
        .filter(is_read=False)
        .exclude(sender=request.user)
        .update(is_read=True, read_at=timezone.now()))

    context = {
        'conversation': conversation,
        'other_user': conversation.other_participant(request.user),
        'message_list': conversation.messages.select_related('sender').prefetch_related('attachments'),
    }
    return render(request, 'chat/conversation_detail.html', context)


@login_required
@require_POST
def send_direct_attachment(request, conversation_id):
    """HTTP fallback / attachment path for a direct conversation. Plain text
    messages normally travel over the websocket in real time; this endpoint
    exists for files (and works fine as a no-JS fallback for text too)."""
    conversation = get_object_or_404(DirectConversation, pk=conversation_id)
    if not conversation.has_participant(request.user):
        return HttpResponseForbidden("You do not have access to this conversation.")

    uploaded_file = request.FILES.get('file')
    content = (request.POST.get('content') or '').strip()

    if not uploaded_file and not content:
        return JsonResponse({'error': 'Nothing to send.'}, status=400)

    if uploaded_file and uploaded_file.size > MAX_ATTACHMENT_SIZE:
        return JsonResponse({'error': 'File is too large (max 15 MB).'}, status=400)

    message = DirectMessage.objects.create(
        conversation=conversation, sender=request.user, content=content
    )

    if uploaded_file:
        DirectMessageAttachment.objects.create(
            message=message,
            file=uploaded_file,
            original_filename=uploaded_file.name,
            content_type=uploaded_file.content_type or '',
            size=uploaded_file.size,
        )

    conversation.touch()
    broadcast_direct_message(message)

    return JsonResponse({'status': 'ok', 'message_id': message.id})


# ---------------------------------------------------------------------------
# Store announcements (owners/managers post, all store members read)
# ---------------------------------------------------------------------------

@login_required
def store_announcements(request, store_id):
    store = get_object_or_404(Store, pk=store_id)
    membership = _membership_for(request.user, store)
    if not membership:
        return HttpResponseForbidden("You are not a member of this store.")

    announcement_list = (
        Announcement.objects.filter(store=store)
        .select_related('author')
        .prefetch_related('attachments')
    )

    context = {
        'store': store,
        'announcement_list': announcement_list,
        'can_post': membership.role in ('Owner', 'Manager'),
    }
    return render(request, 'chat/announcements.html', context)


@login_required
@require_POST
def post_announcement(request, store_id):
    store = get_object_or_404(Store, pk=store_id)
    membership = _membership_for(request.user, store)
    if not membership or membership.role not in ('Owner', 'Manager'):
        return HttpResponseForbidden("Only owners and managers can post announcements.")

    content = (request.POST.get('content') or '').strip()
    uploaded_files = request.FILES.getlist('files')

    if not content and not uploaded_files:
        messages.error(request, 'An announcement needs text or an attachment.')
        return redirect('chat:store_announcements', store_id=store.id)

    for uploaded_file in uploaded_files:
        if uploaded_file.size > MAX_ATTACHMENT_SIZE:
            messages.error(request, f'"{uploaded_file.name}" is too large (max 15 MB).')
            return redirect('chat:store_announcements', store_id=store.id)

    announcement = Announcement.objects.create(store=store, author=request.user, content=content)

    for uploaded_file in uploaded_files:
        AnnouncementAttachment.objects.create(
            announcement=announcement,
            file=uploaded_file,
            original_filename=uploaded_file.name,
            content_type=uploaded_file.content_type or '',
            size=uploaded_file.size,
        )

    messages.success(request, 'Announcement posted.')
    return redirect('chat:store_announcements', store_id=store.id)
