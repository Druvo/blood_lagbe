from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class MonthlyAward(models.Model):
    """The final winner of one award for one calendar month.

    Written by ``award_monthly_heroes`` once a month is over. One winner per
    award per month (the unique constraint makes the command idempotent). The
    count is a snapshot; who may be named is checked again whenever it is shown,
    so a member who later opts out of the leaderboard disappears from the page.
    """

    class Kind(models.TextChoices):
        DONOR = 'donor', _('Donor of the Month')
        SQUAD = 'squad', _('Squad of the Month')
        HELPER = 'helper', _('Helper of the Month')

    kind = models.CharField(max_length=10, choices=Kind.choices)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True,
        blank=True, related_name='monthly_awards',
    )
    squad = models.ForeignKey(
        'squads.Squad', on_delete=models.CASCADE, null=True, blank=True,
        related_name='monthly_awards',
    )
    count = models.PositiveIntegerField(default=0)
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', '-month', 'kind']
        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'year', 'month'],
                name='one_award_per_kind_per_month',
            ),
            models.CheckConstraint(
                name='award_has_one_winner',
                check=(
                    (Q(kind='squad') & Q(squad__isnull=False)
                     & Q(user__isnull=True))
                    | (~Q(kind='squad') & Q(user__isnull=False)
                       & Q(squad__isnull=True))
                ),
            ),
        ]

    def __str__(self):
        # Never the phone number: User.__str__ is the phone.
        return f'{self.kind} {self.year}-{self.month:02d}'
