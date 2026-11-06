import csv

from django.apps import apps
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils.formats import date_format
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from atlas import data, geo, layers, render as svg
from rewards.blood import BLOOD_GROUP_CHOICES
from rewards.geo import DISTRICT_CHOICES
from rewards.models import BloodRequest

MAX_URGENT = 8
MAX_DRIVES = 5
MAX_SQUADS = 6


def clean_district(raw):
    """A district name from a query string (name or slug, any case), or ''."""
    raw = (raw or '').strip()
    for name in geo.DISTRICT_NAMES:
        if raw.lower() in (name.lower(), geo.SLUGS[name]):
            return name
    return ''


# -------------------------------------------------------------------- map

@require_GET
def map_view(request):
    layer = layers.normalise(request.GET.get('layer'))
    built = layers.build(layer)
    switch = [
        {
            'label': label,
            'url': f'{reverse("atlas:map")}?layer={key}',
            'active': key == layer,
        }
        for key, label in layers.LAYER_CHOICES
    ]
    return render(request, 'atlas/map.html', {
        'layer': layer,
        'built': built,
        'switch': switch,
        'map_svg': svg.tile_map(built),
        'legend_label': layers.legend_label(layer),
        'legend_note': layers.legend_note(layer),
        'divisions': [
            {'name': name, 'index': geo.DIVISION_INDEX[name]}
            for name in geo.DIVISION_NAMES
        ],
        'levels': range(layers.LEVELS + 1),
        'rows': built['rows'],
    })


# -------------------------------------------------------------------- gap

def _group_rows(gap):
    rows = []
    for row in gap['rows']:
        rows.append(dict(row, supply_text=row['supply']['text']))
    return rows


@require_GET
def gap_view(request):
    district = clean_district(request.GET.get('district'))
    gap = data.gap(district or None)
    rows = _group_rows(gap)
    by_group = {r['group']: r for r in rows}
    top = [by_group[g] for g in gap['top']]

    invite = None
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if (
            profile and profile.blood_group in gap['short']
            and not profile.in_directory
        ):
            invite = profile.blood_group

    return render(request, 'atlas/gap.html', {
        'district': district,
        'district_choices': DISTRICT_CHOICES,
        'gap': gap,
        'rows': rows,
        'top': top,
        'chart': svg.gap_chart(gap['rows']),
        'invite_group': invite,
        'has_demand': gap['units'] > 0,
        'blood_groups': [code for code, _l in BLOOD_GROUP_CHOICES],
    })


# --------------------------------------------------------------- district

def _drives(district):
    """Upcoming approved drives in the district (events app, if installed)."""
    if not apps.is_installed('events'):
        return []
    try:
        from events.models import Event
        rows = (
            Event.objects.upcoming().filter(district=district)
            .order_by('starts_at', 'pk')
            .values('pk', 'title', 'starts_at', 'venue')[:MAX_DRIVES]
        )
        out = []
        for row in rows:
            out.append(dict(row, url=reverse('events:detail', args=[row['pk']])))
        return out
    except (ImportError, NoReverseMatch):
        return []


def _squads(district):
    """Public squads based in the district. Private squads never appear."""
    if not apps.is_installed('squads'):
        return []
    from squads.models import Squad
    out = []
    for squad in Squad.objects.filter(
        is_public=True, district=district,
    ).order_by('name')[:MAX_SQUADS]:
        try:
            url = reverse('squads:detail', args=[squad.pk])
        except NoReverseMatch:
            continue
        out.append({'name': squad.name, 'url': url})
    return out


def urgent_requests(district):
    return list(
        BloodRequest.objects.still_needed().public()
        .filter(district=district)
        .only('blood_group', 'units_needed', 'needed_by')
        .order_by('needed_by', '-created_at')[:MAX_URGENT]
    )


def count_other_open(blood_request):
    """Other public, open requests in the same district. A live count (one
    query) so a request that was just closed never inflates the line."""
    return (
        BloodRequest.objects.still_needed().public()
        .filter(district=blood_request.district)
        .exclude(pk=blood_request.pk).count()
    )


def district_context(district):
    need = data.demand_for(district)
    available = data.district_available(district)
    counts = data.district_available_by_group(district)
    months = data.donations()['months']
    series = data.donations()['by_district'].get(district, [0] * 12)
    gap = data.gap(district)
    drives = _drives(district)
    return {
        'name': district,
        'slug': geo.SLUGS[district],
        'division': geo.DISTRICT_TO_DIVISION[district],
        'requests': need['requests'],
        'available': available,
        'available_text': data.count_text(available),
        'donations': sum(series),
        'groups': [
            {'group': g, 'supply': data.supply_value(counts[g], exact=False)}
            for g in data.BLOOD_GROUPS
        ],
        'urgent': urgent_requests(district),
        'drives': drives,
        'squads': _squads(district),
        'spark': svg.sparkline(months, series),
        'spark_total': sum(series),
        'busiest': _busiest_month(months, series),
        'top_groups': gap['top'],
        'short_groups': gap['short'],
    }


def _busiest_month(months, series):
    top = max(series)
    if not top:
        return None
    month = months[len(series) - 1 - series[::-1].index(top)]
    return {'label': date_format(month, 'F Y'), 'count': top}


@require_GET
def district_view(request, slug):
    district = geo.district_for_slug(slug)
    if district is None:
        raise Http404('Unknown district')
    ctx = district_context(district)
    siblings = [n for n in geo.DISTRICT_NAMES
                if geo.DISTRICT_TO_DIVISION[n] == ctx['division']
                and n != district]
    ctx['siblings'] = [{'name': n, 'slug': geo.SLUGS[n]} for n in siblings]
    return render(request, 'atlas/district.html', ctx)


# -------------------------------------------------------------- divisions

def divisions_context(today=None):
    board = data.standings(today)
    ranked = data.rank(board['this'])
    last = data.winners(board['last'])
    return {
        'ranked': ranked,
        'this_label': date_format(board['this_start'], 'F Y'),
        'last_label': date_format(board['last_start'], 'F Y'),
        'last_winners': last,
        'last_top': max(board['last'].values(), default=0),
        'total': sum(board['this'].values()),
        'leaders': data.winners(board['this']),
    }


@require_GET
def divisions_view(request):
    ctx = divisions_context()
    rows = [
        {'rank': place if ctx['total'] else '–', 'name': name,
         'count': count,
         'index': geo.DIVISION_INDEX[name]}
        for place, name, count in ctx['ranked']
    ]
    ctx.update({
        'chart': svg.division_chart(ctx['ranked']),
        'rows': rows,
    })
    return render(request, 'atlas/divisions.html', ctx)


# -------------------------------------------------------------------- csv

@require_GET
@cache_control(public=True, max_age=data.CACHE_TTL)
def data_csv(request):
    """The public aggregate, one row per district. No per-person data; donor
    counts below 3 are the text 'fewer than 3'. Headers stay in English so
    the file is the same whatever the page language is."""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="atlas-districts.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'district', 'division', 'open_requests', 'verified_donations_12m',
        'available_donors',
    ])
    need = data.demand()['districts']
    for name in geo.DISTRICT_NAMES:
        available = data.district_available(name)
        writer.writerow([
            name, geo.DISTRICT_TO_DIVISION[name],
            need.get(name, {}).get('requests', 0),
            data.donations_12m(name),
            'fewer than 3' if data.is_suppressed(available) else available,
        ])
    return response
