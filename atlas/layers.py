"""The four map layers: what each district shows and how it is shaded."""
import math

from django.utils.translation import gettext, gettext_lazy as _, ngettext

from atlas import data, geo

DEMAND, DONORS, DONATIONS, GAP = 'demand', 'donors', 'donations', 'gap'
LAYER_CHOICES = [
    (DEMAND, _('Requests')),
    (DONORS, _('Donors')),
    (DONATIONS, _('Donations')),
    (GAP, _('Gap')),
]
LAYERS = [key for key, _label in LAYER_CHOICES]
DEFAULT_LAYER = DEMAND
LEVELS = 4

# Open requests per available donor: below the first limit is the lightest
# shade, above the last one the darkest.
GAP_LIMITS = (0.1, 0.25, 0.5)


def normalise(layer):
    return layer if layer in LAYERS else DEFAULT_LAYER


def compact(value):
    if value >= 10_000:
        return f'{value / 1000:.0f}k'
    if value >= 1_000:
        return f'{value / 1000:.1f}'.rstrip('0').rstrip('.') + 'k'
    return str(value)


def _level(value, top):
    if not value or not top:
        return 0
    return min(LEVELS, max(1, math.ceil(value / top * LEVELS)))


def _gap_level(requests, available):
    """(level, tile text). Fewer than 3 donors is shown as '!': the real
    number is never used to pick a shade, so the shade cannot reveal it."""
    if not requests:
        return 0, '–'
    if data.is_suppressed(available):
        return LEVELS, '!'
    ratio = requests / available
    level = 1 + sum(1 for limit in GAP_LIMITS if ratio >= limit)
    per_ten = round(ratio * 10)
    return level, str(per_ten) if per_ten else '<1'


def _requests_text(n):
    return ngettext('%(n)s open request', '%(n)s open requests', n) % {'n': n}


def _donors_text(available):
    if data.is_suppressed(available):
        return gettext('fewer than 3 available donors')
    return ngettext(
        '%(n)s available donor', '%(n)s available donors', available,
    ) % {'n': available}


def _donations_text(n):
    return ngettext(
        '%(n)s verified donation in the last 12 months',
        '%(n)s verified donations in the last 12 months', n,
    ) % {'n': n}


def _gap_text(requests, available):
    if not requests:
        return gettext('no open requests')
    if data.is_suppressed(available):
        return gettext(
            '%(requests)s, fewer than 3 available donors'
        ) % {'requests': _requests_text(requests)}
    return gettext('%(requests)s, %(donors)s') % {
        'requests': _requests_text(requests),
        'donors': _donors_text(available),
    }


def build(layer):
    """Tiles, legend numbers, a summary sentence and the table rows for one
    layer. Pure aggregates; safe to render on a public page."""
    layer = normalise(layer)
    need = data.demand()['districts']
    sheet = data.donations()['by_district']
    tiles, rows = {}, []
    values = {}
    for name in geo.DISTRICT_NAMES:
        requests = need.get(name, {}).get('requests', 0)
        available = data.district_available(name)
        donated = sum(sheet.get(name, []))
        values[name] = {
            DEMAND: requests, DONORS: available, DONATIONS: donated,
        }
        rows.append({
            'name': name, 'slug': geo.SLUGS[name],
            'division': geo.DISTRICT_TO_DIVISION[name],
            'requests': requests,
            'donors': data.count_text(available),
            'donations': donated,
            'gap': _gap_text(requests, available),
        })

    numeric = {
        name: (v[layer] if layer != DONORS or not data.is_suppressed(
            v[layer]) else 0)
        for name, v in values.items() if layer != GAP
    }
    top = max(numeric.values(), default=0)
    for name in geo.DISTRICT_NAMES:
        v = values[name]
        col, row = geo.TILES[name]
        tile = {
            'name': name, 'slug': geo.SLUGS[name], 'code': geo.CODES[name],
            'division': geo.DISTRICT_TO_DIVISION[name], 'col': col, 'row': row,
        }
        if layer == DEMAND:
            tile.update(level=_level(v[DEMAND], top),
                        text=compact(v[DEMAND]), detail=_requests_text(v[DEMAND]))
        elif layer == DONORS:
            if data.is_suppressed(v[DONORS]):
                tile.update(level=0, text='<3', detail=_donors_text(v[DONORS]))
            else:
                tile.update(level=_level(v[DONORS], top),
                            text=compact(v[DONORS]),
                            detail=_donors_text(v[DONORS]))
        elif layer == DONATIONS:
            tile.update(level=_level(v[DONATIONS], top),
                        text=compact(v[DONATIONS]),
                        detail=_donations_text(v[DONATIONS]))
        else:
            level, text = _gap_level(v[DEMAND], v[DONORS])
            tile.update(level=level, text=text,
                        detail=_gap_text(v[DEMAND], v[DONORS]))
        tiles[name] = tile

    return {
        'layer': layer, 'tiles': tiles, 'rows': rows, 'top': top,
        'summary': _summary(layer, values, top),
        'empty': _is_empty(layer, values, top),
    }


def _is_empty(layer, values, top):
    if layer == GAP:
        return not any(v[DEMAND] for v in values.values())
    if layer == DONORS:
        return top == 0
    return top == 0


def _busiest(values, layer):
    best = max(geo.DISTRICT_NAMES, key=lambda n: (values[n][layer], -geo.DISTRICT_NAMES.index(n)))
    return best, values[best][layer]


def _summary(layer, values, top):
    """The text alternative for the picture (aria-label)."""
    if layer == DEMAND:
        total = sum(v[DEMAND] for v in values.values())
        if not total:
            return gettext('No open blood requests in any district right now.')
        name, n = _busiest(values, DEMAND)
        return gettext(
            'Open blood requests by district: %(total)s in total, the most in '
            '%(district)s (%(n)s).'
        ) % {'total': total, 'district': name, 'n': n}
    if layer == DONORS:
        if not top:
            return gettext(
                'Available donors by district. Every district has fewer than '
                '3 donors listed so far.'
            )
        shown = {n: v for n, v in values.items()
                 if not data.is_suppressed(v[DONORS])}
        name = max(shown, key=lambda n: (shown[n][DONORS], -geo.DISTRICT_NAMES.index(n)))
        return gettext(
            'Available donors by district. Districts with fewer than 3 show '
            '"<3". The most is in %(district)s (%(n)s).'
        ) % {'district': name, 'n': shown[name][DONORS]}
    if layer == DONATIONS:
        total = sum(v[DONATIONS] for v in values.values())
        if not total:
            return gettext(
                'No verified donations in the last 12 months yet.'
            )
        name, n = _busiest(values, DONATIONS)
        return gettext(
            'Verified donations in the last 12 months by district: %(total)s '
            'in total, the most in %(district)s (%(n)s).'
        ) % {'total': total, 'district': name, 'n': n}
    open_districts = sum(1 for v in values.values() if v[DEMAND])
    if not open_districts:
        return gettext('No open blood requests in any district right now.')
    return gettext(
        'Open requests compared with available donors, by district. Darker '
        'means more requests for each donor. %(n)s districts have open '
        'requests.'
    ) % {'n': open_districts}


def legend_note(layer):
    """One line under the legend that says what the number on a tile is."""
    return {
        DEMAND: _('Number on a tile: open requests.'),
        DONORS: _('Number on a tile: available donors. "<3" means fewer than '
                  '3 are listed.'),
        DONATIONS: _('Number on a tile: verified donations in the last 12 '
                     'months.'),
        GAP: _('Number on a tile: open requests for every 10 available '
               'donors. "!" means fewer than 3 donors are listed. A dash '
               'means no open requests.'),
    }[layer]


def legend_label(layer):
    return {
        DEMAND: _('More open requests'),
        DONORS: _('More available donors'),
        DONATIONS: _('More donations'),
        GAP: _('More requests for each donor'),
    }[layer]
