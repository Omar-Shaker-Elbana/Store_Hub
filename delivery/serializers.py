from django.utils import timezone
from rest_framework import serializers

from . import conf
from .models import CourierEarning, DeliveryMission, Warehouse


def _seconds_left(mission):
    if not mission.expires_at:
        return None
    return max(0, int((mission.expires_at - timezone.now()).total_seconds()))


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ["id", "name", "address", "phone", "latitude", "longitude"]


class PoolMissionSerializer(serializers.ModelSerializer):
    """What couriers see BEFORE claiming: areas only, no exact addresses or contacts."""

    items_count = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = DeliveryMission
        fields = [
            "id", "kind", "kind_display", "fee", "items_count",
            "pickup_area", "dropoff_area", "attempts", "created_at",
        ]

    def get_items_count(self, obj):
        return sum(int(i.get("quantity", 1)) for i in obj.items_snapshot)


class MissionDetailSerializer(serializers.ModelSerializer):
    """Full details for the courier who holds the mission. Never includes the code."""

    order_id = serializers.ReadOnlyField()
    store_order_id = serializers.ReadOnlyField()
    warehouse = WarehouseSerializer(read_only=True)
    items = serializers.JSONField(source="items_snapshot", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    seconds_remaining = serializers.SerializerMethodField()
    requires_code = serializers.SerializerMethodField()

    class Meta:
        model = DeliveryMission
        fields = [
            "id", "kind", "kind_display", "status", "status_display",
            "order_id", "store_order_id", "warehouse",
            "pickup_address", "pickup_contact", "dropoff_address", "dropoff_contact",
            "items", "fee", "attempts",
            "claimed_at", "picked_up_at", "delivered_at", "expires_at",
            "seconds_remaining", "requires_code",
        ]

    def get_seconds_remaining(self, obj):
        return _seconds_left(obj)

    def get_requires_code(self, obj):
        return obj.kind in conf.CODE_REQUIRED_FOR


class TrackingSerializer(serializers.ModelSerializer):
    """Shopper-facing. Shows the handover code only while a dropoff is underway."""

    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    store_order_id = serializers.ReadOnlyField()

    class Meta:
        model = DeliveryMission
        fields = [
            "id", "kind", "kind_display", "status", "status_display", "store_order_id",
            "claimed_at", "picked_up_at", "delivered_at", "expires_at", "confirmation_code",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        live = instance.status in (DeliveryMission.Status.CLAIMED, DeliveryMission.Status.IN_TRANSIT)
        if not (instance.kind == DeliveryMission.Kind.DROPOFF and live):
            data.pop("confirmation_code", None)
        return data


class EarningSerializer(serializers.ModelSerializer):
    mission_id = serializers.ReadOnlyField()

    class Meta:
        model = CourierEarning
        fields = ["id", "mission_id", "amount", "created_at"]