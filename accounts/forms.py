import re

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.validators import MaxLengthValidator

from accounts.models import Profile
from rewards.blood import BLOOD_GROUP_CHOICES
from rewards.geo import DISTRICT_CHOICES

User = get_user_model()


class PrivacyForm(forms.ModelForm):
    """Donor profile and what other people may see of it."""

    class Meta:
        model = Profile
        fields = [
            'blood_group', 'district', 'in_directory', 'available_to_donate',
            'show_on_leaderboard', 'public_badge_card',
        ]

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('in_directory'):
            for name in ('blood_group', 'district'):
                if not cleaned_data.get(name) and name not in self.errors:
                    self.add_error(
                        name, 'Needed so people can find you in the directory.',
                    )
        return cleaned_data


def normalize_phone(raw):
    """Digits only, with a Bangladesh country code (+880 / 880) folded back
    to the local 01XXXXXXXXX form, so people can type their number any way."""
    phone = re.sub(r'[\s\-().]', '', raw or '')
    if phone.startswith('+'):
        phone = phone[1:]
    if phone.startswith('880') and len(phone) == 13:
        phone = phone[2:]
    return phone


class LoginForm(forms.Form):
    phone = forms.CharField(label='Phone Number')
    password = forms.CharField(label='Password', widget=forms.PasswordInput)

    user = None

    def clean_phone(self):
        return normalize_phone(self.cleaned_data.get('phone'))

    def clean(self):
        cleaned_data = super().clean()
        phone = cleaned_data.get('phone')
        password = cleaned_data.get('password')
        if phone and password:
            self.user = authenticate(phone=phone, password=password)
            if not self.user:
                raise forms.ValidationError(
                    'That phone number and password do not match. '
                    'Check them and try again.',
                    code='invalid_login',
                )
        return cleaned_data


class RegistrationForm(UserCreationForm):
    """Self-registration. Besides the account it collects the donor basics
    (blood group, district) that live on the Profile, and an opt-in to the
    donor directory that is honoured only when both are given."""

    blood_group = forms.ChoiceField(
        label='Blood group', required=False,
        choices=[('', 'Not sure / skip')] + list(BLOOD_GROUP_CHOICES),
    )
    district = forms.ChoiceField(
        label='District', required=False,
        choices=[('', 'Skip for now')] + list(DISTRICT_CHOICES),
    )
    in_directory = forms.BooleanField(
        label='List me in the donor directory', required=False,
    )

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['email'].required = False
        # '+880 1712-345678' is longer than the stored 11 digits: the length
        # limit is enforced on the model after normalisation instead.
        phone = self.fields['phone']
        phone.max_length = None
        phone.validators = [
            v for v in phone.validators if not isinstance(v, MaxLengthValidator)
        ]
        phone.widget.attrs.pop('maxlength', None)

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data.get('phone'))
        if not phone or phone[:2] != '01' or phone[:3] in ('010', '011', '012'):
            raise forms.ValidationError('Invalid Phone number')
        return phone

    @property
    def directory_ignored(self):
        """They ticked the directory box but skipped what it needs."""
        data = getattr(self, 'cleaned_data', {})
        return bool(
            data.get('in_directory')
            and not (data.get('blood_group') and data.get('district'))
        )

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            self.save_profile(user)
        return user

    def save_profile(self, user):
        data = self.cleaned_data
        profile = user.profile
        profile.blood_group = data.get('blood_group') or ''
        profile.district = data.get('district') or ''
        profile.in_directory = bool(
            data.get('in_directory') and profile.blood_group and profile.district
        )
        profile.save()
