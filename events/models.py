"""Events: blood drives, RSVPs, squad entries, seasonal campaigns (with
badges) and squad challenges.

Everything public here is admin-approved first (drives) or admin-created
(campaigns, challenges). Challenge numbers count admin-approved donations
only.
"""
import datetime

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rewards.geo import DISTRICT_CHOICES
from rewards.leaderboard import display_name

# An event with no end time is treated as lasting this long (calendar file,
# "has it finished?" checks and the attendance window).
DEFAULT_DURATION = datetime.timedelta(hours=3)

# Anti-spam caps for organizers.
MAX_EVENTS_PER_DAY = getattr(settings, 'EVENTS_MAX_PER_DAY', 3)
MAX_PENDING_EVENTS = getattr(settings, 'EVENTS_MAX_PENDING', 5)


def _subquery_count(qs):
    return Coalesce(Subquery(
        qs.order_by().values('event').annotate(c=Count('pk')).values('c'),
        output_field=IntegerField(),
    ), 0)


class VerifiedOrganizer(models.Model):
    """A user staff trust to run drives (a hospital club, a youth group).
    Managed in the admin; shown as a badge on their events."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='verified_organizer',
    )
    note = models.CharField(
        _('Note (staff only)'), max_length=200, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Verified organizer {self.user_id}'


class EventQuerySet(models.QuerySet):
    @staticmethod
    def _live_q(now):
        """Not finished yet."""
        return (
            Q(ends_at__gte=now)
            | Q(ends_at__isnull=True, starts_at__gte=now - DEFAULT_DURATION)
        )

    @staticmethod
    def _ended_q(now):
        return (
            Q(ends_at__lt=now)
            | Q(ends_at__isnull=True, starts_at__lt=now - DEFAULT_DURATION)
        )

    def upcoming(self, now=None):
        """Approved and not finished: the public list."""
        now = now or timezone.now()
        return self.filter(status=Event.Status.APPROVED).filter(
            self._live_q(now),
        )

    def completed(self, now=None):
        """Approved events that have finished, plus any marked completed."""
        now = now or timezone.now()
        return self.filter(
            Q(status=Event.Status.COMPLETED)
            | Q(status=Event.Status.APPROVED) & self._ended_q(now)
        )

    def with_stats(self):
        """Counts, the verified badge and the joins every card needs, in one
        query (no per-row lookups on list pages)."""
        return self.select_related(
            'organizer__profile', 'place',
        ).annotate(
            going_count=_subquery_count(
                EventRSVP.objects.filter(event=OuterRef('pk')),
            ),
            team_count=_subquery_count(
                EventTeam.objects.filter(event=OuterRef('pk')),
            ),
            organizer_verified=Exists(
                VerifiedOrganizer.objects.filter(user=OuterRef('organizer')),
            ),
        )


class Event(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Waiting for approval')
        APPROVED = 'approved', _('Approved')
        CANCELLED = 'cancelled', _('Cancelled')
        COMPLETED = 'completed', _('Completed')

    title = models.CharField(_('Title'), max_length=120)
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='organized_events',
    )
    organizer_name = models.CharField(
        _('Organizer name (optional)'), max_length=80, blank=True,
        help_text=_('A club or group name shown instead of your own.'),
    )
    starts_at = models.DateTimeField(_('Starts'))
    ends_at = models.DateTimeField(_('Ends (optional)'), null=True, blank=True)
    district = models.CharField(
        _('District'), max_length=100, choices=DISTRICT_CHOICES,
    )
    place = models.ForeignKey(
        'rewards.Place', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='events', verbose_name=_('Listed hospital / blood bank'),
    )
    venue = models.CharField(
        _('Venue'), max_length=200, blank=True,
        help_text=_('Where exactly, for example the hall or the campus gate.'),
    )
    description = models.TextField(_('Details'), max_length=2000, blank=True)
    capacity = models.PositiveIntegerField(
        _('Spots (optional)'), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10000)],
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
        db_index=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ['starts_at', 'pk']
        indexes = [models.Index(fields=['status', 'starts_at'])]

    def __str__(self):
        return self.title

    # -- time ---------------------------------------------------------

    @property
    def effective_end(self):
        return self.ends_at or (self.starts_at + DEFAULT_DURATION)

    @property
    def is_past(self):
        return self.effective_end < timezone.now()

    @property
    def has_started(self):
        return self.starts_at <= timezone.now()

    # -- state --------------------------------------------------------

    @property
    def is_public(self):
        """Visible to everyone: approved or completed, or cancelled after it
        had been approved (people who planned to come must see that)."""
        if self.status in (self.Status.APPROVED, self.Status.COMPLETED):
            return True
        return self.status == self.Status.CANCELLED and bool(self.approved_at)

    @property
    def accepts_rsvps(self):
        return self.status == self.Status.APPROVED and not self.is_past

    @property
    def is_verified_organizer(self):
        if hasattr(self, 'organizer_verified'):
            return self.organizer_verified
        return VerifiedOrganizer.objects.filter(
            user_id=self.organizer_id,
        ).exists()

    @property
    def public_organizer(self):
        """The organizer's group name, or their name only if they allow
        public naming (the leaderboard switch), else a neutral label."""
        if self.organizer_name:
            return self.organizer_name
        try:
            listed = self.organizer.profile.show_on_leaderboard
        except ObjectDoesNotExist:
            listed = False
        return display_name(self.organizer) if listed else 'A community member'

    @property
    def where(self):
        parts = []
        if self.venue:
            parts.append(self.venue)
        elif self.place_id:
            parts.append(self.place.name)
        parts.append(self.district)
        return ', '.join(p for p in parts if p)

    def spots_left(self, going=None):
        if self.capacity is None:
            return None
        if going is None:
            going = self.rsvps.count()
        return max(self.capacity - going, 0)

    def clean(self):
        errors = {}
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors['ends_at'] = _('The end must be after the start.')
        if (
            self._state.adding and self.starts_at
            and self.starts_at < timezone.now()
        ):
            errors['starts_at'] = _('An event cannot start in the past.')
        if self.place_id and self.district and self.place.district != self.district:
            errors['place'] = _('That place is in a different district.')
        if errors:
            raise ValidationError(errors)


class EventRSVP(models.Model):
    """One going member. Cancelling before the start deletes the row; after
    the start it is kept as the attendance record."""

    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name='rsvps',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='event_rsvps',
    )
    share_name = models.BooleanField(
        _('Let the organizer see my name'), default=False,
        help_text=_('Otherwise the organizer sees "Donor #id".'),
    )
    checked_in = models.BooleanField(_('Checked in'), default=False)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    verified = models.BooleanField(
        _('Verified by staff'), default=False,
        help_text=_('Staff confirm this person really attended.'),
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'user'], name='one_rsvp_per_user_per_event',
            ),
        ]

    def __str__(self):
        return f'RSVP {self.user_id} -> {self.event_id}'

    @property
    def organizer_label(self):
        """What the organizer sees. Never a phone number."""
        if self.share_name:
            return display_name(self.user)
        return f'Donor #{self.user_id}'


class EventTeam(models.Model):
    """A squad entered into a drive by its owner."""

    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name='teams',
    )
    squad = models.ForeignKey(
        'squads.Squad', on_delete=models.CASCADE, related_name='event_entries',
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'squad'], name='one_entry_per_squad_per_event',
            ),
        ]

    def __str__(self):
        return f'Squad {self.squad_id} in event {self.event_id}'


# -- campaigns ---------------------------------------------------------

CAMPAIGN_THEMES = [
    ('red', 'Red'),
    ('green', 'Green'),
    ('blue', 'Blue'),
    ('amber', 'Amber'),
    ('violet', 'Violet'),
]

# Feather icons present in staticfiles/css/feather.css (a test checks this).
CAMPAIGN_ICONS = [
    ('droplet', 'droplet'), ('heart', 'heart'), ('award', 'award'),
    ('flag', 'flag'), ('star', 'star'), ('sun', 'sun'), ('moon', 'moon'),
    ('gift', 'gift'), ('users', 'users'), ('calendar', 'calendar'),
    ('shield', 'shield'), ('zap', 'zap'), ('globe', 'globe'),
    ('target', 'target'), ('thumbs-up', 'thumbs-up'), ('bell', 'bell'),
    ('sunrise', 'sunrise'), ('sunset', 'sunset'), ('map-pin', 'map-pin'),
]


def _month_day(year, month, day):
    try:
        return datetime.date(year, month, day)
    except ValueError:  # 29 February in a common year
        return datetime.date(year, month, 28)


class Campaign(models.Model):
    """A seasonal push with a themed banner and a one-time badge.

    Dates: either fixed (``starts_on`` and ``ends_on``, for movable dates
    such as Ramadan) or a yearly day (``recurring_month`` and
    ``recurring_day``, lasting ``duration_days``). Fixed dates win. Seeded
    campaigns are inactive: staff switch them on (``is_active``).
    """

    name = models.CharField(_('Name'), max_length=80)
    slug = models.SlugField(_('Slug'), max_length=80, unique=True)
    starts_on = models.DateField(_('Starts on'), null=True, blank=True)
    ends_on = models.DateField(_('Ends on'), null=True, blank=True)
    recurring_month = models.PositiveSmallIntegerField(
        _('Every year: month'), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    recurring_day = models.PositiveSmallIntegerField(
        _('Every year: day'), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    duration_days = models.PositiveSmallIntegerField(
        _('Days it lasts (yearly campaigns)'), default=3,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
    )
    headline = models.CharField(_('Headline'), max_length=120)
    body = models.TextField(_('Message'), max_length=600, blank=True)
    theme = models.CharField(
        _('Color'), max_length=10, choices=CAMPAIGN_THEMES, default='red',
    )
    icon = models.CharField(
        _('Icon'), max_length=20, choices=CAMPAIGN_ICONS, default='heart',
    )
    badge_name = models.CharField(_('Badge name'), max_length=50)
    badge_icon = models.CharField(
        _('Badge icon'), max_length=20, choices=CAMPAIGN_ICONS,
        default='award',
    )
    is_active = models.BooleanField(
        _('Active'), default=False,
        help_text=_('Off by default. Turn on to show the banner and award '
                    'the badge for donations inside the dates.'),
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if bool(self.starts_on) != bool(self.ends_on):
            errors['ends_on'] = _('Set both dates, or neither.')
        elif self.starts_on and self.ends_on < self.starts_on:
            errors['ends_on'] = _('The end cannot be before the start.')
        if bool(self.recurring_month) != bool(self.recurring_day):
            errors['recurring_day'] = _('Set both month and day, or neither.')
        elif self.recurring_month:
            try:
                datetime.date(2000, self.recurring_month, self.recurring_day)
            except ValueError:
                errors['recurring_day'] = _('That is not a real date.')
        if errors:
            raise ValidationError(errors)

    def windows_near(self, day):
        """Date windows that could contain ``day`` (list of (start, end))."""
        if self.starts_on and self.ends_on:
            return [(self.starts_on, self.ends_on)]
        if self.recurring_month and self.recurring_day:
            return [
                self.yearly_window(year)
                for year in (day.year - 1, day.year)
            ]
        return []

    def yearly_window(self, year):
        start = _month_day(year, self.recurring_month, self.recurring_day)
        return start, start + datetime.timedelta(days=self.duration_days - 1)

    def windows_between(self, first, last):
        """Every window that touches ``first``..``last``."""
        if self.starts_on and self.ends_on:
            return [(self.starts_on, self.ends_on)]
        if not (self.recurring_month and self.recurring_day):
            return []
        return [
            self.yearly_window(year)
            for year in range(first.year - 1, last.year + 1)
        ]

    def contains(self, day):
        return any(start <= day <= end for start, end in self.windows_near(day))

    def current_window(self, day):
        for start, end in self.windows_near(day):
            if start <= day <= end:
                return start, end
        return None

    @classmethod
    def active_on(cls, day=None):
        """Active campaigns whose window includes ``day`` (default: today)."""
        day = day or timezone.localdate()
        return [c for c in cls.objects.filter(is_active=True) if c.contains(day)]


class CampaignBadgeAward(models.Model):
    """A donor's one-time badge for a campaign."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='campaign_badges',
    )
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='awards',
    )
    donation = models.ForeignKey(
        'rewards.Donation', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-awarded_at', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'campaign'], name='one_campaign_badge_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.campaign_id} badge for {self.user_id}'


# -- squad challenges --------------------------------------------------

class Challenge(models.Model):
    """A time-boxed squad goal or squad-vs-squad race. Created by staff in
    the admin. Progress is computed from approved donations."""

    class Scope(models.TextChoices):
        GOAL = 'goal', _('Squad goal (every squad aims for the target)')
        VERSUS = 'versus', _('Squad vs squad (most donations wins)')

    title = models.CharField(_('Title'), max_length=100)
    description = models.CharField(_('About'), max_length=300, blank=True)
    starts_on = models.DateField(_('Starts on'))
    ends_on = models.DateField(_('Ends on'))
    target_donations = models.PositiveIntegerField(
        _('Target (verified donations per squad)'),
        validators=[MinValueValidator(1)],
    )
    scope = models.CharField(
        max_length=10, choices=Scope.choices, default=Scope.GOAL,
    )
    is_published = models.BooleanField(_('Published'), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, editable=False, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-starts_on', '-pk']

    def __str__(self):
        return self.title

    def clean(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError(
                {'ends_on': _('The end cannot be before the start.')}
            )

    @property
    def days(self):
        return (self.ends_on - self.starts_on).days + 1

    def state(self, today=None):
        today = today or timezone.localdate()
        if today < self.starts_on:
            return 'upcoming'
        if today > self.ends_on:
            return 'finished'
        return 'active'
