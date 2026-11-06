"""The weekly digest: what goes in it, and the signed unsubscribe link.

Plain text only. It names no other member and carries no phone number or
email address, only the member's own numbers and links back to the site.
"""
from collections import namedtuple

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from habits import content
from habits.models import DigestLog
from habits.services import streak_info
from rewards.blood import COMPATIBLE_DONORS
from rewards.leaderboard import PERIOD_MONTH
from rewards.models import BloodRequest, get_donor_points, get_eligibility
from rewards.quests import get_level
from squads.services import member_board

SALT = 'habits.digest.unsubscribe'
# An unsubscribe link in an old email should keep working for a long while.
TOKEN_MAX_AGE = 60 * 60 * 24 * 730
MAX_REQUESTS = 3

Digest = namedtuple('Digest', 'subject body points level')


def site_url():
    """Absolute site root for links in emails: SITE_URL if set, otherwise
    the first real entry of ALLOWED_HOSTS."""
    configured = getattr(settings, 'SITE_URL', '')
    if configured:
        return configured.rstrip('/')
    for host in settings.ALLOWED_HOSTS:
        if host and not host.startswith(('*', '.')):
            local = host in ('localhost', '127.0.0.1', '[::1]')
            return f'{"http" if local else "https"}://{host}'
    return 'http://localhost:8000'


def absolute(path):
    return site_url() + path


def make_token(user):
    return signing.dumps({'u': user.pk}, salt=SALT)


def read_token(token):
    """The user id inside a valid, unexpired token, or None."""
    try:
        data = signing.loads(token, salt=SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:  # also covers SignatureExpired
        return None
    user_id = data.get('u') if isinstance(data, dict) else None
    return user_id if isinstance(user_id, int) else None


def unsubscribe_path(user):
    return reverse('habits:unsubscribe', args=[make_token(user)])


def iso_week(day=None):
    iso = (day or timezone.localdate()).isocalendar()
    return iso[0], iso[1]


def week_key(day=None):
    year, week = iso_week(day)
    return f'{year}-W{week:02d}'


def previous_log(user):
    return DigestLog.objects.filter(user=user).order_by(
        '-iso_year', '-iso_week', '-pk',
    ).first()


def _matching_requests(user):
    """Open urgent requests in the member's district for a group they can
    give to. Empty when they are resting, unavailable or have no profile
    details: the digest never nudges anyone inside their recovery gap."""
    profile = user.profile
    if not (profile.blood_group and profile.district):
        return []
    if not profile.available_to_donate or not get_eligibility(user).eligible:
        return []
    recipients = [
        group for group, donors in COMPATIBLE_DONORS.items()
        if profile.blood_group in donors
    ]
    return list(
        BloodRequest.objects.still_needed().public()
        .filter(district=profile.district, blood_group__in=recipients)
        .exclude(requester=user).exclude(responses__donor=user)
        .order_by('needed_by', '-created_at', 'pk')[:MAX_REQUESTS]
    )


def _squad_line(user, day):
    for membership in user.squad_memberships.select_related('squad').order_by(
        'joined_at', 'pk',
    ):
        for entry in member_board(membership.squad, PERIOD_MONTH):
            if entry['user'].pk == user.pk and entry['donations'] > 0:
                return (
                    f'Squad: you are #{entry["rank"]} in '
                    f'{membership.squad.name} this month ({day:%B}).'
                )
    return ''


def _xp_lines(user):
    points = get_donor_points(user)
    info = get_level(points)
    level = info.level
    lines = [f'XP: {points}, level {level.number} ({level.name}).']
    prev = previous_log(user)
    if prev is not None:
        change = points - prev.points
        if level.number > prev.level:
            lines.append(f'You reached level {level.number} since your last email. Well done.')
        if change > 0:
            lines.append(f'That is +{change} XP since your last email.')
        elif change == 0:
            lines.append('Your XP is steady since your last email.')
    if not info.is_max:
        lines.append(
            f'{info.points_to_next} XP to {info.next_level.name}. '
            'XP only ever comes from verified donations, never from rushing.'
        )
    return points, level.number, lines


def build_digest(user, day=None):
    """The digest for `user` as of local date `day`."""
    day = day or timezone.localdate()
    site = settings.SITE_NAME
    name = (user.first_name or '').strip() or 'there'
    points, level_number, xp_lines = _xp_lines(user)

    lines = [f'Hi {name},', '', f'Your week on {site}.', '']
    lines += xp_lines

    squad = _squad_line(user, day)
    if squad:
        lines += ['', squad]

    streak = streak_info(user, day)
    lines.append('')
    if streak['current'] > 0:
        lines.append(
            f'Daily visits: a {streak["current"]}-day streak. It is only a '
            'friendly habit: it never affects your points or your donation schedule.'
        )
    else:
        lines.append(
            'Daily visits: a new streak starts the next time you finish a '
            'small action on your Today page. There is no pressure.'
        )
    lines.append(f'Today page: {absolute(reverse("habits:today"))}')

    eligibility = get_eligibility(user)
    requests = _matching_requests(user)
    lines.append('')
    if requests:
        lines.append(
            f'Open urgent requests near you ({user.profile.district}) that '
            'your blood group can usually help:'
        )
        for r in requests:
            lines.append(
                f'- {r.blood_group} needed at {r.hospital}, {r.district}, '
                f'by {r.needed_by:%b} {r.needed_by.day}: '
                f'{absolute(reverse("request-detail", args=[r.pk]))}'
            )
        lines.append(
            'Only donate if you feel well and your recovery gap has passed. '
            'Sharing a request helps too.'
        )
    elif not eligibility.eligible:
        lines.append(
            'You are resting between donations, which is exactly right. '
            'You can still help by sharing an urgent request with a friend.'
        )
    else:
        lines.append(
            'No open urgent requests match your group and district right now.'
        )

    lines += [
        '',
        f'A reminder, not medical advice: {content.FACT_FOOTER} The recovery '
        f'gap ({content.interval_label()}) is a general guide, and your blood '
        'bank may ask for a different one.',
        '',
        f'Turn this email off with one click: {absolute(unsubscribe_path(user))}',
        f'Settings: {absolute(reverse("habits:settings"))}',
        '',
        site,
    ]
    return Digest(
        subject=f'Your week on {site}', body='\n'.join(lines),
        points=points, level=level_number,
    )
