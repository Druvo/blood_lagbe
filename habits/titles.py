"""Special titles that recognise ways of helping other than donating blood.

They are for everyone, including people who cannot donate (deferral, age).
None of them touches donor points, tiers or the leaderboard.

Other apps call `titles_for(user)` for the titles someone has earned. A public
page must still respect that person's privacy settings (show nothing for a
member who has hidden themselves from the leaderboard and public lists).
"""
from collections import namedtuple

from django.contrib.auth import get_user_model
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce

from habits.models import DailyCompletion, ShareAction
from rewards.models import BloodRequest, RequestResponse
from squads.models import Squad
from stories.models import Story

CHAMPION_TARGET = 10
CONNECTOR_TARGET = 5
STORYTELLER_TARGET = 1
CAPTAIN_MEMBERS = 3
EARLY_BIRD_TARGET = 7
EARLY_BIRD_BEFORE_HOUR = 9

User = get_user_model()

Title = namedtuple(
    'Title', 'key icon name description earned current target percent hint',
)


def _title(key, icon, name, description, current, target, hint):
    current = max(int(current), 0)
    shown = min(current, target)
    return Title(
        key, icon, name, description, current >= target, shown, target,
        int(shown / target * 100), hint,
    )


def _count(queryset, group_field, expr='pk', distinct=False):
    """A correlated COUNT as a subquery (0 when there are no rows)."""
    return Coalesce(Subquery(
        queryset.order_by().values(group_field)
        .annotate(c=Count(expr, distinct=distinct)).values('c')[:1],
        output_field=IntegerField(),
    ), 0)


def title_counts(user):
    """The five numbers behind the titles, in a single query:
    distinct requests shared, pledges on requests later fulfilled here,
    approved stories, members of the biggest squad they own, and daily
    actions finished before 9am. Only approved stories count, and only
    pledges on requests closed as fulfilled."""
    biggest_squad = (
        Squad.objects.filter(owner=OuterRef('pk'))
        .annotate(member_count=Count('memberships'))
        .order_by('-member_count').values('member_count')[:1]
    )
    return User.objects.filter(pk=user.pk).annotate(
        n_shares=_count(
            ShareAction.objects.filter(user=OuterRef('pk')), 'user',
            expr='blood_request', distinct=True,
        ),
        n_connector=_count(
            RequestResponse.objects.filter(
                donor=OuterRef('pk'),
                blood_request__status=BloodRequest.Status.CLOSED,
                blood_request__outcome=BloodRequest.Outcome.FULFILLED_HERE,
            ).exclude(blood_request__requester=OuterRef('pk')), 'donor',
        ),
        n_stories=_count(
            Story.objects.filter(
                author=OuterRef('pk'), status=Story.Status.APPROVED,
            ), 'author',
        ),
        n_captain=Coalesce(Subquery(
            biggest_squad, output_field=IntegerField(),
        ), 0),
        n_early=_count(
            DailyCompletion.objects.filter(
                user=OuterRef('pk'), hour__lt=EARLY_BIRD_BEFORE_HOUR,
            ), 'user',
        ),
    ).values('n_shares', 'n_connector', 'n_stories', 'n_captain', 'n_early').get()


def all_titles(user):
    """Every title for `user`, earned or not, each with its progress."""
    counts = title_counts(user)
    return [
        _title(
            'champion', 'send', 'Request Champion',
            'Shared urgent requests so more people could see them.',
            counts['n_shares'], CHAMPION_TARGET,
            f'Share {CHAMPION_TARGET} different requests from your Today page.',
        ),
        _title(
            'connector', 'link', 'Connector',
            'Offered to help on requests that were later fulfilled here.',
            counts['n_connector'], CONNECTOR_TARGET,
            f'Say "I can help" on {CONNECTOR_TARGET} requests that end up fulfilled.',
        ),
        _title(
            'storyteller', 'book-open', 'Storyteller',
            'Shared a story the community team published.',
            counts['n_stories'], STORYTELLER_TARGET,
            'Share a true story. It counts once the team has published it.',
        ),
        _title(
            'captain', 'users', 'Squad Captain',
            'Leads a squad with three or more members.',
            counts['n_captain'], CAPTAIN_MEMBERS,
            f'Start a squad and bring it to {CAPTAIN_MEMBERS} members.',
        ),
        _title(
            'early_bird', 'sunrise', 'Early Bird',
            'Finished daily actions before 9 in the morning.',
            counts['n_early'], EARLY_BIRD_TARGET,
            f'Finish {EARLY_BIRD_TARGET} daily actions before 9am.',
        ),
    ]


def titles_for(user):
    """Titles `user` has earned, each with icon, name, description and
    progress. Newcomers get an empty list."""
    return [t for t in all_titles(user) if t.earned]


def next_title(user):
    """The unearned title closest to done, or None when all are earned."""
    pending = [t for t in all_titles(user) if not t.earned]
    if not pending:
        return None
    return max(pending, key=lambda t: (t.percent, -pending.index(t)))
