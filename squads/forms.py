from django import forms

from squads.models import Squad
from squads.services import normalize_code


class SquadForm(forms.ModelForm):
    class Meta:
        model = Squad
        fields = ['name', 'description', 'district', 'is_public']
        widgets = {
            'description': forms.TextInput(attrs={
                'placeholder': 'Who is this squad for? (optional)',
            }),
            'name': forms.TextInput(
                attrs={'placeholder': 'e.g. Dhaka Uni Blood Club'},
            ),
        }

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        taken = Squad.objects.filter(name__iexact=name, is_public=True)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise forms.ValidationError(
                'A public squad already uses this name. Try a different one.'
            )
        return name


class JoinForm(forms.Form):
    code = forms.CharField(max_length=24, label='Join code')

    def clean_code(self):
        return normalize_code(self.cleaned_data['code'])
