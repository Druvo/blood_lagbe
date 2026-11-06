"""Moments logic: impact updates, thank-you notes, celebrations, the ready
countdown and reminders, streak freezes and the live pledge pulse.

Public helpers for other lanes: ``thanks_for(user)``, ``ready_in_days(user)``
(and ``moments.rare.is_rare`` / ``rare_label``).

Eligibility and the donation interval are reminders, never medical clearance.
Nothing here changes when anyone may donate again.
"""
import logging
import os
import re
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.urls import reverse
from django.utils import timezone

from moments.models import (
    NOTE_MAX_LENGTH,
    Celebration,
    ImpactUpdate,
    ReminderPref,
    StreakFreeze,
    ThankNote,
)
from moments.rare import rare_label
from rewards.leaderboard import display_name
from rewards.models import (
    DONATION_CYCLE_DAYS,
    STREAK_GRACE_DAYS,
    BloodRequest,
    Donation,
    RequestResponse,
    get_current_streak,
    get_donor_badge_progress,
    get_eligibility,
)

logger = logging.getLogger(__name__)

HUB_UPDATES_SHOWN = 3
UPDATE_VISIBLE_DAYS = 30
THANKS_ON_HUB = 5
MAX_NOTES_PER_DAY = 30  # notes one requester can send in a rolling 24 hours
REMINDER_WINDOW_DAYS = 7  # a reminder only goes out this soon after "ready"
CELEBRATION_WINDOW_DAYS = 45  # older achievements are marked seen, not shown
MIN_STREAK_TO_SAVE = 2  # a freeze must forgive a gap that joins 2+ donations
OFF_SALT = 'moments.email-off'
OFF_KINDS = ('ready', 'impact')


def _site():
    return getattr(settings, 'SITE_NAME', 'BloodBond')


def absolute_url(path):
    """Links in emails sent from a management command have no request, so
    the origin comes from SITE_URL (setting or environment)."""
    base = (
        getattr(settings, 'SITE_URL', '')
        or os.environ.get('SITE_URL', '')
        or 'http://localhost:8000'
    )
    return base.rstrip('/') + path


def requester_label(user):
    """Public name for a requester. Someone hidden from public listings is
    never named. Never a phone number."""
    profile = getattr(user, 'profile', None)
    if profile is not None and not profile.show_on_leaderboard:
        return 'A requester'
    return display_name(user)


def get_pref(user):
    pref, _ = ReminderPref.objects.get_or_create(user=user)
    return pref


# ------------------------------------------------------------ signed links

def make_off_token(user, kind):
    return signing.dumps({'u': user.pk, 'k': kind}, salt=OFF_SALT)


def read_off_token(token):
    """(user_id, kind) for a valid token, else None. No expiry: an old email
    must still be able to switch itself off."""
    try:
        data = signing.loads(token, salt=OFF_SALT)
        uid, kind = int(data['u']), data['k']
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
    return (uid, kind) if kind in OFF_KINDS else None


def off_url(user, kind):
    return absolute_url(
        reverse('moments:reminder-off', args=[make_off_token(user, kind)]),
    )


# ---------------------------------------------------------- impact updates

def is_fulfilled_close(blood_request):
    return (
        blood_request.status == BloodRequest.Status.CLOSED
        and blood_request.outcome == BloodRequest.Outcome.FULFILLED_HERE
    )


def donated_for(blood_request, user):
    """True if the donor has a verified donation linked to the request."""
    return Donation.objects.filter(
        donor=user, blood_request=blood_request,
        status=Donation.Status.APPROVED,
    ).exists()


def _send_impact_email(update):
    user = update.user
    if not user.email or not get_pref(user).impact_email:
        return False
    site = _site()
    if donated_for(update.blood_request, user):
        lead = 'Your blood reached someone.'
    else:
        lead = 'Thank you for stepping up.'
    where = f' in {update.district}' if update.district else ''
    body = (
        f'{lead} A request for {update.blood_group} blood{where} that you '
        'offered to help with was fulfilled.\n\n'
        'We share no patient details. Open your hub to see the update:\n'
        f'{absolute_url(reverse("log-donation"))}\n\n'
        f'To stop these emails: {off_url(user, "impact")}\n\n{site}'
    )
    message = EmailMessage(
        f'{site}: a request you offered to help with was fulfilled', body,
        settings.DEFAULT_FROM_EMAIL, [user.email],
        headers={'List-Unsubscribe': f'<{off_url(user, "impact")}>'},
    )
    try:
        message.send()
    except Exception:  # never break closing a request over an email
        logger.exception('Impact email failed for user %s', user.pk)
        return False
    return True


def deliver_impact_update(blood_request, user):
    """One update per (request, donor). Returns (update, created); the email
    only goes out when the row is created."""
    update, created = ImpactUpdate.objects.get_or_create(
        blood_request=blood_request, user=user,
        defaults={
            'blood_group': blood_request.blood_group,
            'district': blood_request.district,
        },
    )
    if created and _send_impact_email(update):
        update.emailed_at = timezone.now()
        update.save(update_fields=['emailed_at'])
    return update, created


def deliver_impact_updates(blood_request):
    """Updates for everyone who offered to help on a request that has been
    closed as fulfilled. Safe to call again."""
    if not is_fulfilled_close(blood_request):
        return 0
    created_count = 0
    donors = (
        blood_request.responses.select_related('donor__profile')
        .order_by('pk')
    )
    for response in donors:
        _, created = deliver_impact_update(blood_request, response.donor)
        created_count += created
    return created_count


def missing_impact_pairs(user=None):
    """Responses on fulfilled-closed requests that have no update yet."""
    has_update = ImpactUpdate.objects.filter(
        blood_request=OuterRef('blood_request'), user=OuterRef('donor'),
    )
    qs = RequestResponse.objects.filter(
        blood_request__status=BloodRequest.Status.CLOSED,
        blood_request__outcome=BloodRequest.Outcome.FULFILLED_HERE,
    ).annotate(has_update=Exists(has_update)).filter(has_update=False)
    if user is not None:
        qs = qs.filter(donor=user)
    return qs.select_related('blood_request', 'donor__profile')


def sync_impact_updates(user=None, dry_run=False):
    """Catch up on requests closed in a way that skipped the signal (the
    close view updates rows in bulk). Idempotent."""
    count = 0
    for response in missing_impact_pairs(user):
        if not dry_run:
            _, created = deliver_impact_update(
                response.blood_request, response.donor,
            )
            if not created:
                continue
        count += 1
    return count


def visible_updates(user):
    since = timezone.now() - timedelta(days=UPDATE_VISIBLE_DAYS)
    updates = list(
        ImpactUpdate.objects.filter(
            user=user, dismissed=False, created_at__gte=since,
        ).select_related('blood_request')[:HUB_UPDATES_SHOWN]
    )
    for update in updates:
        update.donated = donated_for(update.blood_request, user)
    return updates


# ------------------------------------------------------------- thank notes

_URL_RE = re.compile(r'(https?:|www\.|\w\.(com|net|org|bd|me|co|io|ly)\b)', re.I)


def clean_note(text):
    """(clean text, error). Plain text only: no links, emails or phone-like
    digit runs, so a note cannot carry contact details or spam."""
    text = ' '.join((text or '').split())
    if not text:
        return '', 'Please write a short note.'
    if len(text) > NOTE_MAX_LENGTH:
        return '', f'Please keep it under {NOTE_MAX_LENGTH} characters.'
    digits = re.sub(r'[\s\-().+]', '', text)
    if re.search(r'\d{7,}', digits) or '@' in text or _URL_RE.search(text):
        return '', (
            'Please leave out phone numbers, emails and links. Thank them '
            'in your own words.'
        )
    return text, ''


def responders_of(blood_request):
    """Responder users, oldest first. Only the requester sees this list."""
    return [
        r.donor for r in blood_request.responses
        .select_related('donor__profile').order_by('pk')
    ]


def thanked_ids(blood_request):
    return set(
        ThankNote.objects.filter(blood_request=blood_request)
        .values_list('recipient_id', flat=True)
    )


def can_thank(user, blood_request):
    return bool(
        user.is_authenticated
        and user.pk == blood_request.requester_id
        and blood_request.status == BloodRequest.Status.CLOSED
    )


def send_thanks(blood_request, sender, recipient, text):
    """Send one note to one responder (``recipient`` a user id) or to every
    responder not yet thanked (``recipient == 'all'``).

    Returns (notes_created, error). One note per responder per request, kept
    even if the responder hides it.
    """
    if not can_thank(sender, blood_request):
        return 0, 'Only the requester of a closed request can send thanks.'
    text, error = clean_note(text)
    if error:
        return 0, error
    responder_ids = {u.pk for u in responders_of(blood_request)}
    if recipient == 'all':
        targets = sorted(responder_ids - thanked_ids(blood_request))
        to_all = True
    else:
        try:
            target = int(recipient)
        except (TypeError, ValueError):
            return 0, 'Please choose who to thank.'
        if target not in responder_ids:
            return 0, 'Please choose who to thank.'
        targets, to_all = [target], False
    if not targets:
        return 0, 'Everyone has already had a note from you.'
    since = timezone.now() - timedelta(days=1)
    sent_today = ThankNote.objects.filter(
        sender=sender, created_at__gte=since,
    ).count()
    if sent_today + len(targets) > MAX_NOTES_PER_DAY:
        return 0, 'You have sent a lot of notes today. Please try tomorrow.'

    created = 0
    for target in targets:
        try:
            with transaction.atomic():
                _, was_created = ThankNote.objects.get_or_create(
                    blood_request=blood_request, recipient_id=target,
                    defaults={
                        'sender': sender, 'text': text, 'to_all': to_all,
                    },
                )
        except IntegrityError:
            was_created = False
        created += was_created
    if not created:
        return 0, 'You have already thanked this person.'
    return created, ''


def thanks_for(user, limit=None):
    """Notes others sent this member that they have not hidden, newest first.
    Each has ``sender_label`` (a requester who keeps their name private shows
    as "A requester")."""
    qs = ThankNote.objects.filter(recipient=user, hidden=False).select_related(
        'sender__profile', 'blood_request',
    )
    return qs[:limit] if limit else qs


# ----------------------------------------------------------- celebrations

def pending_celebration(user, progress=None):
    """The one milestone to celebrate now, or None. Every milestone found is
    recorded as seen, so it shows once; older ones are recorded silently so a
    first visit does not replay history. ``progress`` is an optional
    (current_badge, approved_count) the caller already has."""
    if progress is None:
        current_badge, _next, count, _pct = get_donor_badge_progress(user)
    else:
        current_badge, count = progress
    window = timezone.now() - timedelta(days=CELEBRATION_WINDOW_DAYS)

    approved = Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
    ).order_by('-updated_at')
    latest = approved.first()
    donation_recent = bool(latest and latest.updated_at >= window)
    first_pledge = (
        RequestResponse.objects.filter(donor=user).order_by('created_at')
        .first()
    )

    # (priority, key, recent, content)
    found = []
    if current_badge:
        found.append((3, f'tier:{current_badge.pk}', donation_recent, {
            'kind': 'tier',
            'title': f'You reached {current_badge.name}',
            'text': f'{count} verified donation{"s" if count != 1 else ""}. '
                    'Thank you for showing up.',
            'icon': current_badge.icon, 'color': current_badge.color,
        }))
    if count >= 1:
        found.append((2, 'first_donation', donation_recent, {
            'kind': 'first_donation',
            'title': 'Your first donation is verified',
            'text': 'Welcome to the donors who keep people going.',
            'icon': 'heart', 'color': '',
        }))
    if first_pledge:
        found.append((1, 'first_pledge', first_pledge.created_at >= window, {
            'kind': 'first_pledge',
            'title': 'You offered to help for the first time',
            'text': 'Stepping up matters, even before anyone gives blood.',
            'icon': 'users', 'color': '',
        }))

    seen = set(
        Celebration.objects.filter(user=user).values_list('key', flat=True)
    )
    fresh = [item for item in found if item[1] not in seen]
    for _prio, key, _recent, _content in fresh:
        Celebration.objects.get_or_create(user=user, key=key)
    shown = [item for item in fresh if item[2]]
    if not shown:
        return None
    return max(shown, key=lambda item: item[0])[3]


# --------------------------------------------------- ready-again countdown

def ready_in_days(user):
    """Days until the donor is past the donation interval, 0 when they
    already are, or None if they have no logged donation to count from.
    Uses the same rule as the rest of the site (``get_eligibility``: pending
    and approved donations count). A reminder, not medical clearance."""
    return _days_from(get_eligibility(user))


def _days_from(eligibility):
    if eligibility.last_date is None:
        return None
    return eligibility.days_left


def ready_reminders_due():
    """Pairs (pref, eligibility) that should get a reminder now."""
    due = []
    prefs = ReminderPref.objects.filter(
        email_enabled=True, user__is_active=True,
    ).exclude(user__email__isnull=True).exclude(user__email='')
    for pref in prefs.select_related('user'):
        eligibility = get_eligibility(pref.user)
        if eligibility.last_date is None or not eligibility.eligible:
            continue
        if pref.last_reminded_cycle == eligibility.last_date:
            continue
        if (timezone.localdate() - eligibility.next_date).days > (
            REMINDER_WINDOW_DAYS
        ):
            continue
        due.append((pref, eligibility))
    return due


def send_ready_reminder(pref, eligibility):
    """Send one reminder for this donation cycle. The cycle is claimed in the
    database first, so a second run (or a parallel one) sends nothing."""
    claimed = ReminderPref.objects.filter(pk=pref.pk).exclude(
        last_reminded_cycle=eligibility.last_date,
    ).update(last_reminded_cycle=eligibility.last_date)
    if not claimed:
        return False
    site = _site()
    user = pref.user
    body = (
        f'It has been {settings.DONATION_INTERVAL_DAYS} days since your last '
        f'logged donation ({eligibility.last_date:%b %d, %Y}).\n\n'
        'This is a reminder only, not medical clearance. Only give blood if '
        'you feel well, and the blood bank decides whether you can donate '
        'on the day.\n\n'
        f'Open {site}: {absolute_url(reverse("log-donation"))}\n\n'
        f'Turn these reminders off in one click: {off_url(user, "ready")}\n\n'
        f'{site}'
    )
    message = EmailMessage(
        f'{site}: a reminder that your recovery interval is over', body,
        settings.DEFAULT_FROM_EMAIL, [user.email],
        headers={'List-Unsubscribe': f'<{off_url(user, "ready")}>'},
    )
    try:
        message.send()
    except Exception:
        logger.exception('Ready reminder failed for user %s', user.pk)
        ReminderPref.objects.filter(pk=pref.pk).update(
            last_reminded_cycle=pref.last_reminded_cycle,
        )
        return False
    return True


# ---------------------------------------------------------- streak freeze

def frozen_gaps(user):
    """{(donation before, donation after)} gaps this member has frozen."""
    return set(
        StreakFreeze.objects.filter(user=user)
        .values_list('gap_start', 'gap_end')
    )


def broken_gaps(user):
    """Gaps between approved donations longer than cycle + grace that are not
    already frozen, oldest first."""
    dates = list(
        Donation.objects.filter(donor=user, status=Donation.Status.APPROVED)
        .order_by('donation_date').values_list('donation_date', flat=True)
    )
    threshold = timedelta(days=DONATION_CYCLE_DAYS + STREAK_GRACE_DAYS)
    gaps = [(a, b) for a, b in zip(dates, dates[1:]) if (b - a) > threshold]
    if not gaps:
        return []
    frozen = frozen_gaps(user)
    return [gap for gap in gaps if gap not in frozen]


def freeze_offer(user, used=None):
    """The freeze this member could use now, or None. Offered only when the
    year's freeze is unused and forgiving the latest broken gap would really
    restore a streak (of at least MIN_STREAK_TO_SAVE donations before it).
    ``used`` is an optional answer to ``freeze_used_this_year``."""
    if used is None:
        used = freeze_used_this_year(user)
    if used:
        return None
    gaps = broken_gaps(user)
    if not gaps:
        return None
    gap = gaps[-1]
    current = get_current_streak(user)
    with_freeze = get_current_streak(user, frozen={gap})
    if with_freeze - current < MIN_STREAK_TO_SAVE:
        return None
    return {
        'gap_start': gap[0], 'gap_end': gap[1],
        'current': current, 'restored': with_freeze,
    }


def freeze_used_this_year(user):
    return StreakFreeze.objects.filter(
        user=user, year=timezone.localdate().year,
    ).exists()


def activate_freeze(user):
    """Use this year's freeze. Returns (freeze, error). Re-checks the offer
    inside a transaction; the unique (user, year) row stops double clicks."""
    year = timezone.localdate().year
    try:
        with transaction.atomic():
            offer = freeze_offer(user)
            if offer is None:
                if freeze_used_this_year(user):
                    return None, 'You already used your streak freeze this year.'
                return None, 'A freeze would not save a streak right now.'
            freeze = StreakFreeze.objects.create(
                user=user, year=year,
                gap_start=offer['gap_start'], gap_end=offer['gap_end'],
            )
    except IntegrityError:
        return None, 'You already used your streak freeze this year.'
    return freeze, ''


# ------------------------------------------------------------ live pulse

def pulse_counts(blood_request, people=None):
    """Counts only, never names. Each responder offers one bag. ``people``
    is an optional response count the caller already has."""
    if people is None:
        people = blood_request.responses.count()
    needed = blood_request.units_needed
    return {
        'people': people,
        'bags_pledged': min(people, needed),
        'still_needed': max(needed - people, 0),
    }


# ------------------------------------------------------------------- hub

def hub_panel(user, known=None):
    """Context for the hub panel, or None when there is nothing to show.
    ``known`` may carry values the hub view already computed
    (``current_badge``, ``approved_count``, ``eligibility``) to save
    queries."""
    known = known or {}
    sync_impact_updates(user)
    updates = visible_updates(user)
    thanks = list(thanks_for(user, THANKS_ON_HUB))
    thanks_total = thanks_for(user).count() if thanks else 0
    if 'current_badge' in known and 'approved_count' in known:
        progress = (known['current_badge'], known['approved_count'])
    else:
        progress = None
    celebration = pending_celebration(user, progress)
    eligibility = known.get('eligibility') or get_eligibility(user)
    days = _days_from(eligibility)
    used = freeze_used_this_year(user)
    offer = freeze_offer(user, used=used)
    label = rare_label(user)
    pref = get_pref(user)
    if not (
        updates or thanks or celebration or days is not None
        or offer or label
    ):
        return None
    return {
        'updates': updates,
        'thanks': thanks,
        'thanks_more': max(thanks_total - len(thanks), 0),
        'celebration': celebration,
        'ready_days': days,
        'ready_now': days == 0,
        'freeze_offer': offer,
        'freeze_used': used,
        'rare_label': label,
        'pref': pref,
        'has_email': bool(user.email),
    }
