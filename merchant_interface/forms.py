from django import forms
from django.db.models import Sum

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

    def __init__(self, *args, inviter_membership=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invitee_email"].required = True
        # Passed in from the view so an owner invitation can be checked
        # against how much profit share the inviter currently has to give.
        self.inviter_membership = inviter_membership

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        wage_type = cleaned_data.get("wage_type")
        wage = cleaned_data.get("wage")

        if role == "owner":
            # Owners are always paid in profit percentage, regardless of
            # what was submitted in the wage_type field.
            cleaned_data["wage_type"] = "percentage"
            if wage is None or wage <= 0:
                self.add_error(
                    "wage", "Enter the profit percentage you want to give."
                )
            elif self.inviter_membership is not None:
                available = self.inviter_membership.wage or 0
                if wage > available:
                    self.add_error(
                        "wage",
                        f"You only own {available}% of this store's profit — "
                        "you can't give away more than that.",
                    )
        elif wage_type == "percentage" and (wage is None or wage > 100):
            self.add_error("wage", "Percentage wage must be between 0 and 100.")

        return cleaned_data


class MembershipForm(forms.ModelForm):
    class Meta:
        model = Membership
        fields = ["role", "wage_type", "wage"]

    def __init__(
        self, *args, target_membership=None, requester_membership=None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        # The membership whose role/wage this form is proposing to change —
        # passed explicitly since this form isn't bound to `instance` when
        # used to build a MembershipChangeRequest.
        self.target_membership = target_membership
        # The membership of whoever is submitting this form. Any increase
        # to the target's owner percentage is funded out of this person's
        # own share, not conjured from nowhere.
        self.requester_membership = requester_membership

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        wage_type = cleaned_data.get("wage_type")
        wage = cleaned_data.get("wage")

        if role == "owner":
            # Owners are always paid in profit percentage.
            cleaned_data["wage_type"] = "percentage"
            if wage is None or wage <= 0:
                self.add_error("wage", "Enter the profit percentage for this owner.")
            else:
                current_wage = (
                    (self.target_membership.wage or 0)
                    if self.target_membership
                    and self.target_membership.role == "owner"
                    else 0
                )
                requester_wage = (
                    (self.requester_membership.wage or 0)
                    if self.requester_membership
                    else 0
                )
                increase = wage - current_wage
                if increase > 0 and increase > requester_wage:
                    self.add_error(
                        "wage",
                        f"You only have {requester_wage}% available to give — "
                        f"that's {increase - requester_wage}% short of what "
                        "this would require.",
                    )

                if self.target_membership is not None:
                    # Exclude both the target AND the requester's own
                    # membership — their combined share is a pool that can
                    # be freely reallocated between the two of them. Only
                    # every OTHER owner's share is a fixed floor.
                    exclude_pks = [self.target_membership.pk]
                    if self.requester_membership is not None:
                        exclude_pks.append(self.requester_membership.pk)
                    other_owned = (
                        Membership.objects.filter(
                            store=self.target_membership.store, role="owner"
                        )
                        .exclude(pk__in=exclude_pks)
                        .aggregate(total=Sum("wage"))["total"]
                        or 0
                    )
                    if other_owned + wage > 100:
                        self.add_error(
                            "wage",
                            f"Other owners hold {other_owned}% of this "
                            f"store — {wage}% would push the total over 100%.",
                        )
        elif wage_type == "percentage" and (wage is None or wage > 100):
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
