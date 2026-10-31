"""
Comprehensive test suite for the delivery app: warehouse + courier missions
(pickup: merchant -> warehouse, dropoff: warehouse -> shopper), 24h claims,
confirmation codes, cash-on-delivery, courier wallet, order-status roll-up,
the StoreOrder signals, views, admin action and management commands.

Conventions
-----------
- Fixtures never touch the network; users are created with the fast MD5
  hasher (override_settings) so the suite stays quick.
- To control signals precisely, tests create StoreOrders in "Processing" and
  then move them with ``so.status = "Shipped"; so.save()`` (the real path:
  fulfillment's finish_claim does exactly that). Where a test needs a row in a
  state *without* firing signals it uses ``queryset.update()``.
- SQLite (the project's test DB) ignores select_for_update(), so race
  guarantees are tested two ways: sequentially through the services, and
  directly against the DB constraints. No real threads.

Known bugs
----------
Tests whose class name starts with ``KnownBug`` assert the CORRECT behavior
and are decorated with ``@unittest.expectedFailure``: they pass (as "expected
failures") today, so CI stays green, and they flip to "unexpected success"
the moment someone fixes the bug. When that happens, delete the decorator.
Each carries a BUG-n tag matching the review notes.
"""

import unittest
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from merchant_interface.models import Niche, Store
from notifications.models import Notification
from orders.models import Order, OrderItem, StoreOrder
from products.models import Category, Product
from users.models import Profile

from . import services
from .models import (
    CLAIM_DURATION,
    DROPOFF_FEE,
    MAX_ACTIVE_CLAIMS,
    MAX_CODE_ATTEMPTS,
    PICKUP_FEE,
    CourierWallet,
    CourierWalletTransaction,
    DeliveryClaim,
    DeliveryMission,
    StorePickupLocation,
    Warehouse,
    area_hint,
    new_confirmation_code,
)

User = get_user_model()

FAST_HASHER = ["django.contrib.auth.hashers.MD5PasswordHasher"]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_seq = {"user": 0, "niche": 0, "category": 0, "store": 0}


def _next(key):
    _seq[key] += 1
    return _seq[key]


def make_user(username=None, **extra):
    username = username or f"user-{_next('user')}"
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        **extra,
    )


def _with_flag(user, flag):
    setattr(user.profile, flag, True)
    user.profile.save()
    return user


def make_courier(username=None):
    return _with_flag(make_user(username), "is_delivery_person")


def make_warehouse_staff(username=None):
    return _with_flag(make_user(username), "is_warehouse_staff")


def make_store(name=None):
    n = _next("store")
    niche = Niche.objects.create(name=f"Niche-{_next('niche')}")
    return Store.objects.create(name=name or f"Store {n}", niche=niche)


def make_product(store, name="Widget", price=Decimal("50.00")):
    category = Category.objects.create(name=f"Category-{_next('category')}")
    return Product.objects.create(
        store=store, category=category, name=name, selling_price=price
    )


def messages_of(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def notes_for(user):
    return list(Notification.objects.filter(recipient=user).values_list("message", flat=True))


# ---------------------------------------------------------------------------
# Base class with a full, realistic world
# ---------------------------------------------------------------------------


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class DeliveryTestCase(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(
            name="Main Warehouse",
            address="Industrial Zone, Cairo, Egypt",
            phone="0100000000",
            is_default=True,
        )
        self.shopper = make_user("shopper")
        self.courier = make_courier("courier")
        self.courier2 = make_courier("courier2")
        self.staff = make_warehouse_staff("wh-staff")

    # -- builders ----------------------------------------------------------

    def new_order(self, shopper=None, address="12 Nile St, Zamalek, Cairo, Egypt", payment_type="cash"):
        return Order.objects.create(
            user=shopper or self.shopper,
            shipping_address=address,
            payment_type=payment_type,
            status="Pending",
        )

    def add_store_order(
        self, order, store=None, status="Processing", qty=2, price=Decimal("50.00"), product_name="Widget"
    ):
        store = store or make_store()
        product = make_product(store, name=product_name, price=price)
        store_order = StoreOrder.objects.create(
            order=order, store=store, status=status, subtotal=price * qty
        )
        OrderItem.objects.create(
            store_order=store_order, product=product, quantity=qty, price_at_purchase=price
        )
        return store_order

    def order_with_stores(self, n=1, **order_kwargs):
        order = self.new_order(**order_kwargs)
        return order, [
            self.add_store_order(order, product_name=f"Item {i + 1}") for i in range(n)
        ]

    def ship(self, store_order):
        """Real path: Processing -> Shipped fires the signal that opens the
        pickup mission. Returns that mission."""
        store_order.status = "Shipped"
        store_order.save()
        return (
            DeliveryMission.objects.filter(store_order=store_order, kind="pickup")
            .exclude(status="cancelled")
            .get()
        )

    def claim(self, mission, courier=None):
        claim, error = services.claim_mission(
            mission_id=mission.pk, user=courier or self.courier
        )
        self.assertIsNone(error)
        return claim

    def pick_up(self, claim, courier=None):
        result, error = services.mark_picked_up(
            claim_id=claim.pk, user=courier or self.courier
        )
        self.assertIsNone(error)
        return result

    def complete(self, claim, courier=None, code=None, cash=True):
        if code is None:
            code = DeliveryMission.objects.get(pk=claim.mission_id).confirmation_code
        return services.complete_claim(
            claim_id=claim.pk, user=courier or self.courier, code=code, cash_collected=cash
        )

    def deliver_pickup(self, store_order, courier=None):
        """Ship, claim, pick up and hand over one store's goods at the
        warehouse. Returns the finished claim."""
        mission = self.ship(store_order)
        claim = self.claim(mission, courier)
        self.pick_up(claim, courier)
        result, error = self.complete(claim, courier)
        self.assertIsNone(error)
        return result

    def order_at_warehouse(self, n=1, **order_kwargs):
        """An order whose store orders are all At Warehouse, so the dropoff
        mission exists. Returns (order, store_orders, dropoff_mission)."""
        order, store_orders = self.order_with_stores(n, **order_kwargs)
        for so in store_orders:
            self.deliver_pickup(so)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        return order, store_orders, dropoff

    def expire(self, claim):
        DeliveryClaim.objects.filter(pk=claim.pk).update(
            deadline=timezone.now() - timedelta(minutes=1)
        )


# ===========================================================================
# 1. Pure helpers
# ===========================================================================


class HelperTests(SimpleTestCase):
    def test_confirmation_code_is_always_six_digits(self):
        for _ in range(300):
            self.assertRegex(new_confirmation_code(), r"^\d{6}$")

    def test_confirmation_codes_are_not_constant(self):
        self.assertGreater(len({new_confirmation_code() for _ in range(50)}), 1)

    def test_area_hint_keeps_last_two_parts(self):
        self.assertEqual(
            area_hint("12 Nile St, Zamalek, Cairo, Egypt"), "Cairo, Egypt"
        )

    def test_area_hint_ignores_blank_parts_and_whitespace(self):
        self.assertEqual(area_hint("A,, ,  B ,C"), "B, C")

    def test_area_hint_empty_and_none(self):
        self.assertEqual(area_hint(""), "")
        self.assertEqual(area_hint(None), "")

    def test_area_hint_of_street_city_postcode_hides_the_street(self):
        # Matches the checkout placeholder "Street, city, postal code..."
        self.assertEqual(area_hint("12 Nile St, Cairo, 11511"), "Cairo, 11511")


# ===========================================================================
# 2. Models & constraints
# ===========================================================================


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class WarehouseModelTests(TestCase):
    def test_only_one_default_warehouse(self):
        a = Warehouse.objects.create(name="A", address="x", is_default=True)
        b = Warehouse.objects.create(name="B", address="y", is_default=True)
        a.refresh_from_db()
        self.assertFalse(a.is_default)
        self.assertTrue(b.is_default)

    def test_saving_non_default_does_not_demote_the_default(self):
        a = Warehouse.objects.create(name="A", address="x", is_default=True)
        Warehouse.objects.create(name="B", address="y", is_default=False)
        a.refresh_from_db()
        self.assertTrue(a.is_default)

    def test_get_default_prefers_the_default_flag(self):
        Warehouse.objects.create(name="A", address="x")
        b = Warehouse.objects.create(name="B", address="y", is_default=True)
        self.assertEqual(Warehouse.get_default(), b)

    def test_get_default_skips_inactive_warehouses(self):
        Warehouse.objects.create(name="A", address="x", is_default=True, is_active=False)
        b = Warehouse.objects.create(name="B", address="y")
        self.assertEqual(Warehouse.get_default(), b)

    def test_get_default_falls_back_to_oldest_active(self):
        a = Warehouse.objects.create(name="A", address="x")
        Warehouse.objects.create(name="B", address="y")
        self.assertEqual(Warehouse.get_default(), a)

    def test_get_default_none_when_nothing_active(self):
        Warehouse.objects.create(name="A", address="x", is_active=False)
        self.assertIsNone(Warehouse.get_default())

    def test_str(self):
        self.assertEqual(str(Warehouse(name="Main")), "Main")


class MissionModelTests(DeliveryTestCase):
    def mission(self, order, store_order=None, kind="pickup", **kw):
        defaults = dict(
            kind=kind,
            order=order,
            store_order=store_order,
            warehouse=self.warehouse,
            pickup_address="A, B, C",
            dropoff_address="D, E, F",
            fee=PICKUP_FEE,
        )
        defaults.update(kw)
        return DeliveryMission.objects.create(**defaults)

    def test_defaults(self):
        order, _ = self.order_with_stores(0)
        m = self.mission(order, kind="dropoff")
        self.assertEqual(m.status, "open")
        self.assertEqual(m.cod_amount, 0)
        self.assertRegex(m.confirmation_code, r"^\d{6}$")
        self.assertEqual(m.items_snapshot, [])

    def test_item_count_sums_quantities_and_tolerates_missing_values(self):
        order, _ = self.order_with_stores(0)
        m = self.mission(
            order,
            kind="dropoff",
            items_snapshot=[{"quantity": 2}, {"quantity": None}, {}, {"quantity": 3}],
        )
        self.assertEqual(m.item_count, 5)

    def test_area_hint_properties(self):
        order, _ = self.order_with_stores(0)
        m = self.mission(order, pickup_address="1 St, Giza, Egypt", dropoff_address="2 St, Cairo, Egypt")
        self.assertEqual(m.pickup_area_hint, "Giza, Egypt")
        self.assertEqual(m.dropoff_area_hint, "Cairo, Egypt")

    def test_str_mentions_kind_order_and_status(self):
        order, _ = self.order_with_stores(0)
        text = str(self.mission(order))
        self.assertIn(f"Order #{order.pk}", text)
        self.assertIn("open", text)

    def test_default_ordering_is_oldest_first(self):
        order, _ = self.order_with_stores(0)
        first = self.mission(order, kind="dropoff")
        order2, _ = self.order_with_stores(0)
        second = self.mission(order2, kind="dropoff")
        self.assertEqual(list(DeliveryMission.objects.all()), [first, second])

    # -- DB constraints ----------------------------------------------------

    def test_two_live_pickups_for_one_store_order_are_rejected(self):
        order, [so] = self.order_with_stores(1)
        self.mission(order, so)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.mission(order, so)

    def test_cancelled_pickup_does_not_block_a_new_one(self):
        order, [so] = self.order_with_stores(1)
        self.mission(order, so, status="cancelled")
        self.mission(order, so)  # no error

    def test_completed_pickup_still_counts_as_live(self):
        order, [so] = self.order_with_stores(1)
        self.mission(order, so, status="completed")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.mission(order, so)

    def test_pickups_for_different_store_orders_coexist(self):
        order, [so1, so2] = self.order_with_stores(2)
        self.mission(order, so1)
        self.mission(order, so2)

    def test_two_live_dropoffs_for_one_order_are_rejected(self):
        order, _ = self.order_with_stores(0)
        self.mission(order, kind="dropoff")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.mission(order, kind="dropoff")

    def test_cancelled_dropoff_does_not_block_a_new_one(self):
        order, _ = self.order_with_stores(0)
        self.mission(order, kind="dropoff", status="cancelled")
        self.mission(order, kind="dropoff")

    def test_pickup_and_dropoff_of_same_order_coexist(self):
        order, [so] = self.order_with_stores(1)
        self.mission(order, so)
        self.mission(order, kind="dropoff")


class ClaimModelTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.order, [self.so] = self.order_with_stores(1)
        self.mission = DeliveryMission.objects.create(
            kind="pickup", order=self.order, store_order=self.so,
            warehouse=self.warehouse, pickup_address="a", dropoff_address="b", fee=PICKUP_FEE,
        )

    def make_claim(self, courier=None, status="claimed", hours=24):
        return DeliveryClaim.objects.create(
            mission=self.mission,
            courier=courier or self.courier,
            status=status,
            deadline=timezone.now() + timedelta(hours=hours),
        )

    def test_only_one_active_claim_per_mission(self):
        self.make_claim()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_claim(self.courier2)

    def test_picked_up_claim_also_blocks_a_second_claim(self):
        self.make_claim(status="picked_up")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_claim(self.courier2)

    def test_history_claims_do_not_block_a_new_active_claim(self):
        for status in ("expired", "released", "cancelled", "delivered"):
            self.make_claim(status=status)
        self.make_claim()  # no error

    def test_is_active(self):
        first = self.make_claim(status="claimed")
        self.assertTrue(first.is_active)
        # Take the first claim out of the active set before adding a second
        # active claim on the same mission — the two can't coexist (see
        # test_only_one_active_claim_per_mission).
        DeliveryClaim.objects.filter(pk=first.pk).update(status="delivered")
        c = self.make_claim(status="picked_up")
        self.assertTrue(c.is_active)
        c.status = "delivered"
        self.assertFalse(c.is_active)

    def test_is_expired_only_when_active_and_past_deadline(self):
        self.assertFalse(self.make_claim().is_expired)
        past = self.make_claim(self.courier2, status="expired", hours=-1)
        self.assertFalse(past.is_expired)  # not active -> not "expired" by this flag

    def test_is_expired_true_for_active_claim_past_deadline(self):
        self.assertTrue(self.make_claim(hours=-1).is_expired)

    def test_time_left(self):
        claim = self.make_claim(hours=2)
        self.assertLessEqual(claim.time_left, timedelta(hours=2))
        self.assertGreater(claim.time_left, timedelta(hours=1, minutes=59))

    def test_time_left_is_zero_when_past_deadline_or_inactive(self):
        self.assertEqual(self.make_claim(hours=-3).time_left, timedelta(0))
        self.assertEqual(self.make_claim(self.courier2, status="released").time_left, timedelta(0))

    def test_wallet_transaction_is_one_per_claim(self):
        claim = self.make_claim(status="delivered")
        wallet = CourierWallet.objects.create(user=self.courier)
        CourierWalletTransaction.objects.create(wallet=wallet, claim=claim, amount=Decimal("25"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            CourierWalletTransaction.objects.create(wallet=wallet, claim=claim, amount=Decimal("25"))

    def test_str_methods_do_not_crash(self):
        claim = self.make_claim()
        wallet = CourierWallet.objects.create(user=self.courier)
        txn = CourierWalletTransaction.objects.create(wallet=wallet, amount=Decimal("1"))
        for obj in (claim, wallet, txn, StorePickupLocation(store=self.so.store, address="x")):
            self.assertTrue(str(obj))


# ===========================================================================
# 3. Roles
# ===========================================================================


class RoleTests(DeliveryTestCase):
    def test_anonymous_has_no_roles(self):
        self.assertFalse(services.is_courier(AnonymousUser()))
        self.assertFalse(services.is_warehouse_staff(AnonymousUser()))

    def test_plain_user_has_no_roles(self):
        self.assertFalse(services.is_courier(self.shopper))
        self.assertFalse(services.is_warehouse_staff(self.shopper))

    def test_flags_grant_roles_independently(self):
        self.assertTrue(services.is_courier(self.courier))
        self.assertFalse(services.is_warehouse_staff(self.courier))
        self.assertTrue(services.is_warehouse_staff(self.staff))
        self.assertFalse(services.is_courier(self.staff))

    def test_user_without_profile_is_not_a_courier(self):
        Profile.objects.filter(user=self.shopper).delete()
        fresh = User.objects.get(pk=self.shopper.pk)
        self.assertFalse(services.is_courier(fresh))
        self.assertFalse(services.is_warehouse_staff(fresh))


# ===========================================================================
# 4. Order status roll-up
# ===========================================================================


class SyncOrderStatusTests(DeliveryTestCase):
    def set_statuses(self, store_orders, *statuses):
        for so, status in zip(store_orders, statuses):
            StoreOrder.objects.filter(pk=so.pk).update(status=status)

    def test_unknown_order_returns_none(self):
        self.assertIsNone(services.sync_order_status(999999))

    def test_order_without_store_orders_is_left_alone(self):
        order = self.new_order()
        self.assertEqual(services.sync_order_status(order.pk), "Pending")

    def test_order_takes_the_furthest_behind_live_status(self):
        order, sos = self.order_with_stores(3)
        self.set_statuses(sos, "Delivered", "Shipped", "At Warehouse")
        self.assertEqual(services.sync_order_status(order.pk), "Shipped")
        order.refresh_from_db()
        self.assertEqual(order.status, "Shipped")

    def test_cancelled_store_orders_are_ignored(self):
        order, sos = self.order_with_stores(2)
        self.set_statuses(sos, "Cancelled", "At Warehouse")
        self.assertEqual(services.sync_order_status(order.pk), "At Warehouse")

    def test_all_cancelled_cancels_the_order_and_stamps_cancelled_at(self):
        order, sos = self.order_with_stores(2)
        self.set_statuses(sos, "Cancelled", "Cancelled")
        self.assertEqual(services.sync_order_status(order.pk), "Cancelled")
        order.refresh_from_db()
        self.assertIsNotNone(order.cancelled_at)

    def test_null_store_order_status_counts_as_pending(self):
        order, [so] = self.order_with_stores(1)
        StoreOrder.objects.filter(pk=so.pk).update(status=None)
        self.assertEqual(services.sync_order_status(order.pk), "Pending")

    def test_timestamps_are_stamped_once_and_never_overwritten(self):
        order, [so] = self.order_with_stores(1)
        self.set_statuses([so], "Shipped")
        services.sync_order_status(order.pk)
        order.refresh_from_db()
        first = order.shipped_at
        self.assertIsNotNone(first)

        self.set_statuses([so], "At Warehouse")
        services.sync_order_status(order.pk)
        self.set_statuses([so], "Shipped")
        services.sync_order_status(order.pk)
        order.refresh_from_db()
        self.assertEqual(order.shipped_at, first)

    def test_delivered_stamps_delivered_at(self):
        order, [so] = self.order_with_stores(1)
        self.set_statuses([so], "Delivered")
        services.sync_order_status(order.pk)
        order.refresh_from_db()
        self.assertIsNotNone(order.delivered_at)

    def test_idempotent(self):
        order, [so] = self.order_with_stores(1)
        self.set_statuses([so], "Processing")
        for _ in range(3):
            self.assertEqual(services.sync_order_status(order.pk), "Processing")
        order.refresh_from_db()
        self.assertIsNotNone(order.processed_at)


# ===========================================================================
# 5. Mission creation
# ===========================================================================


class CreatePickupMissionTests(DeliveryTestCase):
    def test_creates_open_pickup_with_flat_fee_and_warehouse_destination(self):
        order, [so] = self.order_with_stores(1)
        mission, error = services.create_pickup_mission(so)
        self.assertIsNone(error)
        self.assertEqual(mission.kind, "pickup")
        self.assertEqual(mission.status, "open")
        self.assertEqual(mission.fee, PICKUP_FEE)
        self.assertEqual(mission.cod_amount, 0)
        self.assertEqual(mission.warehouse, self.warehouse)
        self.assertEqual(mission.dropoff_address, self.warehouse.address)
        self.assertIn("Main Warehouse", mission.dropoff_contact)
        self.assertIn("0100000000", mission.dropoff_contact)
        self.assertEqual(mission.order_id, order.pk)

    def test_uses_the_stores_pickup_location_when_present(self):
        order, [so] = self.order_with_stores(1)
        StorePickupLocation.objects.create(store=so.store, address="5 Souq St, Giza", phone="0111")
        mission, _ = services.create_pickup_mission(so)
        self.assertEqual(mission.pickup_address, "5 Souq St, Giza")
        self.assertIn(so.store.name, mission.pickup_contact)
        self.assertIn("0111", mission.pickup_contact)

    def test_falls_back_to_placeholder_when_no_pickup_location(self):
        order, [so] = self.order_with_stores(1)
        mission, _ = services.create_pickup_mission(so)
        self.assertIn("no pickup address on file", mission.pickup_address)
        self.assertIn(so.store.name, mission.pickup_address)

    def test_snapshot_contains_only_this_stores_items(self):
        order, [so1, so2] = self.order_with_stores(2)
        mission, _ = services.create_pickup_mission(so1)
        self.assertEqual(len(mission.items_snapshot), 1)
        self.assertEqual(mission.items_snapshot[0]["product"], "Item 1")
        self.assertEqual(mission.items_snapshot[0]["quantity"], 2)
        self.assertEqual(mission.items_snapshot[0]["store"], so1.store.name)

    def test_is_idempotent(self):
        order, [so] = self.order_with_stores(1)
        first, _ = services.create_pickup_mission(so)
        second, error = services.create_pickup_mission(so)
        self.assertIsNone(error)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(DeliveryMission.objects.filter(kind="pickup").count(), 1)

    def test_a_cancelled_pickup_is_replaced_not_reused(self):
        order, [so] = self.order_with_stores(1)
        first, _ = services.create_pickup_mission(so)
        DeliveryMission.objects.filter(pk=first.pk).update(status="cancelled")
        second, _ = services.create_pickup_mission(so)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.status, "open")

    def test_no_warehouse_returns_error_and_creates_nothing(self):
        Warehouse.objects.all().delete()
        order, [so] = self.order_with_stores(1)
        mission, error = services.create_pickup_mission(so)
        self.assertIsNone(mission)
        self.assertIn("warehouse", error.lower())
        self.assertFalse(DeliveryMission.objects.exists())

    def test_snapshot_is_frozen_against_later_edits(self):
        order, [so] = self.order_with_stores(1)
        StorePickupLocation.objects.create(store=so.store, address="Old Address, Giza")
        mission, _ = services.create_pickup_mission(so)
        StorePickupLocation.objects.filter(store=so.store).update(address="New Address, Giza")
        self.warehouse.address = "Moved, Alexandria"
        self.warehouse.save()
        mission.refresh_from_db()
        self.assertEqual(mission.pickup_address, "Old Address, Giza")
        self.assertEqual(mission.dropoff_address, "Industrial Zone, Cairo, Egypt")


class CreateDropoffMissionTests(DeliveryTestCase):
    def test_creates_dropoff_with_cod_of_live_subtotals(self):
        order, [so1, so2] = self.order_with_stores(2)  # 2 x 50 = 100 each
        mission, error = services.create_dropoff_mission(order)
        self.assertIsNone(error)
        self.assertEqual(mission.kind, "dropoff")
        self.assertIsNone(mission.store_order)
        self.assertEqual(mission.fee, DROPOFF_FEE)
        self.assertEqual(mission.cod_amount, Decimal("200.00"))
        self.assertEqual(mission.pickup_address, self.warehouse.address)
        self.assertEqual(mission.dropoff_address, order.shipping_address)
        self.assertEqual(mission.dropoff_contact, "shopper")
        self.assertEqual(len(mission.items_snapshot), 2)

    def test_cancelled_store_orders_are_excluded_from_cod_and_items(self):
        order, [so1, so2] = self.order_with_stores(2)
        StoreOrder.objects.filter(pk=so2.pk).update(status="Cancelled")
        mission, _ = services.create_dropoff_mission(order)
        self.assertEqual(mission.cod_amount, Decimal("100.00"))
        self.assertEqual([i["product"] for i in mission.items_snapshot], ["Item 1"])

    def test_no_cod_when_not_cash(self):
        order, _ = self.order_with_stores(1, payment_type=None)
        mission, _ = services.create_dropoff_mission(order)
        self.assertEqual(mission.cod_amount, Decimal("0.00"))

    def test_null_subtotal_is_treated_as_zero(self):
        order, [so] = self.order_with_stores(1)
        StoreOrder.objects.filter(pk=so.pk).update(subtotal=None)
        mission, _ = services.create_dropoff_mission(order)
        self.assertEqual(mission.cod_amount, Decimal("0.00"))

    def test_contact_prefers_full_name_over_username(self):
        self.shopper.first_name, self.shopper.last_name = "Sara", "Ali"
        self.shopper.save()
        order, _ = self.order_with_stores(1)
        mission, _ = services.create_dropoff_mission(order)
        self.assertEqual(mission.dropoff_contact, "Sara Ali")

    def test_order_without_a_user_still_gets_a_mission(self):
        order = Order.objects.create(user=None, shipping_address="x, y", payment_type="cash")
        self.add_store_order(order)
        mission, error = services.create_dropoff_mission(order)
        self.assertIsNone(error)
        self.assertEqual(mission.dropoff_contact, "")

    def test_is_idempotent(self):
        order, _ = self.order_with_stores(1)
        first, _ = services.create_dropoff_mission(order)
        second, _ = services.create_dropoff_mission(order)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(DeliveryMission.objects.filter(kind="dropoff").count(), 1)

    def test_no_warehouse_returns_error(self):
        Warehouse.objects.all().delete()
        order, _ = self.order_with_stores(1)
        mission, error = services.create_dropoff_mission(order)
        self.assertIsNone(mission)
        self.assertIn("warehouse", error.lower())


class AdvanceOrderIfReadyTests(DeliveryTestCase):
    def force(self, store_orders, *statuses):
        for so, status in zip(store_orders, statuses):
            StoreOrder.objects.filter(pk=so.pk).update(status=status)

    def test_not_ready_until_every_live_store_order_is_at_warehouse(self):
        order, sos = self.order_with_stores(2)
        self.force(sos, "At Warehouse", "Shipped")
        self.assertEqual(services.advance_order_if_ready(order), (None, None))
        self.assertFalse(DeliveryMission.objects.filter(kind="dropoff").exists())

    def test_creates_dropoff_once_all_are_at_warehouse_and_notifies_shopper(self):
        order, sos = self.order_with_stores(2)
        self.force(sos, "At Warehouse", "At Warehouse")
        mission, error = services.advance_order_if_ready(order)
        self.assertIsNone(error)
        self.assertEqual(mission.kind, "dropoff")
        self.assertTrue(any("at our warehouse" in n for n in notes_for(self.shopper)))

    def test_second_call_does_not_duplicate_or_renotify(self):
        order, sos = self.order_with_stores(1)
        self.force(sos, "At Warehouse")
        services.advance_order_if_ready(order)
        self.assertEqual(services.advance_order_if_ready(order), (None, None))
        self.assertEqual(DeliveryMission.objects.filter(kind="dropoff").count(), 1)
        self.assertEqual(len(notes_for(self.shopper)), 1)

    def test_cancelled_store_orders_do_not_block(self):
        order, sos = self.order_with_stores(2)
        self.force(sos, "Cancelled", "At Warehouse")
        mission, _ = services.advance_order_if_ready(order)
        self.assertIsNotNone(mission)

    def test_nothing_to_do_when_every_store_order_is_cancelled(self):
        order, sos = self.order_with_stores(1)
        self.force(sos, "Cancelled")
        self.assertEqual(services.advance_order_if_ready(order), (None, None))

    def test_surfaces_the_no_warehouse_error(self):
        Warehouse.objects.all().delete()
        order, sos = self.order_with_stores(1)
        self.force(sos, "At Warehouse")
        mission, error = services.advance_order_if_ready(order)
        self.assertIsNone(mission)
        self.assertIn("warehouse", error.lower())
        self.assertEqual(notes_for(self.shopper), [])


# ===========================================================================
# 6. Signals (StoreOrder -> missions / order status)
# ===========================================================================


class StoreOrderSignalTests(DeliveryTestCase):
    def test_moving_to_shipped_opens_a_pickup_mission(self):
        order, [so] = self.order_with_stores(1)
        self.assertFalse(DeliveryMission.objects.exists())
        mission = self.ship(so)
        self.assertEqual(mission.status, "open")
        self.assertEqual(mission.store_order, so)

    def test_creating_a_store_order_directly_as_shipped_opens_a_pickup(self):
        order = self.new_order()
        so = self.add_store_order(order, status="Shipped")
        self.assertTrue(DeliveryMission.objects.filter(store_order=so, kind="pickup").exists())

    def test_resaving_without_a_status_change_does_not_duplicate(self):
        order, [so] = self.order_with_stores(1)
        self.ship(so)
        so.refresh_from_db()
        so.subtotal = Decimal("999.00")
        so.save()
        self.assertEqual(DeliveryMission.objects.filter(kind="pickup").count(), 1)

    def test_other_status_changes_do_not_create_missions(self):
        order, [so] = self.order_with_stores(1)
        for status in ("Pending", "Processing", "At Warehouse", "Out for Delivery", "Delivered"):
            so.status = status
            so.save()
        self.assertFalse(DeliveryMission.objects.exists())

    def test_every_save_keeps_the_order_status_in_step(self):
        order, [so1, so2] = self.order_with_stores(2)
        so1.status = "Shipped"
        so1.save()
        order.refresh_from_db()
        self.assertEqual(order.status, "Processing")  # so2 is still Processing
        so2.status = "Shipped"
        so2.save()
        order.refresh_from_db()
        self.assertEqual(order.status, "Shipped")
        self.assertIsNotNone(order.shipped_at)

    def test_no_warehouse_logs_a_warning_and_never_breaks_the_save(self):
        Warehouse.objects.all().delete()
        order, [so] = self.order_with_stores(1)
        so.status = "Shipped"
        with self.assertLogs("delivery.signals", level="WARNING"):
            so.save()
        so.refresh_from_db()
        self.assertEqual(so.status, "Shipped")
        self.assertFalse(DeliveryMission.objects.exists())

    def test_a_crash_inside_the_delivery_reaction_never_breaks_the_save(self):
        order, [so] = self.order_with_stores(1)
        so.status = "Shipped"
        with mock.patch("delivery.services.create_pickup_mission", side_effect=RuntimeError("boom")):
            with self.assertLogs("delivery.signals", level="ERROR"):
                so.save()
        so.refresh_from_db()
        self.assertEqual(so.status, "Shipped")

    def test_store_order_without_an_order_is_ignored(self):
        so = StoreOrder.objects.create(order=None, store=make_store(), status="Shipped")
        self.assertFalse(DeliveryMission.objects.exists())
        self.assertIsNotNone(so.pk)

    def test_cancelling_cancels_the_open_pickup_and_the_couriers_claim(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        claim = self.claim(mission)
        self.pick_up(claim)

        so.status = "Cancelled"
        so.save()

        mission.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(mission.status, "cancelled")
        self.assertEqual(claim.status, "cancelled")
        self.assertTrue(any("was cancelled" in n for n in notes_for(self.courier)))
        so.refresh_from_db()
        self.assertIsNone(so.delivery_person)

    def test_cancelling_an_unclaimed_pickup_just_cancels_it(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        so.status = "Cancelled"
        so.save()
        mission.refresh_from_db()
        self.assertEqual(mission.status, "cancelled")
        self.assertFalse(DeliveryClaim.objects.exists())

    def test_cancelling_a_store_order_releases_the_rest_of_the_order(self):
        """Store A already at the warehouse, store B cancelled -> A's goods
        must not be stranded: the dropoff opens, for A's items only."""
        order, [so1, so2] = self.order_with_stores(2)
        self.deliver_pickup(so1)
        self.assertFalse(DeliveryMission.objects.filter(kind="dropoff").exists())
        mission_b = self.ship(so2)

        so2.status = "Cancelled"
        so2.save()

        mission_b.refresh_from_db()
        self.assertEqual(mission_b.status, "cancelled")
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        self.assertEqual(dropoff.cod_amount, so1.subtotal)
        self.assertEqual([i["product"] for i in dropoff.items_snapshot], ["Item 1"])

    def test_cancelling_the_only_store_order_cancels_the_order(self):
        order, [so] = self.order_with_stores(1)
        so.status = "Cancelled"
        so.save()
        order.refresh_from_db()
        self.assertEqual(order.status, "Cancelled")
        self.assertFalse(DeliveryMission.objects.exists())

    def test_shipping_again_after_a_cancel_opens_a_fresh_pickup(self):
        order, [so] = self.order_with_stores(1)
        self.ship(so)
        so.status = "Cancelled"
        so.save()
        so.status = "Shipped"
        so.save()
        live = DeliveryMission.objects.filter(store_order=so).exclude(status="cancelled")
        self.assertEqual(live.count(), 1)
        self.assertEqual(DeliveryMission.objects.filter(store_order=so).count(), 2)


# ===========================================================================
# 7. claim_mission
# ===========================================================================


class ClaimMissionTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.order, [self.so] = self.order_with_stores(1)
        self.mission = self.ship(self.so)

    def test_courier_can_claim_an_open_mission(self):
        claim, error = services.claim_mission(mission_id=self.mission.pk, user=self.courier)
        self.assertIsNone(error)
        self.assertEqual(claim.status, "claimed")
        self.assertEqual(claim.courier, self.courier)
        self.assertEqual(claim.mission, self.mission)

    def test_deadline_is_24_hours_out(self):
        before = timezone.now()
        claim, _ = services.claim_mission(mission_id=self.mission.pk, user=self.courier)
        self.assertGreaterEqual(claim.deadline, before + CLAIM_DURATION)
        self.assertLessEqual(claim.deadline, timezone.now() + CLAIM_DURATION)

    def test_claiming_assigns_the_courier_to_the_store_order(self):
        self.claim(self.mission)
        self.so.refresh_from_db()
        self.assertEqual(self.so.delivery_person, self.courier)

    def test_non_courier_cannot_claim(self):
        claim, error = services.claim_mission(mission_id=self.mission.pk, user=self.shopper)
        self.assertIsNone(claim)
        self.assertIn("delivery partners", error)
        self.assertFalse(DeliveryClaim.objects.exists())

    def test_warehouse_staff_alone_cannot_claim(self):
        claim, error = services.claim_mission(mission_id=self.mission.pk, user=self.staff)
        self.assertIsNone(claim)
        self.assertIsNotNone(error)

    def test_unknown_mission(self):
        claim, error = services.claim_mission(mission_id=987654, user=self.courier)
        self.assertIsNone(claim)
        self.assertEqual(error, "Mission not found.")

    def test_second_courier_loses_the_race(self):
        self.claim(self.mission)
        claim, error = services.claim_mission(mission_id=self.mission.pk, user=self.courier2)
        self.assertIsNone(claim)
        self.assertIn("Someone else", error)
        self.assertEqual(DeliveryClaim.objects.count(), 1)

    def test_same_courier_cannot_double_claim(self):
        self.claim(self.mission)
        claim, error = services.claim_mission(mission_id=self.mission.pk, user=self.courier)
        self.assertIsNone(claim)
        self.assertIsNotNone(error)

    def test_completed_and_cancelled_missions_are_not_available(self):
        for status in ("completed", "cancelled"):
            DeliveryMission.objects.filter(pk=self.mission.pk).update(status=status)
            claim, error = services.claim_mission(mission_id=self.mission.pk, user=self.courier)
            self.assertIsNone(claim)
            self.assertIn("no longer available", error)

    def test_active_claim_cap(self):
        missions = [self.mission]
        for _ in range(MAX_ACTIVE_CLAIMS):
            order, [so] = self.order_with_stores(1)
            missions.append(self.ship(so))
        for mission in missions[:MAX_ACTIVE_CLAIMS]:
            self.claim(mission)
        claim, error = services.claim_mission(mission_id=missions[-1].pk, user=self.courier)
        self.assertIsNone(claim)
        self.assertIn(f"at most {MAX_ACTIVE_CLAIMS}", error)

    def test_the_cap_counts_picked_up_claims_too(self):
        missions = [self.mission]
        for _ in range(MAX_ACTIVE_CLAIMS):
            order, [so] = self.order_with_stores(1)
            missions.append(self.ship(so))
        for mission in missions[:MAX_ACTIVE_CLAIMS]:
            self.pick_up(self.claim(mission))
        claim, error = services.claim_mission(mission_id=missions[-1].pk, user=self.courier)
        self.assertIsNone(claim)
        self.assertIn("at most", error)

    def test_the_cap_is_per_courier(self):
        missions = [self.mission]
        for _ in range(MAX_ACTIVE_CLAIMS):
            order, [so] = self.order_with_stores(1)
            missions.append(self.ship(so))
        for mission in missions[:MAX_ACTIVE_CLAIMS]:
            self.claim(mission)
        self.claim(missions[-1], self.courier2)  # no error

    def test_releasing_frees_a_slot_under_the_cap(self):
        missions = [self.mission]
        for _ in range(MAX_ACTIVE_CLAIMS):
            order, [so] = self.order_with_stores(1)
            missions.append(self.ship(so))
        claims = [self.claim(m) for m in missions[:MAX_ACTIVE_CLAIMS]]
        services.release_claim(claim_id=claims[0].pk, user=self.courier)
        self.claim(missions[-1])  # no error now

    def test_claiming_sweeps_expired_claims_first(self):
        """Courier A sat on the mission past 24h; courier B claims without
        anyone running the sweeper manually."""
        stale = self.claim(self.mission)
        self.expire(stale)
        claim, error = services.claim_mission(mission_id=self.mission.pk, user=self.courier2)
        self.assertIsNone(error)
        stale.refresh_from_db()
        self.assertEqual(stale.status, "expired")
        self.assertEqual(claim.courier, self.courier2)

    def test_courier_can_reclaim_after_releasing(self):
        claim = self.claim(self.mission)
        services.release_claim(claim_id=claim.pk, user=self.courier)
        again, error = services.claim_mission(mission_id=self.mission.pk, user=self.courier)
        self.assertIsNone(error)
        self.assertNotEqual(again.pk, claim.pk)

    def test_dropoff_claim_assigns_every_live_store_order(self):
        order, [so1, so2], dropoff = self.order_at_warehouse(2)
        StoreOrder.objects.filter(pk=so2.pk).update(status="Cancelled")
        self.claim(dropoff, self.courier2)
        so1.refresh_from_db()
        so2.refresh_from_db()
        self.assertEqual(so1.delivery_person, self.courier2)
        self.assertNotEqual(so2.delivery_person, self.courier2)


# ===========================================================================
# 8. release_claim (voluntary)
# ===========================================================================


class ReleaseClaimTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.order, [self.so] = self.order_with_stores(1)
        self.mission = self.ship(self.so)
        self.claim_obj = self.claim(self.mission)

    def test_courier_can_release_before_pickup(self):
        claim, error = services.release_claim(claim_id=self.claim_obj.pk, user=self.courier)
        self.assertIsNone(error)
        self.assertEqual(claim.status, "released")
        self.assertIn(self.mission, services.open_missions())

    def test_release_unassigns_the_courier(self):
        services.release_claim(claim_id=self.claim_obj.pk, user=self.courier)
        self.so.refresh_from_db()
        self.assertIsNone(self.so.delivery_person)

    def test_only_the_owner_can_release(self):
        claim, error = services.release_claim(claim_id=self.claim_obj.pk, user=self.courier2)
        self.assertIsNone(claim)
        self.assertIn("Only the courier", error)
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "claimed")

    def test_cannot_release_after_pickup(self):
        self.pick_up(self.claim_obj)
        claim, error = services.release_claim(claim_id=self.claim_obj.pk, user=self.courier)
        self.assertIsNone(claim)
        self.assertIn("contact support", error)
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "picked_up")

    def test_cannot_release_twice(self):
        services.release_claim(claim_id=self.claim_obj.pk, user=self.courier)
        claim, error = services.release_claim(claim_id=self.claim_obj.pk, user=self.courier)
        self.assertIsNone(claim)
        self.assertIn("no longer active", error)

    def test_unknown_claim(self):
        claim, error = services.release_claim(claim_id=424242, user=self.courier)
        self.assertEqual((claim, error), (None, "Claim not found."))

    def test_releasing_an_expired_claim_reports_the_expiry(self):
        self.expire(self.claim_obj)
        claim, error = services.release_claim(claim_id=self.claim_obj.pk, user=self.courier)
        self.assertIsNone(claim)
        self.assertIn("expired", error)


# ===========================================================================
# 9. mark_picked_up
# ===========================================================================


class MarkPickedUpTests(DeliveryTestCase):
    def test_pickup_leg_marks_claim_and_leaves_store_order_shipped(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        result = self.pick_up(claim)
        self.assertEqual(result.status, "picked_up")
        self.assertIsNotNone(result.picked_up_at)
        so.refresh_from_db()
        self.assertEqual(so.status, "Shipped")

    def test_dropoff_leg_moves_store_orders_out_for_delivery_and_notifies_with_code(self):
        order, [so1, so2], dropoff = self.order_at_warehouse(2)
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)

        for so in (so1, so2):
            so.refresh_from_db()
            self.assertEqual(so.status, "Out for Delivery")
        order.refresh_from_db()
        self.assertEqual(order.status, "Out for Delivery")
        out_notes = [n for n in notes_for(self.shopper) if "out for delivery" in n]
        self.assertEqual(len(out_notes), 1)
        self.assertIn(dropoff.confirmation_code, out_notes[0])

    def test_dropoff_pickup_leaves_cancelled_store_orders_alone(self):
        order, [so1, so2], dropoff = self.order_at_warehouse(2)
        StoreOrder.objects.filter(pk=so2.pk).update(status="Cancelled")
        self.pick_up(self.claim(dropoff, self.courier2), self.courier2)
        so2.refresh_from_db()
        self.assertEqual(so2.status, "Cancelled")

    def test_only_the_owner_can_pick_up(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        result, error = services.mark_picked_up(claim_id=claim.pk, user=self.courier2)
        self.assertIsNone(result)
        self.assertIn("Only the courier", error)

    def test_cannot_pick_up_twice(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        self.pick_up(claim)
        result, error = services.mark_picked_up(claim_id=claim.pk, user=self.courier)
        self.assertIsNone(result)
        self.assertIn("already picked up", error)

    def test_unknown_claim(self):
        self.assertEqual(
            services.mark_picked_up(claim_id=31337, user=self.courier),
            (None, "Claim not found."),
        )

    def test_cannot_pick_up_after_the_window_expired(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        self.expire(claim)
        result, error = services.mark_picked_up(claim_id=claim.pk, user=self.courier)
        self.assertIsNone(result)
        self.assertIn("expired", error)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "expired")


# ===========================================================================
# 10. complete_claim (confirmation code, cash, wallet, order roll-up)
# ===========================================================================


class CompleteClaimTests(DeliveryTestCase):
    def pickup_claim_picked_up(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        claim = self.claim(mission)
        self.pick_up(claim)
        return order, so, mission, claim

    def dropoff_claim_picked_up(self, **order_kwargs):
        order, sos, dropoff = self.order_at_warehouse(1, **order_kwargs)
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        return order, sos[0], dropoff, claim

    # -- happy paths -------------------------------------------------------

    def test_pickup_handover_moves_store_order_to_warehouse_and_pays_the_courier(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        result, error = self.complete(claim)
        self.assertIsNone(error)

        result.refresh_from_db()
        self.assertEqual(result.status, "delivered")
        self.assertEqual(result.fee_earned, PICKUP_FEE)
        self.assertIsNotNone(result.finished_at)
        self.assertFalse(result.cash_collected)
        mission.refresh_from_db()
        self.assertEqual(mission.status, "completed")
        self.assertIsNotNone(mission.completed_at)
        so.refresh_from_db()
        self.assertEqual(so.status, "At Warehouse")
        self.assertEqual(CourierWallet.objects.get(user=self.courier).balance, PICKUP_FEE)
        self.assertTrue(any("reached our warehouse" in n for n in notes_for(self.shopper)))

    def test_single_store_order_pickup_opens_the_dropoff(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        self.complete(claim)
        self.assertTrue(DeliveryMission.objects.filter(order=order, kind="dropoff", status="open").exists())

    def test_dropoff_handover_delivers_the_order(self):
        order, so, dropoff, claim = self.dropoff_claim_picked_up()
        result, error = self.complete(claim, self.courier2)
        self.assertIsNone(error)
        so.refresh_from_db()
        order.refresh_from_db()
        dropoff.refresh_from_db()
        self.assertEqual(so.status, "Delivered")
        self.assertEqual(order.status, "Delivered")
        self.assertIsNotNone(order.delivered_at)
        self.assertEqual(dropoff.status, "completed")
        self.assertEqual(result.fee_earned, DROPOFF_FEE)
        self.assertTrue(any("was delivered" in n for n in notes_for(self.shopper)))

    def test_wallet_accumulates_and_ledger_has_one_row_per_claim(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        self.complete(claim)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        claim2 = self.claim(dropoff)  # same courier does both legs
        self.pick_up(claim2)
        self.complete(claim2)

        wallet = CourierWallet.objects.get(user=self.courier)
        self.assertEqual(wallet.balance, PICKUP_FEE + DROPOFF_FEE)
        txns = wallet.transactions.all()
        self.assertEqual(txns.count(), 2)
        self.assertEqual(sum(t.amount for t in txns), PICKUP_FEE + DROPOFF_FEE)
        self.assertTrue(all(f"order #{order.pk}" in t.description for t in txns))

    # -- confirmation code -------------------------------------------------

    def test_wrong_code_is_rejected_and_counted(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        wrong = "000000" if mission.confirmation_code != "000000" else "000001"
        result, error = self.complete(claim, code=wrong)
        self.assertIsNone(result)
        self.assertIn("incorrect", error)
        self.assertIn(f"{MAX_CODE_ATTEMPTS - 1} attempt", error)
        claim.refresh_from_db()
        self.assertEqual(claim.code_attempts, 1)
        self.assertEqual(claim.status, "picked_up")
        self.assertFalse(CourierWallet.objects.filter(user=self.courier).exists())

    def test_the_counter_persists_across_calls_and_locks_after_the_limit(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        wrong = "000000" if mission.confirmation_code != "000000" else "000001"
        for i in range(MAX_CODE_ATTEMPTS):
            result, error = self.complete(claim, code=wrong)
            self.assertIn("incorrect", error)
        self.assertIn("0 attempt", error)

        # Locked out: even the right code no longer works.
        result, error = self.complete(claim)
        self.assertIsNone(result)
        self.assertIn("Too many wrong codes", error)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "picked_up")
        self.assertEqual(claim.code_attempts, MAX_CODE_ATTEMPTS)

    def test_code_is_trimmed(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        result, error = self.complete(claim, code=f"  {mission.confirmation_code}\n")
        self.assertIsNone(error)

    def test_empty_and_none_codes_are_wrong_not_crashes(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        for code in ("", None):
            result, error = services.complete_claim(claim_id=claim.pk, user=self.courier, code=code)
            self.assertIsNone(result)
            self.assertIn("incorrect", error)

    def test_non_ascii_code_is_a_wrong_code_not_a_server_error(self):
        # Egyptian users may type Arabic-Indic digits.
        order, so, mission, claim = self.pickup_claim_picked_up()
        result, error = self.complete(claim, code="١٢٣٤٥٦")
        self.assertIsNone(result)
        self.assertIn("incorrect", error)

    def test_a_correct_code_for_another_mission_is_rejected(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        order2, [so2] = self.order_with_stores(1)
        other = self.ship(so2)
        DeliveryMission.objects.filter(pk=mission.pk).update(confirmation_code="111111")
        DeliveryMission.objects.filter(pk=other.pk).update(confirmation_code="222222")
        result, error = self.complete(claim, code="222222")
        self.assertIsNone(result)
        self.assertIn("incorrect", error)

    # -- cash on delivery --------------------------------------------------

    def test_dropoff_with_cod_requires_the_cash_confirmation(self):
        order, so, dropoff, claim = self.dropoff_claim_picked_up()
        self.assertGreater(dropoff.cod_amount, 0)
        result, error = self.complete(claim, self.courier2, cash=False)
        self.assertIsNone(result)
        self.assertIn("cash", error)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "picked_up")
        self.assertEqual(claim.code_attempts, 0)  # the code was right

    def test_dropoff_with_cod_records_that_cash_was_collected(self):
        order, so, dropoff, claim = self.dropoff_claim_picked_up()
        result, error = self.complete(claim, self.courier2, cash=True)
        self.assertIsNone(error)
        result.refresh_from_db()
        self.assertTrue(result.cash_collected)

    def test_dropoff_without_cod_does_not_ask_for_cash(self):
        order, so, dropoff, claim = self.dropoff_claim_picked_up(payment_type=None)
        self.assertEqual(dropoff.cod_amount, 0)
        result, error = self.complete(claim, self.courier2, cash=False)
        self.assertIsNone(error)
        result.refresh_from_db()
        self.assertFalse(result.cash_collected)

    def test_pickup_leg_never_records_cash_even_if_the_flag_is_sent(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        result, error = self.complete(claim, cash=True)
        self.assertIsNone(error)
        result.refresh_from_db()
        self.assertFalse(result.cash_collected)

    # -- guards ------------------------------------------------------------

    def test_cannot_complete_before_picking_up(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        result, error = self.complete(claim)
        self.assertIsNone(result)
        self.assertIn("Pick the goods up", error)

    def test_only_the_owner_can_complete(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        result, error = self.complete(claim, self.courier2)
        self.assertIsNone(result)
        self.assertIn("Only the courier", error)

    def test_cannot_complete_twice_and_wallet_is_credited_once(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        self.complete(claim)
        result, error = self.complete(claim)
        self.assertIsNone(result)
        self.assertIn("no longer active", error)
        self.assertEqual(CourierWallet.objects.get(user=self.courier).balance, PICKUP_FEE)
        self.assertEqual(CourierWalletTransaction.objects.count(), 1)

    def test_cannot_complete_after_the_window_expired(self):
        order, so, mission, claim = self.pickup_claim_picked_up()
        self.expire(claim)
        result, error = self.complete(claim)
        self.assertIsNone(result)
        self.assertIn("expired", error)
        self.assertFalse(CourierWallet.objects.filter(user=self.courier).exists())

    def test_unknown_claim(self):
        self.assertEqual(
            services.complete_claim(claim_id=777, user=self.courier, code="123456"),
            (None, "Claim not found."),
        )


class MultiStoreOrderFlowTests(DeliveryTestCase):
    """The whole life of an order that spans two merchants."""

    def test_full_lifecycle(self):
        order, [so1, so2] = self.order_with_stores(2)

        # Store 1 ships and its goods reach the warehouse.
        self.deliver_pickup(so1)
        order.refresh_from_db()
        self.assertEqual(order.status, "Processing")  # so2 hasn't shipped yet
        self.assertFalse(DeliveryMission.objects.filter(kind="dropoff").exists())

        # Store 2 ships; its pickup is still outstanding.
        mission2 = self.ship(so2)
        order.refresh_from_db()
        self.assertEqual(order.status, "Shipped")
        self.assertFalse(DeliveryMission.objects.filter(kind="dropoff").exists())

        # Its goods arrive -> the single dropoff mission appears.
        claim2 = self.claim(mission2, self.courier2)
        self.pick_up(claim2, self.courier2)
        self.complete(claim2, self.courier2)
        order.refresh_from_db()
        self.assertEqual(order.status, "At Warehouse")
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        self.assertEqual(dropoff.cod_amount, Decimal("200.00"))
        self.assertEqual(dropoff.item_count, 4)

        # A courier takes the whole order out and hands it over.
        claim3 = self.claim(dropoff)
        self.pick_up(claim3)
        order.refresh_from_db()
        self.assertEqual(order.status, "Out for Delivery")
        result, error = self.complete(claim3)
        self.assertIsNone(error)
        order.refresh_from_db()
        self.assertEqual(order.status, "Delivered")
        self.assertEqual(DeliveryMission.objects.filter(kind="dropoff").count(), 1)

        # Money: courier 1 earned a pickup + the dropoff, courier 2 a pickup.
        self.assertEqual(CourierWallet.objects.get(user=self.courier).balance, PICKUP_FEE + DROPOFF_FEE)
        self.assertEqual(CourierWallet.objects.get(user=self.courier2).balance, PICKUP_FEE)

    def test_the_dropoff_is_created_exactly_once_however_the_pickups_interleave(self):
        order, [so1, so2] = self.order_with_stores(2)
        m1, m2 = self.ship(so1), self.ship(so2)
        c1, c2 = self.claim(m1), self.claim(m2, self.courier2)
        self.pick_up(c1)
        self.pick_up(c2, self.courier2)
        self.complete(c2, self.courier2)
        self.complete(c1)
        self.assertEqual(DeliveryMission.objects.filter(order=order, kind="dropoff").count(), 1)

    def test_work_pool_only_shows_the_dropoff_when_every_store_arrived(self):
        order, [so1, so2] = self.order_with_stores(2)
        self.deliver_pickup(so1)
        self.ship(so2)
        self.assertEqual(services.open_missions("dropoff").count(), 0)


# ===========================================================================
# 11. Expiry sweep
# ===========================================================================


class ReleaseExpiredClaimsTests(DeliveryTestCase):
    def test_returns_zero_when_nothing_expired(self):
        order, [so] = self.order_with_stores(1)
        self.claim(self.ship(so))
        self.assertEqual(services.release_expired_claims(), 0)

    def test_expired_claim_goes_back_to_the_pool_and_courier_is_told(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        claim = self.claim(mission)
        self.expire(claim)
        self.assertEqual(services.release_expired_claims(), 1)

        claim.refresh_from_db()
        mission.refresh_from_db()
        so.refresh_from_db()
        self.assertEqual(claim.status, "expired")
        self.assertEqual(mission.status, "open")
        self.assertIn(mission, services.open_missions())
        self.assertIsNone(so.delivery_person)
        self.assertTrue(any("expired" in n for n in notes_for(self.courier)))

    def test_expired_claim_is_not_swept_twice(self):
        order, [so] = self.order_with_stores(1)
        self.expire(self.claim(self.ship(so)))
        self.assertEqual(services.release_expired_claims(), 1)
        self.assertEqual(services.release_expired_claims(), 0)
        self.assertEqual(len(notes_for(self.courier)), 1)

    def test_picked_up_dropoff_that_expires_reverts_the_order_to_at_warehouse(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        so.refresh_from_db()
        self.assertEqual(so.status, "Out for Delivery")

        self.expire(claim)
        services.release_expired_claims()

        so.refresh_from_db()
        order.refresh_from_db()
        dropoff.refresh_from_db()
        self.assertEqual(so.status, "At Warehouse")
        self.assertEqual(order.status, "At Warehouse")
        self.assertEqual(dropoff.status, "open")
        self.assertIn(dropoff, services.open_missions("dropoff"))
        # ...and a new courier can take it from there.
        self.claim(dropoff, self.courier)

    def test_picked_up_pickup_that_expires_does_not_touch_the_store_order_status(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        self.pick_up(claim)
        self.expire(claim)
        services.release_expired_claims()
        so.refresh_from_db()
        self.assertEqual(so.status, "Shipped")

    def test_fresh_claims_of_other_couriers_are_untouched(self):
        order, [so1] = self.order_with_stores(1)
        order2, [so2] = self.order_with_stores(1)
        old = self.claim(self.ship(so1))
        fresh = self.claim(self.ship(so2), self.courier2)
        self.expire(old)
        services.release_expired_claims()
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, "claimed")

    def test_completed_claims_are_never_expired(self):
        order, [so] = self.order_with_stores(1)
        claim = self.deliver_pickup(so)
        DeliveryClaim.objects.filter(pk=claim.pk).update(deadline=timezone.now() - timedelta(days=3))
        self.assertEqual(services.release_expired_claims(), 0)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "delivered")

    def test_a_claim_exactly_at_its_deadline_counts_as_expired(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        DeliveryClaim.objects.filter(pk=claim.pk).update(deadline=timezone.now())
        self.assertEqual(services.release_expired_claims(), 1)


# ===========================================================================
# 12. Staff force-release & admin
# ===========================================================================


class ForceReleaseTests(DeliveryTestCase):
    def test_force_release_of_a_picked_up_dropoff_reverts_and_notifies(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        admin_user = make_user("boss", is_staff=True)

        result, error = services.force_release_claim(claim_id=claim.pk, staff_user=admin_user)

        self.assertIsNone(error)
        self.assertEqual(result.status, "released")
        so.refresh_from_db()
        self.assertEqual(so.status, "At Warehouse")
        self.assertIn(dropoff, services.open_missions())
        note = Notification.objects.get(recipient=self.courier2)
        self.assertEqual(note.sender, admin_user)
        self.assertIn("returned to the work pool by staff", note.message)

    def test_force_release_of_a_claim_that_is_not_active(self):
        order, [so] = self.order_with_stores(1)
        claim = self.deliver_pickup(so)
        result, error = services.force_release_claim(claim_id=claim.pk)
        self.assertEqual((result, error), (None, "That claim is not active."))

    def test_force_release_of_unknown_claim(self):
        self.assertEqual(
            services.force_release_claim(claim_id=12345),
            (None, "That claim is not active."),
        )

    def test_admin_action_returns_selected_claims_to_the_pool(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        claim = self.claim(mission)
        admin_user = make_user("root", is_staff=True, is_superuser=True)
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse("admin:delivery_deliveryclaim_changelist"),
            {"action": "return_to_pool", "_selected_action": [claim.pk]},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "released")
        self.assertContains(response, "1 claim(s) returned to the pool.")

    def test_admin_changelists_render(self):
        order, [so] = self.order_with_stores(1)
        self.claim(self.ship(so))
        CourierWallet.objects.create(user=self.courier)
        self.client.force_login(make_user("root2", is_staff=True, is_superuser=True))
        for name in (
            "warehouse", "storepickuplocation", "deliverymission",
            "deliveryclaim", "courierwallet", "courierwallettransaction",
        ):
            response = self.client.get(reverse(f"admin:delivery_{name}_changelist"))
            self.assertEqual(response.status_code, 200, name)


# ===========================================================================
# 13. Work pool query
# ===========================================================================


class OpenMissionsTests(DeliveryTestCase):
    def test_claimed_missions_leave_the_pool_and_return_when_released(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        self.assertIn(mission, services.open_missions())
        claim = self.claim(mission)
        self.assertNotIn(mission, services.open_missions())
        services.release_claim(claim_id=claim.pk, user=self.courier)
        self.assertIn(mission, services.open_missions())

    def test_a_mission_with_history_appears_once(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        for courier in (self.courier, self.courier2):
            services.release_claim(
                claim_id=self.claim(mission, courier).pk, user=courier
            )
        self.assertEqual(list(services.open_missions()), [mission])

    def test_only_open_missions_are_listed(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        DeliveryMission.objects.filter(pk=mission.pk).update(status="completed")
        self.assertEqual(services.open_missions().count(), 0)

    def test_kind_filter(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        order2, [so2] = self.order_with_stores(1)
        pickup = self.ship(so2)
        self.assertEqual(set(services.open_missions()), {pickup, dropoff})
        self.assertEqual(list(services.open_missions("pickup")), [pickup])
        self.assertEqual(list(services.open_missions("dropoff")), [dropoff])
        self.assertEqual(set(services.open_missions("nonsense")), {pickup, dropoff})


# ===========================================================================
# 14. Views
# ===========================================================================


class WorkPoolViewTests(DeliveryTestCase):
    url = property(lambda self: reverse("delivery:work_pool"))

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertRedirects(
            response, f"{settings.LOGIN_URL}?next={self.url}", fetch_redirect_response=False
        )

    def test_non_courier_is_sent_home_with_an_error(self):
        self.client.force_login(self.shopper)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertTrue(any("delivery partners" in m for m in messages_of(response)))

    def test_courier_sees_open_missions_and_own_claims(self):
        order, [so1] = self.order_with_stores(1)
        order2, [so2] = self.order_with_stores(1)
        mine = self.claim(self.ship(so1))
        open_one = self.ship(so2)
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["available_missions"]), [open_one])
        self.assertEqual(list(response.context["my_active_claims"]), [mine])

    def test_other_couriers_claims_are_not_listed_as_mine(self):
        order, [so] = self.order_with_stores(1)
        self.claim(self.ship(so), self.courier2)
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["my_active_claims"]), [])
        self.assertEqual(list(response.context["available_missions"]), [])

    def test_kind_filter(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        order2, [so2] = self.order_with_stores(1)
        pickup = self.ship(so2)
        self.client.force_login(self.courier)
        self.assertEqual(
            list(self.client.get(self.url, {"kind": "pickup"}).context["available_missions"]), [pickup]
        )
        self.assertEqual(
            list(self.client.get(self.url, {"kind": "dropoff"}).context["available_missions"]), [dropoff]
        )

    def test_visiting_sweeps_expired_claims(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        self.expire(self.claim(mission, self.courier2))
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["available_missions"]), [mission])

    def test_pool_never_reveals_the_full_dropoff_address_or_codes(self):
        order, [so], dropoff = self.order_at_warehouse(1, address="77 Secret Lane, Zamalek, Cairo, Egypt")
        DeliveryMission.objects.filter(pk=dropoff.pk).update(confirmation_code="482916")
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertContains(response, "Cairo, Egypt")
        self.assertNotContains(response, "77 Secret Lane")
        self.assertNotContains(response, "482916")


class ClaimMissionViewTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.order, [self.so] = self.order_with_stores(1)
        self.mission = self.ship(self.so)
        self.url = reverse("delivery:claim_mission", args=[self.mission.pk])

    def test_anonymous_is_sent_to_login(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(settings.LOGIN_URL, response["Location"])
        self.assertFalse(DeliveryClaim.objects.exists())

    def test_non_courier_cannot_claim_through_the_view(self):
        self.client.force_login(self.shopper)
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertFalse(DeliveryClaim.objects.exists())

    def test_get_does_not_claim(self):
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("delivery:work_pool"), fetch_redirect_response=False)
        self.assertFalse(DeliveryClaim.objects.exists())

    def test_post_claims_and_redirects_to_the_claim_page(self):
        self.client.force_login(self.courier)
        response = self.client.post(self.url)
        claim = DeliveryClaim.objects.get()
        self.assertRedirects(
            response, reverse("delivery:claim_detail", args=[claim.pk]), fetch_redirect_response=False
        )
        self.assertTrue(any("24 hours" in m for m in messages_of(response)))

    def test_losing_the_race_flashes_an_error_and_returns_to_the_pool(self):
        self.claim(self.mission, self.courier2)
        self.client.force_login(self.courier)
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse("delivery:work_pool"), fetch_redirect_response=False)
        self.assertTrue(any("Someone else" in m for m in messages_of(response)))

    def test_unknown_mission_id(self):
        self.client.force_login(self.courier)
        response = self.client.post(reverse("delivery:claim_mission", args=[999999]))
        self.assertRedirects(response, reverse("delivery:work_pool"), fetch_redirect_response=False)
        self.assertTrue(any("not found" in m for m in messages_of(response)))


class ClaimDetailViewTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.order, [self.so] = self.order_with_stores(1)
        self.mission = self.ship(self.so)
        self.claim_obj = self.claim(self.mission)
        self.url = reverse("delivery:claim_detail", args=[self.claim_obj.pk])

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_owner_can_view_and_act(self):
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_act"])
        self.assertFalse(response.context["needs_cash"])

    def test_other_courier_is_refused(self):
        self.client.force_login(self.courier2)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertTrue(any("permission" in m for m in messages_of(response)))

    def test_shopper_is_refused(self):
        self.client.force_login(self.shopper)
        self.assertRedirects(self.client.get(self.url), reverse("home"), fetch_redirect_response=False)

    def test_staff_can_view_but_not_act(self):
        self.client.force_login(make_user("inspector", is_staff=True))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_act"])

    def test_missing_claim_is_a_404(self):
        self.client.force_login(self.courier)
        self.assertEqual(
            self.client.get(reverse("delivery:claim_detail", args=[999999])).status_code, 404
        )

    def test_opening_an_expired_claim_releases_it(self):
        self.expire(self.claim_obj)
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_act"])
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "expired")
        self.assertIn(self.mission, services.open_missions())

    def test_dropoff_with_cod_flags_needs_cash(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        claim = self.claim(dropoff, self.courier2)
        self.client.force_login(self.courier2)
        response = self.client.get(reverse("delivery:claim_detail", args=[claim.pk]))
        self.assertTrue(response.context["needs_cash"])
        self.assertContains(response, "200.00" if False else f"{dropoff.cod_amount:.2f}")

    def test_the_confirmation_code_is_never_shown_to_the_courier(self):
        DeliveryMission.objects.filter(pk=self.mission.pk).update(confirmation_code="731904")
        self.client.force_login(self.courier)
        self.assertNotContains(self.client.get(self.url), "731904")
        self.pick_up(self.claim_obj)
        self.assertNotContains(self.client.get(self.url), "731904")


class ClaimActionViewTests(DeliveryTestCase):
    """pickup / complete / release endpoints."""

    def setUp(self):
        super().setUp()
        self.order, [self.so] = self.order_with_stores(1)
        self.mission = self.ship(self.so)
        self.claim_obj = self.claim(self.mission)
        self.detail = reverse("delivery:claim_detail", args=[self.claim_obj.pk])
        self.client.force_login(self.courier)

    def urls(self):
        return {
            "pickup": reverse("delivery:pick_up_claim", args=[self.claim_obj.pk]),
            "complete": reverse("delivery:complete_claim", args=[self.claim_obj.pk]),
            "release": reverse("delivery:release_claim", args=[self.claim_obj.pk]),
        }

    def test_get_never_changes_anything(self):
        for url in self.urls().values():
            self.assertRedirects(self.client.get(url), self.detail, fetch_redirect_response=False)
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "claimed")

    def test_anonymous_cannot_use_any_action(self):
        self.client.logout()
        for url in self.urls().values():
            self.assertEqual(self.client.post(url).status_code, 302)
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "claimed")

    def test_non_courier_cannot_use_any_action(self):
        self.client.force_login(self.shopper)
        for url in self.urls().values():
            self.assertRedirects(self.client.post(url), reverse("home"), fetch_redirect_response=False)
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "claimed")

    def test_pickup_post(self):
        response = self.client.post(self.urls()["pickup"])
        self.assertRedirects(response, self.detail, fetch_redirect_response=False)
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "picked_up")
        self.assertTrue(any("picked up" in m for m in messages_of(response)))

    def test_pickup_post_twice_flashes_an_error(self):
        self.client.post(self.urls()["pickup"])
        response = self.client.post(self.urls()["pickup"])
        self.assertTrue(any("already picked up" in m for m in messages_of(response)))

    def test_another_courier_cannot_drive_my_claim(self):
        self.client.force_login(self.courier2)
        response = self.client.post(self.urls()["pickup"])
        self.assertTrue(any("Only the courier" in m for m in messages_of(response)))
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "claimed")

    def test_release_post_returns_to_the_pool(self):
        response = self.client.post(self.urls()["release"])
        self.assertRedirects(response, reverse("delivery:work_pool"), fetch_redirect_response=False)
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "released")

    def test_release_error_stays_on_the_claim_page(self):
        self.pick_up(self.claim_obj)
        response = self.client.post(self.urls()["release"])
        self.assertRedirects(response, self.detail, fetch_redirect_response=False)
        self.assertTrue(any("contact support" in m for m in messages_of(response)))

    def test_complete_with_wrong_code_flashes_an_error(self):
        self.pick_up(self.claim_obj)
        self.mission.refresh_from_db()
        wrong = "000000" if self.mission.confirmation_code != "000000" else "000001"
        response = self.client.post(self.urls()["complete"], {"code": wrong})
        self.assertTrue(any("incorrect" in m for m in messages_of(response)))
        self.claim_obj.refresh_from_db()
        self.assertEqual(self.claim_obj.status, "picked_up")

    def test_complete_with_right_code_credits_the_wallet(self):
        self.pick_up(self.claim_obj)
        self.mission.refresh_from_db()
        response = self.client.post(
            self.urls()["complete"], {"code": self.mission.confirmation_code}
        )
        self.assertRedirects(response, self.detail, fetch_redirect_response=False)
        self.assertTrue(any("wallet was credited" in m for m in messages_of(response)))
        self.assertEqual(CourierWallet.objects.get(user=self.courier).balance, PICKUP_FEE)

    def test_cash_checkbox_value_on_is_what_counts(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        self.client.force_login(self.courier2)
        url = reverse("delivery:complete_claim", args=[claim.pk])
        dropoff.refresh_from_db()

        no_cash = self.client.post(url, {"code": dropoff.confirmation_code})
        self.assertTrue(any("cash" in m for m in messages_of(no_cash)))
        with_cash = self.client.post(url, {"code": dropoff.confirmation_code, "cash_collected": "on"})
        self.assertTrue(any("wallet was credited" in m for m in messages_of(with_cash)))
        claim.refresh_from_db()
        self.assertTrue(claim.cash_collected)

    def test_cash_checkbox_with_a_value_other_than_on_is_ignored(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        claim = self.claim(dropoff, self.courier2)
        self.pick_up(claim, self.courier2)
        self.client.force_login(self.courier2)
        response = self.client.post(
            reverse("delivery:complete_claim", args=[claim.pk]),
            {"code": dropoff.confirmation_code, "cash_collected": "true"},
        )
        self.assertTrue(any("cash" in m for m in messages_of(response)))


class WalletViewTests(DeliveryTestCase):
    url = property(lambda self: reverse("delivery:my_wallet"))

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_non_courier_is_sent_home(self):
        self.client.force_login(self.shopper)
        self.assertRedirects(self.client.get(self.url), reverse("home"), fetch_redirect_response=False)

    def test_first_visit_creates_an_empty_wallet(self):
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["wallet"].balance, Decimal("0.00"))
        self.assertEqual(CourierWallet.objects.filter(user=self.courier).count(), 1)

    def test_shows_balance_transactions_and_history_but_not_active_claims(self):
        order, [so1] = self.order_with_stores(1)
        self.deliver_pickup(so1)
        order2, [so2] = self.order_with_stores(1)
        active = self.claim(self.ship(so2))
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertEqual(response.context["wallet"].balance, PICKUP_FEE)
        self.assertEqual(len(response.context["transactions"]), 1)
        history = list(response.context["history"])
        self.assertEqual(len(history), 1)
        self.assertNotIn(active, history)

    def test_couriers_only_see_their_own_money(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so, self.courier2)
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertEqual(response.context["wallet"].balance, Decimal("0.00"))
        self.assertEqual(len(response.context["transactions"]), 0)
        self.assertEqual(len(response.context["history"]), 0)

    def test_history_is_capped_at_25_rows(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        for _ in range(30):
            claim = self.claim(mission)
            services.release_claim(claim_id=claim.pk, user=self.courier)
        self.client.force_login(self.courier)
        self.assertEqual(len(self.client.get(self.url).context["history"]), 25)


class WarehouseIncomingViewTests(DeliveryTestCase):
    url = property(lambda self: reverse("delivery:warehouse_incoming"))

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_courier_who_is_not_warehouse_staff_is_refused(self):
        self.client.force_login(self.courier)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertTrue(any("warehouse staff" in m for m in messages_of(response)))

    def test_shopper_is_refused(self):
        self.client.force_login(self.shopper)
        self.assertRedirects(self.client.get(self.url), reverse("home"), fetch_redirect_response=False)

    def test_staff_sees_the_code_of_couriers_who_are_on_their_way_in(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        DeliveryMission.objects.filter(pk=mission.pk).update(confirmation_code="246810")
        claim = self.claim(mission)
        self.client.force_login(self.staff)

        # Merely claimed -> the courier hasn't left the store yet: no code.
        self.assertNotContains(self.client.get(self.url), "246810")

        self.pick_up(claim)
        response = self.client.get(self.url)
        self.assertContains(response, "246810")
        self.assertEqual(list(response.context["arriving_claims"]), [claim])

    def test_staff_sees_dropoffs_waiting_for_a_courier(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["awaiting_dropoff"]), [dropoff])
        self.claim(dropoff)
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["awaiting_dropoff"]), [])

    def test_completed_handovers_disappear_from_arriving(self):
        order, [so] = self.order_with_stores(1)
        self.deliver_pickup(so)
        self.client.force_login(self.staff)
        self.assertEqual(list(self.client.get(self.url).context["arriving_claims"]), [])

    def test_expired_couriers_disappear_from_arriving(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        self.pick_up(claim)
        self.expire(claim)
        self.client.force_login(self.staff)
        self.assertEqual(list(self.client.get(self.url).context["arriving_claims"]), [])


class OrderTrackingViewTests(DeliveryTestCase):
    def url(self, order):
        return reverse("delivery:order_tracking", args=[order.pk])

    def test_anonymous_is_sent_to_login(self):
        order, _ = self.order_with_stores(1)
        self.assertEqual(self.client.get(self.url(order)).status_code, 302)

    def test_owner_sees_their_order(self):
        order, _ = self.order_with_stores(1)
        self.client.force_login(self.shopper)
        response = self.client.get(self.url(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["order"], order)
        self.assertIsNone(response.context["confirmation_code"])

    def test_someone_elses_order_is_a_404(self):
        order, _ = self.order_with_stores(1)
        self.client.force_login(make_user("nosy"))
        self.assertEqual(self.client.get(self.url(order)).status_code, 404)

    def test_couriers_cannot_track_orders_they_do_not_own(self):
        order, _ = self.order_with_stores(1)
        self.client.force_login(self.courier)
        self.assertEqual(self.client.get(self.url(order)).status_code, 404)

    def test_dropoff_code_is_revealed_only_while_the_courier_is_on_the_way(self):
        order, [so], dropoff = self.order_at_warehouse(1)
        DeliveryMission.objects.filter(pk=dropoff.pk).update(confirmation_code="135790")
        self.client.force_login(self.shopper)

        self.assertNotContains(self.client.get(self.url(order)), "135790")

        claim = self.claim(dropoff, self.courier2)
        self.assertNotContains(self.client.get(self.url(order)), "135790")  # claimed, not left yet

        self.pick_up(claim, self.courier2)
        response = self.client.get(self.url(order))
        self.assertContains(response, "135790")
        self.assertEqual(response.context["confirmation_code"], "135790")
        self.assertEqual(response.context["dropoff_claim"], claim)

        self.complete(claim, self.courier2, code="135790")
        self.assertNotContains(self.client.get(self.url(order)), "135790")

    def test_pickup_codes_are_never_shown_to_the_shopper(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        DeliveryMission.objects.filter(pk=mission.pk).update(confirmation_code="864209")
        self.pick_up(self.claim(mission))
        self.client.force_login(self.shopper)
        self.assertNotContains(self.client.get(self.url(order)), "864209")


class EndToEndViewFlowTest(DeliveryTestCase):
    def test_courier_completes_a_pickup_entirely_through_the_ui(self):
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        self.client.force_login(self.courier)

        self.client.post(reverse("delivery:claim_mission", args=[mission.pk]))
        claim = DeliveryClaim.objects.get()
        self.client.post(reverse("delivery:pick_up_claim", args=[claim.pk]))
        mission.refresh_from_db()
        self.client.post(
            reverse("delivery:complete_claim", args=[claim.pk]), {"code": mission.confirmation_code}
        )

        claim.refresh_from_db()
        so.refresh_from_db()
        self.assertEqual(claim.status, "delivered")
        self.assertEqual(so.status, "At Warehouse")
        wallet_page = self.client.get(reverse("delivery:my_wallet"))
        self.assertEqual(wallet_page.context["wallet"].balance, PICKUP_FEE)


# ===========================================================================
# 15. Management commands
# ===========================================================================


class BackfillCommandTests(DeliveryTestCase):
    def run_command(self):
        out, err = StringIO(), StringIO()
        call_command("backfill_delivery_missions", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def silently_set(self, store_order, status):
        StoreOrder.objects.filter(pk=store_order.pk).update(status=status)

    def test_creates_missing_pickup_and_dropoff_missions(self):
        order1, [so1] = self.order_with_stores(1)
        order2, [so2] = self.order_with_stores(1)
        self.silently_set(so1, "Shipped")       # shipped without a signal
        self.silently_set(so2, "At Warehouse")  # arrived without a signal
        out, err = self.run_command()
        self.assertIn("Created 1 pickup and 1 dropoff", out)
        self.assertEqual(err, "")
        self.assertTrue(DeliveryMission.objects.filter(store_order=so1, kind="pickup").exists())
        self.assertTrue(DeliveryMission.objects.filter(order=order2, kind="dropoff").exists())

    def test_is_safe_to_run_repeatedly(self):
        order, [so] = self.order_with_stores(1)
        self.silently_set(so, "Shipped")
        self.run_command()
        out, _ = self.run_command()
        self.assertIn("Created 0 pickup and 0 dropoff", out)
        self.assertEqual(DeliveryMission.objects.count(), 1)

    def test_reports_the_missing_warehouse_instead_of_crashing(self):
        Warehouse.objects.all().delete()
        order, [so] = self.order_with_stores(1)
        self.silently_set(so, "Shipped")
        out, err = self.run_command()
        self.assertIn("warehouse", err.lower())
        self.assertIn("Created 0 pickup and 0 dropoff", out)

    def test_waits_for_every_store_order_before_creating_a_dropoff(self):
        order, [so1, so2] = self.order_with_stores(2)
        self.silently_set(so1, "At Warehouse")
        self.silently_set(so2, "Shipped")
        out, _ = self.run_command()
        self.assertIn("1 pickup and 0 dropoff", out)

    def test_does_not_touch_orders_that_are_not_shipped_or_at_warehouse(self):
        order, [so] = self.order_with_stores(1)
        out, _ = self.run_command()
        self.assertIn("Created 0 pickup and 0 dropoff", out)
        self.assertFalse(DeliveryMission.objects.exists())


class ReleaseExpiredCommandTests(DeliveryTestCase):
    def test_reports_and_releases(self):
        order, [so] = self.order_with_stores(1)
        claim = self.claim(self.ship(so))
        self.expire(claim)
        out = StringIO()
        call_command("release_expired_delivery_claims", stdout=out)
        self.assertIn("Released 1 expired claim(s).", out.getvalue())
        claim.refresh_from_db()
        self.assertEqual(claim.status, "expired")

    def test_nothing_to_do(self):
        out = StringIO()
        call_command("release_expired_delivery_claims", stdout=out)
        self.assertIn("Released 0 expired claim(s).", out.getvalue())


# ===========================================================================
# 16. KNOWN BUGS — each asserts the CORRECT behavior and is an expected
#     failure today. Remove the decorator once the bug is fixed.
# ===========================================================================


class KnownBugTests(DeliveryTestCase):
    @unittest.expectedFailure
    def test_BUG1_area_hint_must_not_reveal_a_short_address_in_full(self):
        """area_hint promises 'coarse location ... without seeing a shopper's
        full address before they claim'. For the very common two-part address
        'Street, City' (the checkout placeholder is 'Street, city, postal
        code...'), the last two parts ARE the whole address, so the work pool
        shows house/street to everyone before anyone claims."""
        self.assertNotEqual(area_hint("12 Tahrir St, Cairo"), "12 Tahrir St, Cairo")
        self.assertNotIn("12 Tahrir St", area_hint("12 Tahrir St, Cairo"))

    @unittest.expectedFailure
    def test_BUG1b_a_single_part_address_is_not_echoed_back_in_the_pool(self):
        self.assertNotIn("Apartment 5", area_hint("12 Tahrir Street Apartment 5"))

    @unittest.expectedFailure
    def test_BUG2_cancelling_a_store_order_after_the_dropoff_exists_updates_cod_and_items(self):
        """handle_store_order_cancelled only closes the cancelled store
        order's own PICKUP mission. A dropoff already in the pool keeps the
        stale COD total and item list, so the courier collects cash for goods
        that will not be delivered. (Reachable via admin: the merchant form
        forbids cancelling past Processing.)"""
        order, [so1, so2], dropoff = self.order_at_warehouse(2)
        self.assertEqual(dropoff.cod_amount, Decimal("200.00"))

        so2.refresh_from_db()
        so2.status = "Cancelled"
        so2.save()

        live = (
            DeliveryMission.objects.filter(order=order, kind="dropoff")
            .exclude(status="cancelled")
            .first()
        )
        self.assertIsNotNone(live)
        self.assertEqual(live.cod_amount, so1.subtotal)
        self.assertEqual([i["product"] for i in live.items_snapshot], ["Item 1"])

    @unittest.expectedFailure
    def test_BUG2b_cancelling_every_store_order_closes_the_open_dropoff(self):
        """If everything is cancelled after the dropoff exists, the mission
        stays in the work pool; a courier can claim, 'pick up' nothing, type
        the code and be paid the fee."""
        order, [so], dropoff = self.order_at_warehouse(1)
        so.refresh_from_db()
        so.status = "Cancelled"
        so.save()
        self.assertEqual(services.open_missions("dropoff").count(), 0)

    @unittest.expectedFailure
    def test_BUG3_a_courier_cannot_deliver_their_own_order(self):
        """Nothing stops the shopper from also being the courier. They claim
        the dropoff of their own order, receive the confirmation code in
        their own notifications, type it in and collect the fee for handing
        goods to themselves."""
        shopper_courier = make_courier("self-dealer")
        order, [so], dropoff = self.order_at_warehouse(1, shopper=shopper_courier)
        claim, error = services.claim_mission(mission_id=dropoff.pk, user=shopper_courier)
        self.assertIsNone(claim)
        self.assertIsNotNone(error)

    @unittest.expectedFailure
    def test_BUG4_warehouse_page_must_not_show_a_courier_their_own_handover_code(self):
        """is_delivery_person and is_warehouse_staff are independent flags. A
        user with both claims a pickup, opens /delivery/warehouse/incoming/ (which
        prints the code meant for the RECEIVER) and completes the handover
        without moving any goods."""
        both = make_courier("both-hats")
        both.profile.is_warehouse_staff = True
        both.profile.save()
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        DeliveryMission.objects.filter(pk=mission.pk).update(confirmation_code="975310")
        claim = self.claim(mission, both)
        self.pick_up(claim, both)

        self.client.force_login(both)
        response = self.client.get(reverse("delivery:warehouse_incoming"))
        self.assertNotContains(response, "975310")