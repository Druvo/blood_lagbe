from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import UserCreationForm

from accounts.models import Profile

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


class LoginForm(forms.Form):
    phone = forms.CharField(label='Phone Number')
    password = forms.CharField(label='Password', widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super().clean()
        phone = cleaned_data.get('phone')
        password = cleaned_data.get('password')
        if phone and password and not authenticate(phone=phone, password=password):
            raise forms.ValidationError("Invalid login")
        return cleaned_data


class RegistrationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone')

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if not phone or phone[:2] != '01' or phone[:3] in ('010', '011', '012'):
            raise forms.ValidationError('Invalid Phone number')
        return phone
