from django import forms
from django.core.exceptions import ValidationError

from .models import Order, StoreOrder

class OrderForm(forms.ModelForm):
    """Enforces real requiredness at checkout even though the Order model
    fields are nullable for testing purposes."""

    shipping_address = forms.CharField(max_length=255, required=True)
    payment_type = forms.ChoiceField(choices=Order.PAYMENT_CHOICES, required=True)

    class Meta:
        model = Order
        fields = ["shipping_address", "payment_type"]

    def clean_shipping_address(self):
        address = (self.cleaned_data.get("shipping_address") or "").strip()
        if not address:
            raise ValidationError("Shipping address is required.")
        return address


class StoreOrderStatusForm(forms.ModelForm):
    """For merchant_interface: transition ONE store's slice of an order.
    This is what bug #7 (unreachable reviews) and merchant order management
    both need — nothing in the current codebase moves an order past
    Pending, so this is the missing status-update path."""

    class Meta:
        model = StoreOrder
        fields = ["status", "delivery_person"]

    def clean_status(self):
        status = self.cleaned_data.get("status")
        current = self.instance.status
        allowed_next = {
            "Pending": {"Processing", "Cancelled"},
            "Processing": {"Shipped", "Cancelled"},
            "Shipped": {"Delivered"},
            "Delivered": set(),
            "Cancelled": set(),
        }
        if (
            current
            and status
            and status != current
            and status not in allowed_next.get(current, set())
        ):
            raise ValidationError(f"Cannot move status from '{current}' to '{status}'.")
        return status
