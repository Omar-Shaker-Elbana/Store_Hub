from django import forms
from .models import Product, Review, Spec

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            'name',
            'description',
            'category',
            'manufacturing_price',
            'selling_price',
            'current_stock'
        ]

class SpecForm(forms.ModelForm):
    class Meta:
        model = Spec
        fields = ['name', 'value']
        
class Review_Form(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['stars', 'comment']
