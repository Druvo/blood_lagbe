"""Growth: invites, trust and identity.

* ``Invite``: one stable invite code per member, created on demand.
* ``Referral``: who invited whom. Rewarded only when the invitee's first
  donation is approved by staff, never at sign-up.
* ``ReferralReward``: the "Friend Maker" recognition levels.
* ``VerifiedHospital``: hospitals staff have confirmed, shown as a trust chip.
"""
import secrets

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from rewards.geo import DISTRICT_CHOICES

# No I, L, O, 0 or 1: codes get read aloud and typed from a phone screen.
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 8

# Rewarded invites needed for each Friend Maker level.
FRIEND_MAKER_LEVELS = (
    (1, 1, 'Friend Maker'),
    (2, 3, 'Friend Maker II'),
    (3, 10, 'Friend Maker III'),
)


def generate_code():
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def unique_code():
    while True:
        code = generate_code()
        if not Invite.objects.filter(code=code).exists():
            return code


class Invite(models.Model):
    """A member's invite code. Stable: it never changes once created."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='invite',
    )
    code = models.CharField(
        max_length=12, unique=True, default=unique_code, editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Invite {self.code} (user {self.user_id})'


class Referral(models.Model):
    """`inviter` brought `invitee` to the site. An invitee has at most one
    inviter. `rewarded_at` is set once, when the invitee's first donation is
    approved."""

    inviter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='referrals_made',
    )
    invitee = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='referred_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    rewarded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                check=~Q(inviter=F('invitee')), name='no_self_referral',
            ),
        ]

    def __str__(self):
        return f'{self.inviter_id} invited {self.invitee_id}'

    @property
    def is_rewarded(self):
        return self.rewarded_at is not None


class ReferralReward(models.Model):
    """A Friend Maker level a member has earned (1, 2 or 3)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='referral_rewards',
    )
    level = models.PositiveSmallIntegerField(
        choices=[(n, name) for n, _needed, name in FRIEND_MAKER_LEVELS],
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user', 'level']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'level'], name='one_referral_reward_per_level',
            ),
        ]

    def __str__(self):
        return f'{self.name} for user {self.user_id}'

    @property
    def name(self):
        return {n: name for n, _needed, name in FRIEND_MAKER_LEVELS}.get(
            self.level, 'Friend Maker',
        )


class VerifiedHospital(models.Model):
    """A hospital staff have confirmed is real. Managed in the admin."""

    name = models.CharField(_('Name'), max_length=255)
    district = models.CharField(
        _('District'), max_length=100, choices=DISTRICT_CHOICES,
    )
    place = models.ForeignKey(
        'rewards.Place', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='verified_hospitals',
        verbose_name=_('Listed place (optional)'),
        help_text=_('Requests that picked this place match it directly.'),
    )
    notes = models.TextField(
        _('Internal notes'), blank=True,
        help_text=_('How it was checked. Never shown to members.'),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['district', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['name', 'district'], name='unique_verified_hospital',
            ),
        ]

    def __str__(self):
        return f'{self.name}, {self.district}'
