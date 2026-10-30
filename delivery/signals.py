import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from orders.models import StoreOrder

from . import services

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=StoreOrder)
def remember_previous_status(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    instance._delivery_prev_status = (
        StoreOrder.objects.filter(pk=instance.pk)
        .values_list("status", flat=True)
        .first()
        if instance.pk
        else None
    )


@receiver(post_save, sender=StoreOrder)
def react_to_store_order_status(sender, instance, **kwargs):
    """Every StoreOrder save keeps Order.status in step; a move to Shipped
    (from fulfillment's finish_claim, the merchant form, or admin alike)
    opens the pickup mission; a move to Cancelled closes it.

    Failures are logged, not raised, so a delivery problem (e.g. no
    warehouse configured yet) can never break fulfillment or merchant
    order management. The backfill_delivery_missions command repairs gaps.
    """
    if kwargs.get("raw") or not instance.order_id:
        return
    previous = getattr(instance, "_delivery_prev_status", None)
    try:
        with transaction.atomic():
            services.sync_order_status(instance.order_id)
            if previous == instance.status:
                return
            if instance.status == "Shipped":
                mission, error = services.create_pickup_mission(instance)
                if error:
                    logger.warning(
                        "No pickup mission for StoreOrder #%s: %s", instance.pk, error
                    )
            elif instance.status == "Cancelled":
                services.handle_store_order_cancelled(instance)
    except Exception:
        logger.exception("delivery: reacting to StoreOrder #%s failed", instance.pk)