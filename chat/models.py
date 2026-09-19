import os

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

User = settings.AUTH_USER_MODEL

# --- attachment policy -----------------------------------------------------
# SVG is deliberately absent from the image set: it can carry <script>, and we
# serve attachments from our own origin.
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
PDF_EXTENSIONS = frozenset({".pdf"})
DOCUMENT_EXTENSIONS = frozenset(
    {".txt", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods"}
)
ALLOWED_ATTACHMENT_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS | DOCUMENT_EXTENSIONS

MAX_ATTACHMENT_SIZE = 15 * 1024 * 1024  # 15 MB per file
MAX_ANNOUNCEMENT_ATTACHMENTS = 5
MAX_MESSAGE_LENGTH = 4000


def attachment_kind(filename):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in PDF_EXTENSIONS:
        return "pdf"
    return "file"


class BaseAttachment(models.Model):
    """Shared shape for every file uploaded into the chat app. Subclasses
    declare their own `file` field because upload_to differs."""

    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    @property
    def kind(self):
        return attachment_kind(self.original_filename or self.file.name)

    def __str__(self):
        return self.original_filename or self.file.name


# ---------------------------------------------------------------------------
# 1-to-1 direct messaging between merchants
# ---------------------------------------------------------------------------


class DirectConversationManager(models.Manager):
    def get_or_create_between(self, user_a, user_b):
        """Two users only ever share a single conversation - participants are
        stored in a canonical (lowest pk first) order so we can rely on the
        unique_together constraint instead of querying both orderings."""
        if user_a.pk == user_b.pk:
            raise ValueError("A conversation requires two distinct users.")

        first, second = sorted([user_a, user_b], key=lambda u: u.pk)
        return self.get_or_create(participant_one=first, participant_two=second)

    def for_user(self, user):
        return self.filter(Q(participant_one=user) | Q(participant_two=user))


class DirectConversation(models.Model):
    """A single 1-to-1 conversation between two merchants."""

    participant_one = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="conversations_as_first"
    )
    participant_two = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="conversations_as_second"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Bumped by touch() every time a new message lands, so the inbox can sort
    # by "most recently active" without joining onto the messages table.
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = DirectConversationManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["participant_one", "participant_two"],
                name="unique_direct_conversation",
            ),
            # The canonical ordering the manager relies on, enforced by the DB
            # rather than by convention.
            models.CheckConstraint(
                condition=Q(participant_one__lt=F("participant_two")),
                name="conversation_participants_ordered",
            ),
        ]
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Conversation({self.participant_one} <-> {self.participant_two})"

    def has_participant(self, user):
        return user.pk in (self.participant_one_id, self.participant_two_id)

    def other_participant(self, user):
        return (
            self.participant_two
            if user.pk == self.participant_one_id
            else self.participant_one
        )

    def touch(self):
        now = timezone.now()
        DirectConversation.objects.filter(pk=self.pk).update(updated_at=now)
        self.updated_at = now


def direct_attachment_path(instance, filename):
    return f"chat/direct/{instance.message.conversation_id}/{filename}"


class DirectMessageQuerySet(models.QuerySet):
    """"Unread for me" means unread *and* not sent by me - that rule lived in
    three places, so it lives here now."""

    def unread_for(self, user):
        return self.filter(is_read=False).exclude(sender=user)

    def mark_read_for(self, user):
        return self.unread_for(user).update(is_read=True, read_at=timezone.now())


class DirectMessage(models.Model):
    conversation = models.ForeignKey(
        DirectConversation, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sent_direct_messages"
    )
    content = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    objects = DirectMessageQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["conversation", "created_at"], name="chat_dm_convo_created_idx"
            ),
            models.Index(
                fields=["conversation", "is_read"], name="chat_dm_convo_read_idx"
            ),
        ]

    def __str__(self):
        return f"{self.sender} @ {self.created_at:%Y-%m-%d %H:%M}"


class DirectMessageAttachment(BaseAttachment):
    message = models.ForeignKey(
        DirectMessage, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to=direct_attachment_path)


# ---------------------------------------------------------------------------
# Store-wide announcements - only owners/managers post, all store members read
# ---------------------------------------------------------------------------


def announcement_attachment_path(instance, filename):
    return f"chat/announcements/{instance.announcement.store_id}/{filename}"


class Announcement(models.Model):
    store = models.ForeignKey(
        "merchant_interface.Store",
        on_delete=models.CASCADE,
        related_name="announcements",
    )
    author = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="store_announcements"
    )
    content = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["store", "-created_at"], name="chat_ann_store_created_idx"
            ),
        ]

    def __str__(self):
        return f"Announcement in {self.store} by {self.author}"


class AnnouncementAttachment(BaseAttachment):
    announcement = models.ForeignKey(
        Announcement, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to=announcement_attachment_path)
