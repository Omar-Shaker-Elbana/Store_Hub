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

    def clean(self):
        cleaned_data = super().clean()
        mfg = cleaned_data.get('manufacturing_price')
        sell = cleaned_data.get('selling_price')
        if mfg is not None and sell is not None and sell < mfg:
            self.add_error('selling_price', "Selling price is below manufacturing price.")
        return cleaned_data
        
class SpecForm(forms.ModelForm):
    class Meta:
        model = Spec
        fields = ['spec_type', 'value']

class Review_Form(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['stars', 'comment']

class Suggest_Category_Form(forms.Form):
    category_name = forms.CharField(max_length=100)