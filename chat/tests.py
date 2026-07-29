from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from merchant_interface.models import Membership, Niche, Store

from .models import Announcement, DirectConversation, DirectMessage

User = get_user_model()


def make_merchant(email):
    user = User.objects.create_user(username=email, email=email, password='pass12345')
    user.profile.is_merchant = True
    user.profile.save()
    return user


class DirectConversationTests(TestCase):
    def setUp(self):
        self.alice = make_merchant('alice@example.com')
        self.bob = make_merchant('bob@example.com')
        self.carol = make_merchant('carol@example.com')  # not a merchant of the pair, used for 3rd-party checks

    def test_get_or_create_between_is_symmetric(self):
        convo1, created1 = DirectConversation.objects.get_or_create_between(self.alice, self.bob)
        convo2, created2 = DirectConversation.objects.get_or_create_between(self.bob, self.alice)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(convo1.pk, convo2.pk)

    def test_only_participants_can_view_conversation(self):
        convo, _ = DirectConversation.objects.get_or_create_between(self.alice, self.bob)

        client = Client()
        client.force_login(self.carol)
        response = client.get(reverse('chat:conversation_detail', args=[convo.id]))
        self.assertEqual(response.status_code, 403)

        client.force_login(self.alice)
        response = client.get(reverse('chat:conversation_detail', args=[convo.id]))
        self.assertEqual(response.status_code, 200)

    def test_start_conversation_rejects_non_merchant(self):
        shopper = User.objects.create_user(username='dan@example.com', email='dan@example.com', password='pass12345')
        client = Client()
        client.force_login(self.alice)
        response = client.get(reverse('chat:start_conversation', args=[shopper.id]))
        self.assertRedirects(response, reverse('chat:inbox'))

    def test_send_attachment_creates_message_and_file(self):
        convo, _ = DirectConversation.objects.get_or_create_between(self.alice, self.bob)
        client = Client()
        client.force_login(self.alice)

        upload = SimpleUploadedFile('note.txt', b'hello world', content_type='text/plain')
        response = client.post(
            reverse('chat:send_direct_attachment', args=[convo.id]),
            {'content': 'see attached', 'file': upload},
        )
        self.assertEqual(response.status_code, 200)

        message = DirectMessage.objects.get(conversation=convo)
        self.assertEqual(message.content, 'see attached')
        self.assertEqual(message.attachments.count(), 1)
        self.assertEqual(message.attachments.first().kind, 'file')


class AnnouncementTests(TestCase):
    def setUp(self):
        niche = Niche.objects.create(name='Electronics')
        self.store = Store.objects.create(name='Test Store', niche=niche)
        self.owner = make_merchant('owner@example.com')
        self.manager = make_merchant('manager@example.com')
        self.helper = make_merchant('helper@example.com')
        Membership.objects.create(user=self.owner, store=self.store, role='Owner')
        Membership.objects.create(user=self.manager, store=self.store, role='Manager')
        Membership.objects.create(user=self.helper, store=self.store, role='Helper')

    def test_owner_and_manager_can_post_helper_cannot(self):
        client = Client()

        client.force_login(self.helper)
        response = client.post(
            reverse('chat:post_announcement', args=[self.store.id]),
            {'content': 'helper trying to post'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Announcement.objects.count(), 0)

        client.force_login(self.manager)
        response = client.post(
            reverse('chat:post_announcement', args=[self.store.id]),
            {'content': 'manager announcement'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Announcement.objects.count(), 1)

    def test_all_members_can_read_announcements(self):
        Announcement.objects.create(store=self.store, author=self.owner, content='welcome')
        client = Client()
        client.force_login(self.helper)
        response = client.get(reverse('chat:store_announcements', args=[self.store.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'welcome')
