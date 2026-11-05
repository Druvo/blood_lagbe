"""Aggregated, anonymised community statistics.

Everything here counts only admin-approved donations. Nothing returned from
this module names a donor, and nothing carries a phone number, so every
value is safe on a public page or in the public JSON feed.

Results are cached for a few minutes. Writes to donations, requests, responses
and profiles bump a cache version (see ``insights.apps``), so a freshly
approved donation shows up quickly; ``clear_cache()`` does the same by hand.
"""
import calendar
import re
import uuid
from datetime import date
from urllib.parse import quote

from django.core.cache import cache
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from accounts.models import Profile
from rewards.blood import BLOOD_GROUP_CHOICES
from rewards.geo import DISTRICT_TO_DIVISION, DIVISIONS
from rewards.models import (
    LIVES_PER_DONATION,
    BloodRequest,
    Donation,
    RequestResponse,
)

CACHE_TTL = 60 * 5
_VERSION_KEY = 'insights:version'

PERIOD_ALL = 'all'
PERIOD_12M = '12m'
PERIOD_YEAR = 'year'
PERIODS = [
    (PERIOD_ALL, 'All time'),
    (PERIOD_12M, 'Last 12 months'),
    (PERIOD_YEAR, 'This year'),
]
UNSPECIFIED = 'Not specified'
BLOOD_GROUPS = [code for code, _ in BLOOD_GROUP_CHOICES]
MIN_FREE_TEXT_PLACE_COUNT = 2  # typed locations must repeat to be shown
_PHONE_LIKE = re.compile(r'\d{6,}')
_EMAIL_LIKE = re.compile(r'\S@\S')


def looks_private(text):
    """True for typed text that carries a phone number or an email address,
    however the digits are spaced (01711 000 001, +880-1711-000001)."""
    return (
        bool(_PHONE_LIKE.search(text))
        or sum(ch.isdigit() for ch in text) >= 7
        or bool(_EMAIL_LIKE.search(text))
    )


# ---------------------------------------------------------------- caching

def _version():
    version = cache.get(_VERSION_KEY)
    if version is None:
        version = uuid.uuid4().hex
        cache.set(_VERSION_KEY, version, None)
    return version


def clear_cache():
    """Forget every cached statistic (used by signals and tests)."""
    cache.set(_VERSION_KEY, uuid.uuid4().hex, None)


def _cached(name, parts, compute):
    key = ':'.join(
        ['insights', _version(), name]
        + [quote(str(p), safe='') for p in parts]
        + [timezone.localdate().isoformat()]
    )
    value = cache.get(key)
    if value is None:
        value = compute()
        cache.set(key, value, CACHE_TTL)
    return value


# ---------------------------------------------------------------- helpers

def normalise_period(value):
    return value if value in dict(PERIODS) else PERIOD_ALL


def period_label(period):
    return dict(PERIODS)[normalise_period(period)]


def add_months(day, months):
    """First day of the month that is `months` after `day`'s month."""
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def period_start(period, today=None):
    today = today or timezone.localdate()
    period = normalise_period(period)
    if period == PERIOD_12M:
        return add_months(today, -11)
    if period == PERIOD_YEAR:
        return date(today.year, 1, 1)
    return None


def _approved(period=PERIOD_ALL, district=None):
    # order_by(): Donation's default ordering would otherwise join the
    # GROUP BY of every values().annotate() and split groups by date.
    qs = Donation.objects.filter(status=Donation.Status.APPROVED).order_by()
    start = period_start(period)
    if start:
        qs = qs.filter(donation_date__gte=start)
    if district:
        qs = qs.filter(district=district)
    return qs


def _requests(period=PERIOD_ALL, district=None):
    qs = BloodRequest.objects.order_by()
    start = period_start(period)
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if district:
        qs = qs.filter(district=district)
    return qs


def percent_change(current, previous):
    """Whole-number % change, or None when there is nothing to compare to."""
    if not previous:
        return None
    return round((current - previous) * 100 / previous)


# ----------------------------------------------------------------- totals

def _request_funnel(period, district=None):
    rows = list(
        _requests(period, district).with_received()
        .values_list('pk', 'units_needed', 'received')
    )
    ids = [pk for pk, _, _ in rows]
    responded = set(
        RequestResponse.objects.filter(blood_request_id__in=ids)
        .order_by().values_list('blood_request_id', flat=True)
    ) if ids else set()
    posted = len(rows)
    fulfilled = sum(1 for _, needed, got in rows if got >= needed)
    pledged = sum(
        1 for pk, needed, got in rows
        if pk in responded or got > 0
    )
    return posted, pledged, fulfilled


def totals(period=PERIOD_ALL):
    period = normalise_period(period)

    def compute():
        approved = _approved(period)
        donations = approved.count()
        donors = approved.values('donor').distinct().count()
        posted, _pledged, fulfilled = _request_funnel(period)
        return {
            'period': period,
            'donations': donations,
            'donors': donors,
            'lives': donations * LIVES_PER_DONATION,  # an estimate
            'requests_posted': posted,
            'requests_fulfilled': fulfilled,
            'fulfilment_rate': (
                round(fulfilled * 100 / posted) if posted else None
            ),
        }
    return _cached('totals', [period], compute)


def headline():
    """All-time headline numbers, for other pages (home, about)."""
    return totals(PERIOD_ALL)


def request_funnel(period=PERIOD_ALL):
    period = normalise_period(period)

    def compute():
        posted, pledged, fulfilled = _request_funnel(period)
        stages = [
            ('Posted', posted),
            ('Donors stepped up', pledged),
            ('Fulfilled', fulfilled),
        ]
        return [
            {
                'label': label,
                'value': value,
                'percent': round(value * 100 / posted) if posted else 0,
            }
            for label, value in stages
        ]
    return _cached('funnel', [period], compute)


# ------------------------------------------------------------ time series

def monthly_series(district=None, today=None):
    """Approved donations for the last 12 calendar months, zero-filled."""
    today = today or timezone.localdate()

    def compute():
        first = add_months(today, -11)
        counts = {}
        rows = (
            _approved(district=district)
            .filter(donation_date__gte=first)
            .annotate(month=TruncMonth('donation_date'))
            .order_by().values('month').annotate(n=Count('pk'))
        )
        for row in rows:
            month = row['month']
            if hasattr(month, 'date'):
                month = month.date()
            counts[(month.year, month.month)] = row['n']
        series = []
        for i in range(12):
            month = add_months(first, i)
            series.append({
                'key': month.strftime('%Y-%m'),
                'label': calendar.month_abbr[month.month],
                'year': month.year,
                'month': month.month,
                'count': counts.get((month.year, month.month), 0),
            })
        return series
    return _cached(
        'monthly', [district or '', today.isoformat()], compute,
    )


def month_comparison(today=None):
    """Donations so far this calendar month against the whole last month."""
    today = today or timezone.localdate()

    def compute():
        this_start = today.replace(day=1)
        last_start = add_months(this_start, -1)
        approved = _approved()
        this_month = approved.filter(donation_date__gte=this_start).count()
        last_month = approved.filter(
            donation_date__gte=last_start, donation_date__lt=this_start,
        ).count()
        return {
            'this_month': this_month,
            'last_month': last_month,
            'this_label': calendar.month_name[this_start.month],
            'last_label': calendar.month_name[last_start.month],
            'change': percent_change(this_month, last_month),
            'delta': this_month - last_month,
        }
    return _cached('compare', [today.isoformat()], compute)


# ----------------------------------------------------------- distributions

def blood_groups(period=PERIOD_ALL, district=None):
    """All eight groups with count and share, in conventional order."""
    period = normalise_period(period)

    def compute():
        counts = {
            row['blood_group']: row['n']
            for row in _approved(period, district)
            .values('blood_group').annotate(n=Count('pk'))
        }
        total = sum(counts.get(code, 0) for code in BLOOD_GROUPS)
        return [
            {
                'group': code,
                'count': counts.get(code, 0),
                'share': round(counts.get(code, 0) * 100 / total, 1)
                if total else 0,
            }
            for code in BLOOD_GROUPS
        ]
    return _cached('groups', [period, district or ''], compute)


def geography(period=PERIOD_ALL):
    """Donations by district and division.

    Blank (or unknown) districts count as ``Not specified`` and stay out of
    every ranking. ``districts`` is the top 10 named districts;
    ``divisions`` always lists all eight.
    """
    period = normalise_period(period)

    def compute():
        by_district = {}
        unspecified = 0
        for row in _approved(period).values('district').annotate(n=Count('pk')):
            name = (row['district'] or '').strip()
            if name in DISTRICT_TO_DIVISION:
                by_district[name] = by_district.get(name, 0) + row['n']
            else:
                unspecified += row['n']
        ranked = sorted(by_district.items(), key=lambda kv: (-kv[1], kv[0]))
        divisions = {name: 0 for name in DIVISIONS}
        for name, count in by_district.items():
            divisions[DISTRICT_TO_DIVISION[name]] += count
        return {
            'districts': [
                {'name': n, 'count': c, 'division': DISTRICT_TO_DIVISION[n]}
                for n, c in ranked[:10]
            ],
            'districts_total': len(by_district),
            'divisions': [
                {'name': n, 'count': c}
                for n, c in sorted(
                    divisions.items(), key=lambda kv: (-kv[1], kv[0]),
                )
            ],
            'unspecified': unspecified,
        }
    return _cached('geo', [period], compute)


def top_places(period=PERIOD_ALL, limit=8):
    """Hospitals and blood banks by verified donations.

    Curated ``Place`` rows are trusted. Typed locations are donor-written
    text, so they only appear once at least two donations share the same
    spelling, and anything that looks like a phone number is dropped.
    """
    period = normalise_period(period)

    def compute():
        merged = {}
        rows = (
            _approved(period)
            .values('place_id', 'place__name', 'place__district', 'location')
            .annotate(n=Count('pk'))
        )
        for row in rows:
            if row['place_id']:
                key = ('place', row['place_id'])
                label = row['place__name']
                where = row['place__district']
            else:
                text = ' '.join((row['location'] or '').split())
                if not text or looks_private(text):
                    continue
                key = ('text', text.lower())
                label = text[:60]
                where = ''
            entry = merged.setdefault(
                key, {'name': label, 'district': where, 'count': 0,
                      'curated': key[0] == 'place'},
            )
            entry['count'] += row['n']
        places = [
            p for p in merged.values()
            if p['curated'] or p['count'] >= MIN_FREE_TEXT_PLACE_COUNT
        ]
        places.sort(key=lambda p: (-p['count'], p['name'].lower()))
        return places[:limit]
    return _cached('places', [period, limit], compute)


# -------------------------------------------------------- supply & demand

def supply_demand():
    """Per blood group: units still needed on open requests, against donors
    who chose to be listed in the directory. Counts only, never people."""

    def compute():
        supply = {code: 0 for code in BLOOD_GROUPS}
        available = {code: 0 for code in BLOOD_GROUPS}
        listed = (
            Profile.objects.filter(in_directory=True, user__is_active=True)
            .exclude(blood_group='')
            .order_by().values('blood_group')
            .annotate(n=Count('pk'),
                      ready=Count('pk', filter=Q(available_to_donate=True)))
        )
        for row in listed:
            if row['blood_group'] in supply:
                supply[row['blood_group']] = row['n']
                available[row['blood_group']] = row['ready']

        units = {code: 0 for code in BLOOD_GROUPS}
        requests = {code: 0 for code in BLOOD_GROUPS}
        where = {code: {} for code in BLOOD_GROUPS}
        open_rows = BloodRequest.objects.still_needed().order_by().values_list(
            'blood_group', 'district', 'units_needed', 'received',
        )
        for group, district, needed, got in open_rows:
            if group not in units:
                continue
            remaining = max(needed - got, 0)
            units[group] += remaining
            requests[group] += 1
            where[group][district] = where[group].get(district, 0) + remaining

        rows = []
        for code in BLOOD_GROUPS:
            top = sorted(where[code].items(), key=lambda kv: (-kv[1], kv[0]))
            if units[code] == 0:
                status = 'quiet'
            elif units[code] > supply[code]:
                status = 'gap'
            else:
                status = 'covered'
            rows.append({
                'group': code,
                'need': units[code],
                'requests': requests[code],
                'donors': supply[code],
                'available': available[code],
                'status': status,
                'top_district': top[0][0] if top else '',
            })
        return rows
    return _cached('supply', [], compute)


def call_to_action(rows=None):
    """The single most useful thing a visitor can do right now.

    kind 'gap': a group needs more units than there are listed donors.
    kind 'open': requests are open but listed donors cover them.
    kind 'recruit': nothing is open, so ask for the scarcest group.
    """
    rows = rows if rows is not None else supply_demand()
    gaps = [r for r in rows if r['status'] == 'gap']
    if gaps:
        best = max(
            gaps,
            key=lambda r: (r['need'] - r['donors'], r['need'],
                           -BLOOD_GROUPS.index(r['group'])),
        )
        return dict(best, kind='gap')
    open_rows = [r for r in rows if r['need'] > 0]
    if open_rows:
        best = max(open_rows, key=lambda r: (r['need'], -r['donors']))
        return dict(best, kind='open')
    best = min(rows, key=lambda r: (r['donors'], BLOOD_GROUPS.index(r['group'])))
    return dict(best, kind='recruit')


# -------------------------------------------------------------- districts

def district_detail(name):
    """Everything the district page shows. `name` must be a valid district."""

    def compute():
        approved = _approved(district=name)
        donations = approved.count()
        everyone = (
            _approved().exclude(district='').values('district')
            .annotate(n=Count('pk')).order_by('-n', 'district')
        )
        order = [r['district'] for r in everyone if r['district'] in
                 DISTRICT_TO_DIVISION]
        rank = order.index(name) + 1 if name in order else None
        open_requests = [
            {
                'pk': r.pk,
                'blood_group': r.blood_group,
                'hospital': r.hospital,
                'remaining': r.units_remaining,
                'needed_by': r.needed_by,
            }
            for r in BloodRequest.objects.still_needed().public().filter(
                district=name,
            ).order_by('needed_by', '-created_at')[:10]
        ]
        donors_available = Profile.objects.filter(
            in_directory=True, available_to_donate=True,
            user__is_active=True, district=name,
        ).exclude(blood_group='').count()
        posted, pledged, fulfilled = _request_funnel(PERIOD_ALL, name)
        return {
            'name': name,
            'division': DISTRICT_TO_DIVISION[name],
            'donations': donations,
            'donors': approved.values('donor').distinct().count(),
            'lives': donations * LIVES_PER_DONATION,
            'rank': rank,
            'ranked_districts': len(order),
            'requests_posted': posted,
            'requests_fulfilled': fulfilled,
            'open_requests': open_requests,
            'donors_available': donors_available,
        }
    return _cached('district', [name], compute)


# ------------------------------------------------------------- dashboards

def dashboard(period=PERIOD_ALL):
    period = normalise_period(period)
    rows = supply_demand()
    return {
        'period': period,
        'period_label': period_label(period),
        'totals': totals(period),
        'monthly': monthly_series(),
        'month_compare': month_comparison(),
        'groups': blood_groups(period),
        'geo': geography(period),
        'places': top_places(period),
        'funnel': request_funnel(period),
        'supply_demand': rows,
        'cta': call_to_action(rows),
    }


def public_payload(period=PERIOD_ALL):
    """The dashboard as JSON-safe data. Aggregates only: no names, no
    contact details, no free-text user input."""
    data = dashboard(period)
    geo = data['geo']
    return {
        'period': data['period'],
        'estimate_note': (
            f'People helped is an estimate: {LIVES_PER_DONATION} per '
            'verified donation, at most. Only admin-verified donations count.'
        ),
        'totals': data['totals'],
        'monthly': [
            {'month': m['key'], 'donations': m['count']}
            for m in data['monthly']
        ],
        'month_compare': {
            k: data['month_compare'][k]
            for k in ('this_month', 'last_month', 'change')
        },
        'blood_groups': [
            {'group': g['group'], 'donations': g['count'], 'share': g['share']}
            for g in data['groups']
        ],
        'divisions': [
            {'division': d['name'], 'donations': d['count']}
            for d in geo['divisions']
        ],
        'districts': [
            {'district': d['name'], 'division': d['division'],
             'donations': d['count']}
            for d in geo['districts']
        ],
        'district_not_specified': geo['unspecified'],
        'request_funnel': [
            {'stage': f['label'], 'requests': f['value']}
            for f in data['funnel']
        ],
        'supply_vs_demand': [
            {'group': r['group'], 'units_needed': r['need'],
             'open_requests': r['requests'], 'donors_listed': r['donors']}
            for r in data['supply_demand']
        ],
    }
