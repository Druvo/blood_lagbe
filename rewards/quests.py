"""Levels and quests for the donor hub, derived from existing data.

Nothing here is stored: XP is the donor's points (``get_donor_points``), a
level is a threshold on those points, and a quest is a yes/no question about
data we already keep. `get_level` and `build_quests` are pure functions so
they are cheap to test; `get_quests` is the thin database wrapper.
"""
from collections import namedtuple

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from rewards.models import Donation, RequestResponse, get_current_streak

Level = namedtuple('Level', 'number name min_points icon color')

# (name, points needed, feather icon, colour). Points are 10 per verified
# donation, +5 per streak step, +20 per donation to an urgent request.
LEVELS = [
    Level(1, _('Spark'), 0, 'zap', '#f5a524'),
    Level(2, _('Helper'), 10, 'heart', '#f97066'),
    Level(3, _('Guardian'), 40, 'shield', '#2e90fa'),
    Level(4, _('Champion'), 100, 'award', '#12b76a'),
    Level(5, _('Hero'), 200, 'star', '#7a5af8'),
    Level(6, _('Lifesaver'), 350, 'life-buoy', '#d8213a'),
    Level(7, _('Legend'), 600, 'sun', '#d4af37'),
    Level(8, _('Immortal'), 1000, 'target', '#00b894'),
]

LevelInfo = namedtuple(
    'LevelInfo',
    'level next_level points progress_percent points_to_next is_max',
)


def get_level(points):
    """Current level, next level and progress inside the current level.

    `progress_percent` is 100 at the top level. Negative or missing points
    are treated as zero.
    """
    points = max(int(points or 0), 0)
    current = LEVELS[0]
    for level in LEVELS:
        if points >= level.min_points:
            current = level
    index = LEVELS.index(current)
    next_level = LEVELS[index + 1] if index + 1 < len(LEVELS) else None
    if next_level is None:
        return LevelInfo(current, None, points, 100, 0, True)
    span = next_level.min_points - current.min_points
    percent = int((points - current.min_points) / span * 100)
    return LevelInfo(
        current, next_level, points, percent,
        next_level.min_points - points, False,
    )


Quest = namedtuple(
    'Quest', 'key title description done url reward progress target',
)

DONATE_TARGET = 3
STREAK_TARGET = 2
TIER_TARGET = 5

QuestFacts = namedtuple(
    'QuestFacts',
    'blood_group district in_directory logged_count approved_count '
    'answered_request request_donations streak',
)


def build_quests(facts):
    """Quests for a donor, as a list in suggested order. Pure: all the
    inputs are in `facts`."""
    def url(name, fragment=''):
        return reverse(name) + fragment

    return [
        Quest(
            'profile', _('Complete your profile'),
            _('Add your blood group and district so people can find you.'),
            bool(facts.blood_group and facts.district),
            url('privacy'), _('Pre-fills your donation form'), None, None,
        ),
        Quest(
            'directory', _('Join the donor directory'),
            _('Let members ask you for help. Your phone stays hidden until you accept.'),
            bool(facts.in_directory),
            url('privacy'), _('Be found by people in need'), None, None,
        ),
        Quest(
            'first', _('Log your first donation'),
            _('An admin verifies it, then it counts.'),
            facts.logged_count >= 1,
            url('log-donation', '#log'), _('Bronze Donor tier at 1 verified'), None, None,
        ),
        Quest(
            'respond', _('Answer an urgent request'),
            _('Tap "I can help" on a request that needs your group.'),
            bool(facts.answered_request),
            url('request-list'), _('Path to Hero badges'), None, None,
        ),
        Quest(
            'request_donation', _('Donate for an urgent request'),
            _('Link a donation to a request you answered for bonus points.'),
            facts.request_donations >= 1,
            url('log-donation', '#log'), _('First Responder badge, +20 points'), None, None,
        ),
        Quest(
            'streak', _('Keep a 2-cycle streak'),
            _('Donate again within one cycle of your last verified donation.'),
            facts.streak >= STREAK_TARGET,
            url('log-donation', '#log'), _('Consistent Donor badge'),
            min(facts.streak, STREAK_TARGET), STREAK_TARGET,
        ),
        Quest(
            'three', _('Donate 3 times'),
            _('Three verified donations show real commitment.'),
            facts.approved_count >= DONATE_TARGET,
            url('log-donation', '#log'), _('Halfway to Silver Donor'),
            min(facts.approved_count, DONATE_TARGET), DONATE_TARGET,
        ),
        Quest(
            'silver', _('Reach Silver Donor'),
            _('Five verified donations.'),
            facts.approved_count >= TIER_TARGET,
            url('log-donation', '#log'), _('Silver Donor tier'),
            min(facts.approved_count, TIER_TARGET), TIER_TARGET,
        ),
    ]


def get_quest_facts(user, approved_count, streak, request_donations,
                    logged_count=None):
    """Collect the inputs for `build_quests`. The hub already knows the
    approved count, streak and request-donation count, so they are passed
    in instead of being queried again."""
    profile = user.profile
    if logged_count is None:
        logged_count = Donation.objects.filter(donor=user).exclude(
            status=Donation.Status.REJECTED,
        ).count()
    answered = (
        request_donations > 0
        or RequestResponse.objects.filter(donor=user).exists()
        or Donation.objects.filter(
            donor=user, blood_request__isnull=False,
        ).exists()
    )
    return QuestFacts(
        blood_group=profile.blood_group,
        district=profile.district,
        in_directory=profile.in_directory,
        logged_count=logged_count,
        approved_count=approved_count,
        answered_request=answered,
        request_donations=request_donations,
        streak=streak,
    )


def get_quests(user, approved_count=None, streak=None, request_donations=None):
    """Quests for `user`, computing whatever the caller did not pass."""
    if approved_count is None:
        approved_count = Donation.objects.filter(
            donor=user, status=Donation.Status.APPROVED,
        ).count()
    if streak is None:
        streak = get_current_streak(user)
    if request_donations is None:
        request_donations = Donation.objects.filter(
            donor=user, status=Donation.Status.APPROVED,
            blood_request__isnull=False,
        ).count()
    return build_quests(
        get_quest_facts(user, approved_count, streak, request_donations),
    )
