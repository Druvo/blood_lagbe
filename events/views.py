from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.utils.translation import gettext

from core import ratelimit
from events import awards, challenges as challenge_rules
from events.forms import EventForm
from events.ics import build_calendar
from events.models import (
    Campaign,
    CampaignBadgeAward,
    Challenge,
    Event,
    EventRSVP,
    EventTeam,
)
from events.services import (
    EventError,
    cancel_rsvp,
    creation_blocked,
    enter_squad,
    rsvp_going,
)
from rewards.geo import DISTRICT_CHOICES, DISTRICT_NAMES
from squads.models import Squad, SquadMember

PAGE_SIZE = 12
RSVP_LIMIT = 60  # RSVP actions per 15 minutes per account


def _district_choices():
    # DISTRICT_CHOICES is already grouped by division for <optgroup>.
    return DISTRICT_CHOICES


def _event_or_404(pk):
    return get_object_or_404(
        Event.objects.with_stats().select_related('organizer'), pk=pk,
    )


def _viewable(event, user):
    """Public events for everyone; anything else only for its organizer and
    staff. Others get a 404 so a pending drive stays unlisted."""
    if event.is_public:
        return True
    if not user.is_authenticated:
        return False
    return event.organizer_id == user.pk or user.is_staff


def _visible_event_or_404(pk, user):
    event = _event_or_404(pk)
    if not _viewable(event, user):
        raise Http404('No such event.')
    return event


def _organizer_event_or_404(request, pk):
    """The event, for its organizer only. Strangers get a 403 for a public
    event and a 404 for one they cannot see."""
    event = _event_or_404(pk)
    if event.organizer_id == request.user.pk:
        return event
    if not _viewable(event, request.user):
        raise Http404('No such event.')
    raise PermissionDenied


def _paginate(request, qs):
    return Paginator(qs, PAGE_SIZE).get_page(request.GET.get('page'))


def _nav_context(active):
    return {'active_tab': active}


# -- lists -------------------------------------------------------------

def event_list(request):
    district = request.GET.get('district', '')
    if district not in DISTRICT_NAMES:
        district = ''
    events = Event.objects.with_stats().upcoming()
    if district:
        events = events.filter(district=district)
    page = _paginate(request, events)
    ctx = {
        'page': page, 'events': page.object_list, 'district': district,
        'district_choices': _district_choices(),
    }
    ctx.update(_nav_context('upcoming'))
    return render(request, 'events/list.html', ctx)


def event_past(request):
    district = request.GET.get('district', '')
    if district not in DISTRICT_NAMES:
        district = ''
    events = Event.objects.with_stats().completed().order_by('-starts_at')
    if district:
        events = events.filter(district=district)
    page = _paginate(request, events)
    ctx = {
        'page': page, 'events': page.object_list, 'district': district,
        'district_choices': _district_choices(),
    }
    ctx.update(_nav_context('past'))
    return render(request, 'events/list.html', ctx)


@login_required
def my_events(request):
    organized = list(
        Event.objects.with_stats().filter(organizer=request.user)
        .order_by('-starts_at')
    )
    going = list(
        Event.objects.with_stats().filter(rsvps__user=request.user)
        .order_by('-starts_at')
    )
    ctx = {'organized': organized, 'going': going}
    ctx.update(_nav_context('mine'))
    return render(request, 'events/mine.html', ctx)


# -- detail and organizer actions ---------------------------------------

def event_detail(request, pk):
    event = _visible_event_or_404(pk, request.user)
    user = request.user
    is_organizer = user.is_authenticated and event.organizer_id == user.pk
    my_rsvp = None
    my_squads = []
    if user.is_authenticated:
        my_rsvp = EventRSVP.objects.filter(event=event, user=user).first()
        entered = EventTeam.objects.filter(event=event).values('squad_id')
        my_squads = list(
            Squad.objects.filter(owner=user).exclude(pk__in=entered)
            .order_by('name')
        )

    member_rsvps = EventRSVP.objects.filter(
        event=OuterRef('event'),
        user__squad_memberships__squad=OuterRef('squad'),
    ).order_by().values('event').annotate(c=Count('pk')).values('c')
    teams = list(
        event.teams.select_related('squad')
        .annotate(going=Coalesce(
            Subquery(member_rsvps, output_field=IntegerField()), 0,
        ))
        .order_by('-going', 'created_at')
    )

    attendees = []
    if is_organizer or user.is_staff:
        attendees = list(
            event.rsvps.select_related('user').order_by('created_at', 'pk')
        )
    now_going = event.going_count
    ctx = {
        'event': event,
        'is_organizer': is_organizer,
        'my_rsvp': my_rsvp,
        'my_squads': my_squads,
        'teams': teams,
        'attendees': attendees,
        'checked_in_count': sum(1 for a in attendees if a.checked_in),
        'spots_left': event.spots_left(now_going),
        'is_full': event.capacity is not None and now_going >= event.capacity,
        'can_edit': is_organizer and not event.is_past and event.status in (
            Event.Status.PENDING, Event.Status.APPROVED),
        'can_check_in': (
            is_organizer and event.has_started
            and event.status in (Event.Status.APPROVED, Event.Status.COMPLETED)
        ),
        'rsvp_open': event.accepts_rsvps,
        'default_share': _default_share(user),
    }
    return render(request, 'events/detail.html', ctx)


def _default_share(user):
    """Name sharing starts on only for people who already allow public
    naming; everyone else opts in per event."""
    if not user.is_authenticated:
        return False
    try:
        return bool(user.profile.show_on_leaderboard)
    except Exception:
        return False


@login_required
def event_create(request):
    blocked = creation_blocked(request.user)
    if blocked and request.method != 'POST':
        messages.info(request, blocked)
    if request.method == 'POST':
        form = EventForm(request.POST)
        if blocked:
            messages.error(request, blocked)
        elif form.is_valid():
            event = form.save(commit=False)
            event.organizer = request.user
            event.status = Event.Status.PENDING
            event.save()
            messages.success(
                request,
                gettext('Thanks. Your drive is waiting for approval and will '
                'appear in the list once a volunteer has checked it.'),
            )
            return redirect('events:detail', pk=event.pk)
    else:
        form = EventForm()
    return render(request, 'events/form.html', {
        'form': form, 'event': None, 'blocked': blocked,
    })


@login_required
def event_edit(request, pk):
    event = _organizer_event_or_404(request, pk)
    if event.status not in (Event.Status.PENDING, Event.Status.APPROVED):
        messages.error(request, gettext('This drive can no longer be edited.'))
        return redirect('events:detail', pk=pk)
    if event.is_past:
        messages.error(request, gettext('A drive that has finished cannot be edited.'))
        return redirect('events:detail', pk=pk)
    instance = Event.objects.get(pk=pk)
    if request.method == 'POST':
        form = EventForm(request.POST, instance=instance)
        if form.is_valid():
            review = (
                instance.status == Event.Status.APPROVED
                and form.needs_review() and not request.user.is_staff
            )
            event = form.save(commit=False)
            if review:
                event.status = Event.Status.PENDING
            event.save()
            if review:
                messages.info(
                    request,
                    gettext('Saved. Because the text changed, a volunteer will '
                    'look at it again before it shows in the list.'),
                )
            else:
                messages.success(request, gettext('Saved.'))
            return redirect('events:detail', pk=pk)
    else:
        form = EventForm(instance=instance)
    return render(request, 'events/form.html', {
        'form': form, 'event': event, 'blocked': '',
    })


@login_required
@require_POST
def event_cancel(request, pk):
    event = _organizer_event_or_404(request, pk)
    if event.status == Event.Status.CANCELLED:
        return redirect('events:detail', pk=pk)
    if event.status == Event.Status.COMPLETED or event.is_past:
        messages.error(request, gettext('A drive that has finished cannot be cancelled.'))
        return redirect('events:detail', pk=pk)
    event.status = Event.Status.CANCELLED
    event.save(update_fields=['status', 'updated_at'])
    messages.success(request, gettext('The drive is cancelled.'))
    return redirect('events:detail', pk=pk)


# -- RSVP, attendance, squads ---------------------------------------------

def _rsvp_limited(request):
    if ratelimit.is_limited('event-rsvp', request.user.pk, RSVP_LIMIT):
        messages.error(request, gettext('Too many tries. Please wait a few minutes.'))
        return True
    ratelimit.hit('event-rsvp', request.user.pk)
    return False


@login_required
@require_POST
def event_rsvp(request, pk):
    event = _visible_event_or_404(pk, request.user)
    if _rsvp_limited(request):
        return redirect('events:detail', pk=pk)
    share = request.POST.get('share_name') == 'on'
    try:
        _rsvp, created = rsvp_going(event.pk, request.user, share)
    except EventError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "You're going. Thank you!" if created else "You're already going.",
        )
    return redirect('events:detail', pk=pk)


@login_required
@require_POST
def event_rsvp_cancel(request, pk):
    event = _visible_event_or_404(pk, request.user)
    if _rsvp_limited(request):
        return redirect('events:detail', pk=pk)
    try:
        removed = cancel_rsvp(event, request.user)
    except EventError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request, 'Your RSVP is cancelled.' if removed
            else 'You had no RSVP for this drive.',
        )
    return redirect('events:detail', pk=pk)


@login_required
@require_POST
def event_checkin(request, pk, rsvp_pk):
    event = _organizer_event_or_404(request, pk)
    if not (
        event.has_started
        and event.status in (Event.Status.APPROVED, Event.Status.COMPLETED)
    ):
        messages.error(
            request, gettext('Attendance opens when the drive starts.'),
        )
        return redirect('events:detail', pk=pk)
    rsvp = get_object_or_404(EventRSVP, pk=rsvp_pk, event=event)
    arrived = request.POST.get('action') != 'out'
    rsvp.checked_in = arrived
    rsvp.checked_in_at = timezone.now() if arrived else None
    if not arrived:
        rsvp.verified = False
        rsvp.verified_by = None
    rsvp.save()
    return redirect(reverse('events:detail', args=[pk]) + '#attendance')


@login_required
@require_POST
def event_enter_squad(request, pk):
    event = _visible_event_or_404(pk, request.user)
    try:
        _team, created = enter_squad(
            event, request.user, request.POST.get('squad') or 0,
        )
    except (EventError, ValueError) as exc:
        messages.error(
            request, str(exc) if isinstance(exc, EventError)
            else 'Pick one of your squads.',
        )
    else:
        messages.success(
            request, 'Your squad is entered.' if created
            else 'Your squad is already entered.',
        )
    return redirect('events:detail', pk=pk)


@login_required
@require_POST
def event_withdraw_squad(request, pk, squad_pk):
    event = _visible_event_or_404(pk, request.user)
    team = EventTeam.objects.filter(
        event=event, squad_id=squad_pk, squad__owner=request.user,
    )
    if not team.exists():
        raise Http404('No such entry.')
    team.delete()
    messages.success(request, gettext('Your squad is withdrawn.'))
    return redirect('events:detail', pk=pk)


# -- calendar --------------------------------------------------------------

@require_GET
def event_calendar(request, pk):
    event = _visible_event_or_404(pk, request.user)
    url = request.build_absolute_uri(reverse('events:detail', args=[pk]))
    body = build_calendar(
        event, url, request.get_host().split(':')[0],
        settings.SITE_NAME, timezone.now(),
    )
    response = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="event-{pk}.ics"'
    return response


# -- campaigns ---------------------------------------------------------------

def campaign_list(request):
    mine = {}
    user = request.user
    if user.is_authenticated:
        awards.sync_user(user)
        mine = {
            a.campaign_id: a for a in
            CampaignBadgeAward.objects.filter(user=user)
            .select_related('campaign')
        }
    today = timezone.localdate()
    campaigns = Campaign.objects.filter(is_active=True)
    shown = []
    for c in campaigns:
        shown.append({
            'campaign': c,
            'award': mine.get(c.pk),
            'window': c.current_window(today),
            'live': c.contains(today),
        })
    # Campaigns that were switched off later still show if you earned them.
    listed = {row['campaign'].pk for row in shown}
    for cid, award in mine.items():
        if cid not in listed:
            shown.append({
                'campaign': award.campaign, 'award': award,
                'window': None, 'live': False,
            })
    shown.sort(key=lambda r: (not r['live'], r['award'] is None, r['campaign'].name))
    return render(request, 'events/campaigns.html', {
        'rows': shown,
        'earned': [r for r in shown if r['award']],
        **_nav_context('campaigns'),
    })


# -- challenges ----------------------------------------------------------------

def challenge_list(request):
    today = timezone.localdate()
    items = list(Challenge.objects.filter(is_published=True))
    current = [c for c in items if c.state(today) != 'finished']
    finished = [c for c in items if c.state(today) == 'finished']
    current.sort(key=lambda c: c.starts_on)
    return render(request, 'events/challenges.html', {
        'current': current, 'finished': finished, 'today': today,
        **_nav_context('challenges'),
    })


def challenge_detail(request, pk):
    challenge = get_object_or_404(Challenge, pk=pk, is_published=True)
    rows = challenge_rules.standings(challenge)
    mine = []
    user = request.user
    if user.is_authenticated:
        my_ids = list(
            SquadMember.objects.filter(user=user).values_list('squad_id', flat=True)
        )
        if my_ids:
            mine = challenge_rules.standings(challenge, squad_ids=my_ids)
    return render(request, 'events/challenge_detail.html', {
        'challenge': challenge,
        'rows': rows,
        'head': challenge_rules.headline(challenge, rows),
        'top': challenge_rules.top_donors(challenge),
        'mine': mine,
        'state': challenge.state(),
        **_nav_context('challenges'),
    })
