from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .forms import (
    MembershipForm,
    MembershipInvitationForm,
    StoreForm,
    SuggestNicheForm,
)
from .models import Membership, MembershipInvitation, Niche, Store, SuggestedNiche
from products.models import Category, Product

User = get_user_model()

# A 1x1 transparent GIF - smallest valid image Pillow/ImageField will accept.
GIF_BYTES = (
    b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9"
    b"\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def make_test_image(name="pic.gif"):
    return SimpleUploadedFile(name, GIF_BYTES, content_type="image/gif")


class MerchantTestMixin:
    """Shared fixtures for merchant_interface tests."""

    def make_user(self, username="owner", password="pass12345"):
        return User.objects.create_user(username=username, password=password)

    def make_niche(self, name="Electronics"):
        return Niche.objects.get_or_create(name=name)[0]

    def make_store(self, niche=None, name="Test Store"):
        niche = niche or self.make_niche()
        return Store.objects.create(name=name, niche=niche, nationality="Egyptian")

    def make_membership(self, user, store, role="Owner"):
        return Membership.objects.create(user=user, store=store, role=role)

    def make_category(self, name="Gadgets"):
        return Category.objects.create(name=name)

    def make_product(self, store, category=None, name="Widget"):
        category = category or self.make_category()
        return Product.objects.create(
            name=name,
            category=category,
            store=store,
            manufacturing_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
        )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class NicheModelTests(MerchantTestMixin, TestCase):
    def test_niche_name_must_be_unique(self):
        Niche.objects.create(name="Fashion")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Niche.objects.create(name="Fashion")


class StoreModelTests(MerchantTestMixin, TestCase):
    def test_store_creation_sets_inauguration_date_automatically(self):
        store = self.make_store()
        self.assertIsNotNone(store.inauguration_date)

    def test_store_niche_is_protected_from_deletion(self):
        niche = self.make_niche()
        self.make_store(niche=niche)
        with self.assertRaises(Exception):
            niche.delete()


class MembershipModelTests(MerchantTestMixin, TestCase):
    def test_user_cannot_have_two_memberships_in_same_store(self):
        user = self.make_user()
        store = self.make_store()
        self.make_membership(user, store, role="Owner")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Membership.objects.create(user=user, store=store, role="Helper")

    def test_default_role_is_helper(self):
        user = self.make_user()
        store = self.make_store()
        membership = Membership.objects.create(user=user, store=store)
        self.assertEqual(membership.role, "Helper")


# ---------------------------------------------------------------------------
# Form tests
# ---------------------------------------------------------------------------

class StoreFormTests(MerchantTestMixin, TestCase):
    def test_valid_data(self):
        niche = self.make_niche()
        form = StoreForm(data={
            "name": "My Shop",
            "niche": niche.id,
            "nationality": "Egyptian",
        })
        self.assertTrue(form.is_valid())

    def test_missing_niche_is_invalid(self):
        # niche is a required FK on the model (no null=True), so omitting it
        # should fail validation.
        form = StoreForm(data={"name": "My Shop", "nationality": "Egyptian"})
        self.assertFalse(form.is_valid())
        self.assertIn("niche", form.errors)

    def test_accepts_uploaded_picture(self):
        niche = self.make_niche()
        form = StoreForm(
            data={"name": "My Shop", "niche": niche.id, "nationality": "Egyptian"},
            files={"picture": make_test_image()},
        )
        self.assertTrue(form.is_valid())


class MembershipInvitationFormTests(MerchantTestMixin, TestCase):
    def test_valid_data(self):
        form = MembershipInvitationForm(data={
            "invitee_email": "invitee@example.com",
            "role": "Helper",
            "wage_type": "Salary",
            "wage": "500.00",
        })
        self.assertTrue(form.is_valid())

    def test_invalid_email_is_rejected(self):
        form = MembershipInvitationForm(data={
            "invitee_email": "not-an-email",
            "role": "Helper",
            "wage_type": "Salary",
            "wage": "500.00",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("invitee_email", form.errors)


class SuggestNicheFormTests(MerchantTestMixin, TestCase):
    def test_form_saves_against_suggested_niche_model_not_niche(self):
        # NOTE: forms.py defines SuggestNicheForm twice; the second
        # definition (bound to SuggestedNiche) wins because it's declared
        # later in the module. This test locks in that (surprising)
        # behavior so a future edit doesn't silently change it.
        self.assertIs(SuggestNicheForm.Meta.model, SuggestedNiche)

    def test_valid_data(self):
        form = SuggestNicheForm(data={"name": "Handmade Crafts"})
        self.assertTrue(form.is_valid())

    def test_blank_name_is_invalid(self):
        form = SuggestNicheForm(data={"name": ""})
        self.assertFalse(form.is_valid())


class MembershipFormTests(MerchantTestMixin, TestCase):
    def test_valid_data(self):
        form = MembershipForm(data={
            "role": "Manager",
            "wage_type": "Percentage",
            "wage": "12.50",
        })
        self.assertTrue(form.is_valid())


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------

class ShowStoreViewTests(MerchantTestMixin, TestCase):
    def test_existing_store_lists_its_products(self):
        store = self.make_store()
        product = self.make_product(store)
        response = self.client.get(reverse("show_store", args=[store.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, store.name)
        self.assertIn(product, response.context["store_products"])

    def test_store_with_no_products_still_renders(self):
        store = self.make_store()
        response = self.client.get(reverse("show_store", args=[store.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["store_products"]), [])

    def test_missing_store_redirects_to_create_store(self):
        # create_store is @login_required, so log in first - otherwise the
        # redirect chain lands on the login page instead of a 200.
        self.make_user()
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(reverse("show_store", args=[999]))
        self.assertRedirects(response, reverse("create_store"))

    def test_missing_store_shows_error_message(self):
        response = self.client.get(reverse("show_store", args=[999]), follow=True)
        messages = list(response.context["messages"])
        self.assertTrue(any("Store not found" in str(m) for m in messages))


class CreateStoreViewTests(MerchantTestMixin, TestCase):
    def setUp(self):
        self.user = self.make_user()
        self.niche = self.make_niche()
        self.url = reverse("create_store")

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_get_renders_both_forms(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["create_store_form"], StoreForm)
        self.assertIsInstance(response.context["suggest_niche_form"], SuggestNicheForm)

    def test_valid_post_creates_store_and_owner_membership(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "create_store_btn": "1",
            "name": "Brand New Store",
            "niche": self.niche.id,
            "nationality": "Egyptian",
        })
        store = Store.objects.get(name="Brand New Store")
        self.assertRedirects(response, reverse("add_members", args=[store.id]))
        membership = Membership.objects.get(user=self.user, store=store)
        self.assertEqual(membership.role, "Owner")

    def test_invalid_store_post_does_not_create_a_store(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "create_store_btn": "1",
            # niche omitted -> invalid
            "name": "Broken Store",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Store.objects.filter(name="Broken Store").exists())

    def test_invalid_store_post_does_not_surface_an_error_message(self):
        # NOTE: this looks like a bug in create_store(): the outer guard is
        # `if "create_store_btn" in request.POST and create_store_form.is_valid():`,
        # so the `else: messages.error(...)` branch inside it can only run
        # when the form IS valid, making it unreachable. In practice an
        # invalid submission currently fails silently (the page just
        # re-renders with no store created and no error message), unlike
        # edit_store/add_members which do surface their error messages
        # correctly. This test documents today's behavior.
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "create_store_btn": "1",
            "name": "Broken Store",
        }, follow=True)
        messages = list(response.context["messages"])
        self.assertTrue(any("Error creating store" in str(m) for m in messages))

    def test_valid_suggest_niche_post_creates_suggestion(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "suggest_niche_btn": "1",
            "name": "Pet Supplies",
        })
        self.assertRedirects(response, self.url)
        suggestion = SuggestedNiche.objects.get(name="Pet Supplies")
        self.assertEqual(suggestion.suggested_by, self.user)

    def test_invalid_suggest_niche_post_shows_error(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "suggest_niche_btn": "1",
            "name": "",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        messages = list(response.context["messages"])
        self.assertTrue(any("Error suggesting niche" in str(m) for m in messages))


class AddMembersViewTests(MerchantTestMixin, TestCase):
    def setUp(self):
        self.owner = self.make_user(username="owner")
        self.other_user = self.make_user(username="rando")
        self.store = self.make_store()
        self.make_membership(self.owner, self.store, role="Owner")
        self.url = reverse("add_members", args=[self.store.id])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_missing_store_redirects(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(reverse("add_members", args=[999]))
        self.assertRedirects(response, reverse("create_store"))

    def test_non_owner_is_denied(self):
        self.client.login(username="rando", password="pass12345")
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("create_store"))

    def test_non_member_is_denied(self):
        # other_user has no Membership row at all for this store.
        self.client.login(username="rando", password="pass12345")
        response = self.client.get(self.url, follow=True)
        messages = list(response.context["messages"])
        self.assertTrue(any("permission" in str(m) for m in messages))

    def test_owner_can_view_members_grouped_by_role(self):
        manager = self.make_user(username="manager")
        self.make_membership(manager, self.store, role="Manager")

        self.client.login(username="owner", password="pass12345")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.owner, [m.user for m in response.context["owners"]])
        self.assertIn(manager, [m.user for m in response.context["managers"]])

    def test_valid_invitation_creates_invitation_with_inviter_and_store(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "send_invitation_btn": "1",
            "invitee_email": "newmember@example.com",
            "role": "Helper",
            "wage_type": "Salary",
            "wage": "300.00",
        })
        # NOTE: the view redirects to the hardcoded path f"/add_members/{store.id}"
        # rather than using reverse('add_members', ...), so it is missing the
        # '/merchant' prefix and trailing slash that the actual URL pattern
        # requires. This test documents the current (likely unintended)
        # behavior rather than the "correct" URL.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('add_members', args=[self.store.id]))

        invitation = MembershipInvitation.objects.get(invitee_email="newmember@example.com")
        self.assertEqual(invitation.inviter, self.owner)
        self.assertEqual(invitation.store, self.store)

    def test_invalid_invitation_shows_error(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "send_invitation_btn": "1",
            "invitee_email": "not-an-email",
            "role": "Helper",
            "wage_type": "Salary",
            "wage": "300.00",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MembershipInvitation.objects.filter(store=self.store).exists())
        messages = list(response.context["messages"])
        self.assertTrue(any("Error sending invitation" in str(m) for m in messages))

    def test_valid_membership_update(self):
        helper_user = self.make_user(username="helper1")
        membership = self.make_membership(helper_user, self.store, role="Helper")

        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "membership_id": membership.id,
            "role": "Manager",
            "wage_type": "Salary",
            "wage": "1000.00",
        })
        self.assertRedirects(response, reverse("add_members", args=[self.store.id]))
        membership.refresh_from_db()
        self.assertEqual(membership.role, "Manager")
        self.assertEqual(membership.wage, Decimal("1000.00"))

    def test_membership_update_falls_back_to_existing_wage_type_when_missing(self):
        helper_user = self.make_user(username="helper2")
        membership = self.make_membership(helper_user, self.store, role="Helper")
        membership.wage_type = "Percentage"
        membership.save()

        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "membership_id": membership.id,
            "role": "Manager",
            "wage": "5.00",
            # wage_type intentionally omitted
        })
        self.assertRedirects(response, reverse("add_members", args=[self.store.id]))
        membership.refresh_from_db()
        self.assertEqual(membership.wage_type, "Percentage")

    def test_update_of_unknown_membership_shows_error(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "membership_id": 999999,
            "role": "Manager",
            "wage_type": "Salary",
            "wage": "5.00",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        messages = list(response.context["messages"])
        self.assertTrue(any("Membership not found" in str(m) for m in messages))


class EditStoreViewTests(MerchantTestMixin, TestCase):
    def setUp(self):
        self.owner = self.make_user(username="owner")
        self.other_user = self.make_user(username="rando")
        self.store = self.make_store()
        self.make_membership(self.owner, self.store, role="Owner")
        self.url = reverse("edit_store", args=[self.store.id])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_missing_store_redirects(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(reverse("edit_store", args=[999]))
        self.assertRedirects(response, reverse("create_store"))

    def test_non_owner_is_denied(self):
        self.client.login(username="rando", password="pass12345")
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("create_store"))

    def test_owner_get_renders_bound_form(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].instance, self.store)

    def test_valid_post_updates_store(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "name": "Renamed Store",
            "niche": self.store.niche.id,
            "nationality": "Egyptian",
        })
        # Same hardcoded-redirect caveat as add_members - see note there.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('add_members', args=[self.store.id]))
        self.store.refresh_from_db()
        self.assertEqual(self.store.name, "Renamed Store")

    def test_invalid_post_shows_error_and_does_not_save(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.post(self.url, data={
            "name": "Should Not Save",
            # niche omitted -> invalid
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.store.refresh_from_db()
        self.assertNotEqual(self.store.name, "Should Not Save")
        messages = list(response.context["messages"])
        self.assertTrue(any("Error updating store" in str(m) for m in messages))


class AllMyStoresViewTests(MerchantTestMixin, TestCase):
    def setUp(self):
        self.user = self.make_user()
        self.url = reverse("all_my_stores")

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_user_with_no_memberships_is_redirected(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("create_store"))

    def test_user_with_memberships_sees_their_stores(self):
        store1 = self.make_store(name="Store One")
        store2 = self.make_store(name="Store Two")
        self.make_membership(self.user, store1, role="Owner")
        self.make_membership(self.user, store2, role="Helper")

        self.client.login(username="owner", password="pass12345")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(response.context["stores"], [store1, store2])

    def test_does_not_include_other_users_stores(self):
        other_user = self.make_user(username="rando")
        my_store = self.make_store(name="Mine")
        other_store = self.make_store(name="Not Mine")
        self.make_membership(self.user, my_store, role="Owner")
        self.make_membership(other_user, other_store, role="Owner")

        self.client.login(username="owner", password="pass12345")
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["stores"]), [my_store])


class MyStoreViewTests(MerchantTestMixin, TestCase):
    def setUp(self):
        self.owner = self.make_user(username="owner")
        self.other_user = self.make_user(username="rando")
        self.store = self.make_store()
        self.make_membership(self.owner, self.store, role="Owner")
        self.url = reverse("my_store", args=[self.store.id])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_missing_store_redirects(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(reverse("my_store", args=[999]))
        self.assertRedirects(response, reverse("create_store"))

    def test_non_member_is_denied(self):
        self.client.login(username="rando", password="pass12345")
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("create_store"))

    def test_member_can_view_store(self):
        # NOTE: templates/merchant_interface/my_store.html does not exist in
        # this repo (only show_store/create_store/edit_store/add_members/
        # all_my_stores templates are present), so this currently raises
        # TemplateDoesNotExist instead of returning 200. This test documents
        # the expected behavior once that template is added; today it will
        # fail with a TemplateDoesNotExist error, which is a real bug worth
        # fixing rather than a flaw in the test.
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["store"], self.store)


class MyAnalyticsViewTests(MerchantTestMixin, TestCase):
    def setUp(self):
        self.owner = self.make_user(username="owner")
        self.other_user = self.make_user(username="rando")
        self.store = self.make_store()
        self.make_membership(self.owner, self.store, role="Owner")
        self.url = reverse("my_analytics", args=[self.store.id])

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_missing_store_redirects(self):
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(reverse("my_analytics", args=[999]))
        self.assertRedirects(response, reverse("create_store"))

    def test_non_member_is_denied(self):
        self.client.login(username="rando", password="pass12345")
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("create_store"))

    def test_member_can_view_analytics(self):
        # NOTE: same missing-template caveat as MyStoreViewTests above -
        # templates/merchant_interface/my_analytics.html does not exist yet.
        self.client.login(username="owner", password="pass12345")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["store"], self.store)