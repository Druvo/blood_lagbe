from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from rewards.geo import DISTRICT_NAMES

MAX_EXTRA_DISTRICTS = 5
DEFAULT_DAILY_CAP = 3
MAX_DAILY_CAP = 10


class Notification(models.Model):
    """One in-app message for one member. Plain text only: it is escaped when
    shown, and it never carries anyone's phone, email or patient details."""

    class Kind(models.TextChoices):
        MATCH = 'match', 'Matched request'
        INFO = 'info', 'Update'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='notifications',
    )
    kind = models.CharField(max_length=30, default=Kind.INFO)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=1000, blank=True)
    # A site-relative path only (checked in alerts.service).
    url = models.CharField(max_length=300, blank=True)
    # Repeating a key for the same user never creates a second notification.
    dedupe_key = models.CharField(max_length=120, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'dedupe_key'], name='alerts_unique_dedupe',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'read_at'], name='alerts_user_read_idx'),
            models.Index(
                fields=['user', 'kind', 'created_at'],
                name='alerts_user_kind_idx',
            ),
            models.Index(fields=['dedupe_key'], name='alerts_dedupe_idx'),
            models.Index(fields=['read_at'], name='alerts_read_at_idx'),
        ]

    def __str__(self):
        return f'{self.kind} for user #{self.user_id}: {self.title[:40]}'

    @property
    def is_read(self):
        return self.read_at is not None


class AlertPreference(models.Model):
    """Opt-in settings for matched request alerts. Everything is off until
    the member turns it on."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='alert_preference',
    )
    enabled = models.BooleanField(default=False)
    email_enabled = models.BooleanField(default=False)
    # Comma list of rewards.geo district names, at most MAX_EXTRA_DISTRICTS,
    # stored without spaces around the commas.
    extra_districts = models.CharField(max_length=200, blank=True)
    only_rare_groups = models.BooleanField(default=False)
    daily_cap = models.PositiveSmallIntegerField(
        default=DEFAULT_DAILY_CAP,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_DAILY_CAP)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Alert preference for user #{self.user_id}'

    @property
    def extra_district_list(self):
        return clean_district_list(self.extra_districts.split(','))


def clean_district_list(names):
    """Keep only known district names, once each, in order, up to the max."""
    seen = []
    for name in names:
        name = name.strip()
        if name in DISTRICT_NAMES and name not in seen:
            seen.append(name)
    return seen[:MAX_EXTRA_DISTRICTS]
