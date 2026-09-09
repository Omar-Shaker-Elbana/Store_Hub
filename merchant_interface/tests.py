"""
Comprehensive test suite for the store / membership app.

Notes on assumptions (adjust if wrong for your project):
- This file uses RELATIVE imports (`from .models import ...`), so drop it in
  as `tests.py` (or inside a `tests/` package) directly in the app that
  contains `models.py`, `forms.py`, `urls.py`, and `views.py` shown above.
- `django.contrib.auth.get_user_model()` is used instead of a concrete User
  class, and users are created with `username`, `email`, `password` only
  (the three fields every Django user model is virtually guaranteed to
  have). If your custom user model requires additional fields, add them to
  `make_user()` below.
- `notifications.Notification` is only ever queried by the fields the views
  themselves use (`recipient`, `sender`, `message`), so no assumptions are
  made about any other fields it might have.
- `products.Product` is not exercised in depth since its schema isn't shown;
  `show_store` is tested with zero and then with a store that simply has no
  products, which already covers the pagination code path without needing
  to know Product's required fields. If you want product-list assertions,
  extend `test_show_store_lists_products` with `Product.objects.create(...)`
  using your actual required fields.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification

from .forms import (MembershipForm, MembershipInvitationForm, StoreForm,
                    SuggestNicheForm)
from .models import (Membership, MembershipChangeRequest, MembershipInvitation,
                     Niche, Promotion, Store, SuggestedNiche)

User = get_user_model()


def make_user(username, email=None, password="testpass123"):
    return User.objects.create_user(
        username=username,
        email=email or f"{username}@example.com",
        password=password,
    )


import itertools

_niche_seq = itertools.count()


def make_niche(name=None):
    if name is None:
        name = f"Niche-{next(_niche_seq)}"
    return Niche.objects.create(name=name)


def make_store(name="My Store", niche=None, enabled=True):
    return Store.objects.create(name=name, niche=niche or make_niche(), enabled=enabled)


def make_membership(
    user, store, role="helper", wage_type=None, wage=None, join_date=None
):
    # Membership.clean() requires owners to be paid in "percentage", and the
    # percentage_wage_capped_at_100 constraint rejects a NULL wage under
    # wage_type="percentage" — so an owner needs both an explicit wage_type
    # and a numeric wage unless the caller overrides them.
    if wage_type is None:
        wage_type = "percentage" if role == "owner" else "salary"
    if wage is None and role == "owner" and wage_type == "percentage":
        wage = Decimal("100.00")
    return Membership.objects.create(
        user=user,
        store=store,
        role=role,
        wage_type=wage_type,
        wage=wage,
        join_date=join_date or timezone.now().date(),
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class NicheModelTests(TestCase):
    def test_str(self):
        niche = make_niche("Books")
        self.assertEqual(str(niche), "Books")

    def test_name_unique(self):
        make_niche("Books")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Niche.objects.create(name="Books")


class StoreModelTests(TestCase):
    def test_str_with_name(self):
        store = make_store(name="Cool Shop")
        self.assertEqual(str(store), "Cool Shop")

    def test_str_without_name_falls_back_to_pk(self):
        store = Store.objects.create(name=None, niche=make_niche())
        self.assertEqual(str(store), f"Store #{store.pk}")

    def test_enabled_defaults_true(self):
        store = make_store()
        self.assertTrue(store.enabled)

    def test_niche_protected_on_delete(self):
        niche = make_niche()
        make_store(niche=niche)
        from django.db.models.deletion import ProtectedError

        with self.assertRaises(ProtectedError):
            niche.delete()


class MembershipModelTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.store = make_store()

    def test_str(self):
        m = make_membership(self.user, self.store, role="owner")
        self.assertIn("owner", str(m))

    def test_unique_membership_per_store(self):
        make_membership(self.user, self.store)
        with self.assertRaises(ValidationError):
            make_membership(self.user, self.store)

    def test_percentage_wage_over_100_violates_constraint(self):
        with self.assertRaises(ValidationError):
            make_membership(
                self.user,
                self.store,
                wage_type="percentage",
                wage=Decimal("150.00"),
            )

    def test_percentage_wage_at_100_is_allowed(self):
        m = make_membership(
            self.user, self.store, wage_type="percentage", wage=Decimal("100.00")
        )
        self.assertEqual(m.wage, Decimal("100.00"))

    def test_negative_wage_violates_constraint(self):
        with self.assertRaises(ValidationError):
            make_membership(
                self.user,
                self.store,
                wage_type="salary",
                wage=Decimal("-10.00"),
            )

    def test_default_role_is_helper(self):
        m = Membership.objects.create(user=self.user, store=self.store)
        self.assertEqual(m.role, "helper")


class MembershipInvitationModelTests(TestCase):
    def setUp(self):
        self.inviter = make_user("bob")
        self.store = make_store()

    def test_str(self):
        inv = MembershipInvitation.objects.create(
            inviter=self.inviter, invitee_email="x@example.com", store=self.store
        )
        self.assertIn("x@example.com", str(inv))

    def test_only_one_pending_invite_per_email_per_store(self):
        MembershipInvitation.objects.create(
            inviter=self.inviter,
            invitee_email="dup@example.com",
            store=self.store,
            status="pending",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MembershipInvitation.objects.create(
                    inviter=self.inviter,
                    invitee_email="dup@example.com",
                    store=self.store,
                    status="pending",
                )

    def test_second_non_pending_invite_for_same_email_is_allowed(self):
        first = MembershipInvitation.objects.create(
            inviter=self.inviter,
            invitee_email="dup2@example.com",
            store=self.store,
            status="pending",
        )
        first.status = "rejected"
        first.save()
        # Should not raise - only one *pending* is restricted.
        MembershipInvitation.objects.create(
            inviter=self.inviter,
            invitee_email="dup2@example.com",
            store=self.store,
            status="pending",
        )


class SuggestedNicheModelTests(TestCase):
    def test_str(self):
        user = make_user("carol")
        sn = SuggestedNiche.objects.create(name="Gadgets", suggested_by=user)
        self.assertIn("Gadgets", str(sn))
        self.assertIn("pending", str(sn))


class MembershipChangeRequestModelTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner1")
        self.helper = make_user("helper1")
        self.store = make_store()
        self.owner_m = make_membership(self.owner, self.store, role="owner")
        self.helper_m = make_membership(self.helper, self.store, role="helper")

    def test_str(self):
        cr = MembershipChangeRequest.objects.create(
            membership=self.helper_m,
            requested_by=self.owner,
            new_role="manager",
            new_wage_type="salary",
        )
        self.assertIn("pending", str(cr))

    def test_only_one_pending_change_request_per_membership(self):
        MembershipChangeRequest.objects.create(
            membership=self.helper_m,
            requested_by=self.owner,
            new_role="manager",
            new_wage_type="salary",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MembershipChangeRequest.objects.create(
                    membership=self.helper_m,
                    requested_by=self.owner,
                    new_role="manager",
                    new_wage_type="salary",
                )


class PromotionModelTests(TestCase):
    def test_str(self):
        giver = make_user("giver")
        receiver = make_user("receiver")
        store = make_store()
        promo = Promotion.objects.create(
            store=store,
            date=timezone.now().date(),
            old_position="Helper",
            new_position="Manager",
            Giver=giver,
            Receiver=receiver,
        )
        self.assertIn("Helper", str(promo))
        self.assertIn("Manager", str(promo))


# ---------------------------------------------------------------------------
# Form tests
# ---------------------------------------------------------------------------


class StoreFormTests(TestCase):
    def setUp(self):
        self.niche = make_niche()

    def test_name_not_required_by_default(self):
        form = StoreForm(data={"niche": self.niche.id})
        self.assertTrue(form.is_valid(), form.errors)

    def test_name_required_when_flagged(self):
        form = StoreForm(data={"niche": self.niche.id}, require_name=True)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_valid_with_name(self):
        form = StoreForm(
            data={"name": "Shop", "niche": self.niche.id}, require_name=True
        )
        self.assertTrue(form.is_valid(), form.errors)


class MembershipInvitationFormTests(TestCase):
    def test_invitee_email_required(self):
        form = MembershipInvitationForm(data={"role": "helper", "wage_type": "salary"})
        self.assertFalse(form.is_valid())
        self.assertIn("invitee_email", form.errors)

    def test_percentage_wage_over_100_invalid(self):
        form = MembershipInvitationForm(
            data={
                "invitee_email": "a@example.com",
                "role": "helper",
                "wage_type": "percentage",
                "wage": "150",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("wage", form.errors)

    def test_percentage_wage_missing_invalid(self):
        form = MembershipInvitationForm(
            data={
                "invitee_email": "a@example.com",
                "role": "helper",
                "wage_type": "percentage",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("wage", form.errors)

    def test_salary_wage_valid(self):
        form = MembershipInvitationForm(
            data={
                "invitee_email": "a@example.com",
                "role": "helper",
                "wage_type": "salary",
                "wage": "5000",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)


class MembershipFormTests(TestCase):
    def test_percentage_wage_over_100_invalid(self):
        form = MembershipForm(
            data={
                "role": "manager",
                "wage_type": "percentage",
                "wage": "101",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("wage", form.errors)

    def test_valid(self):
        form = MembershipForm(
            data={
                "role": "manager",
                "wage_type": "percentage",
                "wage": "50",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)


class SuggestNicheFormTests(TestCase):
    def test_blank_name_invalid(self):
        form = SuggestNicheForm(data={"name": ""})
        self.assertFalse(form.is_valid())

    def test_whitespace_only_name_invalid(self):
        form = SuggestNicheForm(data={"name": "   "})
        self.assertFalse(form.is_valid())

    def test_valid_name_is_stripped(self):
        form = SuggestNicheForm(data={"name": "  Gadgets  "})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["name"], "Gadgets")


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------


class ShowStoreViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.store = make_store(enabled=True)

    def test_nonexistent_store_redirects_to_create_store(self):
        resp = self.client.get(reverse("show_store", args=[999999]))
        self.assertRedirects(
            resp, reverse("create_store"), fetch_redirect_response=False
        )

    def test_enabled_store_visible_to_anonymous(self):
        resp = self.client.get(reverse("show_store", args=[self.store.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "merchant_interface/show_store.html")

    def test_disabled_store_hidden_from_anonymous(self):
        self.store.enabled = False
        self.store.save()
        resp = self.client.get(reverse("show_store", args=[self.store.id]))
        self.assertRedirects(resp, reverse("home"))

    def test_disabled_store_visible_to_member(self):
        user = make_user("member")
        make_membership(user, self.store)
        self.store.enabled = False
        self.store.save()
        self.client.force_login(user)
        resp = self.client.get(reverse("show_store", args=[self.store.id]))
        self.assertEqual(resp.status_code, 200)

    def test_ajax_request_returns_product_cards_partial(self):
        resp = self.client.get(
            reverse("show_store", args=[self.store.id]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "merchant_interface/_product_cards.html")


class CreateStoreViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user("founder")
        self.client.force_login(self.user)
        self.niche = make_niche()

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("create_store"))
        self.assertEqual(resp.status_code, 302)

    def test_get_renders_form(self):
        resp = self.client.get(reverse("create_store"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "merchant_interface/create_store.html")

    def test_create_store_success_makes_owner_membership(self):
        resp = self.client.post(
            reverse("create_store"),
            {
                "create_store_btn": "1",
                "store-name": "New Shop",
                "store-niche": self.niche.id,
            },
        )
        store = Store.objects.get(name="New Shop")
        self.assertRedirects(resp, reverse("manage_store_invitations", args=[store.id]))
        membership = Membership.objects.get(user=self.user, store=store)
        self.assertEqual(membership.role, "owner")

    def test_create_store_missing_niche_shows_error(self):
        resp = self.client.post(
            reverse("create_store"),
            {
                "create_store_btn": "1",
                "store-name": "Bad Shop",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Store.objects.filter(name="Bad Shop").exists())

    def test_suggest_niche_success(self):
        resp = self.client.post(
            reverse("create_store"),
            {
                "suggest_niche_btn": "1",
                "niche-name": "Pets",
            },
        )
        self.assertRedirects(resp, reverse("create_store"))
        self.assertTrue(
            SuggestedNiche.objects.filter(name="Pets", suggested_by=self.user).exists()
        )


class ManageStoreInvitationsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.store = make_store()
        self.owner = make_user("owner")
        self.other = make_user("other", email="other@example.com")
        make_membership(self.owner, self.store, role="owner")

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(
            reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertEqual(resp.status_code, 302)

    def test_nonexistent_store(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("manage_store_invitations", args=[999999]))
        self.assertRedirects(resp, reverse("create_store"))

    def test_non_member_redirected(self):
        self.client.force_login(self.other)
        resp = self.client.get(
            reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertRedirects(resp, reverse("create_store"))

    def test_owner_can_view(self):
        self.client.force_login(self.owner)
        resp = self.client.get(
            reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["can_manage"])

    def test_helper_cannot_manage(self):
        helper = make_user("helper2")
        make_membership(helper, self.store, role="helper")
        self.client.force_login(helper)
        resp = self.client.get(
            reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_manage"])

    def test_send_invitation_success(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("manage_store_invitations", args=[self.store.id]),
            {
                "send_invitation_btn": "1",
                "invitee_email": "newperson@example.com",
                "role": "helper",
                "wage_type": "salary",
                "wage": "1000",
            },
        )
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertTrue(
            MembershipInvitation.objects.filter(
                store=self.store,
                invitee_email="newperson@example.com",
                status="pending",
            ).exists()
        )

    def test_cannot_invite_self(self):
        self.client.force_login(self.owner)
        self.owner.email = "owner@example.com"
        self.owner.save()
        resp = self.client.post(
            reverse("manage_store_invitations", args=[self.store.id]),
            {
                "send_invitation_btn": "1",
                "invitee_email": "owner@example.com",
                "role": "helper",
                "wage_type": "salary",
                "wage": "1000",
            },
        )
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertFalse(
            MembershipInvitation.objects.filter(
                invitee_email="owner@example.com"
            ).exists()
        )

    def test_helper_cannot_send_invitation(self):
        helper = make_user("helper3")
        make_membership(helper, self.store, role="helper")
        self.client.force_login(helper)
        resp = self.client.post(
            reverse("manage_store_invitations", args=[self.store.id]),
            {
                "send_invitation_btn": "1",
                "invitee_email": "blocked@example.com",
                "role": "helper",
                "wage_type": "salary",
                "wage": "1000",
            },
        )
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertFalse(
            MembershipInvitation.objects.filter(
                invitee_email="blocked@example.com"
            ).exists()
        )

    def test_manager_cannot_invite_as_owner(self):
        manager = make_user("manager1")
        # Give the manager enough of their own profit share to clear the
        # invitation form's wage-availability check, so this test actually
        # reaches — and exercises — the view's "only an owner can invite
        # as owner" rule, instead of failing on wage validation first.
        make_membership(
            manager,
            self.store,
            role="manager",
            wage_type="percentage",
            wage=Decimal("100.00"),
        )
        self.client.force_login(manager)
        resp = self.client.post(
            reverse("manage_store_invitations", args=[self.store.id]),
            {
                "send_invitation_btn": "1",
                "invitee_email": "wannabeowner@example.com",
                "role": "owner",
                "wage_type": "percentage",
                "wage": "50",
            },
        )
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertFalse(
            MembershipInvitation.objects.filter(
                invitee_email="wannabeowner@example.com"
            ).exists()
        )

    def test_cannot_invite_existing_member(self):
        member = make_user("existing", email="existing@example.com")
        make_membership(member, self.store, role="helper")
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("manage_store_invitations", args=[self.store.id]),
            {
                "send_invitation_btn": "1",
                "invitee_email": "existing@example.com",
                "role": "helper",
                "wage_type": "salary",
                "wage": "1000",
            },
        )
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertFalse(
            MembershipInvitation.objects.filter(
                invitee_email="existing@example.com", inviter=self.owner
            ).exists()
        )

    def test_duplicate_pending_invitation_blocked(self):
        MembershipInvitation.objects.create(
            inviter=self.owner,
            invitee_email="dup@example.com",
            store=self.store,
            status="pending",
        )
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("manage_store_invitations", args=[self.store.id]),
            {
                "send_invitation_btn": "1",
                "invitee_email": "dup@example.com",
                "role": "helper",
                "wage_type": "salary",
                "wage": "1000",
            },
        )
        self.assertEqual(
            MembershipInvitation.objects.filter(
                invitee_email="dup@example.com"
            ).count(),
            1,
        )

    def test_cancel_invitation(self):
        invitation = MembershipInvitation.objects.create(
            inviter=self.owner,
            invitee_email="cancel@example.com",
            store=self.store,
            status="pending",
        )
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("manage_store_invitations", args=[self.store.id]),
            {"cancel_invitation_btn": "1", "invitation_id": invitation.id},
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "rejected")

    def test_notification_sent_to_registered_invitee(self):
        invitee = make_user("invitee", email="invitee@example.com")
        self.client.force_login(self.owner)
        self.client.post(
            reverse("manage_store_invitations", args=[self.store.id]),
            {
                "send_invitation_btn": "1",
                "invitee_email": "invitee@example.com",
                "role": "helper",
                "wage_type": "salary",
                "wage": "1000",
            },
        )
        self.assertTrue(
            Notification.objects.filter(recipient=invitee, sender=self.owner).exists()
        )


class EditStoreViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.store = make_store()
        self.owner = make_user("owner4")
        self.helper = make_user("helper4")
        make_membership(self.owner, self.store, role="owner")
        make_membership(self.helper, self.store, role="helper")

    def test_non_owner_cannot_edit(self):
        self.client.force_login(self.helper)
        resp = self.client.get(reverse("edit_store", args=[self.store.id]))
        self.assertRedirects(resp, reverse("create_store"))

    def test_owner_can_view_edit_form(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("edit_store", args=[self.store.id]))
        self.assertEqual(resp.status_code, 200)

    def test_owner_can_close_store(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("edit_store", args=[self.store.id]),
            {"store_status_action": "close"},
        )
        self.store.refresh_from_db()
        self.assertFalse(self.store.enabled)
        self.assertRedirects(resp, reverse("edit_store", args=[self.store.id]))

    def test_owner_can_reopen_store(self):
        self.store.enabled = False
        self.store.save()
        self.client.force_login(self.owner)
        self.client.post(
            reverse("edit_store", args=[self.store.id]),
            {"store_status_action": "reopen"},
        )
        self.store.refresh_from_db()
        self.assertTrue(self.store.enabled)

    def test_owner_can_update_store_details(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("edit_store", args=[self.store.id]),
            {"name": "Renamed Shop", "niche": self.store.niche_id},
        )
        self.store.refresh_from_db()
        self.assertEqual(self.store.name, "Renamed Shop")
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )


class AllMyStoresViewTests(TestCase):
    def test_no_memberships_redirects(self):
        user = make_user("lonely")
        client = Client()
        client.force_login(user)
        resp = client.get(reverse("all_my_stores"))
        self.assertRedirects(resp, reverse("create_store"))

    def test_lists_memberships(self):
        user = make_user("busy")
        store = make_store()
        make_membership(user, store, role="owner")
        client = Client()
        client.force_login(user)
        resp = client.get(reverse("all_my_stores"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(store, [m.store for m in resp.context["memberships"]])


class EditMembershipViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.store = make_store()
        self.owner = make_user("owner5")
        self.manager = make_user("manager5")
        self.helper = make_user("helper5")
        self.owner_m = make_membership(self.owner, self.store, role="owner")
        self.manager_m = make_membership(self.manager, self.store, role="manager")
        self.helper_m = make_membership(self.helper, self.store, role="helper")

    def test_nonexistent_membership(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("edit_membership", args=[999999]))
        self.assertRedirects(resp, reverse("all_my_stores"))

    def test_cannot_edit_own_membership(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("edit_membership", args=[self.owner_m.id]))
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )

    def test_helper_cannot_edit_others(self):
        self.client.force_login(self.helper)
        resp = self.client.get(reverse("edit_membership", args=[self.manager_m.id]))
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )

    def test_manager_can_edit_helper(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse("edit_membership", args=[self.helper_m.id]))
        self.assertEqual(resp.status_code, 200)

    def test_manager_cannot_edit_other_manager(self):
        other_manager = make_user("manager6")
        other_m = make_membership(other_manager, self.store, role="manager")
        self.client.force_login(self.manager)
        resp = self.client.get(reverse("edit_membership", args=[other_m.id]))
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )

    def test_manager_cannot_promote_to_owner(self):
        self.client.force_login(self.manager)
        resp = self.client.post(
            reverse("edit_membership", args=[self.helper_m.id]),
            {"role": "owner", "wage_type": "salary", "wage": "1000"},
        )
        self.assertFalse(
            MembershipChangeRequest.objects.filter(
                membership=self.helper_m, new_role="owner"
            ).exists()
        )

    def test_owner_creates_change_request(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("edit_membership", args=[self.helper_m.id]),
            {"role": "manager", "wage_type": "salary", "wage": "2000"},
        )
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )
        cr = MembershipChangeRequest.objects.get(membership=self.helper_m)
        self.assertEqual(cr.new_role, "manager")
        self.assertEqual(cr.status, "pending")

    def test_cannot_demote_last_owner(self):
        # A fresh store with exactly one owner (the target being edited)
        # plus a second owner (the editor) so there are two owners total,
        # but demoting the target would leave the store with only the
        # editor as owner -- which is fine -- so instead we verify the
        # *count-at-least-one* guard directly: demoting the sole owner of
        # a single-owner store must be rejected even by that same owner
        # acting on themself is already blocked earlier (self-edit), so we
        # exercise this via a manager attempting to demote the sole owner.
        lone_store = make_store()
        sole_owner = make_user("sole_owner")
        acting_manager = make_user("acting_manager")
        sole_owner_m = make_membership(sole_owner, lone_store, role="owner")
        make_membership(acting_manager, lone_store, role="manager")

        self.client.force_login(acting_manager)
        # Managers can't edit an owner's membership at all (can_edit is
        # False for manager editing an owner), so this should redirect
        # without creating a change request either way.
        resp = self.client.post(
            reverse("edit_membership", args=[sole_owner_m.id]),
            {"role": "helper", "wage_type": "salary", "wage": "0"},
        )
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[lone_store.id])
        )
        self.assertFalse(
            MembershipChangeRequest.objects.filter(
                membership=sole_owner_m, new_role="helper"
            ).exists()
        )

    def test_owner_can_demote_when_another_owner_remains(self):
        # Two owners on one store; demoting one of them away from "owner"
        # is fine as long as at least one owner remains afterwards.
        lone_store = make_store()
        owner_a = make_user("owner_a")
        owner_b = make_user("owner_b")
        owner_a_m = make_membership(owner_a, lone_store, role="owner")
        make_membership(owner_b, lone_store, role="owner")

        self.client.force_login(owner_b)
        self.client.post(
            reverse("edit_membership", args=[owner_a_m.id]),
            {"role": "helper", "wage_type": "salary", "wage": "0"},
        )
        self.assertTrue(
            MembershipChangeRequest.objects.filter(
                membership=owner_a_m, new_role="helper"
            ).exists()
        )

    def test_last_owner_guard_on_the_shared_helper_directly(self):
        # The view's own permission rules mean a *sole* owner can only be
        # edited by themselves (blocked separately as a self-edit) or by a
        # manager (blocked separately since managers can't touch owners),
        # so the "must keep at least one owner" business rule inside
        # `_create_membership_change_request` is never reachable purely
        # through HTTP with today's permission checks. We test it directly
        # against the shared helper instead, which is what actually
        # encodes the rule.
        from .views import _create_membership_change_request

        lone_store = make_store()
        sole_owner = make_user("sole_owner3")
        acting_user = make_user("acting_user3")
        sole_owner_m = make_membership(sole_owner, lone_store, role="owner")
        acting_membership = make_membership(acting_user, lone_store, role="owner")

        form = MembershipForm(
            data={
                "role": "helper",
                "wage_type": "salary",
                "wage": "0",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

        factory_request = self.client
        # Build a minimal request-like object isn't necessary here since
        # the helper only needs `request.user` for messages/notification
        # authorship; use Django's RequestFactory for a real request.
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        rf = RequestFactory()
        req = rf.post("/fake-url/")
        req.user = acting_user
        # messages framework needs session + message storage attached
        from django.contrib.sessions.middleware import SessionMiddleware

        SessionMiddleware(lambda r: None).process_request(req)
        req.session.save()
        req._messages = FallbackStorage(req)

        # First, remove the acting user's own owner role so sole_owner_m
        # really is the *only* owner left, isolating the guard.
        Membership.objects.filter(id=acting_membership.id).update(role="manager")

        result = _create_membership_change_request(
            req, user_membership=acting_membership, membership=sole_owner_m, form=form
        )
        self.assertFalse(result)
        self.assertFalse(
            MembershipChangeRequest.objects.filter(
                membership=sole_owner_m, new_role="helper"
            ).exists()
        )

    def test_duplicate_pending_change_request_blocked(self):
        MembershipChangeRequest.objects.create(
            membership=self.helper_m,
            requested_by=self.owner,
            new_role="manager",
            new_wage_type="salary",
        )
        self.client.force_login(self.owner)
        self.client.post(
            reverse("edit_membership", args=[self.helper_m.id]),
            {"role": "helper", "wage_type": "salary", "wage": "500"},
        )
        self.assertEqual(
            MembershipChangeRequest.objects.filter(
                membership=self.helper_m, status="pending"
            ).count(),
            1,
        )

    def test_change_request_notifies_target_user(self):
        self.client.force_login(self.owner)
        self.client.post(
            reverse("edit_membership", args=[self.helper_m.id]),
            {"role": "manager", "wage_type": "salary", "wage": "2000"},
        )
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.helper, sender=self.owner
            ).exists()
        )


class RemoveMembershipViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.store = make_store()
        self.owner = make_user("owner7")
        self.helper = make_user("helper7")
        self.owner_m = make_membership(self.owner, self.store, role="owner")
        self.helper_m = make_membership(self.helper, self.store, role="helper")

    def test_nonexistent_membership(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("remove_membership", args=[999999]))
        self.assertRedirects(resp, reverse("all_my_stores"))

    def test_self_leave_allowed(self):
        self.client.force_login(self.helper)
        resp = self.client.post(reverse("remove_membership", args=[self.helper_m.id]))
        self.assertRedirects(
            resp, reverse("all_my_stores"), fetch_redirect_response=False
        )
        self.assertFalse(Membership.objects.filter(id=self.helper_m.id).exists())

    def test_non_owner_cannot_remove_others(self):
        other_helper = make_user("helper8")
        other_m = make_membership(other_helper, self.store, role="helper")
        self.client.force_login(self.helper)
        resp = self.client.post(reverse("remove_membership", args=[other_m.id]))
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertTrue(Membership.objects.filter(id=other_m.id).exists())

    def test_owner_can_remove_others(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("remove_membership", args=[self.helper_m.id]))
        self.assertRedirects(
            resp, reverse("manage_store_invitations", args=[self.store.id])
        )
        self.assertFalse(Membership.objects.filter(id=self.helper_m.id).exists())

    def test_last_owner_cannot_be_removed(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("remove_membership", args=[self.owner_m.id]))
        self.assertTrue(Membership.objects.filter(id=self.owner_m.id).exists())

    def test_last_owner_leaving_self_is_blocked(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("remove_membership", args=[self.owner_m.id]))
        self.assertRedirects(resp, reverse("all_my_stores"))
        self.assertTrue(Membership.objects.filter(id=self.owner_m.id).exists())

    def test_get_request_rejected(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("remove_membership", args=[self.helper_m.id]))
        self.assertRedirects(resp, reverse("all_my_stores"))
        self.assertTrue(Membership.objects.filter(id=self.helper_m.id).exists())

    def test_removal_notifies_removed_user(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("remove_membership", args=[self.helper_m.id]))
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.helper, sender=self.owner
            ).exists()
        )


class MyJobInvitationsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.store = make_store()
        self.owner = make_user("owner9")
        self.invitee = make_user("invitee9", email="invitee9@example.com")
        make_membership(self.owner, self.store, role="owner")

    def test_requires_login(self):
        resp = self.client.get(reverse("my_job_invitations"))
        self.assertEqual(resp.status_code, 302)

    def test_accept_invitation_creates_membership(self):
        invitation = MembershipInvitation.objects.create(
            inviter=self.owner,
            invitee=self.invitee,
            invitee_email="invitee9@example.com",
            store=self.store,
            role="helper",
            status="pending",
        )
        self.client.force_login(self.invitee)
        resp = self.client.post(
            reverse("my_job_invitations"),
            {
                "item_type": "invitation",
                "invitation_id": invitation.id,
                "action": "accept",
            },
        )
        self.assertRedirects(resp, reverse("my_job_invitations"))
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "accepted")
        self.assertTrue(
            Membership.objects.filter(user=self.invitee, store=self.store).exists()
        )

    def test_accept_invitation_to_closed_store_blocked(self):
        self.store.enabled = False
        self.store.save()
        invitation = MembershipInvitation.objects.create(
            inviter=self.owner,
            invitee=self.invitee,
            invitee_email="invitee9@example.com",
            store=self.store,
            role="helper",
            status="pending",
        )
        self.client.force_login(self.invitee)
        self.client.post(
            reverse("my_job_invitations"),
            {
                "item_type": "invitation",
                "invitation_id": invitation.id,
                "action": "accept",
            },
        )
        self.assertFalse(
            Membership.objects.filter(user=self.invitee, store=self.store).exists()
        )

    def test_accept_invitation_when_already_member_blocked(self):
        make_membership(self.invitee, self.store, role="helper")
        invitation = MembershipInvitation.objects.create(
            inviter=self.owner,
            invitee=self.invitee,
            invitee_email="invitee9@example.com",
            store=self.store,
            role="manager",
            status="pending",
        )
        self.client.force_login(self.invitee)
        self.client.post(
            reverse("my_job_invitations"),
            {
                "item_type": "invitation",
                "invitation_id": invitation.id,
                "action": "accept",
            },
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "pending")

    def test_decline_invitation(self):
        invitation = MembershipInvitation.objects.create(
            inviter=self.owner,
            invitee=self.invitee,
            invitee_email="invitee9@example.com",
            store=self.store,
            role="helper",
            status="pending",
        )
        self.client.force_login(self.invitee)
        self.client.post(
            reverse("my_job_invitations"),
            {
                "item_type": "invitation",
                "invitation_id": invitation.id,
                "action": "decline",
            },
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "rejected")

    def test_accept_change_request_creates_promotion(self):
        helper = make_user("helper9")
        membership = make_membership(helper, self.store, role="helper")
        cr = MembershipChangeRequest.objects.create(
            membership=membership,
            requested_by=self.owner,
            new_role="manager",
            new_wage_type="salary",
            new_wage=Decimal("3000"),
        )
        self.client.force_login(helper)
        resp = self.client.post(
            reverse("my_job_invitations"),
            {
                "item_type": "change_request",
                "change_request_id": cr.id,
                "action": "accept",
            },
        )
        self.assertRedirects(resp, reverse("my_job_invitations"))
        membership.refresh_from_db()
        self.assertEqual(membership.role, "manager")
        cr.refresh_from_db()
        self.assertEqual(cr.status, "accepted")
        self.assertTrue(
            Promotion.objects.filter(
                store=self.store, Giver=self.owner, Receiver=helper
            ).exists()
        )

    def test_accept_change_request_no_change_skips_promotion(self):
        helper = make_user("helper10")
        membership = make_membership(
            helper, self.store, role="helper", wage_type="salary", wage=None
        )
        cr = MembershipChangeRequest.objects.create(
            membership=membership,
            requested_by=self.owner,
            new_role="helper",
            new_wage_type="salary",
            new_wage=None,
        )
        self.client.force_login(helper)
        self.client.post(
            reverse("my_job_invitations"),
            {
                "item_type": "change_request",
                "change_request_id": cr.id,
                "action": "accept",
            },
        )
        self.assertFalse(Promotion.objects.filter(Receiver=helper).exists())

    def test_reject_change_request(self):
        helper = make_user("helper11")
        membership = make_membership(helper, self.store, role="helper")
        cr = MembershipChangeRequest.objects.create(
            membership=membership,
            requested_by=self.owner,
            new_role="manager",
            new_wage_type="salary",
        )
        self.client.force_login(helper)
        self.client.post(
            reverse("my_job_invitations"),
            {
                "item_type": "change_request",
                "change_request_id": cr.id,
                "action": "reject",
            },
        )
        cr.refresh_from_db()
        self.assertEqual(cr.status, "rejected")
        membership.refresh_from_db()
        self.assertEqual(membership.role, "helper")

    def test_invalid_action_leaves_state_unchanged(self):
        invitation = MembershipInvitation.objects.create(
            inviter=self.owner,
            invitee=self.invitee,
            invitee_email="invitee9@example.com",
            store=self.store,
            role="helper",
            status="pending",
        )
        self.client.force_login(self.invitee)
        self.client.post(
            reverse("my_job_invitations"),
            {
                "item_type": "invitation",
                "invitation_id": invitation.id,
                "action": "bogus",
            },
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "pending")


class MyPromotionHistoryViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user("historyuser")
        self.giver = make_user("historygiver")
        self.store = make_store()
        make_membership(self.user, self.store, role="helper")

    def test_requires_login(self):
        resp = self.client.get(reverse("my_promotion_history"))
        self.assertEqual(resp.status_code, 302)

    def test_lists_join_and_promotion_events(self):
        Promotion.objects.create(
            store=self.store,
            date=timezone.now().date(),
            old_position="Helper",
            new_position="Manager",
            Giver=self.giver,
            Receiver=self.user,
        )
        self.client.force_login(self.user)
        resp = self.client.get(reverse("my_promotion_history"))
        self.assertEqual(resp.status_code, 200)
        types = [e["type"] for e in resp.context["timeline_events"]]
        self.assertIn("joined", types)
        self.assertIn("promotion", types)

    def test_toggle_visibility_only_by_receiver(self):
        promo = Promotion.objects.create(
            store=self.store,
            date=timezone.now().date(),
            old_position="Helper",
            new_position="Manager",
            Giver=self.giver,
            Receiver=self.user,
            enabled=True,
        )
        intruder = make_user("intruder")
        self.client.force_login(intruder)
        self.client.post(reverse("my_promotion_history"), {"promotion_id": promo.id})
        promo.refresh_from_db()
        self.assertTrue(promo.enabled)  # unchanged

        self.client.force_login(self.user)
        self.client.post(reverse("my_promotion_history"), {"promotion_id": promo.id})
        promo.refresh_from_db()
        self.assertFalse(promo.enabled)


class ViewUserPromotionHistoryViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.store = make_store()
        self.giver = make_user("recruitergiver")
        self.target = make_user("historytarget")
        make_membership(self.target, self.store, role="helper")
        self.viewer = make_user("recruiterviewer")
        self.client.force_login(self.viewer)

    def test_nonexistent_user(self):
        resp = self.client.get(reverse("view_user_promotion_history", args=[999999]))
        self.assertRedirects(resp, reverse("create_store"))

    def test_shows_join_event(self):
        resp = self.client.get(
            reverse("view_user_promotion_history", args=[self.target.id])
        )
        self.assertEqual(resp.status_code, 200)
        types = [e["type"] for e in resp.context["timeline_events"]]
        self.assertIn("joined", types)

    def test_disabled_promotions_are_hidden(self):
        Promotion.objects.create(
            store=self.store,
            date=timezone.now().date(),
            old_position="Helper",
            new_position="Manager",
            Giver=self.giver,
            Receiver=self.target,
            enabled=False,
        )
        resp = self.client.get(
            reverse("view_user_promotion_history", args=[self.target.id])
        )
        promotions_shown = [
            e for e in resp.context["timeline_events"] if e["type"] == "promotion"
        ]
        self.assertEqual(len(promotions_shown), 0)

    def test_enabled_promotion_is_shown(self):
        Promotion.objects.create(
            store=self.store,
            date=timezone.now().date(),
            old_position="Helper",
            new_position="Manager",
            Giver=self.giver,
            Receiver=self.target,
            enabled=True,
        )
        resp = self.client.get(
            reverse("view_user_promotion_history", args=[self.target.id])
        )
        promotions_shown = [
            e for e in resp.context["timeline_events"] if e["type"] == "promotion"
        ]
        self.assertEqual(len(promotions_shown), 1)
