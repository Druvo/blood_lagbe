"""Donor style: a warm label for how someone takes part, never a score.

``style_for(user)`` returns the member's top archetype, or None when there is
no activity to describe yet. The copy is recognition only: nothing here asks
anyone to donate, and progress towards a milestone is shown as "so far", with
no deadline. Verified donations only count when an admin approved them, so
the pace always follows the donation interval.

Archetypes, in the order the top one is chosen:

* The Regular: 3 or more verified donations.
* The Organizer: approved drives hosted (events app, optional).
* The Connector: friends invited whose first donation was verified, so the
  invite was rewarded (growth app, optional).
* The First Responder: pledged "I can help" on requests.
* The Storyteller: approved stories.
* Rising Star: a member from the last 90 days with an early action.
"""
import datetime

from django.core.cache import cache
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rewards.models import Donation, RequestResponse

try:  # optional lanes: a missing app must never break the page
    from growth.models import Referral
except ImportError:  # pragma: no cover
    Referral = None
try:
    from events.models import Event, EventRSVP
except ImportError:  # pragma: no cover
    Event = EventRSVP = None
try:
    from stories.models import Story
except ImportError:  # pragma: no cover
    Story = None

REGULAR = 'regular'
RESPONDER = 'first_responder'
CONNECTOR = 'connector'
STORYTELLER = 'storyteller'
ORGANIZER = 'organizer'
RISING = 'rising_star'

# Highest first: the first earned one is the member's top style.
ORDER = (REGULAR, ORGANIZER, CONNECTOR, RESPONDER, STORYTELLER, RISING)
REGULAR_AT = 3
RISING_DAYS = 90
CACHE_SECONDS = 60

# key -> name, one-line explanation, next-step label, URL name, milestones.
ARCHETYPES = {
    REGULAR: {
        'name': _('The Regular'),
        'line': _('You have 3 or more verified donations. Thank you for '
                  'showing up, at your own pace.'),
        'next': _('Share your story'), 'url': 'story-create',
        'milestones': (3, 5, 10, 20),
        'progress': _('%(current)s of %(target)s verified donations so far'),
        'color': 'var(--bb-green)',
    },
    ORGANIZER: {
        'name': _('The Organizer'),
        'line': _('You hosted a blood drive that was approved. You bring '
                  'people together.'),
        'next': _('See your drives'), 'url': 'events:mine',
        'milestones': (1, 3, 5, 10),
        'progress': _('%(current)s of %(target)s approved drives so far'),
        'color': 'var(--bb-amber)',
    },
    CONNECTOR: {
        'name': _('The Connector'),
        'line': _('A friend you invited gave a verified donation. You '
                  'widen the circle.'),
        'next': _('Share your invite link'), 'url': 'growth:invite-home',
        'milestones': (1, 3, 5, 10),
        'progress': _('%(current)s of %(target)s friends so far'),
        'color': 'var(--bb-blue)',
    },
    RESPONDER: {
        'name': _('The First Responder'),
        'line': _('You said "I can help" on a request. Quick kindness '
                  'matters.'),
        'next': _('See open requests'), 'url': 'request-list',
        'milestones': (1, 3, 10, 25),
        'progress': _('%(current)s of %(target)s pledges so far'),
        'color': 'var(--bb-red)',
    },
    STORYTELLER: {
        'name': _('The Storyteller'),
        'line': _('Your story was published. Real voices make giving '
                  'blood feel closer.'),
        'next': _('Read community stories'), 'url': 'stories',
        'milestones': (1, 3, 5),
        'progress': _('%(current)s of %(target)s stories so far'),
        'color': 'var(--bb-violet)',
    },
    RISING: {
        'name': _('Rising Star'),
        'line': _('You are new here and already taking part. Welcome!'),
        'next': _('Read the Learn guide'), 'url': 'learn',
        'milestones': (4,),
        'progress': _('%(current)s of %(target)s ways to take part so far'),
        'color': 'var(--bb-rose)',
    },
}
NO_STYLE = {
    'next': _('See open requests'), 'url': 'request-list',
}


class Style:
    """One member's result. ``earned`` lists every archetype they qualify
    for (the top one first); ``counts`` has the number behind each."""

    def __init__(self, key, counts, earned):
        info = ARCHETYPES[key]
        self.key = key
        self.name = info['name']
        self.line = info['line']
        self.earned = earned
        self.counts = counts
        count = counts.get(key, 0)
        target = next((m for m in info['milestones'] if count < m),
                      info['milestones'][-1])
        shown = min(count, target)
        self.progress = {
            'current': shown, 'target': target,
            'percent': min(100, int(shown * 100 / target)) if target else 100,
            'label': info['progress'] % {'current': shown, 'target': target},
        }
        self.next_label = info['next']
        self.next_url = _url(info['url'])

    def __repr__(self):
        return f'<Style {self.key}>'


def _url(name):
    try:
        return reverse(name)
    except NoReverseMatch:  # an optional lane is not installed
        return reverse('home')


def counts_for(user):
    """The number behind every archetype for this member."""
    verified = Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
    ).count()
    counts = {
        REGULAR: verified,
        RESPONDER: RequestResponse.objects.filter(donor=user).count(),
        CONNECTOR: 0, STORYTELLER: 0, ORGANIZER: 0, RISING: 0,
    }
    if Referral is not None:
        counts[CONNECTOR] = Referral.objects.filter(
            inviter=user, rewarded_at__isnull=False,
        ).count()
    if Story is not None:
        counts[STORYTELLER] = Story.objects.filter(
            author=user, status=Story.Status.APPROVED,
        ).count()
    if Event is not None:
        counts[ORGANIZER] = Event.objects.filter(
            organizer=user,
            status__in=(Event.Status.APPROVED, Event.Status.COMPLETED),
        ).count()
    return counts


def _early_ways(user, logged, pledged):
    """How many different ways a new member has taken part (0 to 4); zero
    for anyone whose account is older than RISING_DAYS."""
    profile = getattr(user, 'profile', None)
    created = getattr(profile, 'created_at', None)
    if created is None or created < timezone.now() - datetime.timedelta(
            days=RISING_DAYS):
        return 0
    ways = int(logged) + int(pledged)
    if Story is not None:
        ways += int(Story.objects.filter(author=user).exists())
    if EventRSVP is not None:
        ways += int(EventRSVP.objects.filter(user=user).exists())
    return ways


def _earned(counts):
    earned = []
    for key in ORDER:
        count = counts.get(key, 0)
        need = REGULAR_AT if key == REGULAR else 1
        if count >= need:
            earned.append(key)
    return earned


def _counts_and_earned(user):
    counts = counts_for(user)
    earned = _earned(counts)
    if not earned:
        # Only looked up when nothing else applies: it costs extra queries.
        counts[RISING] = _early_ways(user, logged=Donation.objects.filter(
            donor=user,
        ).exclude(status=Donation.Status.REJECTED).exists(), pledged=False)
        earned = _earned(counts)
    return counts, earned


def style_for(user, use_cache=False):
    """The member's top archetype as a ``Style``, or None if they have not
    taken part in any way yet. ``use_cache`` keeps the numbers for a minute
    (the busy hub panel uses it); the wording is always built fresh, so it
    follows the active language."""
    if user is None or not getattr(user, 'is_authenticated', True):
        return None
    key = f'spotlight:style:{user.pk}'
    cached = cache.get(key) if use_cache else None
    if cached is None:
        cached = _counts_and_earned(user)
        if use_cache:
            cache.set(key, cached, CACHE_SECONDS)
    counts, earned = cached
    if not earned:
        return None
    return Style(earned[0], counts, earned)


def all_styles(style):
    """Every archetype for the style page: key, name, line, emblem colour and
    whether the member has earned it (top one marked)."""
    earned = style.earned if style else []
    return [{
        'key': key,
        'name': ARCHETYPES[key]['name'],
        'line': ARCHETYPES[key]['line'],
        'color': ARCHETYPES[key]['color'],
        'earned': key in earned,
        'top': bool(style) and key == style.key,
    } for key in ORDER]
