"""Aggregate numbers behind the atlas pages.

Everything here is a count. Nothing names a member, a hospital or a patient.

Privacy rules, in one place:

* Supply counts only members who opted in (`in_directory`) and said they are
  available. Demand counts only public, open, not-flagged requests that
  still need blood (`still_needed().public()`).
* A donor count below `MIN_COUNT` is never shown as a number. District
  totals are exact from 3 up. Anything finer (a district split by blood
  group, or a sum of compatible groups) is shown in ranges, because two exact
  numbers that overlap can be subtracted to recover a cell below 3.
* Nationally the supply for a group is exact only while every blood group has
  at least `MIN_COUNT` donors; on a very small site it falls back to ranges
  too.

Caching: each aggregate is computed once per `CACHE_TTL` (five minutes) and
the key carries today's date. A new request, donation or opt-in therefore
shows up on the pages after at most five minutes (`clear_cache()` forgets it
all at once). Stale numbers are harmless here: they are counts, never people.
Each aggregate is one database query, so a page costs a small fixed number of
queries whatever the amount of data.
"""
import uuid
from collections import defaultdict
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Profile
from atlas import geo
from insights.stats import add_months
from rewards.blood import BLOOD_GROUP_CHOICES, COMPATIBLE_DONORS
from rewards.models import BloodRequest, Donation

CACHE_TTL = 60 * 5
MIN_COUNT = 3
BLOOD_GROUPS = [code for code, _label in BLOOD_GROUP_CHOICES]
_VERSION_KEY = 'atlas:version'

# (lowest, highest, label, middle used to draw a bar). The first band is the
# suppressed one: its middle is only a drawing aid, never shown.
BANDS = [
    (0, MIN_COUNT - 1, _('fewer than 3'), 1),
    (3, 9, _('3 to 9'), 6),
    (10, 24, _('10 to 24'), 17),
    (25, 49, _('25 to 49'), 37),
    (50, 99, _('50 to 99'), 75),
    (100, None, _('100 or more'), 100),
]
FEWER_THAN = _('fewer than 3')


# ---------------------------------------------------------------- caching

def _version():
    version = cache.get(_VERSION_KEY)
    if version is None:
        version = uuid.uuid4().hex
        cache.set(_VERSION_KEY, version, None)
    return version


def clear_cache():
    """Forget every cached aggregate (tests and staff tooling)."""
    cache.set(_VERSION_KEY, uuid.uuid4().hex, None)


def _cached(name, parts, compute):
    key = ':'.join(
        ['atlas', _version(), name]
        + [quote(str(p), safe='') for p in parts]
        + [timezone.localdate().isoformat()]
    )
    value = cache.get(key)
    if value is None:
        value = compute()
        cache.set(key, value, CACHE_TTL)
    return value


# ------------------------------------------------------------ suppression

def band(count):
    """(label, middle) of the range a count falls in."""
    for low, high, label, middle in BANDS:
        if count >= low and (high is None or count <= high):
            return label, middle
    return BANDS[-1][2], BANDS[-1][3]


def is_suppressed(count):
    return count < MIN_COUNT


def count_text(count):
    """An exact count, or 'fewer than 3'."""
    return FEWER_THAN if is_suppressed(count) else str(count)


def supply_value(count, exact=True):
    """How a donor count is shown: ``est`` (for drawing and ranking), ``text``
    and ``exact``. Counts below 3 are never exact; in range mode nothing is."""
    if exact and not is_suppressed(count):
        return {'est': count, 'text': str(count), 'exact': True}
    label, middle = band(count)
    return {'est': middle, 'text': label, 'exact': False}


# ----------------------------------------------------------------- demand

def demand():
    """Open, public requests that still need blood.

    ``districts[name]`` = ``{'requests', 'units', 'groups': {group: units}}``;
    ``national`` has the same shape over every request, whatever its district
    text says. One query.
    """
    def compute():
        districts = {}
        national = {'requests': 0, 'units': 0, 'groups': defaultdict(int)}
        rows = (
            BloodRequest.objects.still_needed().public().order_by()
            .values_list('district', 'blood_group', 'units_needed', 'received')
        )
        for district, group, needed, got in rows:
            remaining = max(needed - got, 0)
            national['requests'] += 1
            national['units'] += remaining
            national['groups'][group] += remaining
            if district in geo.TILES:
                entry = districts.setdefault(
                    district,
                    {'requests': 0, 'units': 0, 'groups': defaultdict(int)},
                )
                entry['requests'] += 1
                entry['units'] += remaining
                entry['groups'][group] += remaining
        national['groups'] = dict(national['groups'])
        for entry in districts.values():
            entry['groups'] = dict(entry['groups'])
        return {'districts': districts, 'national': national}
    return _cached('demand', [], compute)


def demand_for(district):
    empty = {'requests': 0, 'units': 0, 'groups': {}}
    return demand()['districts'].get(district, empty)


# ----------------------------------------------------------------- donors

def donors():
    """Opted-in, available donors.

    ``cells[(district, group)] = (available, ready)`` where ``ready`` also
    leaves out people still inside the donation interval (pending or approved
    donation in the last ``DONATION_INTERVAL_DAYS`` days; the same rule the
    donor's own eligibility uses). ``national[group]`` is the same over every
    listed donor, with or without a district. One query.
    """
    def compute():
        cutoff = timezone.localdate() - timedelta(
            days=settings.DONATION_INTERVAL_DAYS,
        )
        recent = Donation.objects.filter(
            donor=OuterRef('user_id'),
            status__in=[Donation.Status.PENDING, Donation.Status.APPROVED],
            donation_date__gt=cutoff,
        )
        rows = (
            Profile.objects.filter(
                in_directory=True, available_to_donate=True,
                user__is_active=True,
            )
            .exclude(blood_group='')
            .annotate(resting=Exists(recent))
            .order_by()
            .values('district', 'blood_group')
            .annotate(
                available=Count('pk'),
                ready=Count('pk', filter=Q(resting=False)),
            )
        )
        cells = {}
        national = {code: [0, 0] for code in BLOOD_GROUPS}
        for row in rows:
            group = row['blood_group']
            if group not in national:
                continue
            national[group][0] += row['available']
            national[group][1] += row['ready']
            if row['district'] in geo.TILES:
                key = (row['district'], group)
                have = cells.get(key, (0, 0))
                cells[key] = (have[0] + row['available'],
                              have[1] + row['ready'])
        return {
            'cells': cells,
            'national': {k: tuple(v) for k, v in national.items()},
        }
    return _cached('donors', [], compute)


def district_available(district):
    """Exact available donors in a district (all blood groups). Callers
    must pass it through `count_text` / `is_suppressed`."""
    cells = donors()['cells']
    return sum(cells.get((district, g), (0, 0))[0] for g in BLOOD_GROUPS)


def district_available_by_group(district):
    cells = donors()['cells']
    return {g: cells.get((district, g), (0, 0))[0] for g in BLOOD_GROUPS}


# -------------------------------------------------------------- donations

def donation_window(today=None):
    today = today or timezone.localdate()
    return add_months(today, -11)


def donations():
    """Verified donations of the last 12 calendar months by district.

    ``months`` is the list of 12 first-of-month dates (oldest first);
    ``by_district[name]`` the 12 counts. Blank or unknown districts are left
    out. One query.
    """
    def compute():
        first = donation_window()
        months = [add_months(first, i) for i in range(12)]
        index = {(m.year, m.month): i for i, m in enumerate(months)}
        rows = (
            Donation.objects.filter(
                status=Donation.Status.APPROVED, donation_date__gte=first,
            )
            .exclude(district='')
            .order_by()
            .annotate(month=TruncMonth('donation_date'))
            .values('district', 'month')
            .annotate(n=Count('pk'))
        )
        by_district = {}
        for row in rows:
            if row['district'] not in geo.TILES:
                continue
            month = row['month']
            if hasattr(month, 'date'):
                month = month.date()
            slot = index.get((month.year, month.month))
            if slot is None:
                continue
            counts = by_district.setdefault(row['district'], [0] * 12)
            counts[slot] += row['n']
        return {'months': months, 'by_district': by_district}
    return _cached('donations', [], compute)


def donations_12m(district):
    return sum(donations()['by_district'].get(district, [0] * 12))


# -------------------------------------------------------------------- gap

def national_exact(own_ready):
    """Show national supply exactly only while no blood group is a small
    cell (see the module docstring)."""
    return all(not is_suppressed(n) for n in own_ready.values())


def gap(district=None):
    """Open demand against ready supply for each blood group.

    ``district=None`` is the whole country (exact while safe); with a
    district the supply is in ranges. Supply for a recipient group is the
    number of ready donors whose own group can give to it
    (`COMPATIBLE_DONORS`). Rows carry ``demand`` (units still needed),
    ``requests``, ``supply`` (see `supply_value`), ``short`` and
    ``pressure`` (demand per donor, for ranking). ``top`` lists up to three
    groups that are open and most under pressure.
    """
    def compute():
        if district:
            need = demand_for(district)
            cells = donors()['cells']
            own = {
                g: cells.get((district, g), (0, 0))[1] for g in BLOOD_GROUPS
            }
            exact = False
        else:
            need = demand()['national']
            own = {g: n[1] for g, n in donors()['national'].items()}
            exact = national_exact(own)
        rows = []
        for group in BLOOD_GROUPS:
            ready = sum(own[g] for g in COMPATIBLE_DONORS[group])
            supply = supply_value(ready, exact=exact)
            units = need['groups'].get(group, 0)
            rows.append({
                'group': group,
                'demand': units,
                'supply': supply,
                'short': units > 0 and units > supply['est'],
                'pressure': units / max(supply['est'], 1),
            })
        ranked = sorted(
            (r for r in rows if r['demand'] > 0),
            key=lambda r: (-r['pressure'], -r['demand'],
                           BLOOD_GROUPS.index(r['group'])),
        )
        return {
            'district': district or '',
            'rows': rows,
            'top': [r['group'] for r in ranked[:3]],
            'short': [r['group'] for r in rows if r['short']],
            'units': sum(r['demand'] for r in rows),
            'exact': exact,
        }
    return _cached('gap', [district or ''], compute)


# ------------------------------------------------------------ divisions

def standings(today=None):
    """Verified donations by division, this month and last month.

    Donations with no district cannot be placed in a division and are not
    counted. Raw counts, no population adjustment. One query.
    """
    def compute():
        now = today or timezone.localdate()
        this_start = now.replace(day=1)
        last_start = add_months(this_start, -1)
        rows = (
            Donation.objects.filter(
                status=Donation.Status.APPROVED,
                donation_date__gte=last_start,
            )
            .exclude(district='')
            .order_by()
            .values('district')
            .annotate(
                this=Count('pk', filter=Q(donation_date__gte=this_start)),
                last=Count('pk', filter=Q(donation_date__lt=this_start)),
            )
        )
        this_month = {name: 0 for name in geo.DIVISION_NAMES}
        last_month = {name: 0 for name in geo.DIVISION_NAMES}
        for row in rows:
            division = geo.DISTRICT_TO_DIVISION.get(row['district'])
            if division:
                this_month[division] += row['this']
                last_month[division] += row['last']
        return {
            'this_start': this_start, 'last_start': last_start,
            'this': this_month, 'last': last_month,
        }
    parts = [today.isoformat()] if today else []
    return _cached('standings', parts, compute)


def rank(counts):
    """Standings with shared ranks: [(rank, division, count)], best first,
    ties alphabetical. Four people on 5, 5, 3, 3 rank 1, 1, 3, 3."""
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    rows, previous, place = [], None, 0
    for position, (name, count) in enumerate(ordered, start=1):
        if count != previous:
            place = position
            previous = count
        rows.append((place, name, count))
    return rows


def winners(counts):
    """Divisions sharing the top place (none when nobody donated)."""
    top = max(counts.values(), default=0)
    if not top:
        return []
    return sorted(name for name, count in counts.items() if count == top)
