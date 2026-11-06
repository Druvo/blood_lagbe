"""Invites, referrals and the Friend Maker recognition.

Anti-gaming rules, all enforced here and covered by tests:
* no reward at sign-up: only the invitee's FIRST approved donation pays out;
* one referral per invitee, and never an inviter who is the invitee, shares
  the invitee's phone number or shares their email address;
* an invite only attaches to an account created in the last few minutes, so
  an old account cannot be "claimed" by a link;
* a daily cap on how many new members one inviter can collect;
* the payout is idempotent (a conditional update, safe against replays).
"""
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from growth.models import (
    FRIEND_MAKER_LEVELS,
    Invite,
    Referral,
    ReferralReward,
    generate_code,
)

SESSION_KEY = 'growth_invite_code'
# The invite is only honoured for an account made this recently.
NEW_ACCOUNT_WINDOW = timedelta(minutes=30)
# Anti-farming: new members one inviter can collect per rolling day.
MAX_NEW_REFERRALS_PER_DAY = 10


def normalize_code(raw):
    return ''.join((raw or '').split()).upper()[:12]


def get_or_create_invite(user):
    """The member's invite, made on first use. Safe against two tabs racing:
    a clash on the user (another tab won) or on the code (vanishingly rare)
    is retried, never raised."""
    invite = Invite.objects.filter(user=user).first()
    if invite:
        return invite
    for _attempt in range(5):
        try:
            with transaction.atomic():
                return Invite.objects.create(user=user, code=generate_code())
        except IntegrityError:
            invite = Invite.objects.filter(user=user).first()
            if invite:
                return invite
    raise RuntimeError('could not allocate an invite code')


def invite_path(invite):
    return reverse('growth:invite', args=[invite.code])


def find_invite(raw_code):
    """The invite for a code typed or linked, or None. Inactive inviters
    do not count."""
    code = normalize_code(raw_code)
    if not code:
        return None
    return (
        Invite.objects.select_related('user__profile')
        .filter(code=code, user__is_active=True).first()
    )


def inviter_label(invite):
    """Who invited you, as a stranger may read it. Only a member who is
    listed publicly is named, through the one shared display-name rule."""
    from rewards.leaderboard import display_name
    user = invite.user
    try:
        public = user.profile.show_on_leaderboard
    except Exception:  # no profile row: treat as private
        public = False
    return display_name(user) if public else 'A friend'


# ---------------------------------------------------------------- attaching

def _same_person(inviter, invitee):
    """Self-referral, and the cheap duplicate-account tricks: the same
    phone number written another way, or the same email address."""
    from accounts.forms import normalize_phone
    if inviter.pk == invitee.pk:
        return True
    a, b = normalize_phone(inviter.phone), normalize_phone(invitee.phone)
    if a and a == b:
        return True
    email_a = (inviter.email or '').strip().lower()
    email_b = (invitee.email or '').strip().lower()
    return bool(email_a and email_a == email_b)


def _is_new_account(user):
    created = getattr(getattr(user, 'profile', None), 'created_at', None)
    return bool(created and timezone.now() - created <= NEW_ACCOUNT_WINDOW)


def attach_referral(request, user):
    """Link a brand-new member to whoever invited them, if the session holds
    an invite code. Returns the Referral, or None when nothing was linked.
    Never raises for bad input: signing up must not fail because of this.

    Called by the ``user_logged_in`` receiver in growth.signals, which fires
    from the sign-up view's ``login()`` while the session still holds the
    code. It is also safe to call directly from a view."""
    session = getattr(request, 'session', None)
    if session is None:
        return None
    code = session.pop(SESSION_KEY, None)  # one try per invite visit
    if not code:
        return None
    invite = find_invite(code)
    if invite is None:
        return None
    inviter = invite.user
    if _same_person(inviter, user) or not _is_new_account(user):
        return None
    if Referral.objects.filter(invitee=user).exists():
        return None
    since = timezone.now() - timedelta(days=1)
    if Referral.objects.filter(
        inviter=inviter, created_at__gte=since,
    ).count() >= MAX_NEW_REFERRALS_PER_DAY:
        return None
    try:
        with transaction.atomic():
            return Referral.objects.create(inviter=inviter, invitee=user)
    except IntegrityError:
        return None


# ---------------------------------------------------------------- rewarding

def award_levels(user):
    """Make sure `user` holds every Friend Maker level their rewarded
    invites have earned. Idempotent."""
    rewarded = Referral.objects.filter(
        inviter=user, rewarded_at__isnull=False,
    ).count()
    earned = []
    for level, needed, _name in FRIEND_MAKER_LEVELS:
        if rewarded >= needed:
            reward, _created = ReferralReward.objects.get_or_create(
                user=user, level=level,
            )
            earned.append(reward)
    return earned


def reward_referral(donation):
    """Pay out the referral behind `donation`, if this is the invitee's
    first approved donation. Returns the Referral when it was just rewarded.

    Safe to call on every save of an approved donation: the conditional
    update only matches once, so a replay or a second approval does nothing.
    """
    from rewards.models import Donation
    if donation.status != Donation.Status.APPROVED:
        return None
    referral = (
        Referral.objects.select_related('inviter')
        .filter(invitee_id=donation.donor_id, rewarded_at__isnull=True).first()
    )
    if referral is None or referral.inviter_id == donation.donor_id:
        return None
    if not referral.inviter.is_active:
        return None
    first = not Donation.objects.filter(
        donor_id=donation.donor_id, status=Donation.Status.APPROVED,
    ).exclude(pk=donation.pk).exists()
    if not first:
        return None
    with transaction.atomic():
        won = Referral.objects.filter(
            pk=referral.pk, rewarded_at__isnull=True,
        ).update(rewarded_at=timezone.now())
        if not won:
            return None
        award_levels(referral.inviter)
    referral.refresh_from_db()
    return referral


# ----------------------------------------------------------------- reading

def referral_stats(user):
    """Counts for the hub panel. Never names the people invited. One query
    for the counts, and a second only for members who earned something."""
    counts = Referral.objects.filter(inviter=user).aggregate(
        joined=Count('pk'),
        rewarded=Count('pk', filter=Q(rewarded_at__isnull=False)),
    )
    joined, rewarded = counts['joined'], counts['rewarded']
    earned = (
        list(ReferralReward.objects.filter(user=user).order_by('-level'))
        if rewarded else []
    )
    next_step = None
    for _level, needed, name in FRIEND_MAKER_LEVELS:
        if rewarded < needed:
            next_step = {
                'name': name, 'needed': needed, 'left': needed - rewarded,
            }
            break
    return {
        'joined': joined,
        'rewarded': rewarded,
        'waiting': joined - rewarded,
        'current': earned[0] if earned else None,
        'levels': earned,
        'next': next_step,
    }


def invite_message():
    return (
        f'Join me on {settings.SITE_NAME}. {settings.SITE_TAGLINE} '
        'Sign up with my link:'
    )
