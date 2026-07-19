from django import forms
from users.models import Profile
from .models import CartItem, WishlistItem

class Cart_Item_Form(forms.ModelForm):
    class Meta:
        model = CartItem
        fields = ['quantity', ]
