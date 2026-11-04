import csv
from urllib.parse import urlencode

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from insights import charts, stats
from rewards.geo import DISTRICT_NAMES, DISTRICT_TO_DIVISION, DIVISIONS
from rewards.models import LIVES_PER_DONATION

FEED_MAX_AGE = stats.CACHE_TTL
_DISTRICTS_LOWER = {name.lower(): name for name in DISTRICT_NAMES}


def _call_to_action_links(cta):
    group = cta['group']
    donor_params = {'group': group, 'available': '1'}
    if cta.get('top_district'):
        donor_params['district'] = cta['top_district']
    return {
        'donors_url': f'{reverse("donor-directory")}?{urlencode(donor_params)}',
        'requests_url': (
            f'{reverse("request-list")}?{urlencode({"blood_group": group})}'
        ),
    }


@require_GET
def impact(request):
    period = stats.normalise_period(request.GET.get('period'))
    data = stats.dashboard(period)
    geo = data['geo']

    district_bars = charts.hbar_rows(
        [
            dict(d, url=reverse('impact-district', args=[d['name']]))
            for d in geo['districts']
        ],
        empty='No district recorded for these donations yet.',
    )
    group_donut = charts.donut(
        [{'label': g['group'], 'count': g['count']} for g in data['groups']],
        center_label='donations',
    )
    funnel_bars = charts.hbar_rows(
        [
            {
                'name': f['label'], 'count': f['value'],
                'display': f'{charts.fmt(f["value"])} ({f["percent"]}%)',
            }
            for f in data['funnel']
        ],
        empty='No requests posted in this period.',
    )
    place_bars = charts.hbar_rows(
        [
            {'name': p['name'], 'count': p['count']}
            for p in data['places']
        ],
        empty='Hospitals will appear here as donations are verified.',
    )
    cta = dict(data['cta'], **_call_to_action_links(data['cta']))

    return render(request, 'insights/impact.html', {
        **data,
        'periods': stats.PERIODS,
        'trend_chart': charts.monthly_chart(data['monthly']),
        'group_donut': group_donut,
        'division_map': charts.division_map(geo['divisions']),
        'district_bars': district_bars,
        'supply_chart': charts.supply_demand_rows(data['supply_demand']),
        'funnel_bars': funnel_bars,
        'place_bars': place_bars,
        'cta': cta,
        'lives_per_donation': LIVES_PER_DONATION,
        'has_data': data['totals']['donations'] > 0,
    })


@require_GET
def district_detail(request, name):
    canonical = _DISTRICTS_LOWER.get(name.strip().lower())
    if canonical is None:
        raise Http404('Unknown district')
    detail = stats.district_detail(canonical)
    division = DISTRICT_TO_DIVISION[canonical]
    groups = stats.blood_groups(district=canonical)
    return render(request, 'insights/district.html', {
        'd': detail,
        'trend_chart': charts.monthly_chart(
            stats.monthly_series(district=canonical),
            empty=f'No verified donations in {canonical} yet.',
        ),
        'group_donut': charts.donut(
            [{'label': g['group'], 'count': g['count']} for g in groups],
            empty=f'No verified donations in {canonical} yet.',
        ),
        'neighbours': [
            n for n in DIVISIONS[division] if n != canonical
        ],
        'donors_url': (
            f'{reverse("donor-directory")}?'
            f'{urlencode({"district": canonical, "available": "1"})}'
        ),
        'lives_per_donation': LIVES_PER_DONATION,
    })


@require_GET
@cache_control(public=True, max_age=FEED_MAX_AGE)
def data_json(request):
    period = stats.normalise_period(request.GET.get('period'))
    payload = stats.public_payload(period)
    payload['generated_at'] = timezone.now().replace(microsecond=0).isoformat()
    response = JsonResponse(payload, json_dumps_params={'indent': 2})
    response['Access-Control-Allow-Origin'] = '*'
    return response


@require_GET
@cache_control(public=True, max_age=FEED_MAX_AGE)
def export_csv(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        'attachment; filename="verified-donations-by-month.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(['month', 'verified_donations'])
    for item in stats.monthly_series():
        writer.writerow([item['key'], item['count']])
    return response
