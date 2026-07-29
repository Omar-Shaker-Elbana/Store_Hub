import os

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

User = settings.AUTH_USER_MODEL


def _attachment_kind(filename):
    ext = os.path.splitext(filename or '')[1].lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'):
        return 'image'
    if ext == '.pdf':
        return 'pdf'
    return 'file'


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

    participant_one = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversations_as_first')
    participant_two = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversations_as_second')
    created_at = models.DateTimeField(auto_now_add=True)
    # Bumped every time a new message lands, so the inbox can sort by
    # "most recently active" without joining onto the messages table.
    updated_at = models.DateTimeField(auto_now_add=True)

    objects = DirectConversationManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['participant_one', 'participant_two'],
                name='unique_direct_conversation',
            )
        ]
        ordering = ['-updated_at']

    def __str__(self):
        return f"Conversation({self.participant_one} <-> {self.participant_two})"

    def has_participant(self, user):
        return user.pk in (self.participant_one_id, self.participant_two_id)

    def other_participant(self, user):
        return self.participant_two if user.pk == self.participant_one_id else self.participant_one

    def touch(self):
        DirectConversation.objects.filter(pk=self.pk).update(updated_at=timezone.now())


def direct_attachment_path(instance, filename):
    return f'chat/direct/{instance.message.conversation_id}/{filename}'


class DirectMessage(models.Model):
    conversation = models.ForeignKey(DirectConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_direct_messages')
    content = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender} @ {self.created_at:%Y-%m-%d %H:%M}"

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])


class DirectMessageAttachment(models.Model):
    message = models.ForeignKey(DirectMessage, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=direct_attachment_path)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    @property
    def kind(self):
        return _attachment_kind(self.original_filename or self.file.name)

    def __str__(self):
        return self.original_filename or self.file.name


# ---------------------------------------------------------------------------
# Store-wide announcements - only owners/managers post, all store members read
# ---------------------------------------------------------------------------

def announcement_attachment_path(instance, filename):
    return f'chat/announcements/{instance.announcement.store_id}/{filename}'


class Announcement(models.Model):
    store = models.ForeignKey('merchant_interface.Store', on_delete=models.CASCADE, related_name='announcements')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='store_announcements')
    content = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Announcement in {self.store} by {self.author}"


class AnnouncementAttachment(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=announcement_attachment_path)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    @property
    def kind(self):
        return _attachment_kind(self.original_filename or self.file.name)

    def __str__(self):
        return self.original_filename or self.file.name
