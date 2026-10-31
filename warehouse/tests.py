from django.urls import reverse

from delivery.models import DeliveryMission
from delivery.tests import DeliveryTestCase, make_courier, make_user, messages_of


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