from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

BLOOD_GROUP_CHOICES = [
    ('A+', 'A+'), ('A-', 'A-'),
    ('B+', 'B+'), ('B-', 'B-'),
    ('AB+', 'AB+'), ('AB-', 'AB-'),
    ('O+', 'O+'), ('O-', 'O-'),
]


def validate_not_future(value):
    if value > timezone.localdate():
        raise ValidationError(_('Donation date cannot be in the future.'))


class Donation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        APPROVED = 'approved', _('Approved')
        REJECTED = 'rejected', _('Rejected')

    donor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='donations',
    )
    blood_group = models.CharField(
        _('Blood Group'), max_length=3, choices=BLOOD_GROUP_CHOICES,
    )
    donation_date = models.DateField(
        _('Donation Date'), validators=[validate_not_future],
    )
    location = models.CharField(_('Location'), max_length=255)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='verified_donations',
    )
    admin_note = models.TextField(_('Admin Note'), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-donation_date']

    def __str__(self):
        return f'{self.donor} - {self.donation_date} ({self.status})'
