from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

DONATION_CYCLE_DAYS = 84  # 12 weeks, the site's minimum donation gap
STREAK_GRACE_DAYS = 14  # slack allowed before a streak is considered broken

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
    """An achievement a donor unlocks: a donation-count tier, or a streak."""

    class Type(models.TextChoices):
        TIER = 'tier', _('Tier (by total donations)')
        STREAK = 'streak', _('Streak (by consecutive donation cycles)')

    name = models.CharField(_('Name'), max_length=50)
    icon = models.CharField(
        _('Icon'), max_length=50, default='award',
        help_text=_("Feather icon name, e.g. 'award' for feather-award."),
    )
    color = models.CharField(_('Color'), max_length=20, default='#cd7f32')
    description = models.CharField(_('Description'), max_length=255, blank=True)
    badge_type = models.CharField(
        _('Type'), max_length=10, choices=Type.choices, default=Type.TIER,
    )
    min_donations = models.PositiveIntegerField(
        _('Minimum Approved Donations'), null=True, blank=True,
        help_text=_('Required for tier badges.'),
    )
    min_streak = models.PositiveIntegerField(
        _('Minimum Streak'), null=True, blank=True,
        help_text=_('Required for streak badges.'),
    )

    class Meta:
        ordering = ['badge_type', 'min_donations', 'min_streak']

    def __str__(self):
        return self.name


def approved_donation_count(user):
    return Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
    ).count()


def get_current_streak(user):
    """Consecutive approved donations spaced no more than one donation
    cycle (+ grace) apart, counting back from the most recent one. Zero
    if the donor hasn't donated within a cycle+grace of today."""
    dates = list(
        Donation.objects.filter(donor=user, status=Donation.Status.APPROVED)
        .order_by('donation_date').values_list('donation_date', flat=True)
    )
    if not dates:
        return 0

    threshold = timedelta(days=DONATION_CYCLE_DAYS + STREAK_GRACE_DAYS)
    streak = 1
    for prev_date, curr_date in zip(dates, dates[1:]):
        streak = streak + 1 if (curr_date - prev_date) <= threshold else 1

    if timezone.localdate() - dates[-1] > threshold:
        return 0
    return streak


def get_donor_points(user):
    return approved_donation_count(user) * 10 + get_current_streak(user) * 5


def get_donor_badge_progress(user):
    """Returns (current_badge, next_badge, approved_count, progress_percent)
    for tier badges only."""
    count = approved_donation_count(user)
    badges = list(Badge.objects.filter(badge_type=Badge.Type.TIER))

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


def get_earned_streak_badges(streak):
    return list(
        Badge.objects.filter(badge_type=Badge.Type.STREAK, min_streak__lte=streak)
    )
