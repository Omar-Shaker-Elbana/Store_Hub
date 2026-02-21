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
            'current_stock',
            'offer',
            'image1',
            'image2',
            'image3',
        ]

class SpecForm(forms.ModelForm):
    class Meta:
        model = Spec
        fields = ['name', 'value']
        
class Review_Form(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['stars', 'comment']

class Suggest_Category_Form(forms.Form):
    category_name = forms.CharField(max_length=100)