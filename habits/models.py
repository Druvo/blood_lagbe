"""Daily and weekly return reasons. Nothing here feeds donor points, tiers,
the leaderboard or the donation streak: these are gentle, cosmetic habits."""
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ShareAction(models.Model):
    """A member opened a request through the tracked share link. One row per
    member, request and day, so a double click or a replay adds nothing."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='share_actions',
    )
    blood_request = models.ForeignKey(
        'rewards.BloodRequest', on_delete=models.CASCADE,
        related_name='share_actions',
    )
    day = models.DateField(_('Day'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-day', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'blood_request', 'day'],
                name='one_share_per_user_request_day',
            ),
        ]

    def __str__(self):
        return f'{self.user_id} shared {self.blood_request_id} on {self.day}'


class DailyCompletion(models.Model):
    """One finished daily micro-action. Unique per member, day and task."""

    class Task(models.TextChoices):
        SHARE = 'share', _('Share an urgent request')
        AVAILABILITY = 'availability', _('Confirm your availability')
        FACT = 'fact', _('Read one fact')
        QUIZ = 'quiz', _('Myth or fact quiz')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='daily_completions',
    )
    day = models.DateField(_('Day'))
    task = models.CharField(max_length=12, choices=Task.choices)
    # Quiz answers ("M,F,F"), or the request id for a share. Free text so a
    # catalog change never needs a migration.
    detail = models.CharField(max_length=40, blank=True)
    # Local hour of the day it was done (0-23): the "Early Bird" title.
    hour = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-day', 'task']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'day', 'task'],
                name='one_completion_per_user_day_task',
            ),
        ]
        indexes = [models.Index(fields=['user', 'day'])]

    def __str__(self):
        return f'{self.user_id} {self.task} on {self.day}'


class DigestPref(models.Model):
    """Weekly digest opt-in. Off until the member turns it on."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='digest_pref',
    )
    enabled = models.BooleanField(_('Send me the weekly email'), default=False)
    # ISO week of the last digest, like "2026-W45".
    last_sent_week = models.CharField(max_length=8, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user_id}: {"on" if self.enabled else "off"}'


class DigestLog(models.Model):
    """One digest sent to one member for one ISO week. The unique constraint
    is what makes the command safe to run twice. `points` and `level` are the
    snapshot the next digest compares against."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='digest_logs',
    )
    iso_year = models.PositiveSmallIntegerField()
    iso_week = models.PositiveSmallIntegerField()
    points = models.PositiveIntegerField(default=0)
    level = models.PositiveSmallIntegerField(default=1)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-iso_year', '-iso_week', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'iso_year', 'iso_week'],
                name='one_digest_per_user_week',
            ),
        ]

    def __str__(self):
        return f'{self.user_id} {self.iso_year}-W{self.iso_week:02d}'

    @property
    def week_key(self):
        return f'{self.iso_year}-W{self.iso_week:02d}'
