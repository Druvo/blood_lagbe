from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from rewards.models import BloodRequest

NOTE_MAX_LENGTH = 200


class ImpactUpdate(models.Model):
    """"Your blood reached someone": a hub card for a donor who offered to
    help on a request that was later closed as fulfilled. Carries the
    district and blood group at most, never patient or contact details."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='impact_updates',
    )
    blood_request = models.ForeignKey(
        BloodRequest, on_delete=models.CASCADE, related_name='impact_updates',
    )
    blood_group = models.CharField(max_length=3)
    district = models.CharField(max_length=100, blank=True)
    dismissed = models.BooleanField(default=False)
    emailed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['blood_request', 'user'],
                name='moments_one_update_per_request_donor',
            ),
        ]

    def __str__(self):
        return f'Update for {self.user_id} on request {self.blood_request_id}'


class ThankNote(models.Model):
    """A short thank-you from a requester to one responder of a closed
    request. Plain text; the responder can hide it (soft delete)."""

    blood_request = models.ForeignKey(
        BloodRequest, on_delete=models.CASCADE, related_name='thank_notes',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='thank_notes_sent',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='thank_notes_received',
    )
    text = models.CharField(_('Note'), max_length=NOTE_MAX_LENGTH)
    to_all = models.BooleanField(
        default=False,
        help_text=_('Sent as one note to every responder.'),
    )
    hidden = models.BooleanField(
        default=False,
        help_text=_('Hidden by the recipient or by staff.'),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['blood_request', 'recipient'],
                name='moments_one_note_per_request_recipient',
            ),
        ]

    def __str__(self):
        return f'Note to {self.recipient_id} on request {self.blood_request_id}'

    @property
    def sender_label(self):
        """Who the note is from. Follows the public naming rules: a
        requester who opted out of public naming is "A requester"."""
        from moments.services import requester_label
        return requester_label(self.sender)


class ReminderPref(models.Model):
    """Per-member email opt-ins. Both default to off; each email carries a
    signed one-click link that switches the matching one off."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='moments_pref',
    )
    email_enabled = models.BooleanField(
        _('Email me when I am ready again'), default=False,
    )
    impact_email = models.BooleanField(
        _('Email me when a request I offered to help with is fulfilled'),
        default=False,
    )
    last_reminded_cycle = models.DateField(
        null=True, blank=True,
        help_text=_(
            'Date of the donation whose cycle was last reminded. One '
            'reminder per cycle.'
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Preferences for {self.user_id}'


class Celebration(models.Model):
    """A milestone the donor has already been shown (one row per key), so
    the overlay appears once."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='celebrations',
    )
    key = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'key'], name='moments_one_celebration_per_key',
            ),
        ]

    def __str__(self):
        return f'{self.key} for {self.user_id}'


class StreakFreeze(models.Model):
    """One per member per calendar year. Forgives one long gap between two
    approved donations in the streak counter. It never changes when anyone
    may donate again."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='streak_freezes',
    )
    year = models.PositiveSmallIntegerField()
    gap_start = models.DateField(help_text=_('Donation before the gap.'))
    gap_end = models.DateField(help_text=_('Donation after the gap.'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'year'], name='moments_one_freeze_per_year',
            ),
        ]

    def __str__(self):
        return f'Freeze {self.year} for {self.user_id}'
