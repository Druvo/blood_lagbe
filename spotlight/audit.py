"""Pages of the spotlight app for the shared privacy crawl.

``paths(cast)`` returns ``[(url name, path), ...]`` for every GET page. Extra
fixtures are made with get_or_create, the way ``lanes_paths`` does in
``rewards/audit_fixtures.py``. Not a test module.

POST-only pages: none (this app has no write endpoints).
Pages that need a token: none (no signed links or invite codes).
Login required: style, certificate, ready-ics. The style card is private
(owner and staff only) unless its owner allows sharing and is on the
leaderboard.
"""
from django.urls import reverse
from django.utils import timezone

from spotlight import heroes
from spotlight.models import MonthlyAward


def paths(cast):
    """The opted-out 'hidden' member holds a saved Donor award and a public
    squad holds a saved Squad award: the hero pages must never name the
    first, and may name the second."""
    u = cast.users
    today = timezone.localdate()
    last_y, last_m = heroes.previous_month(today.year, today.month)
    older_y, older_m = heroes.previous_month(last_y, last_m)
    MonthlyAward.objects.get_or_create(
        kind='donor', year=last_y, month=last_m,
        defaults={'user': u['hidden'], 'count': 2},
    )
    MonthlyAward.objects.get_or_create(
        kind='squad', year=older_y, month=older_m,
        defaults={'squad': cast.squad, 'count': 3},
    )
    return [
        ('spotlight:home', reverse('spotlight:home')),
        ('spotlight:month', reverse('spotlight:month', args=[today.year, today.month])),
        ('spotlight:month', reverse('spotlight:month', args=[last_y, last_m])),
        ('spotlight:month', reverse('spotlight:month', args=[older_y, older_m])),
        ('spotlight:month', reverse('spotlight:month', args=[today.year + 1, 1])),
        ('spotlight:month', reverse('spotlight:month', args=[today.year, 13])),
        ('spotlight:pulse', reverse('spotlight:pulse')),
        ('spotlight:style', reverse('spotlight:style')),
        ('spotlight:style-card', reverse('spotlight:style-card', args=[u['hidden'].pk])),
        ('spotlight:style-card', reverse('spotlight:style-card', args=[u['private_card'].pk])),
        ('spotlight:style-card', reverse('spotlight:style-card', args=[u['visible'].pk])),
        ('spotlight:style-card', reverse('spotlight:style-card', args=[u['owner'].pk])),
        ('spotlight:certificate', reverse('spotlight:certificate', args=[cast.owner_approved.pk])),
        ('spotlight:certificate', reverse('spotlight:certificate', args=[cast.owner_pending.pk])),
        ('spotlight:ready-ics', reverse('spotlight:ready-ics')),
    ]
