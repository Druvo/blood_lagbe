import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from growth import services, share
from growth.models import FRIEND_MAKER_LEVELS
from rewards.leaderboard import display_name, initials
from rewards.models import (
    get_current_streak,
    get_donor_badge_progress,
    get_donor_points,
)
from rewards.quests import get_level
from squads.models import SquadMember
from stories.models import Story

User = get_user_model()
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ invites

@never_cache
def invite_landing(request, code):
    """Public page behind an invite link. Remembers the code in the session
    so the sign-up that follows can link the new member to their inviter."""
    invite = services.find_invite(code)
    if invite is None:
        raise Http404('No such invite')
    signed_in = request.user.is_authenticated
    if not signed_in:
        request.session[services.SESSION_KEY] = invite.code
    return render(request, 'growth/invite_landing.html', {
        'invite': invite,
        'inviter': services.inviter_label(invite),
        'signed_in': signed_in,
        'is_owner': signed_in and request.user.pk == invite.user_id,
    })


@login_required
def invite_home(request):
    """My invite link, share buttons, and how my invites are going."""
    invite = services.get_or_create_invite(request.user)
    return render(request, 'growth/invite_home.html', {
        'invite': invite,
        'share': share.invite_share(request, invite),
        'stats': services.referral_stats(request.user),
        'levels': FRIEND_MAKER_LEVELS,
    })


# ----------------------------------------------------------- public profile

def _titles(user):
    """Titles from the habits lane, if that module exists (it is written by
    another lane, so it is optional here)."""
    try:
        from habits.titles import titles_for
    except ImportError:
        return []
    try:
        raw = titles_for(user) or []
    except Exception:  # a broken title must not break the profile
        logger.exception('growth: titles_for failed')
        return []
    labels = []
    for item in raw:
        if isinstance(item, str):
            label = item
        elif isinstance(item, dict):
            label = item.get('name') or item.get('title') or item.get('label')
        else:
            label = (
                getattr(item, 'name', None) or getattr(item, 'title', None)
                or getattr(item, 'label', None)
            )
        if label:
            labels.append(str(label))
    return labels


@never_cache
def public_profile(request, user_id):
    """A member's opt-in public page. Anyone not allowed to see it gets a
    plain 404, the same as for a member who does not exist, so the page
    never reveals who has an account."""
    try:
        member = User.objects.select_related('profile').get(
            pk=user_id, is_active=True,
        )
    except User.DoesNotExist:
        raise Http404('No such profile')
    profile = member.profile
    # Someone hidden from public listings is never named, so a public
    # profile needs both switches.
    is_public = profile.public_profile and profile.show_on_leaderboard
    is_owner = request.user.is_authenticated and request.user.pk == member.pk
    is_staff = request.user.is_authenticated and request.user.is_staff
    if not (is_public or is_owner or is_staff):
        raise Http404('No such profile')

    name = display_name(member)
    tier, _next, donations, _percent = get_donor_badge_progress(member)
    points = get_donor_points(member)
    squads = [
        m.squad for m in SquadMember.objects.filter(
            user=member, squad__is_public=True,
        ).select_related('squad').order_by('squad__name')
    ]
    # Anonymous stories never carry the author's name, so they are left out.
    stories = list(
        Story.objects.approved().filter(author=member, anonymous=False)
        .order_by('-approved_at', '-id')[:5]
    )
    return render(request, 'growth/profile.html', {
        'member': member,
        'name': name,
        'initials': initials(name),
        'image': profile.image if profile.image else None,
        'tier': tier,
        'donations': donations,
        'level': get_level(points).level,
        'streak': get_current_streak(member),
        'squads': squads,
        'stories': stories,
        'titles': _titles(member),
        # District only for people who chose to be findable in the directory.
        'district': profile.district if profile.in_directory else '',
        'is_public': is_public,
        'is_owner': is_owner,
        'og_description': (
            f'{donations} verified donation{"s" if donations != 1 else ""}'
            + (f', {tier.name} tier' if tier else '')
            + '.'
        ),
        'profile_url': request.build_absolute_uri(request.path),
    })
