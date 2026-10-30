from rest_framework.permissions import BasePermission


class IsCourier(BasePermission):
    message = "Only approved couriers can use the delivery work pool."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        courier = getattr(user, "courier_profile", None)
        return bool(courier and courier.is_active)