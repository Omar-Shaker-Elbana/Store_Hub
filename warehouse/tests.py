from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from delivery import services as delivery_services
from delivery.models import DeliveryMission
from delivery.tests import (
    DeliveryTestCase,
    make_courier,
    make_user,
    make_warehouse_staff,
    messages_of,
    notes_for,
)
from orders.models import StoreOrder
from payments.rates import shipping_fee
from warehouse import services
from warehouse.models import (
    MAX_ACTIVE_PACK_CLAIMS,
    PACK_CLAIM_DURATION,
    Warehouse,
    WarehouseClaim,
    WarehouseTask,
)


class WarehouseAdminTests(DeliveryTestCase):
    def test_changelist_renders(self):
        self.client.force_login(make_user("root-wh", is_staff=True, is_superuser=True))
        response = self.client.get(reverse("admin:warehouse_warehouse_changelist"))
        self.assertEqual(response.status_code, 200)


class IncomingViewTests(DeliveryTestCase):
    url = property(lambda self: reverse("warehouse:incoming"))

    def test_url_is_under_warehouse(self):
        self.assertEqual(self.url, "/warehouse/incoming/")

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

    def test_a_courier_never_sees_their_own_handover_code(self):
        both = make_courier("both-hats")
        both.profile.is_warehouse_staff = True
        both.profile.save()
        order, [so] = self.order_with_stores(1)
        mission = self.ship(so)
        DeliveryMission.objects.filter(pk=mission.pk).update(confirmation_code="975310")
        claim = self.claim(mission, both)
        self.pick_up(claim, both)

        self.client.force_login(both)
        response = self.client.get(self.url)
        self.assertNotContains(response, "975310")


# ===========================================================================
# Packing tasks: a warehouse worker claims a whole order and packs it; only
# then does the courier-facing dropoff mission exist.
# ===========================================================================


class PackingTestCase(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.staff2 = make_warehouse_staff("wh-staff-2")

    def ready_order(self, n=1, **order_kwargs):
        """An order whose every part has reached the warehouse, so its
        packing task is open. Returns (order, store_orders, task)."""
        order, store_orders = self.order_with_stores(n, **order_kwargs)
        for so in store_orders:
            self.deliver_pickup(so)
        return order, store_orders, WarehouseTask.objects.get(order=order)

    def claim_pack(self, task, worker=None):
        claim, error = services.claim_task(task_id=task.pk, user=worker or self.staff)
        self.assertIsNone(error)
        return claim

    def expire_pack(self, claim):
        WarehouseClaim.objects.filter(pk=claim.pk).update(
            deadline=timezone.now() - timedelta(minutes=1)
        )

    def force(self, store_order, status):
        StoreOrder.objects.filter(pk=store_order.pk).update(status=status)


class PackingTaskCreationTests(PackingTestCase):
    def test_no_task_until_every_store_has_arrived(self):
        order, [so1, so2] = self.order_with_stores(2)
        self.deliver_pickup(so1)
        self.assertFalse(WarehouseTask.objects.filter(order=order).exists())
        self.deliver_pickup(so2)
        self.assertEqual(WarehouseTask.objects.filter(order=order).count(), 1)

    def test_task_is_open_in_the_default_warehouse_and_has_no_dropoff_yet(self):
        order, _, task = self.ready_order(1)
        self.assertEqual(task.status, "open")
        self.assertEqual(task.warehouse, self.warehouse)
        self.assertFalse(DeliveryMission.objects.filter(order=order, kind="dropoff").exists())

    def test_task_snapshots_the_items_of_every_store(self):
        order, _, task = self.ready_order(2)
        self.assertEqual(
            sorted(i["product"] for i in task.items_snapshot), ["Item 1", "Item 2"]
        )
        self.assertEqual(task.item_count, 4)

    def test_cancelled_store_orders_do_not_block_or_appear(self):
        order, [so1, so2] = self.order_with_stores(2)
        self.force(so1, "At Warehouse")
        self.force(so2, "Cancelled")
        task, error = delivery_services.advance_order_if_ready(order)
        self.assertIsNone(error)
        self.assertEqual([i["product"] for i in task.items_snapshot], ["Item 1"])

    def test_shopper_is_told_the_order_is_being_packed(self):
        order, _, _ = self.ready_order(1)
        self.assertTrue(any("packing them now" in n for n in notes_for(self.shopper)))

    def test_missing_warehouse_is_reported_without_a_task_or_notification(self):
        order, [so] = self.order_with_stores(1)
        self.force(so, "At Warehouse")
        Warehouse.objects.all().delete()
        task, error = delivery_services.advance_order_if_ready(order)
        self.assertIsNone(task)
        self.assertIn("warehouse", error.lower())
        self.assertEqual(notes_for(self.shopper), [])

    def test_create_task_is_idempotent(self):
        order, _, task = self.ready_order(1)
        again, error = services.create_task(order)
        self.assertIsNone(error)
        self.assertEqual(again, task)
        self.assertEqual(WarehouseTask.objects.filter(order=order).count(), 1)

    def test_only_one_live_task_per_order_but_cancelled_ones_dont_count(self):
        order, _, task = self.ready_order(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WarehouseTask.objects.create(order=order, warehouse=self.warehouse)
        WarehouseTask.objects.filter(pk=task.pk).update(status="cancelled")
        WarehouseTask.objects.create(order=order, warehouse=self.warehouse)  # fine now

    def test_legacy_orders_that_already_have_a_dropoff_get_no_packing_task(self):
        """Orders that were At Warehouse before this feature already have an
        open dropoff mission; they must keep working untouched."""
        order, [so] = self.order_with_stores(1)
        self.force(so, "At Warehouse")
        delivery_services.create_dropoff_mission(order)
        self.assertEqual(delivery_services.advance_order_if_ready(order), (None, None))
        self.assertFalse(WarehouseTask.objects.filter(order=order).exists())
        self.claim(DeliveryMission.objects.get(order=order, kind="dropoff"))


class ClaimTaskTests(PackingTestCase):
    def test_staff_can_claim_and_gets_a_24_hour_window(self):
        order, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.assertEqual(claim.status, "claimed")
        self.assertEqual(claim.worker, self.staff)
        expected = timezone.now() + PACK_CLAIM_DURATION
        self.assertLess(abs((claim.deadline - expected).total_seconds()), 5)

    def test_claiming_leaves_the_store_order_statuses_alone(self):
        order, sos, task = self.ready_order(2)
        self.claim_pack(task)
        for so in sos:
            so.refresh_from_db()
            self.assertEqual(so.status, "At Warehouse")

    def test_a_claimed_task_leaves_the_pool(self):
        _, _, task = self.ready_order(1)
        self.assertEqual(list(services.open_tasks()), [task])
        self.claim_pack(task)
        self.assertEqual(list(services.open_tasks()), [])

    def test_couriers_and_shoppers_cannot_claim(self):
        _, _, task = self.ready_order(1)
        for user in (self.courier, make_user("random-shopper")):
            claim, error = services.claim_task(task_id=task.pk, user=user)
            self.assertIsNone(claim)
            self.assertIn("warehouse staff", error)

    def test_nobody_can_pack_their_own_order(self):
        self.shopper.profile.is_warehouse_staff = True
        self.shopper.profile.save()
        _, _, task = self.ready_order(1)
        claim, error = services.claim_task(task_id=task.pk, user=self.shopper)
        self.assertIsNone(claim)
        self.assertIn("own order", error)

    def test_a_second_worker_cannot_take_a_claimed_task(self):
        _, _, task = self.ready_order(1)
        self.claim_pack(task)
        claim, error = services.claim_task(task_id=task.pk, user=self.staff2)
        self.assertIsNone(claim)
        self.assertIn("Someone else", error)

    def test_database_allows_only_one_active_claim_per_task(self):
        _, _, task = self.ready_order(1)
        self.claim_pack(task)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WarehouseClaim.objects.create(
                task=task, worker=self.staff2, deadline=timezone.now() + timedelta(hours=1)
            )

    def test_unknown_task(self):
        claim, error = services.claim_task(task_id=999999, user=self.staff)
        self.assertIsNone(claim)
        self.assertEqual(error, "Task not found.")

    def test_completed_and_cancelled_tasks_cannot_be_claimed(self):
        _, _, task = self.ready_order(1)
        for status in ("completed", "cancelled"):
            WarehouseTask.objects.filter(pk=task.pk).update(status=status)
            claim, error = services.claim_task(task_id=task.pk, user=self.staff)
            self.assertIsNone(claim)
            self.assertIn("no longer available", error)

    def test_refused_when_a_part_is_no_longer_at_the_warehouse(self):
        order, [so], task = self.ready_order(1)
        self.force(so, "Shipped")
        claim, error = services.claim_task(task_id=task.pk, user=self.staff)
        self.assertIsNone(claim)
        self.assertIn("at the warehouse", error)

    def test_a_worker_can_hold_at_most_the_cap(self):
        tasks = [self.ready_order(1)[2] for _ in range(MAX_ACTIVE_PACK_CLAIMS + 1)]
        for task in tasks[:-1]:
            self.claim_pack(task)
        claim, error = services.claim_task(task_id=tasks[-1].pk, user=self.staff)
        self.assertIsNone(claim)
        self.assertIn(str(MAX_ACTIVE_PACK_CLAIMS), error)


class ReleaseAndExpiryTests(PackingTestCase):
    def test_release_puts_the_task_back_for_someone_else(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        released, error = services.release_claim(claim_id=claim.pk, user=self.staff)
        self.assertIsNone(error)
        self.assertEqual(released.status, "released")
        self.assertEqual(list(services.open_tasks()), [task])
        self.claim_pack(task, self.staff2)

    def test_only_the_claimant_can_release(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        result, error = services.release_claim(claim_id=claim.pk, user=self.staff2)
        self.assertIsNone(result)
        self.assertIn("Only the worker", error)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "claimed")

    def test_expired_claims_return_to_the_pool_and_notify_the_worker(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.expire_pack(claim)
        self.assertEqual(services.release_expired_claims(), 1)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "expired")
        self.assertEqual(list(services.open_tasks()), [task])
        self.assertTrue(any("expired" in n for n in notes_for(self.staff)))
        self.claim_pack(task, self.staff2)

    def test_live_claims_are_left_alone(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.assertEqual(services.release_expired_claims(), 0)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "claimed")

    def test_an_expired_claim_cannot_be_finished_or_released(self):
        order, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.expire_pack(claim)
        _, error = services.finish_claim(claim_id=claim.pk, user=self.staff)
        self.assertIn("expired", error)
        _, error = services.release_claim(claim_id=claim.pk, user=self.staff)
        self.assertIsNotNone(error)
        self.assertFalse(DeliveryMission.objects.filter(order=order, kind="dropoff").exists())

    def test_the_expiry_command_also_releases_packing_claims(self):
        _, _, task = self.ready_order(1)
        pack_claim = self.claim_pack(task)
        self.expire_pack(pack_claim)
        order2, [so2] = self.order_with_stores(1)
        courier_claim = self.claim(self.ship(so2))
        self.expire(courier_claim)
        out = StringIO()
        call_command("release_expired_delivery_claims", stdout=out)
        self.assertIn("Released 2 expired claim(s).", out.getvalue())


class FinishClaimTests(PackingTestCase):
    def test_finishing_opens_the_dropoff_with_the_whole_order(self):
        order, sos, task = self.ready_order(2)
        claim = self.claim_pack(task)
        finished, error = services.finish_claim(claim_id=claim.pk, user=self.staff)
        self.assertIsNone(error)
        self.assertEqual(finished.status, "finished")
        self.assertIsNotNone(finished.finished_at)
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertIsNotNone(task.completed_at)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        self.assertEqual(dropoff.status, "open")
        self.assertEqual(dropoff.item_count, 4)
        self.assertEqual(dropoff.cod_amount, sum(so.subtotal for so in sos) + shipping_fee(len(sos)))

    def test_couriers_cannot_see_or_claim_the_dropoff_before_packing(self):
        order, _, task = self.ready_order(1)
        self.assertFalse(DeliveryMission.objects.filter(order=order, kind="dropoff").exists())
        self.assertEqual(list(delivery_services.open_missions("dropoff")), [])
        claim = self.claim_pack(task)
        self.assertEqual(list(delivery_services.open_missions("dropoff")), [])
        services.finish_claim(claim_id=claim.pk, user=self.staff)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        self.assertEqual(list(delivery_services.open_missions("dropoff")), [dropoff])
        self.claim(dropoff)

    def test_shopper_is_told_the_order_is_packed(self):
        order, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        services.finish_claim(claim_id=claim.pk, user=self.staff)
        self.assertTrue(any("packed and ready" in n for n in notes_for(self.shopper)))

    def test_order_status_stays_at_warehouse_until_the_courier_picks_up(self):
        order, sos, task = self.ready_order(1)
        claim = self.claim_pack(task)
        services.finish_claim(claim_id=claim.pk, user=self.staff)
        order.refresh_from_db()
        self.assertEqual(order.status, "At Warehouse")
        sos[0].refresh_from_db()
        self.assertEqual(sos[0].status, "At Warehouse")

    def test_only_the_claimant_can_finish(self):
        order, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        result, error = services.finish_claim(claim_id=claim.pk, user=self.staff2)
        self.assertIsNone(result)
        self.assertIn("Only the worker", error)
        task.refresh_from_db()
        self.assertEqual(task.status, "open")
        self.assertFalse(DeliveryMission.objects.filter(order=order, kind="dropoff").exists())

    def test_finishing_twice_never_makes_a_second_dropoff(self):
        order, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        services.finish_claim(claim_id=claim.pk, user=self.staff)
        result, error = services.finish_claim(claim_id=claim.pk, user=self.staff)
        self.assertIsNone(result)
        self.assertIn("no longer active", error)
        self.assertEqual(DeliveryMission.objects.filter(order=order, kind="dropoff").count(), 1)

    def test_unknown_claim(self):
        result, error = services.finish_claim(claim_id=999999, user=self.staff)
        self.assertIsNone(result)
        self.assertEqual(error, "Claim not found.")

    def test_no_active_warehouse_leaves_everything_untouched(self):
        order, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        Warehouse.objects.update(is_active=False)
        result, error = services.finish_claim(claim_id=claim.pk, user=self.staff)
        self.assertIsNone(result)
        self.assertEqual(error, "No active warehouse is configured.")
        claim.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(claim.status, "claimed")
        self.assertEqual(task.status, "open")
        self.assertFalse(DeliveryMission.objects.filter(order=order, kind="dropoff").exists())

    def test_refused_if_a_part_left_the_warehouse_in_the_meantime(self):
        order, [so], task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.force(so, "Shipped")
        result, error = services.finish_claim(claim_id=claim.pk, user=self.staff)
        self.assertIsNone(result)
        self.assertIn("at the warehouse", error)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "claimed")
        self.assertFalse(DeliveryMission.objects.filter(order=order, kind="dropoff").exists())

    def test_full_journey_from_store_to_shopper(self):
        order, sos, task = self.ready_order(2)
        claim = self.claim_pack(task, self.staff2)
        services.finish_claim(claim_id=claim.pk, user=self.staff2)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        courier_claim = self.claim(dropoff, self.courier2)
        self.pick_up(courier_claim, self.courier2)
        order.refresh_from_db()
        self.assertEqual(order.status, "Out for Delivery")
        result, error = self.complete(courier_claim, self.courier2)
        self.assertIsNone(error)
        order.refresh_from_db()
        self.assertEqual(order.status, "Delivered")
        for so in sos:
            so.refresh_from_db()
            self.assertEqual(so.status, "Delivered")
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")


class CancellationTests(PackingTestCase):
    def test_cancelling_every_store_order_closes_an_unclaimed_task(self):
        order, [so], task = self.ready_order(1)
        so.status = "Cancelled"
        so.save()
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(list(services.open_tasks()), [])
        self.assertFalse(DeliveryMission.objects.filter(order=order, kind="dropoff").exists())

    def test_cancelling_every_store_order_cancels_a_held_claim_and_tells_the_worker(self):
        order, [so], task = self.ready_order(1)
        claim = self.claim_pack(task)
        so.status = "Cancelled"
        so.save()
        claim.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(claim.status, "cancelled")
        self.assertEqual(task.status, "cancelled")
        self.assertTrue(any("was cancelled" in n for n in notes_for(self.staff)))

    def test_cancelling_one_of_two_refreshes_the_task_and_keeps_the_claim(self):
        order, [so1, so2], task = self.ready_order(2)
        claim = self.claim_pack(task)
        so2.status = "Cancelled"
        so2.save()
        task.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(task.status, "open")
        self.assertEqual(claim.status, "claimed")
        self.assertEqual([i["product"] for i in task.items_snapshot], ["Item 1"])

        services.finish_claim(claim_id=claim.pk, user=self.staff)
        dropoff = DeliveryMission.objects.get(order=order, kind="dropoff")
        self.assertEqual([i["product"] for i in dropoff.items_snapshot], ["Item 1"])
        self.assertEqual(dropoff.cod_amount, so1.subtotal + shipping_fee(1))


class PackingModelTests(PackingTestCase):
    def test_claim_properties(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.assertTrue(claim.is_active)
        self.assertFalse(claim.is_expired)
        self.assertGreater(claim.time_left, timedelta(hours=23))
        self.expire_pack(claim)
        claim.refresh_from_db()
        self.assertTrue(claim.is_expired)
        self.assertEqual(claim.time_left, timedelta(0))

    def test_inactive_claims_have_no_time_left(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        services.release_claim(claim_id=claim.pk, user=self.staff)
        claim.refresh_from_db()
        self.assertFalse(claim.is_active)
        self.assertFalse(claim.is_expired)
        self.assertEqual(claim.time_left, timedelta(0))

    def test_string_representations(self):
        order, _, task = self.ready_order(1)
        self.assertIn(f"#{order.pk}", str(task))
        claim = self.claim_pack(task)
        self.assertIn(f"#{task.pk}", str(claim))


class PackingAdminTests(PackingTestCase):
    def test_changelists_render(self):
        self.ready_order(1)
        self.client.force_login(make_user("root-pack", is_staff=True, is_superuser=True))
        for name in ("warehousetask", "warehouseclaim"):
            response = self.client.get(reverse(f"admin:warehouse_{name}_changelist"))
            self.assertEqual(response.status_code, 200)


class PackPoolViewTests(PackingTestCase):
    url = property(lambda self: reverse("warehouse:pack_pool"))

    def test_url_is_under_warehouse(self):
        self.assertEqual(self.url, "/warehouse/pool/")

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_non_staff_are_refused(self):
        for user in (self.courier, self.shopper):
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
            self.assertTrue(any("warehouse staff" in m for m in messages_of(response)))

    def test_lists_open_tasks_and_my_claims(self):
        order1, _, task1 = self.ready_order(1)
        order2, _, task2 = self.ready_order(1)
        claim = self.claim_pack(task2)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["available_tasks"]), [task1])
        self.assertEqual(list(response.context["my_active_claims"]), [claim])
        self.assertContains(response, f"Order #{order1.pk}")

    def test_another_workers_claims_are_not_listed_as_mine(self):
        _, _, task = self.ready_order(1)
        self.claim_pack(task, self.staff2)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["available_tasks"]), [])
        self.assertEqual(list(response.context["my_active_claims"]), [])

    def test_expired_claims_are_swept_when_the_page_loads(self):
        _, _, task = self.ready_order(1)
        self.expire_pack(self.claim_pack(task, self.staff2))
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["available_tasks"]), [task])

    def test_incoming_and_delivery_pages_link_to_the_packing_pool(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("warehouse:incoming")), self.url)
        both = make_courier("courier-and-staff")
        both.profile.is_warehouse_staff = True
        both.profile.save()
        self.client.force_login(both)
        self.assertContains(self.client.get(reverse("delivery:work_pool")), self.url)


class PackActionViewTests(PackingTestCase):
    def test_get_requests_never_claim(self):
        _, _, task = self.ready_order(1)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("warehouse:claim_task", args=[task.pk]))
        self.assertRedirects(response, reverse("warehouse:pack_pool"), fetch_redirect_response=False)
        self.assertFalse(WarehouseClaim.objects.exists())

    def test_claim_then_open_the_detail_page(self):
        order, [so], task = self.ready_order(1)
        self.client.force_login(self.staff)
        response = self.client.post(reverse("warehouse:claim_task", args=[task.pk]))
        claim = WarehouseClaim.objects.get(task=task)
        self.assertRedirects(
            response,
            reverse("warehouse:claim_detail", args=[claim.pk]),
            fetch_redirect_response=False,
        )
        detail = self.client.get(reverse("warehouse:claim_detail", args=[claim.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, so.store.name)
        self.assertContains(detail, "Item 1")
        self.assertContains(detail, "Mark as packed")

    def test_claiming_a_taken_task_shows_an_error(self):
        _, _, task = self.ready_order(1)
        self.claim_pack(task, self.staff2)
        self.client.force_login(self.staff)
        response = self.client.post(reverse("warehouse:claim_task", args=[task.pk]))
        self.assertRedirects(response, reverse("warehouse:pack_pool"), fetch_redirect_response=False)
        self.assertTrue(any("Someone else" in m for m in messages_of(response)))

    def test_non_staff_cannot_claim_through_the_view(self):
        _, _, task = self.ready_order(1)
        self.client.force_login(self.courier)
        self.client.post(reverse("warehouse:claim_task", args=[task.pk]))
        self.assertFalse(WarehouseClaim.objects.exists())

    def test_detail_is_private_to_the_worker_but_open_to_admins(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        url = reverse("warehouse:claim_detail", args=[claim.pk])

        self.client.force_login(self.staff2)
        response = self.client.get(url)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertTrue(any("permission" in m for m in messages_of(response)))

        self.client.force_login(make_user("root-detail", is_staff=True, is_superuser=True))
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_detail_of_unknown_claim_is_a_404(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(reverse("warehouse:claim_detail", args=[999999])).status_code, 404
        )

    def test_get_requests_never_finish_or_release(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.client.force_login(self.staff)
        self.client.get(reverse("warehouse:finish_claim", args=[claim.pk]))
        self.client.get(reverse("warehouse:release_claim", args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, "claimed")

    def test_finish_opens_the_dropoff_and_hides_the_buttons(self):
        order, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.client.force_login(self.staff)
        url = reverse("warehouse:claim_detail", args=[claim.pk])
        response = self.client.post(reverse("warehouse:finish_claim", args=[claim.pk]))
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertTrue(any("packed" in m for m in messages_of(response)))
        self.assertTrue(DeliveryMission.objects.filter(order=order, kind="dropoff").exists())
        self.assertNotContains(self.client.get(url), "Mark as packed")

    def test_finish_error_is_shown(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.client.force_login(self.staff2)
        response = self.client.post(reverse("warehouse:finish_claim", args=[claim.pk]))
        self.assertTrue(any("Only the worker" in m for m in messages_of(response)))

    def test_release_returns_the_task_to_the_pool(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.client.force_login(self.staff)
        response = self.client.post(reverse("warehouse:release_claim", args=[claim.pk]))
        self.assertRedirects(response, reverse("warehouse:pack_pool"), fetch_redirect_response=False)
        self.assertEqual(list(services.open_tasks()), [task])

    def test_release_error_is_shown(self):
        _, _, task = self.ready_order(1)
        claim = self.claim_pack(task)
        self.client.force_login(self.staff2)
        response = self.client.post(reverse("warehouse:release_claim", args=[claim.pk]))
        self.assertRedirects(
            response,
            reverse("warehouse:claim_detail", args=[claim.pk]),
            fetch_redirect_response=False,
        )
        self.assertTrue(any("Only the worker" in m for m in messages_of(response)))