from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class LoginForm(forms.ModelForm):
    password = forms.CharField(label='Password', widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ('phone', 'password')

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
