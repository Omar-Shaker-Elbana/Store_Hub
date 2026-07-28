from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

# Create your models here.

User = settings.AUTH_USER_MODEL


# class Notification(models.Model):
#     class NotificationType(models.TextChoices):
#         ORDER_STATUS = 'order_status', 'Order status update'
#         LOW_STOCK = 'low_stock', 'Low stock alert'
#         NEW_REVIEW = 'new_review', 'New review'
#         MEMBERSHIP_INVITE = 'membership_invite', 'Membership invitation'
#         MEMBERSHIP_UPDATE = 'membership_update', 'Membership update'
#         WISHLIST_PRICE_DROP = 'wishlist_price_drop', 'Wishlist price drop'
#         SUGGESTION_REVIEWED = 'suggestion_reviewed', 'Category/niche suggestion reviewed'
#         SYSTEM = 'system', 'System announcement'

#     recipient = models.ForeignKey(
#         User, on_delete=models.CASCADE, related_name='notifications'
#     )
#     actor = models.ForeignKey(
#         User, on_delete=models.SET_NULL, null=True, blank=True,
#         related_name='+'
#     )
#     notification_type = models.CharField(
#         max_length=25, choices=NotificationType.choices
#     )
#     title = models.CharField(max_length=150)
#     message = models.CharField(max_length=500, null=True, blank=True)

#     # Generic relation so a notification can point at whatever triggered it
#     # (an Order, a Product, a MembershipInvitation, a Review, ...) without
#     # notifications needing an FK to every other app.
#     content_type = models.ForeignKey(
#         ContentType, on_delete=models.CASCADE, null=True, blank=True
#     )
#     object_id = models.PositiveIntegerField(null=True, blank=True)
#     target = GenericForeignKey('content_type', 'object_id')

#     is_read = models.BooleanField(default=False)
#     read_at = models.DateTimeField(null=True, blank=True)
#     created_at = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         ordering = ['-created_at']
#         indexes = [
#             models.Index(fields=['recipient', 'is_read']),
#             models.Index(fields=['content_type', 'object_id']),
#         ]

#     def __str__(self):
#         return f"{self.get_notification_type_display()} -> {self.recipient}"

#     def mark_as_read(self):
#         if not self.is_read:
#             self.is_read = True
#             self.read_at = timezone.now()
#             self.save(update_fields=['is_read', 'read_at'])


# class NotificationPreference(models.Model):
#     user = models.OneToOneField(
#         User, on_delete=models.CASCADE, related_name='notification_preference'
#     )
#     order_updates = models.BooleanField(default=True)
#     low_stock_alerts = models.BooleanField(default=True)
#     new_reviews = models.BooleanField(default=True)
#     membership_invites = models.BooleanField(default=True)
#     wishlist_price_drops = models.BooleanField(default=True)
#     suggestion_reviewed = models.BooleanField(default=True)
#     email_notifications = models.BooleanField(default=True)

#     def __str__(self):
#         return f"{self.user}'s notification preferences"


# @receiver(post_save, sender=settings.AUTH_USER_MODEL)
# def create_notification_preference(sender, instance, created, **kwargs):
#     if created:
#         NotificationPreference.objects.create(user=instance)

class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_notifications')
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)