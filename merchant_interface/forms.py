from django import forms
from .models import MembershipInvitation, Store, Niche, Membership, SuggestedNiche

class StoreForm(forms.ModelForm):
    class Meta:
        model = Store
        fields = ['name', 'niche', 'picture', 'nationality']
        
class MembershipInvitationForm(forms.ModelForm):
    class Meta:
        model = MembershipInvitation
        fields = ['invitee_email', 'role', 'wage_type', 'wage']

class SuggestNicheForm(forms.ModelForm):
    class Meta:
        model = Niche
        fields = ['name']

class MembershipForm(forms.ModelForm):
    class Meta:
        model = Membership
        fields = ['role', 'wage_type', 'wage']

class SuggestNicheForm(forms.ModelForm):
    class Meta:
        model = SuggestedNiche
        fields = ['name']