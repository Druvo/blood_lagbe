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


class Badge(models.Model):
    """A donor tier, unlocked once a donor has enough approved donations."""

    name = models.CharField(_('Name'), max_length=50)
    icon = models.CharField(
        _('Icon'), max_length=50, default='award',
        help_text=_("Feather icon name, e.g. 'award' for feather-award."),
    )
    color = models.CharField(_('Color'), max_length=20, default='#cd7f32')
    description = models.CharField(_('Description'), max_length=255, blank=True)
    min_donations = models.PositiveIntegerField(
        _('Minimum Approved Donations'), unique=True,
    )

    class Meta:
        ordering = ['min_donations']

    def __str__(self):
        return self.name


def approved_donation_count(user):
    return Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
    ).count()


def get_donor_badge_progress(user):
    """Returns (current_badge, next_badge, approved_count, progress_percent)."""
    count = approved_donation_count(user)
    badges = list(Badge.objects.all())

    current_badge = None
    next_badge = None
    for badge in badges:
        if count >= badge.min_donations:
            current_badge = badge
        elif next_badge is None:
            next_badge = badge

    if next_badge is None:
        progress_percent = 100
    else:
        lower = current_badge.min_donations if current_badge else 0
        span = next_badge.min_donations - lower
        progress_percent = int((count - lower) / span * 100) if span else 100

    return current_badge, next_badge, count, progress_percent
