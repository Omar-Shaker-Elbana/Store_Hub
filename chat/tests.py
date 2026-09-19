from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import (Client, TestCase, TransactionTestCase,
                         override_settings)
from django.urls import reverse

from merchant_interface.models import Membership, Niche, Store

from .models import Announcement, DirectConversation, DirectMessage
from .routing import websocket_urlpatterns

User = get_user_model()

IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def make_merchant(email):
    user = User.objects.create_user(username=email, email=email, password="pass12345")
    user.profile.is_merchant = True
    user.profile.save()
    return user


class DirectConversationTests(TestCase):
    def setUp(self):
        self.alice = make_merchant("alice@example.com")
        self.bob = make_merchant("bob@example.com")
        self.carol = make_merchant("carol@example.com")

    def test_get_or_create_between_is_symmetric(self):
        convo1, created1 = DirectConversation.objects.get_or_create_between(
            self.alice, self.bob
        )
        convo2, created2 = DirectConversation.objects.get_or_create_between(
            self.bob, self.alice
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(convo1.pk, convo2.pk)

    def test_reversed_participants_rejected_by_db(self):
        low, high = sorted([self.alice, self.bob], key=lambda u: u.pk)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DirectConversation.objects.create(
                    participant_one=high, participant_two=low
                )

    def test_touch_moves_updated_at(self):
        convo, _ = DirectConversation.objects.get_or_create_between(self.alice, self.bob)
        before = convo.updated_at
        convo.touch()
        convo.refresh_from_db()
        self.assertGreater(convo.updated_at, before)

    def test_only_participants_can_view_conversation(self):
        convo, _ = DirectConversation.objects.get_or_create_between(self.alice, self.bob)
        client = Client()
        client.force_login(self.carol)
        response = client.get(reverse("chat:conversation_detail", args=[convo.id]))
        self.assertEqual(response.status_code, 403)

        client.force_login(self.alice)
        response = client.get(reverse("chat:conversation_detail", args=[convo.id]))
        self.assertEqual(response.status_code, 200)

    def test_non_merchant_cannot_open_inbox(self):
        shopper = User.objects.create_user(
            username="dan@example.com", email="dan@example.com", password="pass12345"
        )
        client = Client()
        client.force_login(shopper)
        self.assertEqual(client.get(reverse("chat:inbox")).status_code, 403)

    def test_start_conversation_requires_post(self):
        client = Client()
        client.force_login(self.alice)
        response = client.get(reverse("chat:start_conversation", args=[self.bob.id]))
        self.assertEqual(response.status_code, 405)

    def test_start_conversation_rejects_non_merchant(self):
        shopper = User.objects.create_user(
            username="dan@example.com", email="dan@example.com", password="pass12345"
        )
        client = Client()
        client.force_login(self.alice)
        response = client.post(reverse("chat:start_conversation", args=[shopper.id]))
        self.assertRedirects(response, reverse("chat:inbox"))

    def test_send_attachment_creates_message_and_file(self):
        convo, _ = DirectConversation.objects.get_or_create_between(self.alice, self.bob)
        client = Client()
        client.force_login(self.alice)

        upload = SimpleUploadedFile(
            "note.txt", b"hello world", content_type="text/plain"
        )
        response = client.post(
            reverse("chat:send_direct_attachment", args=[convo.id]),
            {"content": "see attached", "file": upload},
        )
        self.assertEqual(response.status_code, 200)

        message = DirectMessage.objects.get(conversation=convo)
        self.assertEqual(message.content, "see attached")
        self.assertEqual(message.attachments.count(), 1)
        self.assertEqual(message.attachments.first().kind, "file")

    def test_disallowed_extension_is_rejected(self):
        convo, _ = DirectConversation.objects.get_or_create_between(self.alice, self.bob)
        client = Client()
        client.force_login(self.alice)
        upload = SimpleUploadedFile(
            "payload.svg", b"<svg onload=alert(1)>", content_type="image/svg+xml"
        )
        response = client.post(
            reverse("chat:send_direct_attachment", args=[convo.id]), {"file": upload}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DirectMessage.objects.count(), 0)

    def test_empty_send_is_rejected(self):
        convo, _ = DirectConversation.objects.get_or_create_between(self.alice, self.bob)
        client = Client()
        client.force_login(self.alice)
        response = client.post(
            reverse("chat:send_direct_attachment", args=[convo.id]), {"content": "   "}
        )
        self.assertEqual(response.status_code, 400)

    def test_unread_only_counts_incoming(self):
        convo, _ = DirectConversation.objects.get_or_create_between(self.alice, self.bob)
        DirectMessage.objects.create(conversation=convo, sender=self.alice, content="a")
        DirectMessage.objects.create(conversation=convo, sender=self.bob, content="b")
        self.assertEqual(convo.messages.unread_for(self.alice).count(), 1)
        convo.messages.mark_read_for(self.alice)
        self.assertEqual(convo.messages.unread_for(self.alice).count(), 0)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_LAYER)
class DirectChatConsumerTests(TransactionTestCase):
    def setUp(self):
        self.alice = make_merchant("alice@example.com")
        self.bob = make_merchant("bob@example.com")
        self.carol = make_merchant("carol@example.com")
        niche = Niche.objects.create(name="Electronics")
        store = Store.objects.create(name="Test Store", niche=niche)
        Membership.objects.create(
            user=self.alice, store=store, role="owner",
            wage_type="percentage", wage=50,
        )
        Membership.objects.create(user=self.bob, store=store, role="helper")
        self.convo, _ = DirectConversation.objects.get_or_create_between(
            self.alice, self.bob
        )

    def _communicator(self, user):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), f"/ws/chat/{self.convo.id}/"
        )
        communicator.scope["user"] = user
        return communicator

    async def test_participant_can_send_and_receive(self):
        communicator = self._communicator(self.alice)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to({"type": "message", "content": "hi bob"})
        response = await communicator.receive_json_from()
        self.assertEqual(response["type"], "message")
        self.assertEqual(response["message"]["content"], "hi bob")

        await communicator.disconnect()

    async def test_outsider_is_rejected(self):
        communicator = self._communicator(self.carol)
        connected, _ = await communicator.connect()
        self.assertFalse(connected)


class AnnouncementTests(TestCase):
    def setUp(self):
        niche = Niche.objects.create(name="Electronics")
        self.store = Store.objects.create(name="Test Store", niche=niche)
        self.owner = make_merchant("owner@example.com")
        self.manager = make_merchant("manager@example.com")
        self.helper = make_merchant("helper@example.com")
        self.outsider = make_merchant("outsider@example.com")
        # Membership.clean() rejects a salaried owner, so wage_type matters here.
        Membership.objects.create(
            user=self.owner,
            store=self.store,
            role="owner",
            wage_type="percentage",
            wage=50,
        )
        Membership.objects.create(user=self.manager, store=self.store, role="manager")
        Membership.objects.create(user=self.helper, store=self.store, role="helper")

    def _post(self, user, content):
        client = Client()
        client.force_login(user)
        return client.post(
            reverse("chat:post_announcement", args=[self.store.id]),
            {"content": content},
        )

    def test_owner_and_manager_can_post_helper_cannot(self):
        self.assertEqual(self._post(self.helper, "helper trying").status_code, 403)
        self.assertEqual(Announcement.objects.count(), 0)

        self.assertEqual(self._post(self.owner, "owner announcement").status_code, 302)
        self.assertEqual(
            self._post(self.manager, "manager announcement").status_code, 302
        )
        self.assertEqual(Announcement.objects.count(), 2)

    def test_non_member_cannot_post_or_read(self):
        self.assertEqual(self._post(self.outsider, "hello").status_code, 403)
        client = Client()
        client.force_login(self.outsider)
        response = client.get(reverse("chat:store_announcements", args=[self.store.id]))
        self.assertEqual(response.status_code, 403)

    def test_empty_announcement_is_rejected(self):
        response = self._post(self.manager, "   ")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Announcement.objects.count(), 0)

    def test_all_members_can_read_announcements(self):
        Announcement.objects.create(
            store=self.store, author=self.owner, content="welcome"
        )
        client = Client()
        client.force_login(self.helper)
        response = client.get(reverse("chat:store_announcements", args=[self.store.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "welcome")