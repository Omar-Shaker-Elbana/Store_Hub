"""
Comprehensive test suite for the fulfillment app (in-store order claiming,
finishing/releasing a claim, and the percentage-wage wallet system).

Notes on assumptions (adjust if wrong for your project):
- Uses django.contrib.auth.get_user_model() with users created via
  username/email/password only, matching the rest of this project's tests.
- Membership rules are mirrored from merchant_interface: owners must be
  wage_type="percentage", and a percentage wage is capped at 100 by a DB
  CheckConstraint. make_membership() below reproduces the same defaulting
  logic already used in merchant_interface/tests.py so fixtures never
  violate those constraints.
- Assumes fulfillment/management/commands/release_expired_claims.py and
  fulfillment/templatetags/fulfillment_extras.py (the `mul` filter used by
  claim_detail.html) already exist. The view tests here render real
  templates end-to-end, and claim_detail.html raises a TemplateSyntaxError
  without that filter registered.
- SQLite (this project's default test database) does not honor
  select_for_update() row locks. The "only one active claim per
  StoreOrder" guarantee is therefore tested two ways: as an integration
  behavior through the service (sequential calls), and directly against
  the partial unique constraint at the DB layer — not via real threads,
  since SQLite can't meaningfully exercise a race condition anyway.
"""

import itertools
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from merchant_interface.models import Membership, Niche, Store
from notifications.models import Notification
from orders.models import Order, OrderItem, StoreOrder
from products.models import Category, Product

from . import services
from .models import PER_ITEM_BASE_RATE, OrderClaim, WalletTransaction

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_user_seq = itertools.count()
_niche_seq = itertools.count()
_category_seq = itertools.count()


def make_user(username=None, email=None, password="testpass123"):
    if username is None:
        username = f"user-{next(_user_seq)}"
    return User.objects.create_user(
        username=username,
        email=email or f"{username}@example.com",
        password=password,
    )


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
    # percentage_wage_capped_at_100 constraint rejects wage > 100 under
    # wage_type="percentage" — mirrors merchant_interface/tests.py exactly.
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


def make_category(name=None):
    if name is None:
        name = f"Category-{next(_category_seq)}"
    return Category.objects.create(name=name)


def make_product(store, category=None, name="Widget", selling_price=Decimal("20.00")):
    return Product.objects.create(
        store=store,
        category=category or make_category(),
        name=name,
        selling_price=selling_price,
    )


def make_order(user=None, shipping_address="123 Test St", payment_type="cash"):
    return Order.objects.create(
        user=user,
        shipping_address=shipping_address,
        payment_type=payment_type,
        status="Pending",
    )


def make_store_order(order, store, status="Pending", subtotal=Decimal("0.00")):
    return StoreOrder.objects.create(
        order=order, store=store, status=status, subtotal=subtotal
    )


def make_order_item(store_order, product=None, quantity=1, price_at_purchase=None):
    product = product or make_product(store_order.store)
    if price_at_purchase is None:
        price_at_purchase = product.selling_price
    return OrderItem.objects.create(
        store_order=store_order,
        product=product,
        quantity=quantity,
        price_at_purchase=price_at_purchase,
    )


def make_claim(store_order, membership, claimant=None, status="claimed", deadline=None):
    return OrderClaim.objects.create(
        store_order=store_order,
        membership=membership,
        claimant=claimant or membership.user,
        status=status,
        deadline=deadline or (timezone.now() + timedelta(hours=24)),
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class OrderClaimModelTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(self.helper, self.store, role="helper")
        self.order = make_order()
        self.store_order = make_store_order(self.order, self.store, status="Pending")

    def test_is_expired_true_when_claimed_and_past_deadline(self):
        claim = make_claim(
            self.store_order, self.membership, deadline=timezone.now() - timedelta(hours=1)
        )
        self.assertTrue(claim.is_expired)

    def test_is_expired_false_when_claimed_and_before_deadline(self):
        claim = make_claim(
            self.store_order, self.membership, deadline=timezone.now() + timedelta(hours=1)
        )
        self.assertFalse(claim.is_expired)

    def test_is_expired_false_when_not_claimed_even_past_deadline(self):
        claim = make_claim(
            self.store_order,
            self.membership,
            status="finished",
            deadline=timezone.now() - timedelta(hours=1),
        )
        self.assertFalse(claim.is_expired)

    def test_time_left_positive_while_claimed(self):
        claim = make_claim(
            self.store_order, self.membership, deadline=timezone.now() + timedelta(hours=5)
        )
        self.assertGreater(claim.time_left, timedelta(hours=4))
        self.assertLessEqual(claim.time_left, timedelta(hours=5))

    def test_time_left_zero_when_not_claimed(self):
        claim = make_claim(self.store_order, self.membership, status="released")
        self.assertEqual(claim.time_left, timedelta(0))

    def test_time_left_never_negative_when_expired(self):
        claim = make_claim(
            self.store_order, self.membership, deadline=timezone.now() - timedelta(days=2)
        )
        self.assertEqual(claim.time_left, timedelta(0))

    def test_clean_rejects_membership_from_a_different_store(self):
        other_store = make_store(niche=self.niche, name="Other Store")
        other_user = make_user("otherhelper")
        other_membership = make_membership(other_user, other_store, role="helper")
        claim = OrderClaim(
            store_order=self.store_order,
            membership=other_membership,
            claimant=other_user,
            deadline=timezone.now() + timedelta(hours=24),
        )
        with self.assertRaises(ValidationError):
            claim.clean()

    def test_only_one_claimed_claim_allowed_per_store_order(self):
        make_claim(self.store_order, self.membership, status="claimed")
        second_helper = make_user("helper2")
        second_membership = make_membership(second_helper, self.store, role="helper")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_claim(
                    self.store_order,
                    second_membership,
                    claimant=second_helper,
                    status="claimed",
                )

    def test_multiple_non_claimed_claims_allowed_per_store_order(self):
        make_claim(self.store_order, self.membership, status="finished")
        second_helper = make_user("helper2")
        second_membership = make_membership(second_helper, self.store, role="helper")
        make_claim(self.store_order, second_membership, claimant=second_helper, status="released")
        self.assertEqual(OrderClaim.objects.filter(store_order=self.store_order).count(), 2)


class WalletTransactionModelTests(TestCase):
    def test_ordered_newest_first(self):
        store = make_store()
        user = make_user("helper1")
        membership = make_membership(
            user, store, role="helper", wage_type="percentage", wage=Decimal("10.00")
        )
        first = WalletTransaction.objects.create(
            membership=membership, amount=Decimal("5.00"), description="first"
        )
        second = WalletTransaction.objects.create(
            membership=membership, amount=Decimal("7.00"), description="second"
        )
        self.assertEqual(list(WalletTransaction.objects.filter(membership=membership)), [second, first])


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

class ReleaseExpiredClaimsServiceTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(self.helper, self.store, role="helper")
        self.order = make_order()
        self.store_order = make_store_order(self.order, self.store, status="Processing")

    def test_expires_claims_past_their_deadline(self):
        claim = make_claim(
            self.store_order, self.membership, deadline=timezone.now() - timedelta(minutes=1)
        )
        services.release_expired_claims()
        claim.refresh_from_db()
        self.assertEqual(claim.status, "expired")

    def test_leaves_claims_before_deadline_untouched(self):
        claim = make_claim(
            self.store_order, self.membership, deadline=timezone.now() + timedelta(hours=1)
        )
        services.release_expired_claims()
        claim.refresh_from_db()
        self.assertEqual(claim.status, "claimed")

    def test_leaves_non_claimed_statuses_untouched_even_past_deadline(self):
        claim = make_claim(
            self.store_order,
            self.membership,
            status="finished",
            deadline=timezone.now() - timedelta(hours=1),
        )
        services.release_expired_claims()
        claim.refresh_from_db()
        self.assertEqual(claim.status, "finished")

    def test_store_filter_only_expires_claims_for_that_store(self):
        other_store = make_store(niche=self.niche, name="Other Store")
        other_order = make_order()
        other_store_order = make_store_order(other_order, other_store, status="Processing")
        other_helper = make_user("otherhelper")
        other_membership = make_membership(other_helper, other_store, role="helper")

        this_claim = make_claim(
            self.store_order, self.membership, deadline=timezone.now() - timedelta(minutes=1)
        )
        other_claim = make_claim(
            other_store_order,
            other_membership,
            claimant=other_helper,
            deadline=timezone.now() - timedelta(minutes=1),
        )

        services.release_expired_claims(store=self.store)

        this_claim.refresh_from_db()
        other_claim.refresh_from_db()
        self.assertEqual(this_claim.status, "expired")
        self.assertEqual(other_claim.status, "claimed")


class EligibleMembershipServiceTests(TestCase):
    def setUp(self):
        self.store = make_store()

    def test_returns_none_for_anonymous_user(self):
        self.assertIsNone(services.eligible_membership(AnonymousUser(), self.store))

    def test_returns_none_when_no_membership(self):
        user = make_user("nobody")
        self.assertIsNone(services.eligible_membership(user, self.store))

    def test_returns_none_for_owner_role(self):
        owner = make_user("owner1")
        make_membership(owner, self.store, role="owner")
        self.assertIsNone(services.eligible_membership(owner, self.store))

    def test_returns_membership_for_helper(self):
        helper = make_user("helper1")
        membership = make_membership(helper, self.store, role="helper")
        self.assertEqual(services.eligible_membership(helper, self.store), membership)

    def test_returns_membership_for_manager(self):
        manager = make_user("manager1")
        membership = make_membership(manager, self.store, role="manager", wage_type="salary")
        self.assertEqual(services.eligible_membership(manager, self.store), membership)


class ClaimStoreOrderServiceTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(self.helper, self.store, role="helper")
        self.customer = make_user("customer1")
        self.order = make_order(user=self.customer)
        self.store_order = make_store_order(self.order, self.store, status="Pending")

    def test_success_creates_claim_with_correct_fields(self):
        claim, error = services.claim_store_order(
            store_order_id=self.store_order.id, store=self.store, user=self.helper
        )
        self.assertIsNone(error)
        self.assertEqual(claim.claimant, self.helper)
        self.assertEqual(claim.membership, self.membership)
        self.assertEqual(claim.status, "claimed")

    def test_success_sets_a_24_hour_deadline(self):
        before = timezone.now()
        claim, _ = services.claim_store_order(
            store_order_id=self.store_order.id, store=self.store, user=self.helper
        )
        self.assertAlmostEqual(claim.deadline, before + timedelta(hours=24), delta=timedelta(seconds=5))

    def test_success_flips_pending_store_order_to_processing(self):
        services.claim_store_order(store_order_id=self.store_order.id, store=self.store, user=self.helper)
        self.store_order.refresh_from_db()
        self.assertEqual(self.store_order.status, "Processing")

    def test_success_notifies_the_customer(self):
        services.claim_store_order(store_order_id=self.store_order.id, store=self.store, user=self.helper)
        self.assertEqual(Notification.objects.filter(recipient=self.customer).count(), 1)

    def test_success_skips_notification_for_guest_checkout(self):
        guest_order = make_order(user=None)
        guest_store_order = make_store_order(guest_order, self.store, status="Pending")
        claim, error = services.claim_store_order(
            store_order_id=guest_store_order.id, store=self.store, user=self.helper
        )
        self.assertIsNone(error)
        self.assertEqual(Notification.objects.count(), 0)

    def test_reclaiming_an_already_processing_order_does_not_duplicate_notification(self):
        services.claim_store_order(store_order_id=self.store_order.id, store=self.store, user=self.helper)
        self.assertEqual(Notification.objects.count(), 1)

        first_claim = OrderClaim.objects.get(store_order=self.store_order)
        services.release_claim(claim_id=first_claim.id, user=self.helper)

        second_helper = make_user("helper2")
        make_membership(second_helper, self.store, role="helper")
        services.claim_store_order(store_order_id=self.store_order.id, store=self.store, user=second_helper)

        self.store_order.refresh_from_db()
        self.assertEqual(self.store_order.status, "Processing")
        self.assertEqual(Notification.objects.count(), 1)

    def test_error_when_user_is_not_eligible(self):
        outsider = make_user("outsider")
        claim, error = services.claim_store_order(
            store_order_id=self.store_order.id, store=self.store, user=outsider
        )
        self.assertIsNone(claim)
        self.assertIn("permission", error)

    def test_error_when_store_order_does_not_exist(self):
        claim, error = services.claim_store_order(store_order_id=999999, store=self.store, user=self.helper)
        self.assertIsNone(claim)
        self.assertEqual(error, "Order not found.")

    def test_error_when_store_order_belongs_to_a_different_store(self):
        other_store = make_store(niche=self.niche, name="Other Store")
        other_store_helper = make_user("otherstorehelper")
        make_membership(other_store_helper, other_store, role="helper")
        claim, error = services.claim_store_order(
            store_order_id=self.store_order.id, store=other_store, user=other_store_helper
        )
        self.assertIsNone(claim)
        self.assertEqual(error, "Order not found.")

    def test_error_when_store_order_status_is_not_claimable(self):
        self.store_order.status = "Shipped"
        self.store_order.save(update_fields=["status"])
        claim, error = services.claim_store_order(
            store_order_id=self.store_order.id, store=self.store, user=self.helper
        )
        self.assertIsNone(claim)
        self.assertEqual(error, "This order is no longer available to claim.")

    def test_error_when_already_actively_claimed(self):
        services.claim_store_order(store_order_id=self.store_order.id, store=self.store, user=self.helper)
        second_helper = make_user("helper2")
        make_membership(second_helper, self.store, role="helper")
        claim, error = services.claim_store_order(
            store_order_id=self.store_order.id, store=self.store, user=second_helper
        )
        self.assertIsNone(claim)
        self.assertEqual(error, "Someone else just claimed this order.")

    def test_expired_claim_is_released_before_claim_attempt_so_reclaiming_succeeds(self):
        stale_claim = make_claim(
            self.store_order, self.membership, deadline=timezone.now() - timedelta(hours=1)
        )
        second_helper = make_user("helper2")
        make_membership(second_helper, self.store, role="helper")
        claim, error = services.claim_store_order(
            store_order_id=self.store_order.id, store=self.store, user=second_helper
        )
        self.assertIsNone(error)
        self.assertIsNotNone(claim)
        stale_claim.refresh_from_db()
        self.assertEqual(stale_claim.status, "expired")


class FinishClaimServiceTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.customer = make_user("customer1")
        self.order = make_order(user=self.customer)
        self.store_order = make_store_order(self.order, self.store, status="Processing")
        make_order_item(self.store_order, quantity=3)
        make_order_item(self.store_order, quantity=2)  # 5 items total

    def _make_claimed(self, wage_type="percentage", wage=Decimal("10.00")):
        membership = make_membership(
            self.helper, self.store, role="helper", wage_type=wage_type, wage=wage
        )
        claim = make_claim(self.store_order, membership, claimant=self.helper)
        return claim, membership

    def test_success_marks_finished_and_tallies_items(self):
        claim, _ = self._make_claimed()
        result, error = services.finish_claim(claim_id=claim.id, user=self.helper)
        self.assertIsNone(error)
        self.assertEqual(result.status, "finished")
        self.assertIsNotNone(result.finished_at)
        self.assertEqual(result.items_processed, 5)

    def test_success_credits_wallet_for_percentage_wage(self):
        claim, membership = self._make_claimed(wage_type="percentage", wage=Decimal("10.00"))
        result, error = services.finish_claim(claim_id=claim.id, user=self.helper)
        self.assertIsNone(error)

        expected = (Decimal(5) * PER_ITEM_BASE_RATE * Decimal("10.00") / Decimal("100")).quantize(
            Decimal("0.01")
        )
        self.assertEqual(result.wage_earned, expected)

        membership.refresh_from_db()
        self.assertEqual(membership.wallet_balance, expected)

        txn = WalletTransaction.objects.get(membership=membership)
        self.assertEqual(txn.amount, expected)
        self.assertEqual(txn.claim, result)
        self.assertIn(str(self.store_order.order_id), txn.description)

    def test_salary_wage_does_not_credit_wallet(self):
        claim, membership = self._make_claimed(wage_type="salary", wage=None)
        result, error = services.finish_claim(claim_id=claim.id, user=self.helper)
        self.assertIsNone(error)
        self.assertIsNone(result.wage_earned)
        membership.refresh_from_db()
        self.assertEqual(membership.wallet_balance, Decimal("0.00"))
        self.assertEqual(WalletTransaction.objects.filter(membership=membership).count(), 0)

    def test_success_ships_a_processing_store_order(self):
        claim, _ = self._make_claimed()
        services.finish_claim(claim_id=claim.id, user=self.helper)
        self.store_order.refresh_from_db()
        self.assertEqual(self.store_order.status, "Shipped")

    def test_success_notifies_the_customer(self):
        claim, _ = self._make_claimed()
        services.finish_claim(claim_id=claim.id, user=self.helper)
        self.assertEqual(Notification.objects.filter(recipient=self.customer).count(), 1)

    def test_error_when_claim_does_not_exist(self):
        result, error = services.finish_claim(claim_id=999999, user=self.helper)
        self.assertIsNone(result)
        self.assertEqual(error, "Claim not found.")

    def test_error_when_user_is_not_the_claimant(self):
        claim, _ = self._make_claimed()
        someone_else = make_user("someoneelse")
        result, error = services.finish_claim(claim_id=claim.id, user=someone_else)
        self.assertIsNone(result)
        self.assertIn("Only the person who claimed", error)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "claimed")

    def test_error_when_claim_already_finished(self):
        claim, _ = self._make_claimed()
        services.finish_claim(claim_id=claim.id, user=self.helper)
        result, error = services.finish_claim(claim_id=claim.id, user=self.helper)
        self.assertIsNone(result)
        self.assertEqual(error, "This claim is no longer active.")

    def test_error_when_claim_has_expired(self):
        membership = make_membership(self.helper, self.store, role="helper")
        claim = make_claim(
            self.store_order, membership, claimant=self.helper, deadline=timezone.now() - timedelta(hours=1)
        )
        result, error = services.finish_claim(claim_id=claim.id, user=self.helper)
        self.assertIsNone(result)
        self.assertIn("expired", error)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "expired")


class ReleaseClaimServiceTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(self.helper, self.store, role="helper")
        self.order = make_order()
        self.store_order = make_store_order(self.order, self.store, status="Processing")
        self.claim = make_claim(self.store_order, self.membership, claimant=self.helper)

    def test_success_releases_claim(self):
        result, error = services.release_claim(claim_id=self.claim.id, user=self.helper)
        self.assertIsNone(error)
        self.assertEqual(result.status, "released")

    def test_error_when_claim_does_not_exist(self):
        result, error = services.release_claim(claim_id=999999, user=self.helper)
        self.assertIsNone(result)
        self.assertEqual(error, "Claim not found.")

    def test_error_when_user_is_not_the_claimant(self):
        someone_else = make_user("someoneelse")
        result, error = services.release_claim(claim_id=self.claim.id, user=someone_else)
        self.assertIsNone(result)
        self.assertIn("Only the person who claimed", error)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "claimed")

    def test_error_when_already_released(self):
        services.release_claim(claim_id=self.claim.id, user=self.helper)
        result, error = services.release_claim(claim_id=self.claim.id, user=self.helper)
        self.assertIsNone(result)
        self.assertEqual(error, "This claim is no longer active.")


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------

class WorkPoolViewTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(self.helper, self.store, role="helper")
        self.order = make_order()
        self.claimable_order = make_store_order(self.order, self.store, status="Pending")

    def test_requires_login(self):
        response = self.client.get(reverse("fulfillment:work_pool", args=[self.store.id]))
        self.assertEqual(response.status_code, 302)

    def test_redirects_non_eligible_user_with_error_message(self):
        outsider = make_user("outsider")
        self.client.force_login(outsider)
        response = self.client.get(
            reverse("fulfillment:work_pool", args=[self.store.id]), follow=True
        )
        self.assertRedirects(response, reverse("create_store"))
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("permission" in str(m) for m in messages))

    def test_redirects_pure_owner_since_owners_cannot_work_orders(self):
        owner = make_user("owner1")
        make_membership(owner, self.store, role="owner")
        self.client.force_login(owner)
        response = self.client.get(
            reverse("fulfillment:work_pool", args=[self.store.id]), follow=True
        )
        self.assertRedirects(response, reverse("create_store"))

    def test_eligible_helper_sees_available_orders(self):
        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:work_pool", args=[self.store.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.claimable_order, response.context["available_orders"])

    def test_claimed_orders_are_excluded_from_the_pool(self):
        other_helper = make_user("helper2")
        other_membership = make_membership(other_helper, self.store, role="helper")
        make_claim(self.claimable_order, other_membership, claimant=other_helper)
        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:work_pool", args=[self.store.id]))
        self.assertNotIn(self.claimable_order, response.context["available_orders"])

    def test_shows_the_users_own_active_claims(self):
        my_order = make_order()
        my_store_order = make_store_order(my_order, self.store, status="Processing")
        my_claim = make_claim(my_store_order, self.membership, claimant=self.helper)
        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:work_pool", args=[self.store.id]))
        self.assertIn(my_claim, response.context["my_active_claims"])

    def test_expired_claims_are_released_on_page_load(self):
        stale_order = make_order()
        stale_store_order = make_store_order(stale_order, self.store, status="Processing")
        stale_claim = make_claim(
            stale_store_order,
            self.membership,
            claimant=self.helper,
            deadline=timezone.now() - timedelta(hours=1),
        )
        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:work_pool", args=[self.store.id]))
        stale_claim.refresh_from_db()
        self.assertEqual(stale_claim.status, "expired")
        self.assertIn(stale_store_order, response.context["available_orders"])


class ClaimOrderViewTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        make_membership(self.helper, self.store, role="helper")
        self.order = make_order()
        self.store_order = make_store_order(self.order, self.store, status="Pending")

    def test_get_request_redirects_without_claiming(self):
        self.client.force_login(self.helper)
        url = reverse("fulfillment:claim_order", args=[self.store.id, self.store_order.id])
        response = self.client.get(url)
        self.assertRedirects(response, reverse("fulfillment:work_pool", args=[self.store.id]))
        self.assertEqual(OrderClaim.objects.count(), 0)

    def test_post_success_creates_claim_and_redirects_to_detail(self):
        self.client.force_login(self.helper)
        url = reverse("fulfillment:claim_order", args=[self.store.id, self.store_order.id])
        response = self.client.post(url, follow=True)
        claim = OrderClaim.objects.get(store_order=self.store_order)
        self.assertRedirects(response, reverse("fulfillment:claim_detail", args=[claim.id]))
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("24 hours" in str(m) for m in messages))

    def test_post_failure_redirects_to_work_pool_with_error(self):
        outsider = make_user("outsider")
        self.client.force_login(outsider)
        url = reverse("fulfillment:claim_order", args=[self.store.id, self.store_order.id])
        response = self.client.post(url)
        self.assertRedirects(
            response,
            reverse("fulfillment:work_pool", args=[self.store.id]),
            fetch_redirect_response=False,
        )
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("permission" in str(m) for m in messages))


class ClaimDetailViewTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(self.helper, self.store, role="helper")
        self.order = make_order()
        self.store_order = make_store_order(self.order, self.store, status="Processing")
        make_order_item(self.store_order)
        self.claim = make_claim(self.store_order, self.membership, claimant=self.helper)

    def test_claimant_can_view_with_can_act_true(self):
        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:claim_detail", args=[self.claim.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_act"])

    def test_owner_can_view_read_only(self):
        owner = make_user("owner1")
        make_membership(owner, self.store, role="owner")
        self.client.force_login(owner)
        response = self.client.get(reverse("fulfillment:claim_detail", args=[self.claim.id]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_act"])

    def test_manager_can_view_read_only(self):
        manager = make_user("manager1")
        make_membership(manager, self.store, role="manager", wage_type="salary")
        self.client.force_login(manager)
        response = self.client.get(reverse("fulfillment:claim_detail", args=[self.claim.id]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_act"])

    def test_unrelated_user_is_redirected(self):
        outsider = make_user("outsider")
        self.client.force_login(outsider)
        response = self.client.get(
            reverse("fulfillment:claim_detail", args=[self.claim.id]), follow=True
        )
        self.assertRedirects(response, reverse("create_store"))

    def test_viewing_an_expired_claim_auto_releases_it(self):
        self.claim.deadline = timezone.now() - timedelta(hours=1)
        self.claim.save(update_fields=["deadline"])
        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:claim_detail", args=[self.claim.id]))
        self.assertEqual(response.context["claim"].status, "expired")
        self.assertFalse(response.context["can_act"])
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "expired")


class FinishClaimViewTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(self.helper, self.store, role="helper")
        self.order = make_order()
        self.store_order = make_store_order(self.order, self.store, status="Processing")
        self.claim = make_claim(self.store_order, self.membership, claimant=self.helper)

    def test_get_does_not_finish_the_claim(self):
        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:finish_claim", args=[self.claim.id]))
        self.assertRedirects(response, reverse("fulfillment:claim_detail", args=[self.claim.id]))
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "claimed")

    def test_post_by_claimant_finishes_it(self):
        self.client.force_login(self.helper)
        response = self.client.post(
            reverse("fulfillment:finish_claim", args=[self.claim.id]), follow=True
        )
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "finished")
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Nice work" in str(m) for m in messages))

    def test_post_by_non_claimant_fails_with_error(self):
        someone_else = make_user("someoneelse")
        self.client.force_login(someone_else)
        response = self.client.post(
            reverse("fulfillment:finish_claim", args=[self.claim.id]), follow=True
        )
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "claimed")
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Only the person" in str(m) for m in messages))


class ReleaseClaimViewTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(self.helper, self.store, role="helper")
        self.order = make_order()
        self.store_order = make_store_order(self.order, self.store, status="Processing")
        self.claim = make_claim(self.store_order, self.membership, claimant=self.helper)

    def test_get_does_not_release_the_claim(self):
        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:release_claim", args=[self.claim.id]))
        self.assertRedirects(response, reverse("fulfillment:claim_detail", args=[self.claim.id]))
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "claimed")

    def test_post_by_claimant_releases_and_redirects_to_work_pool(self):
        self.client.force_login(self.helper)
        response = self.client.post(
            reverse("fulfillment:release_claim", args=[self.claim.id]), follow=True
        )
        self.assertRedirects(response, reverse("fulfillment:work_pool", args=[self.store.id]))
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "released")

    def test_post_by_non_claimant_fails_and_stays_on_detail(self):
        someone_else = make_user("someoneelse")
        self.client.force_login(someone_else)
        response = self.client.post(reverse("fulfillment:release_claim", args=[self.claim.id]))
        self.assertRedirects(
            response,
            reverse("fulfillment:claim_detail", args=[self.claim.id]),
            fetch_redirect_response=False,
        )
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "claimed")


class StoreOverviewViewTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.owner = make_user("owner1")
        make_membership(self.owner, self.store, role="owner")
        self.manager = make_user("manager1")
        self.manager_membership = make_membership(
            self.manager, self.store, role="manager", wage_type="percentage", wage=Decimal("20.00")
        )
        self.helper = make_user("helper1")
        self.helper_membership = make_membership(
            self.helper, self.store, role="helper", wage_type="percentage", wage=Decimal("15.00")
        )

    def test_helper_cannot_view_overview(self):
        self.client.force_login(self.helper)
        response = self.client.get(
            reverse("fulfillment:store_overview", args=[self.store.id]), follow=True
        )
        self.assertRedirects(response, reverse("create_store"))

    def test_owner_can_view_overview(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("fulfillment:store_overview", args=[self.store.id]))
        self.assertEqual(response.status_code, 200)

    def test_manager_can_view_overview(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("fulfillment:store_overview", args=[self.store.id]))
        self.assertEqual(response.status_code, 200)

    def test_active_claims_context(self):
        order = make_order()
        store_order = make_store_order(order, self.store, status="Processing")
        claim = make_claim(store_order, self.helper_membership, claimant=self.helper)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("fulfillment:store_overview", args=[self.store.id]))
        self.assertIn(claim, response.context["active_claims"])

    def test_recent_claims_excludes_active_claims(self):
        order = make_order()
        store_order = make_store_order(order, self.store, status="Processing")
        active_claim = make_claim(store_order, self.helper_membership, claimant=self.helper, status="claimed")

        finished_order = make_order()
        finished_store_order = make_store_order(finished_order, self.store, status="Shipped")
        finished_claim = make_claim(
            finished_store_order, self.helper_membership, claimant=self.helper, status="finished"
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("fulfillment:store_overview", args=[self.store.id]))
        self.assertIn(finished_claim, response.context["recent_claims"])
        self.assertNotIn(active_claim, response.context["recent_claims"])

    def test_staff_wallets_excludes_owner(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("fulfillment:store_overview", args=[self.store.id]))
        staff = list(response.context["staff_wallets"])
        self.assertIn(self.helper_membership, staff)
        self.assertIn(self.manager_membership, staff)
        owner_membership = Membership.objects.get(user=self.owner, store=self.store)
        self.assertNotIn(owner_membership, staff)

    def test_expired_claims_are_released_before_rendering(self):
        order = make_order()
        store_order = make_store_order(order, self.store, status="Processing")
        stale_claim = make_claim(
            store_order, self.helper_membership, claimant=self.helper, deadline=timezone.now() - timedelta(hours=1)
        )
        self.client.force_login(self.owner)
        self.client.get(reverse("fulfillment:store_overview", args=[self.store.id]))
        stale_claim.refresh_from_db()
        self.assertEqual(stale_claim.status, "expired")


class MyWalletViewTests(TestCase):
    def setUp(self):
        self.niche = make_niche()
        self.store = make_store(niche=self.niche)
        self.helper = make_user("helper1")
        self.membership = make_membership(
            self.helper, self.store, role="helper", wage_type="percentage", wage=Decimal("10.00")
        )

    def test_requires_login(self):
        response = self.client.get(reverse("fulfillment:my_wallet", args=[self.store.id]))
        self.assertEqual(response.status_code, 302)

    def test_404_when_user_has_no_membership(self):
        outsider = make_user("outsider")
        self.client.force_login(outsider)
        response = self.client.get(reverse("fulfillment:my_wallet", args=[self.store.id]))
        self.assertEqual(response.status_code, 404)

    def test_shows_own_wallet_and_transactions(self):
        order = make_order()
        store_order = make_store_order(order, self.store, status="Processing")
        make_order_item(store_order, quantity=4)
        claim = make_claim(store_order, self.membership, claimant=self.helper)
        services.finish_claim(claim_id=claim.id, user=self.helper)

        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:my_wallet", args=[self.store.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["membership"], self.membership)
        self.assertEqual(len(response.context["transactions"]), 1)

    def test_only_shows_this_stores_membership_transactions(self):
        other_store = make_store(niche=self.niche, name="Other Store")
        other_membership = make_membership(
            self.helper, other_store, role="helper", wage_type="percentage", wage=Decimal("50.00")
        )
        WalletTransaction.objects.create(
            membership=other_membership, amount=Decimal("99.00"), description="From another store"
        )

        self.client.force_login(self.helper)
        response = self.client.get(reverse("fulfillment:my_wallet", args=[self.store.id]))
        self.assertEqual(len(response.context["transactions"]), 0)