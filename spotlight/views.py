import datetime
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from rewards.leaderboard import display_name
from rewards.models import Donation, get_eligibility
from spotlight import heroes, ics, pulse as pulse_feed, styles

User = get_user_model()


# ------------------------------------------------------------------ helpers

def month_label(year, month):
    return date_format(datetime.date(year, month, 1), 'F Y')


def certificate_number(donation):
    """Site-name prefix plus the donation id, such as BB-000123."""
    name = settings.SITE_NAME
    letters = re.findall(r'[A-Z]', name)
    prefix = ''.join(letters[:3]) if len(letters) >= 2 else (
        re.sub(r'[^A-Za-z0-9]', '', name)[:3].upper())
    return f'{prefix or "ID"}-{donation.pk:06d}'


def _decorate(entries):
    for entry in entries:
        entry['title'] = heroes.KIND_INFO[entry['kind']]['title']
        entry['month_label'] = month_label(entry['year'], entry['month'])
    return entries


def _month_nav(year, month):
    today = timezone.localdate()
    prev_y, prev_m = heroes.previous_month(year, month)
    next_y, next_m = heroes.next_month(year, month)
    return {
        'prev': (reverse('spotlight:month', args=[prev_y, prev_m])
                 if heroes.valid_month(prev_y, prev_m, today) else None),
        'prev_label': month_label(prev_y, prev_m),
        'next': (reverse('spotlight:month', args=[next_y, next_m])
                 if heroes.valid_month(next_y, next_m, today) else None),
        'next_label': month_label(next_y, next_m),
    }


# ----------------------------------------------------------------- heroes

@require_GET
def home(request):
    today = timezone.localdate()
    last_y, last_m = heroes.previous_month(today.year, today.month)
    return render(request, 'spotlight/home.html', {
        'now_label': month_label(today.year, today.month),
        'last_label': month_label(last_y, last_m),
        'this_month': _decorate(heroes.heroes_for_month(today.year, today.month)),
        'last_month': _decorate(heroes.heroes_for_month(last_y, last_m))
        if heroes.valid_month(last_y, last_m, today) else [],
        'hall': _decorate(heroes.hall_of_fame()),
        'last_url': reverse('spotlight:month', args=[last_y, last_m]),
        'kinds': [(k, heroes.KIND_INFO[k]) for k in heroes.KINDS],
        'tie_rule': heroes.TIE_RULE,
    })


@require_GET
def month(request, year, month):
    today = timezone.localdate()
    if not heroes.valid_month(year, month, today):
        raise Http404('No such month.')
    final = heroes.month_is_final(year, month, today)
    return render(request, 'spotlight/month.html', {
        'year': year, 'month_number': month,
        'label': month_label(year, month),
        'final': final,
        'entries': _decorate(heroes.heroes_for_month(year, month)),
        'nav': _month_nav(year, month),
        'kinds': [(k, heroes.KIND_INFO[k]) for k in heroes.KINDS],
        'tie_rule': heroes.TIE_RULE,
    })


# ------------------------------------------------------------------ pulse

@require_GET
@cache_control(private=True, max_age=pulse_feed.CACHE_SECONDS)
def pulse_json(request):
    events = pulse_feed.pulse()
    return JsonResponse({
        'events': [
            {'kind': e['kind'], 'icon': e['icon'], 'text': e['text'],
             'when': e['when']} for e in events
        ],
        'count': len(events),
        'show': pulse_feed.show_panel(events),
        'refresh': pulse_feed.CACHE_SECONDS,
    })


# ------------------------------------------------------------------ style

@login_required
@require_GET
def my_style(request):
    style = styles.style_for(request.user)
    profile = request.user.profile
    return render(request, 'spotlight/style.html', {
        'style': style,
        'all_styles': styles.all_styles(style),
        'card_is_public': profile.public_badge_card and profile.show_on_leaderboard,
        'next_label': style.next_label if style else styles.NO_STYLE['next'],
        'next_url': style.next_url if style else reverse(styles.NO_STYLE['url']),
    })


@require_GET
def style_card(request, user_id):
    """Public only when the owner allows badge-card sharing and is listed on
    the leaderboard; otherwise only the owner and staff. 404 for anyone
    else, so the card's existence is not revealed."""
    owner = get_object_or_404(User.objects.select_related('profile'), pk=user_id)
    viewer = request.user
    public = owner.profile.public_badge_card and owner.profile.show_on_leaderboard
    allowed = public or (
        viewer.is_authenticated and (viewer.pk == owner.pk or viewer.is_staff)
    )
    if not allowed:
        raise Http404('This style card is private.')
    style = styles.style_for(owner)
    if style is None:
        raise Http404('No style yet.')
    name = display_name(owner)
    return render(request, 'spotlight/style_card.html', {
        'owner': owner, 'name': name, 'style': style,
        'is_public': public,
        'share_url': request.build_absolute_uri(request.path),
    })


# ------------------------------------------------------------ certificate

@login_required
@require_GET
def certificate(request, donation_id):
    donation = get_object_or_404(
        Donation.objects.select_related('donor'),
        pk=donation_id, status=Donation.Status.APPROVED,
    )
    user = request.user
    if donation.donor_id != user.pk and not user.is_staff:
        raise Http404('No such certificate.')
    # The member's own name if they set one, never a phone or email.
    name = ' '.join(filter(None, [
        (donation.donor.first_name or '').strip(),
        (donation.donor.last_name or '').strip(),
    ])) or gettext('Donor')
    return render(request, 'spotlight/certificate.html', {
        'donation': donation, 'name': name,
        'number': certificate_number(donation),
        'place': donation.location,
    })


# --------------------------------------------------------------- calendar

@login_required
@require_GET
def ready_ics(request):
    eligibility = get_eligibility(request.user)
    if eligibility.last_date is None:
        return render(request, 'spotlight/ready_none.html', status=404)
    # Already past the interval: the reminder lands on today.
    day = max(eligibility.next_date, timezone.localdate())
    host = request.get_host().split(':')[0] or 'localhost'
    description = gettext(
        'A reminder based on the usual gap of %(interval)s between '
        'donations. Blood banks may ask for a different gap. This is not '
        'medical advice.'
    ) % {'interval': _interval_label()}
    body = ics.all_day_calendar(
        uid=f'ready-{request.user.pk}-{day:%Y%m%d}@{host}',
        day=day,
        summary=gettext(
            'Ready to donate again (a reminder, not medical clearance)'),
        description=description,
        site_name=settings.SITE_NAME,
        now=timezone.now(),
    )
    response = HttpResponse(body, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="ready-again.ics"'
    response['Cache-Control'] = 'private, no-store'
    return response


def _interval_label():
    from rewards.context_processors import donation_interval
    return donation_interval(None)['donation_interval_label']
