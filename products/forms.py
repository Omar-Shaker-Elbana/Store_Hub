import difflib

from django import forms
from django.forms import inlineformset_factory
from django.forms.formsets import DELETION_FIELD_NAME
from django.forms.models import BaseInlineFormSet

from .models import (Category, Product, Product_Image, Review, Spec, SpecType,
                     SuggestedCategory)


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "category",
            "manufacturing_price",
            "selling_price",
            "current_stock",
            "offer",
        ]

    def clean(self):
        cleaned_data = super().clean()
        mfg = cleaned_data.get("manufacturing_price")
        sell = cleaned_data.get("selling_price")
        if mfg is not None and sell is not None and sell < mfg:
            self.add_error(
                "selling_price", "Selling price is below manufacturing price."
            )
        return cleaned_data


class SpecForm(forms.ModelForm):
    spec_type = forms.CharField(
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. Color, Size, Material",
                "autocomplete": "off",
                "class": "spec-type-input",
            }
        ),
    )

    class Meta:
        model = Spec
        fields = ["spec_type", "value"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The model field is a FK, so model_to_dict() would otherwise put the
        # raw SpecType id into this text input. Show the name instead.
        if self.instance and self.instance.pk and self.instance.spec_type_id:
            self.initial["spec_type"] = self.instance.spec_type.name

    def clean_spec_type(self):
        name = (self.cleaned_data.get("spec_type") or "").strip()
        name = " ".join(name.split())  # collapse repeated/internal whitespace
        if not name:
            raise forms.ValidationError("Spec type is required.")

        spec_type = SpecType.objects.filter(name__iexact=name).first()
        if spec_type is None:
            close_matches = difflib.get_close_matches(
                name,
                SpecType.objects.values_list("name", flat=True),
                n=1,
                cutoff=0.8,
            )
            if close_matches:
                raise forms.ValidationError(
                    f'A similar spec type already exists: "{close_matches[0]}". '
                    f"Please use the existing one from the search box, or pick a more distinct name."
                )
            spec_type = SpecType.objects.create(name=name)
        return spec_type

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get(DELETION_FIELD_NAME):
            return cleaned_data

        spec_type = cleaned_data.get("spec_type")
        if spec_type is None or self.instance.product_id is None:
            return cleaned_data

        already_used = (
            Spec.objects.filter(
                product_id=self.instance.product_id, spec_type=spec_type
            )
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if already_used:
            self.add_error(
                "spec_type",
                f'"{spec_type.name}" is already added to this product.',
            )

        return cleaned_data


class BaseSpecFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        seen_spec_type_ids = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue  # this form already has its own errors

            if form.cleaned_data.get(DELETION_FIELD_NAME):
                continue

            spec_type = form.cleaned_data.get("spec_type")
            if spec_type is None:
                continue

            if spec_type.pk in seen_spec_type_ids:
                form.add_error(
                    "spec_type",
                    f'"{spec_type.name}" is already used in another row above.',
                )
            else:
                seen_spec_type_ids.add(spec_type.pk)


SpecFormSet = inlineformset_factory(
    Product,
    Spec,
    form=SpecForm,
    formset=BaseSpecFormSet,
    fields=["spec_type", "value"],
    extra=1,  # blank rows shown for adding new specs
    can_delete=True,  # per-row delete checkbox
)


class Review_Form(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["stars", "comment"]


class Suggest_Category_Form(forms.Form):
    category_name = forms.CharField(max_length=100)

    def clean_category_name(self):
        name = self.cleaned_data["category_name"].strip()
        if Category.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("This category already exists.")
        if SuggestedCategory.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("This category has already been suggested.")
        return name


class BaseProductImageFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        surviving_count = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue  # this form already has its own errors

            if form.cleaned_data.get(DELETION_FIELD_NAME):
                continue

            has_new_file = bool(form.cleaned_data.get("image"))
            has_existing_file = bool(form.instance.pk and form.instance.image)
            if has_new_file or has_existing_file:
                surviving_count += 1

        if surviving_count < 1:
            raise forms.ValidationError("A product must have at least one image.")


ProductImageFormSet = inlineformset_factory(
    Product,
    Product_Image,
    formset=BaseProductImageFormSet,
    fields=["image", "is_primary", "order"],
    widgets={"image": forms.FileInput},  # plain input, no "Currently: <filename>" text
    extra=3,  # empty rows shown by default when creating a product
    can_delete=True,  # lets a merchant remove an image on edit
)


class SuggestedCategoryStatusForm(forms.ModelForm):
    class Meta:
        model = SuggestedCategory
        fields = ["status"]
