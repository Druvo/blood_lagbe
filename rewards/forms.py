import datetime

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rewards.blood import BLOOD_GROUP_CHOICES
from rewards.models import (
    MAX_OPEN_REQUESTS_PER_USER,
    MAX_REQUESTS_PER_DAY,
    BloodRequest,
    Donation,
    Place,
)


# Sanity bounds on typed dates: nobody donated in 1900, and an "urgent"
# request needed in the year 9999 is a typo or spam.
MAX_DONATION_AGE_DAYS = 365 * 40
MAX_REQUEST_LEAD_DAYS = 60


class PlaceSelect(forms.Select):
    """A select whose options carry their district, so place-select.js can
    narrow the list to the chosen district. Without JS every place shows and
    the form still checks the pair server-side."""

    def create_option(self, name, value, label, selected, index,
                      subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs,
        )
        instance = getattr(value, 'instance', None)
        if instance is not None:
            option['attrs']['data-district'] = instance.district
        return option


class PlaceFieldsMixin:
    """district + place select + typed fallback, kept consistent.

    Subclasses set `text_field` ('location' or 'hospital'), the model field
    that always ends up holding a readable name.
    """

    text_field = 'location'

    def setup_place_fields(self):
        self.fields['district'].required = True
        place = self.fields['place']
        # Widget first: assigning the queryset hands its choices to it.
        place.widget = PlaceSelect(attrs=place.widget.attrs)
        place.queryset = Place.objects.filter(is_approved=True)
        place.empty_label = _('-- Other (type below) --')
        place.required = False
        self.fields[self.text_field].required = False
        self.fields[self.text_field].label = 'Other place (if not listed)'

    def clean_place_fields(self, cleaned_data):
        place = cleaned_data.get('place')
        district = cleaned_data.get('district')
        text = (cleaned_data.get(self.text_field) or '').strip()
        if place:
            if district and place.district != district:
                self.add_error(
                    'place',
                    _('%(place)s is in %(actual)s, not %(chosen)s.') % {
                        'place': place.name, 'actual': place.district,
                        'chosen': district,
                    },
                )
            else:
                cleaned_data[self.text_field] = place.name
        elif not text:
            self.add_error(
                self.text_field,
                _('Pick a place from the list, or type its name.'),
            )
        else:
            cleaned_data[self.text_field] = text
        return cleaned_data


class DonationForm(PlaceFieldsMixin, forms.ModelForm):
    class Meta:
        model = Donation
        fields = (
            'blood_group', 'donation_date', 'district', 'place', 'location',
            'blood_request',
        )
        widgets = {
            'donation_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.setup_place_fields()
        field = self.fields['blood_request']
        # Only requests this donor pledged to, so the bonus can't be farmed
        # by linking donations to arbitrary requests.
        field.queryset = (
            BloodRequest.objects.filter(responses__donor=user)
            if user is not None else BloodRequest.objects.none()
        )
        field.required = False
        field.empty_label = _('-- none --')

    def clean_donation_date(self):
        date = self.cleaned_data['donation_date']
        today = timezone.localdate()
        if date < today - datetime.timedelta(days=MAX_DONATION_AGE_DAYS):
            raise forms.ValidationError(
                _('That date is too long ago. Check the year.')
            )
        if self.user is not None and Donation.objects.filter(
            donor=self.user, donation_date=date,
        ).exclude(status=Donation.Status.REJECTED).exists():
            # Also what a double click on "Log donation" runs into.
            raise forms.ValidationError(
                _('You already logged a donation on this date.')
            )
        return date

    def clean(self):
        return self.clean_place_fields(super().clean())


class BloodRequestForm(PlaceFieldsMixin, forms.ModelForm):
    text_field = 'hospital'

    class Meta:
        model = BloodRequest
        fields = (
            'blood_group', 'district', 'place', 'hospital', 'units_needed',
            'needed_by', 'contact_phone', 'patient_name', 'note',
        )
        widgets = {
            'blood_group': forms.RadioSelect,
            'units_needed': forms.Select(
                choices=[(n, str(n)) for n in range(1, 11)],
            ),
            'needed_by': forms.DateInput(attrs={'type': 'date'}),
            'patient_name': forms.TextInput(attrs={
                'placeholder': _('Optional. First name or initials only'),
                'autocomplete': 'off',
            }),
            'note': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': _('Optional. Ward, bed, or anything a donor '
                               'should know'),
            }),
        }
        help_texts = {
            'patient_name': 'Never enter medical record numbers or IDs.',
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.setup_place_fields()
        # A radio group has no use for the "---------" blank choice.
        self.fields['blood_group'].choices = BLOOD_GROUP_CHOICES
        today = timezone.localdate()
        self.fields['needed_by'].widget.attrs['min'] = today.isoformat()
        self.fields['needed_by'].widget.attrs['max'] = (
            today + datetime.timedelta(days=MAX_REQUEST_LEAD_DAYS)
        ).isoformat()
        if user is not None and not self.is_bound:
            self.fields['contact_phone'].initial = user.phone
            profile = getattr(user, 'profile', None)
            if profile is not None and profile.district:
                self.fields['district'].initial = profile.district
            self.fields['needed_by'].initial = timezone.localdate()

    def clean_needed_by(self):
        date = self.cleaned_data['needed_by']
        if date > timezone.localdate() + datetime.timedelta(
            days=MAX_REQUEST_LEAD_DAYS,
        ):
            raise forms.ValidationError(
                _('Pick a date within the next %(n)s days.')
                % {'n': MAX_REQUEST_LEAD_DAYS}
            )
        return date

    def clean_patient_name(self):
        name = ' '.join(self.cleaned_data.get('patient_name', '').split())
        if sum(ch.isdigit() for ch in name) > 1:
            raise forms.ValidationError(
                _('Use a first name or initials only. Please do not enter '
                'record numbers or IDs.')
            )
        return name

    def clean(self):
        cleaned_data = self.clean_place_fields(super().clean())
        if self.user is not None and not self.errors and (
            BloodRequest.objects.open_and_current().filter(
                requester=self.user,
                blood_group=cleaned_data.get('blood_group'),
                district=cleaned_data.get('district'),
                hospital__iexact=cleaned_data.get('hospital'),
                needed_by=cleaned_data.get('needed_by'),
            ).exists()
        ):
            # Also what a double click on "Post request" would create twice.
            raise forms.ValidationError(
                _('You already posted this request. Open "My requests" to '
                'update or close it.')
            )
        if self.user is not None:
            open_count = BloodRequest.objects.open_and_current().filter(
                requester=self.user,
            ).count()
            if open_count >= MAX_OPEN_REQUESTS_PER_USER:
                raise forms.ValidationError(
                    _('You already have %(n)s open requests. '
                      'Close one before posting another.')
                    % {'n': MAX_OPEN_REQUESTS_PER_USER}
                )
            posted_today = BloodRequest.objects.filter(
                requester=self.user,
                created_at__gte=timezone.now() - datetime.timedelta(days=1),
            ).count()
            if posted_today >= MAX_REQUESTS_PER_DAY:
                raise forms.ValidationError(
                    _('You have posted a lot of requests today. Please try '
                    'again tomorrow.')
                )
        return cleaned_data
