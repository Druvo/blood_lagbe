"""Urgent blood requests, patient side: the board, posting, the detail page
with its timeline, pledging, updates, reports and the owner's own list."""
import datetime
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from rewards.blood import BLOOD_GROUP_CHOICES, donor_groups_for
from rewards.forms import BloodRequestForm
from rewards.geo import DISTRICT_CHOICES, DISTRICT_NAMES
from rewards.leaderboard import display_name as _display_name
from rewards.models import (
    MAX_OPEN_REQUESTS_PER_USER,
    MAX_PLEDGES_PER_DAY,
    MAX_REPORTS_PER_DAY,
    BloodRequest,
    RequestReport,
    RequestResponse,
    RequestUpdate,
    get_eligibility,
)
from rewards.request_notify import close_and_notify, notify_pledge

User = get_user_model()

PAGE_SIZE = 12
MAX_UPDATES_PER_REQUEST = 10
PLEDGE_MILESTONES = (1, 3, 5, 10, 20)
WHEN_CHOICES = (('today', 'Today'), ('week', 'This week'), ('', 'All'))


def _day_ago():
    return timezone.now() - datetime.timedelta(days=1)


def _is_staff(user):
    return user.is_authenticated and user.is_staff


def _qs(params, **overrides):
    """Query string from the active filters, with some values replaced."""
    merged = {k: v for k, v in params.items() if v}
    merged.update(overrides)
    merged = {k: v for k, v in merged.items() if v}
    return '?' + urlencode(merged) if merged else '?'


def _safe_next(request, fallback):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return fallback


# ---------------------------------------------------------------- the board

def request_list(request):
    """Public board of requests that still need donors, most urgent first."""
    group = request.GET.get('blood_group', '')
    if group not in dict(BLOOD_GROUP_CHOICES):
        group = ''
    district = request.GET.get('district', '')
    if district not in DISTRICT_NAMES:
        district = ''
    when = request.GET.get('when', '')
    if when not in ('today', 'week'):
        when = ''

    today = timezone.localdate()
    qs = (
        BloodRequest.objects.still_needed().with_pledge_count()
        .select_related('requester')
    )
    if _is_staff(request.user):
        qs = qs.with_report_count()
    else:
        qs = qs.public()
    if group:
        qs = qs.filter(blood_group=group)
    if district:
        qs = qs.filter(district=district)
    if when == 'today':
        qs = qs.filter(needed_by=today)
    elif when == 'week':
        qs = qs.filter(needed_by__lte=today + datetime.timedelta(days=7))

    page = Paginator(qs.order_by('needed_by', '-created_at', '-pk'), PAGE_SIZE)
    page = page.get_page(request.GET.get('page'))

    filters = {'blood_group': group, 'district': district, 'when': when}
    return render(request, 'rewards/request_list.html', {
        'blood_requests': page,
        'page': page,
        'total': page.paginator.count,
        'blood_groups': [
            {
                'code': code,
                'active': code == group,
                'url': _qs(filters, blood_group=None if code == group else code),
            }
            for code, _ in BLOOD_GROUP_CHOICES
        ],
        'any_group_url': _qs(filters, blood_group=''),
        'when_options': [
            {'value': v, 'label': label, 'active': v == when,
             'url': _qs(filters, when=v)}
            for v, label in WHEN_CHOICES
        ],
        'district_choices': DISTRICT_CHOICES,
        'selected': group,
        'district': district,
        'when': when,
        'filtered': bool(group or district or when),
        'page_query': _qs(filters)[1:],
        'eligibility': (
            get_eligibility(request.user)
            if request.user.is_authenticated else None
        ),
    })


# ---------------------------------------------------------- donors who can help

def donors_who_can_help(blood_request):
    """Counts only (never names or phones) of directory donors whose group is
    compatible with the patient's, and how many of them live in the same
    district. Compatibility is a search aid; the hospital decides."""
    groups = donor_groups_for(blood_request.blood_group, True)
    counts = User.objects.filter(
        is_active=True, profile__in_directory=True,
        profile__blood_group__in=groups,
    ).exclude(pk=blood_request.requester_id).aggregate(
        total=Count('pk'),
        nearby=Count('pk', filter=Q(profile__district=blood_request.district)),
    )
    query = urlencode({
        'group': blood_request.blood_group,
        'district': blood_request.district,
        'compatible': 1,
    })
    return {
        'total': counts['total'],
        'nearby': counts['nearby'],
        'groups': groups,
        'url': f"{reverse('donor-directory')}?{query}",
    }


# ------------------------------------------------------------------ timeline

def build_timeline(blood_request, pledge_times=None):
    """Public history of a request, newest first. Counts and dates only: no
    donor names and no phone numbers."""
    events = []
    pledge_times = (
        pledge_times if pledge_times is not None
        else list(blood_request.responses.order_by('created_at')
                  .values_list('created_at', flat=True))
    )
    for count in PLEDGE_MILESTONES:
        if len(pledge_times) >= count:
            events.append({
                'when': pledge_times[count - 1], 'icon': 'users', 'done': True,
                'title': (
                    '1 donor offered to help' if count == 1
                    else f'{count} donors offered to help'
                ),
            })
    donations = list(
        blood_request.donations.filter(status='approved')
        .order_by('updated_at', 'pk').values_list('updated_at', flat=True)
    )
    for number, when in enumerate(donations, start=1):
        events.append({
            'when': when, 'icon': 'check-circle', 'done': True,
            'title': 'A donation was verified',
            'text': (
                f'{number} of {blood_request.units_needed} '
                f'bag{"s" if blood_request.units_needed != 1 else ""} covered'
            ),
        })
    for update in blood_request.updates.all():
        events.append({
            'when': update.created_at, 'icon': 'message-circle',
            'title': update.get_kind_display(), 'text': update.text,
            'done': update.kind == RequestUpdate.Kind.FOUND_DONOR,
        })
    if blood_request.status == BloodRequest.Status.CLOSED:
        events.append({
            'when': blood_request.closed_at, 'icon': 'lock', 'done': True,
            'title': 'Closed by the requester',
            'text': (
                blood_request.get_outcome_display()
                if blood_request.outcome else ''
            ),
            'closing': True,
        })
    events.append({
        'when': blood_request.created_at, 'icon': 'send', 'done': True,
        'title': 'Request posted', 'first': True,
    })
    far_future = timezone.now() + datetime.timedelta(days=3650)
    # Ties (the clock can repeat) resolve so that "closed" is newest and
    # "posted" is oldest.
    events.sort(
        key=lambda e: (
            e['when'] or far_future,
            2 if e.get('closing') else 0 if e.get('first') else 1,
        ),
        reverse=True,
    )
    return events


# -------------------------------------------------------------------- detail

def request_detail(request, pk):
    blood_request = get_object_or_404(
        BloodRequest.objects.select_related('requester')
        .with_received().with_report_count(),
        pk=pk,
    )
    user = request.user
    is_requester = user.is_authenticated and user == blood_request.requester
    is_owner = is_requester or _is_staff(user)
    # A flagged request stays reachable by its link, but strangers cannot act
    # on it or see the contact number until staff have looked.
    under_review = blood_request.is_flagged and not is_owner
    active = blood_request.is_active and not under_review

    pledge_times = list(
        blood_request.responses.order_by('created_at')
        .values_list('created_at', flat=True)
    )
    responses = []
    if is_owner:
        responses = [
            {
                'name': _display_name(r.donor), 'phone': r.donor.phone,
                'when': r.created_at,
            }
            for r in blood_request.responses.select_related('donor')
        ]

    has_responded = has_reported = False
    eligibility = None
    if user.is_authenticated:
        has_responded = blood_request.responses.filter(donor=user).exists()
        has_reported = blood_request.reports.filter(reporter=user).exists()
        eligibility = get_eligibility(user)

    share_url = request.build_absolute_uri(request.path)
    return render(request, 'rewards/request_detail.html', {
        'share_url': share_url,
        'share_text': (
            f'Urgent: {blood_request.blood_group} blood needed at '
            f'{blood_request.hospital}, {blood_request.district}. '
            'Can you help?'
        ),
        'share_filename': f'blood-request-{blood_request.pk}.png',
        'share_card': {
            'type': 'request',
            'bloodGroup': blood_request.blood_group,
            'hospital': blood_request.hospital,
            'district': blood_request.district,
            'remaining': blood_request.units_remaining,
            'neededBy': f'{blood_request.needed_by:%b %d}',
        },
        'blood_request': blood_request,
        'is_owner': is_owner,
        'is_requester': is_requester,
        'active': active,
        'under_review': under_review,
        'responses': responses,
        'response_count': len(pledge_times),
        'has_responded': has_responded,
        'has_reported': has_reported,
        'eligibility': eligibility,
        'timeline': build_timeline(blood_request, pledge_times),
        'help_panel': (
            donors_who_can_help(blood_request) if is_owner and active else None
        ),
        'outcome_choices': BloodRequest.Outcome.choices,
        'update_kinds': RequestUpdate.Kind.choices,
        'report_reasons': RequestReport.Reason.choices,
        'can_update': (
            is_requester and blood_request.status == BloodRequest.Status.OPEN
        ),
    })


# -------------------------------------------------------------------- create

@login_required
def request_create(request):
    today = timezone.localdate()
    open_count = BloodRequest.objects.open_and_current().filter(
        requester=request.user,
    ).count()
    if request.method == 'POST':
        form = BloodRequestForm(request.POST, user=request.user)
        if form.is_valid():
            blood_request = form.save(commit=False)
            blood_request.requester = request.user
            blood_request.save()
            messages.success(
                request, 'Your request is posted. Donors can see it now.',
            )
            return redirect('request-detail', pk=blood_request.pk)
    else:
        form = BloodRequestForm(user=request.user)

    return render(request, 'rewards/request_form.html', {
        'form': form,
        'at_limit': open_count >= MAX_OPEN_REQUESTS_PER_USER,
        'max_open': MAX_OPEN_REQUESTS_PER_USER,
        'date_chips': [
            {'label': label, 'iso': (today + datetime.timedelta(days=n)).isoformat()}
            for label, n in (('Today', 0), ('Tomorrow', 1), ('In 3 days', 3))
        ],
    })


# ------------------------------------------------------------------- pledge

@login_required
@require_POST
def request_respond(request, pk):
    blood_request = get_object_or_404(
        BloodRequest.objects.with_report_count(), pk=pk,
    )

    if blood_request.requester_id == request.user.id:
        messages.error(request, "You can't respond to your own request.")
    elif not blood_request.is_active or blood_request.is_flagged:
        messages.error(request, 'This request is no longer open.')
    else:
        eligibility = get_eligibility(request.user)
        if not eligibility.eligible:
            messages.error(
                request,
                f'You can donate again on {eligibility.next_date:%b %d, %Y}. '
                'Please let someone else respond to this request.',
            )
        elif RequestResponse.objects.filter(
            donor=request.user, created_at__gte=_day_ago(),
        ).count() >= MAX_PLEDGES_PER_DAY and not RequestResponse.objects.filter(
            blood_request=blood_request, donor=request.user,
        ).exists():
            messages.error(
                request,
                'You have offered to help a lot today. Please try again '
                'tomorrow.',
            )
        else:
            _, created = RequestResponse.objects.get_or_create(
                blood_request=blood_request, donor=request.user,
            )
            if created:
                notify_pledge(
                    blood_request, request.user,
                    request.build_absolute_uri(
                        reverse('request-detail', args=[pk]),
                    ),
                )
            messages.success(
                request,
                'Thank you! The requester can now see your name and phone.',
            )
    return redirect('request-detail', pk=pk)


# --------------------------------------------------------------------- close

@login_required
@require_POST
def request_close(request, pk):
    blood_request = get_object_or_404(BloodRequest, pk=pk)
    back = _safe_next(request, reverse('request-detail', args=[pk]))
    if not (request.user == blood_request.requester or request.user.is_staff):
        messages.error(request, "You can't close someone else's request.")
        return redirect(back)

    outcome = request.POST.get('outcome', '')
    if outcome and outcome not in BloodRequest.Outcome.values:
        messages.error(request, 'Please pick what happened.')
        return redirect(back)

    closed = close_and_notify(
        blood_request, outcome,
        request.build_absolute_uri(reverse('request-detail', args=[pk])),
    )
    if closed:
        messages.success(request, 'Request closed. Thank you for letting us know.')
    else:
        messages.info(request, 'This request was already closed.')
    return redirect(back)


# -------------------------------------------------------------------- update

@login_required
@require_POST
def request_update(request, pk):
    blood_request = get_object_or_404(BloodRequest, pk=pk)
    if request.user != blood_request.requester:
        messages.error(request, 'Only the requester can post updates.')
        return redirect('request-detail', pk=pk)
    if blood_request.status != BloodRequest.Status.OPEN:
        messages.error(request, 'This request is closed.')
        return redirect('request-detail', pk=pk)

    kind = request.POST.get('kind', RequestUpdate.Kind.NOTE)
    text = ' '.join(request.POST.get('text', '').split())
    if kind not in RequestUpdate.Kind.values:
        messages.error(request, 'Pick the kind of update.')
    elif not text:
        messages.error(request, 'Write a short update first.')
    elif len(text) > RequestUpdate._meta.get_field('text').max_length:
        messages.error(request, 'Please keep the update under 300 characters.')
    elif blood_request.updates.count() >= MAX_UPDATES_PER_REQUEST:
        messages.error(
            request,
            f'You can post up to {MAX_UPDATES_PER_REQUEST} updates on one '
            'request. Close it, or post a new request if things changed.',
        )
    else:
        RequestUpdate.objects.create(
            blood_request=blood_request, author=request.user,
            kind=kind, text=text,
        )
        messages.success(request, 'Update posted.')
    return redirect('request-detail', pk=pk)


# -------------------------------------------------------------------- report

@login_required
@require_POST
def request_report(request, pk):
    blood_request = get_object_or_404(BloodRequest, pk=pk)
    if blood_request.requester_id == request.user.id:
        messages.error(request, "You can't report your own request.")
        return redirect('request-detail', pk=pk)

    reason = request.POST.get('reason', '')
    if reason not in RequestReport.Reason.values:
        messages.error(request, 'Pick a reason for the report.')
        return redirect('request-detail', pk=pk)

    if RequestReport.objects.filter(
        reporter=request.user, created_at__gte=_day_ago(),
    ).count() >= MAX_REPORTS_PER_DAY:
        messages.error(
            request, 'You have filed a lot of reports today. Please try again tomorrow.',
        )
        return redirect('request-detail', pk=pk)

    _, created = RequestReport.objects.get_or_create(
        blood_request=blood_request, reporter=request.user,
        defaults={
            'reason': reason,
            'text': ' '.join(request.POST.get('text', '').split())[:300],
        },
    )
    if created:
        messages.success(
            request, 'Thank you. Our team will look at this request.',
        )
    else:
        messages.info(request, 'You already reported this request.')
    return redirect('request-detail', pk=pk)


# ---------------------------------------------------------------- my requests

@login_required
def request_mine(request):
    """The signed-in user's own requests, open ones first."""
    mine = list(
        BloodRequest.objects.filter(requester=request.user)
        .with_received().with_pledge_count().with_report_count()
        .order_by('-created_at', '-pk')[:50]
    )
    mine.sort(key=lambda r: (not r.is_active, 0))  # stable: open first
    return render(request, 'rewards/request_mine.html', {
        'my_requests': mine,
        'active_count': sum(1 for r in mine if r.is_active),
        'at_limit': sum(
            1 for r in mine
            if r.status == BloodRequest.Status.OPEN and not r.is_expired
        ) >= MAX_OPEN_REQUESTS_PER_USER,
        'max_open': MAX_OPEN_REQUESTS_PER_USER,
        'outcome_choices': BloodRequest.Outcome.choices,
    })
