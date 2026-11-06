"""Server-side SVG for the atlas: tile map, paired bars, division bars and a
sparkline. Plain functions returning safe markup; every piece of text goes
through `escape`. Colours are CSS classes (see atlas/static/atlas/atlas.css),
so light and dark mode need nothing here.
"""
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext, ngettext

from atlas import geo

TILE, GAP_PX, PAD = 46, 4, 2
PITCH = TILE + GAP_PX


def map_size():
    return (
        geo.COLUMNS * PITCH - GAP_PX + 2 * PAD,
        geo.ROWS * PITCH - GAP_PX + 2 * PAD,
    )


def tile_map(built, compact=False):
    """The cartogram. `built` comes from `layers.build`. With `compact` the
    tiles carry no text and no links: it is a small picture to sit beside a
    link to the full map."""
    width, height = map_size()
    summary = escape(built['summary'])
    css = 'at-map at-map--mini' if compact else 'at-map'
    parts = [
        f'<svg class="{css}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{summary}" focusable="false">',
        f'<title>{summary}</title>',
    ]
    by_division = {name: [] for name in geo.DIVISION_NAMES}
    for tile in built['tiles'].values():
        by_division[tile['division']].append(tile)
    for division in geo.DIVISION_NAMES:
        parts.append(f'<g class="at-division at-d{geo.DIVISION_INDEX[division]}">')
        for tile in by_division[division]:
            parts.append(_tile(tile, compact))
        parts.append('</g>')
    parts.append('</svg>')
    return mark_safe(''.join(parts))


def _tile(tile, compact):
    x = PAD + tile['col'] * PITCH
    y = PAD + tile['row'] * PITCH
    level = tile['level']
    body = (
        f'<rect class="at-tile" x="{x}" y="{y}" width="{TILE}" height="{TILE}" '
        'rx="8"/>'
    )
    if compact:
        return f'<g class="at-t at-l{level}">{body}</g>'
    label = gettext('%(district)s, %(division)s division: %(detail)s') % {
        'district': tile['name'], 'division': tile['division'],
        'detail': tile['detail'],
    }
    url = reverse('atlas:district', args=[tile['slug']])
    cx = x + TILE / 2
    return (
        f'<a class="at-t at-l{level}" href="{escape(url)}" '
        f'aria-label="{escape(label)}">'
        f'<title>{escape(label)}</title>{body}'
        f'<rect class="at-band" x="{x + 6}" y="{y + 4}" width="{TILE - 12}" '
        'height="4" rx="2"/>'
        f'<text class="at-abbr" x="{cx}" y="{y + 23}" text-anchor="middle">'
        f'{escape(tile["code"])}</text>'
        f'<text class="at-num" x="{cx}" y="{y + 40}" text-anchor="middle">'
        f'{escape(tile["text"])}</text></a>'
    )


# ------------------------------------------------------------ paired bars

def gap_chart(rows):
    """For each blood group, a red bar of units still needed above a blue bar
    of donors who can give to that group. One shared scale."""
    width, label_w, bars_w = 360, 46, 190
    row_h, top = 52, 6
    height = top + row_h * len(rows)
    scale = max(
        [r['demand'] for r in rows] + [r['supply']['est'] for r in rows] + [1]
    )

    def bar_len(value):
        return max(value * bars_w / scale, 3) if value else 0

    label = escape(gettext(
        'Units needed and donors available, for each blood group. The '
        'numbers are in the table below the chart.'
    ))
    parts = [
        f'<svg class="at-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{label}" focusable="false">',
        f'<title>{label}</title>',
    ]
    for index, row in enumerate(rows):
        y = top + index * row_h
        supply = row['supply']
        need_text = ngettext(
            '%(n)s unit', '%(n)s units', row['demand'],
        ) % {'n': row['demand']}
        supply_text = supply['text']
        supply_title = gettext('%(group)s: %(need)s needed, %(donors)s donors ready') % {
            'group': row['group'], 'need': need_text, 'donors': supply['text'],
        }
        parts.append(
            f'<g class="at-gaprow{" is-short" if row["short"] else ""}">'
            f'<title>{escape(supply_title)}</title>'
            f'<text class="at-glabel" x="0" y="{y + 28}">{escape(row["group"])}</text>'
            f'<rect class="at-track" x="{label_w}" y="{y + 6}" width="{bars_w}" height="14" rx="7"/>'
            f'<rect class="at-bar at-bar--need" x="{label_w}" y="{y + 6}" '
            f'width="{bar_len(row["demand"]):.1f}" height="14" rx="7"/>'
            f'<text class="at-gval" x="{label_w + bars_w + 8}" y="{y + 18}">{escape(need_text)}</text>'
            f'<rect class="at-track" x="{label_w}" y="{y + 26}" width="{bars_w}" height="14" rx="7"/>'
            f'<rect class="at-bar at-bar--supply" x="{label_w}" y="{y + 26}" '
            f'width="{bar_len(supply["est"]):.1f}" height="14" rx="7"/>'
            f'<text class="at-gval" x="{label_w + bars_w + 8}" y="{y + 38}">{escape(supply_text)}</text>'
            '</g>'
        )
    parts.append('</svg>')
    return mark_safe(''.join(parts))


# --------------------------------------------------------- division bars

def division_chart(ranked):
    """Horizontal bars, one per division, coloured by division. `ranked` is
    [(rank, division, count)], already in order, ties sharing a rank."""
    width, label_w, bars_w = 360, 104, 190
    row_h, top = 38, 4
    height = top + row_h * len(ranked)
    scale = max([count for _r, _d, count in ranked] + [1])
    label = escape(gettext(
        'Verified donations this month, by division. The numbers are in the '
        'table below the chart.'
    ))
    parts = [
        f'<svg class="at-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{label}" focusable="false">',
        f'<title>{label}</title>',
    ]
    for index, (place, division, count) in enumerate(ranked):
        y = top + index * row_h
        length = max(count * bars_w / scale, 3) if count else 0
        text = ngettext('%(n)s donation', '%(n)s donations', count) % {'n': count}
        title = gettext('%(rank)s. %(division)s: %(text)s') % {
            'rank': place, 'division': division, 'text': text,
        }
        parts.append(
            '<g class="at-divrow">'
            f'<title>{escape(title)}</title>'
            f'<text class="at-rank" x="0" y="{y + 22}">{place}</text>'
            f'<text class="at-dname" x="18" y="{y + 22}">{escape(division)}</text>'
            f'<rect class="at-track" x="{label_w}" y="{y + 8}" width="{bars_w}" height="18" rx="9"/>'
            f'<rect class="at-bar at-d{geo.DIVISION_INDEX[division]}" x="{label_w}" y="{y + 8}" '
            f'width="{length:.1f}" height="18" rx="9"/>'
            f'<text class="at-gval" x="{label_w + bars_w + 8}" y="{y + 22}">{count}</text>'
            '</g>'
        )
    parts.append('</svg>')
    return mark_safe(''.join(parts))


# -------------------------------------------------------------- sparkline

def sparkline(months, counts):
    """Verified donations per month as a small line with an end dot.
    Returns '' when there is nothing to draw."""
    from django.utils.formats import date_format

    if not any(counts):
        return ''
    width, height, pad_x, pad_top, pad_bottom = 240, 64, 6, 8, 16
    top = max(counts)
    step = (width - 2 * pad_x) / (len(counts) - 1)

    def point(i):
        x = pad_x + i * step
        y = pad_top + (height - pad_top - pad_bottom) * (1 - counts[i] / top)
        return x, y

    pts = [point(i) for i in range(len(counts))]
    line = ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)
    base = height - pad_bottom
    area = f'{pts[0][0]:.1f},{base} {line} {pts[-1][0]:.1f},{base}'
    labels = [date_format(m, 'M Y') for m in months]
    summary = gettext('Verified donations per month, %(first)s to %(last)s: %(values)s') % {
        'first': labels[0], 'last': labels[-1],
        'values': ', '.join(f'{lab} {n}' for lab, n in zip(labels, counts)),
    }
    last_x, last_y = pts[-1]
    return mark_safe(
        f'<svg class="at-spark" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(summary)}" focusable="false">'
        f'<title>{escape(summary)}</title>'
        f'<line class="at-spark__base" x1="{pad_x}" x2="{width - pad_x}" y1="{base}" y2="{base}"/>'
        f'<polygon class="at-spark__area" points="{area}"/>'
        f'<polyline class="at-spark__line" points="{line}"/>'
        f'<circle class="at-spark__dot" cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5"/>'
        f'<text class="at-spark__txt" x="{pad_x}" y="{height - 3}">{escape(labels[0])}</text>'
        f'<text class="at-spark__txt" x="{width - pad_x}" y="{height - 3}" text-anchor="end">{escape(labels[-1])}</text>'
        '</svg>'
    )
