from django import forms
from django.core.exceptions import ValidationError

from .models import CartItem, WishlistItem


class CartItemForm(forms.ModelForm):
    """Quantity is capped against current_stock here — server-side, not just
    the HTML max= attribute (bug #4 in your notes)."""

    class Meta:
        model = CartItem
        fields = ["quantity"]

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        # allow the view to pass the product explicitly (e.g. add-to-cart flow)
        # or fall back to the instance's product (e.g. editing an existing CartItem)
        self.product = product or getattr(self.instance, "product", None)

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is None:
            return quantity
        if quantity < 1:
            raise ValidationError("Quantity must be at least 1.")
        stock = getattr(self.product, "current_stock", None)
        if stock is not None and quantity > stock:
            raise ValidationError(f"Only {stock} left in stock.")
        return quantity