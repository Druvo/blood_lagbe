from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from events.models import Event
from rewards.models import Place

DT_FORMAT = '%Y-%m-%dT%H:%M'

# Changing these on an approved drive sends it back for another look, so an
# approved title cannot be swapped for spam afterwards.
REVIEW_FIELDS = {
    'title', 'organizer_name', 'description', 'venue', 'place',
}


class EventForm(forms.ModelForm):
    starts_at = forms.DateTimeField(
        label=_('Starts'), input_formats=[DT_FORMAT],
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local'}, format=DT_FORMAT,
        ),
    )
    ends_at = forms.DateTimeField(
        label=_('Ends (optional)'), required=False, input_formats=[DT_FORMAT],
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local'}, format=DT_FORMAT,
        ),
    )

    class Meta:
        model = Event
        fields = [
            'title', 'organizer_name', 'starts_at', 'ends_at', 'district',
            'place', 'venue', 'description', 'capacity',
        ]
        widgets = {
            'title': forms.TextInput(
                attrs={'placeholder': _('e.g. Campus blood drive')},
            ),
            'description': forms.Textarea(attrs={'rows': 5}),
            'capacity': forms.NumberInput(attrs={'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['place'].queryset = Place.objects.filter(is_approved=True)
        self.fields['place'].empty_label = _('Not listed')
        self.fields['district'].required = True
        self.fields['description'].max_length = 2000

    def clean_title(self):
        return ' '.join(self.cleaned_data['title'].split())

    def clean_starts_at(self):
        # New drives are checked by Event.clean(); an edit only when the
        # start moved (a drive already under way can keep its time).
        starts = self.cleaned_data['starts_at']
        if (
            self.instance.pk and 'starts_at' in self.changed_data
            and starts < timezone.now()
        ):
            raise forms.ValidationError(_('An event cannot start in the past.'))
        return starts

    def clean_capacity(self):
        capacity = self.cleaned_data.get('capacity')
        if capacity and self.instance.pk:
            going = self.instance.rsvps.count()
            if capacity < going:
                raise forms.ValidationError(
                    _('%(n)s people are already going, so spots cannot go '
                      'below that.') % {'n': going}
                )
        return capacity

    def clean(self):
        data = super().clean()
        if not data.get('venue') and not data.get('place'):
            self.add_error('venue', _('Say where it is: pick a place or type a venue.'))
        return data

    def needs_review(self):
        return bool(REVIEW_FIELDS & set(self.changed_data))
