from django import forms

from .models import (Membership, MembershipInvitation, Niche, Store,
                     SuggestedNiche)


class StoreForm(forms.ModelForm):
    class Meta:
        model = Store
        fields = [
            "name",
            "description",
            "niche",
            "main_picture",
            "cover_picture",
            "nationality",
        ]

    def __init__(self, *args, require_name=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["niche"].empty_label = "Select a niche"
        if require_name:
            self.fields["name"].required = True


class MembershipInvitationForm(forms.ModelForm):
    class Meta:
        model = MembershipInvitation
        fields = ["invitee_email", "role", "wage_type", "wage"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invitee_email"].required = True

    def clean(self):

        cleaned_data = super().clean()
        wage_type = cleaned_data.get("wage_type")
        wage = cleaned_data.get("wage")
        if wage_type == "percentage" and (wage is None or wage > 100):
            self.add_error("wage", "Percentage wage must be between 0 and 100.")
        return cleaned_data


class MembershipForm(forms.ModelForm):
    class Meta:
        model = Membership
        fields = ["role", "wage_type", "wage"]

    def clean(self):
        cleaned_data = super().clean()
        wage_type = cleaned_data.get("wage_type")
        wage = cleaned_data.get("wage")
        if wage_type == "percentage" and (wage is None or wage > 100):
            self.add_error("wage", "Percentage wage must be between 0 and 100.")
        return cleaned_data


class SuggestNicheForm(forms.ModelForm):
    class Meta:
        model = SuggestedNiche
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = True

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Please enter a niche name.")
        return name
