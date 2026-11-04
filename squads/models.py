"""Squads: small teams (friends, offices, universities, villages) that
donate and climb the board together."""
import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from rewards.geo import DISTRICT_CHOICES

MAX_MEMBERS_PER_SQUAD = 200
MAX_SQUADS_PER_USER = 5

# No I, L, O, 0 or 1: codes get read aloud and typed from a phone screen.
# 8 characters from 31 symbols is about 8.5e11 codes, too many to guess.
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 8


def generate_code():
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def unique_code():
    while True:
        code = generate_code()
        if not Squad.objects.filter(join_code=code).exists():
            return code


class Squad(models.Model):
    name = models.CharField(_('Squad name'), max_length=60)
    description = models.CharField(_('About'), max_length=300, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='owned_squads',
    )
    join_code = models.CharField(
        max_length=12, unique=True, default=unique_code, editable=False,
    )
    is_public = models.BooleanField(
        _('Show in the public squad list'), default=False,
    )
    district = models.CharField(
        _('District'), max_length=100, blank=True, choices=DISTRICT_CHOICES,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def regenerate_code(self):
        self.join_code = unique_code()
        self.save(update_fields=['join_code'])


class SquadMember(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', _('Owner')
        MEMBER = 'member', _('Member')

    squad = models.ForeignKey(
        Squad, on_delete=models.CASCADE, related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='squad_memberships',
    )
    role = models.CharField(
        max_length=10, choices=Role.choices, default=Role.MEMBER,
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['joined_at', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['squad', 'user'], name='one_membership_per_squad_user',
            ),
        ]

    def __str__(self):
        return f'{self.user_id} in {self.squad_id} ({self.role})'
