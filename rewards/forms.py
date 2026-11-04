from django import forms

from rewards.models import (
    MAX_OPEN_REQUESTS_PER_USER,
    BloodRequest,
    Donation,
)


class DonationForm(forms.ModelForm):
    class Meta:
        model = Donation
        fields = ('blood_group', 'donation_date', 'location', 'blood_request')
        widgets = {
            'donation_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields['blood_request']
        # Only requests this donor pledged to, so the bonus can't be farmed
        # by linking donations to arbitrary requests.
        field.queryset = (
            BloodRequest.objects.filter(responses__donor=user)
            if user is not None else BloodRequest.objects.none()
        )
        field.required = False
        field.empty_label = '-- none --'


class BloodRequestForm(forms.ModelForm):
    class Meta:
        model = BloodRequest
        fields = (
            'blood_group', 'hospital', 'district', 'units_needed',
            'needed_by', 'contact_phone', 'note',
        )
        widgets = {
            'needed_by': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None and not self.is_bound:
            self.fields['contact_phone'].initial = user.phone

    def clean(self):
        cleaned_data = super().clean()
        if self.user is not None:
            open_count = BloodRequest.objects.open_and_current().filter(
                requester=self.user,
            ).count()
            if open_count >= MAX_OPEN_REQUESTS_PER_USER:
                raise forms.ValidationError(
                    f'You already have {MAX_OPEN_REQUESTS_PER_USER} open '
                    'requests. Close one before posting another.'
                )
        return cleaned_data
