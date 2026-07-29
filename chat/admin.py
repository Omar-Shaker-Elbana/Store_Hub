from django.contrib import admin

from .models import (
    Announcement,
    AnnouncementAttachment,
    DirectConversation,
    DirectMessage,
    DirectMessageAttachment,
)


class DirectMessageAttachmentInline(admin.TabularInline):
    model = DirectMessageAttachment
    extra = 0


@admin.register(DirectConversation)
class DirectConversationAdmin(admin.ModelAdmin):
    list_display = ('id', 'participant_one', 'participant_two', 'updated_at')
    search_fields = ('participant_one__email', 'participant_two__email')


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation', 'sender', 'created_at', 'is_read')
    list_filter = ('is_read',)
    inlines = [DirectMessageAttachmentInline]


class AnnouncementAttachmentInline(admin.TabularInline):
    model = AnnouncementAttachment
    extra = 0


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('id', 'store', 'author', 'created_at')
    list_filter = ('store',)
    inlines = [AnnouncementAttachmentInline]
