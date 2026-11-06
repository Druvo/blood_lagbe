from django import forms
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from alerts.models import (
    MAX_DAILY_CAP,
    MAX_EXTRA_DISTRICTS,
    AlertPreference,
    clean_district_list,
)
from rewards.geo import DISTRICT_CHOICES


class AlertPreferenceForm(forms.ModelForm):
    extra_districts = forms.MultipleChoiceField(
        label=_('Also alert me about these districts'),
        choices=DISTRICT_CHOICES, required=False,
        help_text=format_lazy(
            _('Up to {n}. Your own district is always included.'),
            n=MAX_EXTRA_DISTRICTS,
        ),
    )
    daily_cap = forms.IntegerField(
        label=_('Most alerts per day'), min_value=1, max_value=MAX_DAILY_CAP,
    )

    class Meta:
        model = AlertPreference
        fields = (
            'enabled', 'email_enabled', 'extra_districts', 'only_rare_groups',
            'daily_cap',
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['extra_districts'].widget.attrs['class'] = 'al-districts'
        if self.instance.pk and not self.is_bound:
            self.initial['extra_districts'] = self.instance.extra_district_list

    def clean_extra_districts(self):
        chosen = self.cleaned_data['extra_districts']
        if len(chosen) > MAX_EXTRA_DISTRICTS:
            raise forms.ValidationError(
                _('Pick at most %(n)s districts.') % {'n': MAX_EXTRA_DISTRICTS},
            )
        return ','.join(clean_district_list(chosen))

    def clean(self):
        data = super().clean()
        if data.get('email_enabled') and self.user is not None \
                and not self.user.email:
            self.add_error(
                'email_enabled',
                _('Add an email address in Profile and privacy first.'),
            )
        return data
