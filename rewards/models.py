from collections import namedtuple
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.db.models import Count, F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rewards.blood import BLOOD_GROUP_CHOICES  # noqa: F401 (re-exported)
from rewards.geo import DISTRICT_CHOICES

DONATION_CYCLE_DAYS = settings.DONATION_INTERVAL_DAYS  # see settings.py
STREAK_GRACE_DAYS = 14  # slack allowed before a streak is considered broken
LIVES_PER_DONATION = 3  # upper-bound estimate of patients one donation can help
REQUEST_BONUS_POINTS = 20  # extra points for a verified donation to a request
MAX_OPEN_REQUESTS_PER_USER = 3  # spam guard on the public request board

MAX_PENDING_CONTACTS_PER_USER = 5  # outgoing contact requests awaiting an answer
CONTACT_RETRY_DAYS = 30  # wait before asking a donor who declined again


def validate_not_future(value):
    if value > timezone.localdate():
        raise ValidationError(_('Donation date cannot be in the future.'))


def validate_not_past(value):
    if value < timezone.localdate():
        raise ValidationError(_('Date cannot be in the past.'))


class Place(models.Model):
    """A hospital, blood bank or donation camp donors can pick from.

    Curated by staff: donors who can't find theirs type a name instead,
    so the list stays free of duplicates and spam.
    """

    class Kind(models.TextChoices):
        HOSPITAL = 'hospital', _('Hospital')
        BLOOD_BANK = 'blood_bank', _('Blood bank')
        CAMP = 'camp', _('Donation camp')

    name = models.CharField(_('Name'), max_length=255)
    district = models.CharField(
        _('District'), max_length=100, choices=DISTRICT_CHOICES,
    )
    kind = models.CharField(
        _('Kind'), max_length=12, choices=Kind.choices, default=Kind.HOSPITAL,
    )
    is_approved = models.BooleanField(
        _('Listed in selects'), default=True,
        help_text=_('Untick to hide it without deleting past records.'),
    )

    class Meta:
        ordering = ['district', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['name', 'district'], name='unique_place_per_district',
            ),
        ]

    def __str__(self):
        return f'{self.name}, {self.district}'


class Donation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        APPROVED = 'approved', _('Approved')
        REJECTED = 'rejected', _('Rejected')

    donor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='donations',
    )
    blood_group = models.CharField(
        _('Blood Group'), max_length=3, choices=BLOOD_GROUP_CHOICES,
    )
    donation_date = models.DateField(
        _('Donation Date'), validators=[validate_not_future],
    )
    district = models.CharField(
        _('District'), max_length=100, choices=DISTRICT_CHOICES, blank=True,
    )
    place = models.ForeignKey(
        Place, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='donations', verbose_name=_('Hospital / blood bank'),
    )
    # Always filled: the place's name, or what the donor typed when theirs
    # wasn't listed. Keeps history readable if a Place is later removed.
    location = models.CharField(_('Location'), max_length=255)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='verified_donations',
    )
    admin_note = models.TextField(_('Admin Note'), blank=True)
    blood_request = models.ForeignKey(
        'rewards.BloodRequest', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='donations',
        verbose_name=_('In response to request'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-donation_date']

    def __str__(self):
        return f'{self.donor} - {self.donation_date} ({self.status})'


class BloodRequestQuerySet(models.QuerySet):
    def with_received(self):
        return self.annotate(received=Count(
            'donations', filter=Q(donations__status='approved'),
        ))

    def open_and_current(self):
        return self.filter(
            status=BloodRequest.Status.OPEN,
            needed_by__gte=timezone.localdate(),
        )

    def still_needed(self):
        """Open, not expired, and not yet covered by verified donations."""
        return (
            self.open_and_current().with_received()
            .filter(received__lt=F('units_needed'))
        )


class BloodRequest(models.Model):
    """A public, urgent call for donors of one blood group."""

    class Status(models.TextChoices):
        OPEN = 'open', _('Open')
        CLOSED = 'closed', _('Closed')

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='blood_requests',
    )
    blood_group = models.CharField(
        _('Blood Group'), max_length=3, choices=BLOOD_GROUP_CHOICES,
    )
    hospital = models.CharField(_('Hospital'), max_length=255)
    place = models.ForeignKey(
        Place, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='blood_requests', verbose_name=_('Hospital / blood bank'),
    )
    district = models.CharField(
        _('District'), max_length=100, choices=DISTRICT_CHOICES,
    )
    units_needed = models.PositiveSmallIntegerField(
        _('Bags needed'), default=1,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    needed_by = models.DateField(
        _('Needed by'), validators=[validate_not_past],
    )
    contact_phone = models.CharField(
        _('Contact phone'), max_length=12,
        validators=[RegexValidator(
            r'^\d{10,12}$', message='Format (ex: 01712345678)',
        )],
    )
    note = models.CharField(_('Note'), max_length=500, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = BloodRequestQuerySet.as_manager()

    class Meta:
        ordering = ['needed_by', '-created_at']

    def __str__(self):
        return f'{self.blood_group} at {self.hospital}, {self.district}'

    @property
    def units_received(self):
        if hasattr(self, 'received'):
            return self.received
        return self.donations.filter(
            status=Donation.Status.APPROVED,
        ).count()

    @property
    def units_remaining(self):
        return max(self.units_needed - self.units_received, 0)

    @property
    def is_fulfilled(self):
        return self.units_received >= self.units_needed

    @property
    def is_expired(self):
        return self.needed_by < timezone.localdate()

    @property
    def is_active(self):
        return (
            self.status == self.Status.OPEN
            and not self.is_expired
            and not self.is_fulfilled
        )


class RequestResponse(models.Model):
    """A donor saying "I can help" on a request. Not a donation: points only
    come from an admin-verified donation linked to the request."""

    blood_request = models.ForeignKey(
        BloodRequest, on_delete=models.CASCADE, related_name='responses',
    )
    donor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='request_responses',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['blood_request', 'donor'],
                name='one_response_per_donor_per_request',
            ),
        ]

    def __str__(self):
        return f'{self.donor} -> {self.blood_request}'


class ContactRequest(models.Model):
    """One person asking a directory donor for help. The donor's phone number
    stays hidden until the donor accepts, so contact is always consented."""

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        ACCEPTED = 'accepted', _('Accepted')
        DECLINED = 'declined', _('Declined')

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='contact_requests_sent',
    )
    donor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='contact_requests_received',
    )
    message = models.CharField(_('Message'), max_length=200, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['sender', 'donor'],
                condition=Q(status='pending'),
                name='one_pending_contact_per_pair',
            ),
            models.CheckConstraint(
                check=~Q(sender=F('donor')), name='no_contact_with_self',
            ),
        ]

    def __str__(self):
        return f'{self.sender} -> {self.donor} ({self.status})'


class Badge(models.Model):
    """An achievement a donor unlocks: a donation-count tier, or a streak."""

    class Type(models.TextChoices):
        TIER = 'tier', _('Tier (by total donations)')
        STREAK = 'streak', _('Streak (by consecutive donation cycles)')
        HERO = 'hero', _('Hero (by verified donations to urgent requests)')

    name = models.CharField(_('Name'), max_length=50)
    icon = models.CharField(
        _('Icon'), max_length=50, default='award',
        help_text=_("Feather icon name, e.g. 'award' for feather-award."),
    )
    color = models.CharField(_('Color'), max_length=20, default='#cd7f32')
    description = models.CharField(_('Description'), max_length=255, blank=True)
    badge_type = models.CharField(
        _('Type'), max_length=10, choices=Type.choices, default=Type.TIER,
    )
    min_donations = models.PositiveIntegerField(
        _('Minimum Approved Donations'), null=True, blank=True,
        help_text=_('Required for tier badges.'),
    )
    min_streak = models.PositiveIntegerField(
        _('Minimum Streak'), null=True, blank=True,
        help_text=_('Required for streak badges.'),
    )
    min_request_donations = models.PositiveIntegerField(
        _('Minimum Donations to Requests'), null=True, blank=True,
        help_text=_('Required for hero badges.'),
    )

    class Meta:
        ordering = [
            'badge_type', 'min_donations', 'min_streak',
            'min_request_donations',
        ]

    def __str__(self):
        return self.name


def approved_donation_count(user):
    return Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
    ).count()


def get_current_streak(user):
    """Consecutive approved donations spaced no more than one donation
    cycle (+ grace) apart, counting back from the most recent one. Zero
    if the donor hasn't donated within a cycle+grace of today."""
    dates = list(
        Donation.objects.filter(donor=user, status=Donation.Status.APPROVED)
        .order_by('donation_date').values_list('donation_date', flat=True)
    )
    if not dates:
        return 0

    threshold = timedelta(days=DONATION_CYCLE_DAYS + STREAK_GRACE_DAYS)
    streak = 1
    for prev_date, curr_date in zip(dates, dates[1:]):
        streak = streak + 1 if (curr_date - prev_date) <= threshold else 1

    if timezone.localdate() - dates[-1] > threshold:
        return 0
    return streak


def get_donor_points(user):
    approved = Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
    )
    request_donations = approved.filter(blood_request__isnull=False).count()
    return (
        approved.count() * 10
        + get_current_streak(user) * 5
        + request_donations * REQUEST_BONUS_POINTS
    )


Eligibility = namedtuple(
    'Eligibility', 'eligible last_date next_date days_left percent',
)


def get_eligibility(user):
    """Cooldown since the last donation. Pending donations count too: if a
    donor just logged one, they must not be nudged to give again before an
    admin has even looked at it. Only rejected ones are ignored."""
    last_date = (
        Donation.objects.filter(donor=user)
        .exclude(status=Donation.Status.REJECTED)
        .order_by('-donation_date')
        .values_list('donation_date', flat=True)
        .first()
    )
    if last_date is None:
        return Eligibility(True, None, None, 0, 100)

    today = timezone.localdate()
    next_date = last_date + timedelta(days=DONATION_CYCLE_DAYS)
    days_left = max((next_date - today).days, 0)
    elapsed = max((today - last_date).days, 0)
    percent = min(100, int(elapsed / DONATION_CYCLE_DAYS * 100))
    return Eligibility(days_left == 0, last_date, next_date, days_left, percent)


def lives_saved(donation_count):
    return donation_count * LIVES_PER_DONATION


def get_community_stats():
    approved = Donation.objects.filter(status=Donation.Status.APPROVED)
    donations = approved.count()
    return {
        'donations': donations,
        # order_by() so the default -donation_date ordering doesn't make
        # distinct() count (donor, date) pairs instead of donors.
        'donors': approved.order_by().values('donor').distinct().count(),
        'lives': lives_saved(donations),
    }


def get_donor_badge_progress(user):
    """Returns (current_badge, next_badge, approved_count, progress_percent)
    for tier badges only."""
    count = approved_donation_count(user)
    badges = list(Badge.objects.filter(badge_type=Badge.Type.TIER))

    current_badge = None
    next_badge = None
    for badge in badges:
        if count >= badge.min_donations:
            current_badge = badge
        elif next_badge is None:
            next_badge = badge

    if next_badge is None:
        progress_percent = 100
    else:
        lower = current_badge.min_donations if current_badge else 0
        span = next_badge.min_donations - lower
        progress_percent = int((count - lower) / span * 100) if span else 100

    return current_badge, next_badge, count, progress_percent


def get_earned_streak_badges(streak):
    return list(
        Badge.objects.filter(badge_type=Badge.Type.STREAK, min_streak__lte=streak)
    )


def request_donation_count(user):
    """Verified donations this donor made in answer to an urgent request."""
    return Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
        blood_request__isnull=False,
    ).count()


def get_earned_hero_badges(request_donations):
    return list(Badge.objects.filter(
        badge_type=Badge.Type.HERO,
        min_request_donations__lte=request_donations,
    ))


def get_donor_badge_snapshot(user):
    """(tier_badge_id, {streak badge ids}, {hero badge ids}) -- for diffing
    before/after a donation gets approved, to detect new badges."""
    tier_badge, _, _, _ = get_donor_badge_progress(user)
    streak_badge_ids = frozenset(
        b.id for b in get_earned_streak_badges(get_current_streak(user))
    )
    hero_badge_ids = frozenset(
        b.id for b in get_earned_hero_badges(request_donation_count(user))
    )
    return (
        (tier_badge.id if tier_badge else None),
        streak_badge_ids,
        hero_badge_ids,
    )


def get_newly_unlocked_badges(before_snapshot, user):
    """Badges earned by `user` now that weren't in `before_snapshot`."""
    before_tier_id, before_streak_ids, before_hero_ids = before_snapshot
    after_tier, _, _, _ = get_donor_badge_progress(user)
    after_streak_badges = get_earned_streak_badges(get_current_streak(user))
    after_hero_badges = get_earned_hero_badges(request_donation_count(user))

    newly_unlocked = []
    if after_tier and after_tier.id != before_tier_id:
        newly_unlocked.append(after_tier)
    newly_unlocked += [
        b for b in after_streak_badges if b.id not in before_streak_ids
    ]
    newly_unlocked += [
        b for b in after_hero_badges if b.id not in before_hero_ids
    ]
    return newly_unlocked
