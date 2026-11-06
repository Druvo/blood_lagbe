"""Who gets told about a new blood request, and the alert itself.

The rules are reminders for people who opted in. They never contact anyone
who has not, and the text carries no phone number, email address or patient
detail.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mass_mail
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from alerts.models import AlertPreference, Notification
from alerts.service import notify_once
from alerts.tokens import make_email_off_token
from rewards.blood import donor_groups_for
from rewards.models import BloodRequest, get_eligibility

try:
    from moments.rare import is_rare as _moments_is_rare
except Exception:  # another lane owns moments; fall back to a local list
    _moments_is_rare = None

logger = logging.getLogger(__name__)

User = get_user_model()

KIND_MATCH = Notification.Kind.MATCH
MAX_CANDIDATES = 200  # donors looked at per request
FALLBACK_RARE = ('O-', 'AB-')

SAFETY_FOOTER = (
    'This is a reminder only, not medical clearance. Only give blood if you '
    'feel well and the hospital agrees. There is no pressure.'
)


def is_rare_group(blood_group):
    if _moments_is_rare is not None:
        try:
            return bool(_moments_is_rare(blood_group))
        except Exception:
            pass
    return blood_group in FALLBACK_RARE


def match_dedupe_key(blood_request):
    return f'match-{blood_request.pk}'


def _district_q(district):
    """Donor lives in ``district`` or lists it as an extra district. The
    extras are a comma list, so match whole names only."""
    return (
        Q(profile__district=district)
        | Q(alert_preference__extra_districts=district)
        | Q(alert_preference__extra_districts__startswith=f'{district},')
        | Q(alert_preference__extra_districts__endswith=f',{district}')
        | Q(alert_preference__extra_districts__contains=f',{district},')
    )


def candidate_donors(blood_request, require_opt_in=True):
    """Users whose group, district and availability fit the request, before
    the cooldown and daily cap are applied."""
    groups = donor_groups_for(blood_request.blood_group, True)
    qs = User.objects.filter(
        is_active=True,
        profile__blood_group__in=groups,
        profile__available_to_donate=True,
    ).exclude(pk=blood_request.requester_id).filter(
        _district_q(blood_request.district),
    )
    if require_opt_in:
        qs = qs.filter(alert_preference__enabled=True)
        if not is_rare_group(blood_request.blood_group):
            qs = qs.exclude(alert_preference__only_rare_groups=True)
    return qs


def _recent_match_counts(user_ids):
    since = timezone.now() - timedelta(hours=24)
    rows = (
        Notification.objects.filter(
            user_id__in=user_ids, kind=KIND_MATCH, created_at__gte=since,
        ).order_by().values('user_id').annotate(n=Count('pk'))
    )
    return {row['user_id']: row['n'] for row in rows}


def eligible_donors(blood_request):
    """Opted-in donors to alert, in a fixed order (by id), looking at no more
    than MAX_CANDIDATES. Each is outside the donation cooldown (pending plus
    approved donations count) and under their daily cap."""
    candidates = list(
        candidate_donors(blood_request)
        .select_related('alert_preference')
        .order_by('pk')[:MAX_CANDIDATES]
    )
    counts = _recent_match_counts([u.pk for u in candidates])
    chosen = []
    for user in candidates:
        prefs = user.alert_preference
        if counts.get(user.pk, 0) >= prefs.daily_cap:
            continue
        if not get_eligibility(user).eligible:
            continue
        chosen.append(user)
    return chosen


def viewer_would_match(blood_request, user, eligibility=None):
    """True if ``user`` fits a request the way an opted-in donor would but
    has not opted in (the daily cap is ignored). Used for the gentle prompt
    on the request page; pass ``eligibility`` if the caller already has it."""
    if user.pk == blood_request.requester_id:
        return False
    fits = candidate_donors(blood_request, require_opt_in=False).filter(
        pk=user.pk,
    ).exclude(alert_preference__enabled=True).exists()
    if not fits:
        return False
    if eligibility is None:
        eligibility = get_eligibility(user)
    return eligibility.eligible


def build_alert(blood_request):
    """(title, body) for the in-app notification."""
    where = f'{blood_request.hospital}, {blood_request.district}'
    title = f'A {blood_request.blood_group} donor is needed at {where}'
    if is_rare_group(blood_request.blood_group):
        title = f'Rare blood group: {title}'
    needed = f'{blood_request.needed_by:%b} {blood_request.needed_by.day}'
    body = f'Needed by {needed}. {SAFETY_FOOTER}'
    return title, body


def _site_root():
    root = getattr(settings, 'SITE_URL', '')
    if not root:
        hosts = [h for h in settings.ALLOWED_HOSTS if h and h[0] not in '.*']
        if hosts:
            scheme = 'http' if settings.DEBUG else 'https'
            root = f'{scheme}://{hosts[0]}'
    return root.rstrip('/')


def build_email(user, title, body, request_path):
    """(subject, text, from, [to]) for one donor. No contact details of
    anyone else; the request itself is behind the login."""
    site = getattr(settings, 'SITE_NAME', 'BloodBond')
    root = _site_root()
    off = root + reverse(
        'alerts:email-off', args=[make_email_off_token(user)],
    )
    text = (
        f'{title}\n\n{body}\n\n'
        f'See the request (you need to log in):\n{root}{request_path}\n\n'
        f'Change your alerts: {root}{reverse("alerts:settings")}\n'
        f'Stop alert emails with one click: {off}\n\n{site}'
    )
    return (
        f'{site}: {title}'[:200], text, settings.DEFAULT_FROM_EMAIL,
        [user.email],
    )


def send_match_alerts(blood_request_id):
    """Notify eligible donors about one request. Safe to call twice: each
    (request, donor) pair is notified once. Returns how many were notified."""
    blood_request = (
        BloodRequest.objects.public().open_and_current()
        .filter(pk=blood_request_id).first()
    )
    if blood_request is None:
        return 0
    title, body = build_alert(blood_request)
    path = reverse('request-detail', args=[blood_request.pk])
    key = match_dedupe_key(blood_request)
    created = 0
    emails = []
    for user in eligible_donors(blood_request):
        _, is_new = notify_once(
            user, KIND_MATCH, title, body, url=path, dedupe_key=key,
        )
        if not is_new:
            continue
        created += 1
        if user.alert_preference.email_enabled and user.email:
            emails.append(build_email(user, title, body, path))
    if emails:
        send_mass_mail(emails, fail_silently=True)
    return created


def alerts_sent_for(blood_request):
    """How many members were alerted about a request (a count, no names)."""
    return Notification.objects.filter(
        kind=KIND_MATCH, dedupe_key=match_dedupe_key(blood_request),
    ).count()


def get_preference(user):
    prefs, _ = AlertPreference.objects.get_or_create(user=user)
    return prefs
