from django.core.management.base import BaseCommand

from delivery import services
from delivery.models import DeliveryMission
from orders.models import Order, StoreOrder


class Command(BaseCommand):
    help = (
        "Create any work items that should exist but don't: pickup missions "
        "for Shipped store orders, and warehouse packing tasks for orders "
        "whose store orders are all At Warehouse (the dropoff mission is "
        "created when a warehouse worker finishes that task). Safe to run "
        "repeatedly."
    )

    def handle(self, *args, **options):
        pickups = tasks = 0
        for store_order in StoreOrder.objects.filter(status="Shipped"):
            existed = (
                DeliveryMission.objects.filter(store_order=store_order, kind="pickup")
                .exclude(status="cancelled")
                .exists()
            )
            mission, error = services.create_pickup_mission(store_order)
            if error:
                self.stderr.write(f"StoreOrder #{store_order.pk}: {error}")
            elif not existed:
                pickups += 1
        for order in Order.objects.filter(store_orders__status="At Warehouse").distinct():
            task, error = services.advance_order_if_ready(order)
            if error:
                self.stderr.write(f"Order #{order.pk}: {error}")
            elif task:
                tasks += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {pickups} pickup mission(s) and {tasks} packing task(s)."
            )
        )