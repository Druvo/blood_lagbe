from django import forms

from rewards.models import Donation


class DonationForm(forms.ModelForm):
    class Meta:
        model = Donation
        fields = ('blood_group', 'donation_date', 'location')
        widgets = {
            'donation_date': forms.DateInput(attrs={'type': 'date'}),
        }
