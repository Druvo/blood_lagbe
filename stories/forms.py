from django import forms

from rewards.models import Donation
from stories.models import BODY_MAX, TITLE_MAX, Story, StoryReport


class StoryForm(forms.ModelForm):
    class Meta:
        model = Story
        fields = [
            'kind', 'title', 'body', 'district', 'blood_group', 'donation',
            'anonymous',
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'maxlength': TITLE_MAX, 'autocomplete': 'off',
                'placeholder': 'A short headline',
            }),
            'body': forms.Textarea(attrs={
                'rows': 8, 'maxlength': BODY_MAX,
                'placeholder': 'What happened? How did it feel? '
                               'What would you tell someone thinking about it?',
            }),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Set early so Story.clean() can check the linked donation's owner.
        self.instance.author = user
        for name in ('district', 'blood_group'):
            field = self.fields[name]
            field.choices = [('', 'Prefer not to say')] + list(field.choices)[1:]
        donations = Donation.objects.filter(
            donor=user, status=Donation.Status.APPROVED,
        ).order_by('-donation_date', '-id')
        field = self.fields['donation']
        field.queryset = donations
        field.empty_label = 'No donation linked'
        field.label_from_instance = lambda d: (
            f'{d.donation_date:%d %b %Y} - {d.location}'
        )

    def clean_title(self):
        return ' '.join(self.cleaned_data['title'].split())


class ReportForm(forms.ModelForm):
    class Meta:
        model = StoryReport
        fields = ['reason']
