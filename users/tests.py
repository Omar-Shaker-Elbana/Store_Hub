from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Profile, UserSettings, Card


class UserSignalTests(TestCase):
    """The post_save signal on User should auto-create related objects."""

    def test_profile_and_settings_created_on_user_creation(self):
        user = User.objects.create_user(
            username="jane@example.com", email="jane@example.com", password="StrongPass123"
        )
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertTrue(UserSettings.objects.filter(user=user).exists())

    def test_profile_str_returns_username(self):
        user = User.objects.create_user(username="strview", password="StrongPass123")
        profile = Profile.objects.get(user=user)
        self.assertEqual(str(profile), str(user))

    def test_default_settings_values(self):
        user = User.objects.create_user(username="defaults", password="StrongPass123")
        settings_obj = UserSettings.objects.get(user=user)
        self.assertEqual(settings_obj.theme, "dark")
        self.assertEqual(settings_obj.language, "en")

    def test_related_objects_not_duplicated_on_update(self):
        # Saving an existing user again must not create duplicate related rows.
        user = User.objects.create_user(username="noupdate", password="StrongPass123")
        user.first_name = "Updated"
        user.save()
        self.assertEqual(Profile.objects.filter(user=user).count(), 1)
        self.assertEqual(UserSettings.objects.filter(user=user).count(), 1)


class ProfileViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="viewer", email="viewer@example.com", password="StrongPass123"
        )
        self.url = reverse("profile")

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)
        self.assertIn(response.status_code, (302, 301))

    def test_get_when_logged_in(self):
        self.client.login(username="viewer", password="StrongPass123")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("update_settings", response.context)
        self.assertIn("update_profile", response.context)
        self.assertIn("update_user", response.context)

    def test_post_valid_update_saves_changes(self):
        self.client.login(username="viewer", password="StrongPass123")
        response = self.client.post(self.url, {
            "first_name": "Jane",
            "last_name": "Doe",
            "gender": "F",
            "country": "Egypt",
            "address1": "123 Main St",
            "address2": "",
            "address3": "",
            "theme": "light",
            "language": "ar",
        })
        self.assertEqual(response.status_code, 302)

        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Jane")
        self.assertEqual(self.user.last_name, "Doe")

        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.country, "Egypt")
        self.assertEqual(profile.address1, "123 Main St")

        settings_obj = UserSettings.objects.get(user=self.user)
        self.assertEqual(settings_obj.theme, "light")
        self.assertEqual(settings_obj.language, "ar")

    def test_post_invalid_update_does_not_crash_and_shows_error(self):
        self.client.login(username="viewer", password="StrongPass123")
        # Invalid choice for 'gender' and 'theme' should fail form validation.
        response = self.client.post(self.url, {
            "first_name": "Jane",
            "last_name": "Doe",
            "gender": "X",       # not a valid GENDER_CHOICES value
            "theme": "purple",   # not a valid THEME_CHOICES value
            "language": "en",
        })
        self.assertEqual(response.status_code, 200)
        messages = list(response.context["messages"])
        self.assertTrue(any("failed" in str(m).lower() for m in messages))

    def test_logout_via_post(self):
        self.client.login(username="viewer", password="StrongPass123")
        response = self.client.post(self.url, {"logout_btn": "1"})
        self.assertEqual(response.status_code, 302)
        # After logout, the profile page should redirect (require login) again.
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 200)


class ChangePasswordViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pwuser", email="pwuser@example.com", password="OldPass123"
        )
        self.url = reverse("change-password")

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 301))

    def test_get_when_logged_in(self):
        self.client.login(username="pwuser", password="OldPass123")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_successful_password_change_keeps_user_logged_in(self):
        self.client.login(username="pwuser", password="OldPass123")
        response = self.client.post(self.url, {
            "old_password": "OldPass123",
            "new_password1": "BrandNewPass456",
            "new_password2": "BrandNewPass456",
        })
        self.assertRedirects(response, reverse("profile"))

        # Session should remain authenticated (update_session_auth_hash was called).
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("BrandNewPass456"))

    def test_wrong_old_password_is_rejected(self):
        self.client.login(username="pwuser", password="OldPass123")
        response = self.client.post(self.url, {
            "old_password": "WrongPassword",
            "new_password1": "BrandNewPass456",
            "new_password2": "BrandNewPass456",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldPass123"))

    def test_mismatched_new_passwords_rejected(self):
        self.client.login(username="pwuser", password="OldPass123")
        response = self.client.post(self.url, {
            "old_password": "OldPass123",
            "new_password1": "BrandNewPass456",
            "new_password2": "SomethingElse789",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldPass123"))


class CardModelTests(TestCase):
    """
    Flags a real data-integrity risk: card_num/OTP are plain IntegerFields.
    A standard 16-digit PAN overflows Django's IntegerField range
    (-2147483648 to 2147483647) on strict backends like PostgreSQL/MySQL.
    SQLite is loosely typed and will NOT catch this, which is why this test
    only documents the boundary rather than asserting a DB-level failure.
    """

    def test_card_number_exceeds_integerfield_safe_range(self):
        user = User.objects.create_user(username="carduser", password="StrongPass123")
        sample_pan = 4111111111111111  # typical 16-digit test card number
        max_safe_integer = 2147483647
        self.assertGreater(
            sample_pan,
            max_safe_integer,
            "A real card number exceeds Django's documented safe IntegerField "
            "range; this field should be a masked/tokenized CharField instead."
        )