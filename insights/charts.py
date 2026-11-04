"""Server-rendered charts: plain functions that return safe SVG / HTML.

No JavaScript, no chart library. Every piece of text that reaches the markup
goes through ``escape``. Colours come from CSS variables (see
``insights/_chart_styles.html``), so dark mode needs no extra work.
"""
import math

from django.utils.html import escape
from django.utils.safestring import mark_safe

TWO_PI = 2 * math.pi


# ---------------------------------------------------------------- helpers

def fmt(value):
    """12345 -> '12,345'. Anything non-numeric becomes 0."""
    try:
        return f'{int(value):,}'
    except (TypeError, ValueError):
        return '0'


def compact(value):
    """1200 -> '1.2k', 2500000 -> '2.5M'. Small numbers stay whole."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return '0'
    if abs(value) >= 1_000_000:
        return f'{value / 1_000_000:.1f}'.rstrip('0').rstrip('.') + 'M'
    if abs(value) >= 10_000:
        return f'{value / 1_000:.0f}k'
    if abs(value) >= 1_000:
        return f'{value / 1_000:.1f}'.rstrip('0').rstrip('.') + 'k'
    return str(value)


def _safe_int(value):
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def nice_ceiling(value, ticks=3):
    """Round `value` up to a tidy axis maximum, returning (top, step)."""
    value = max(_safe_int(value), 1)
    if value <= ticks:
        return ticks, 1
    raw = value / ticks
    magnitude = 10 ** int(math.floor(math.log10(raw)))
    step = magnitude
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if step * ticks >= value:
            break
    step = max(int(math.ceil(step)), 1)
    return step * ticks, step


def empty_state(message='Nothing to show yet.', icon='bar-chart-2'):
    return mark_safe(
        '<div class="bb-empty bb-empty--chart">'
        f'<i class="feather-{escape(icon)}" aria-hidden="true"></i>'
        f'<p>{escape(message)}</p></div>'
    )


# ---------------------------------------------------------- monthly bars

def monthly_chart(series, unit='donations', empty='No verified donations yet.'):
    """Bar chart with axis labels. `series` items need `label` and `count`;
    `year` is optional and shown under January and the first bar."""
    series = list(series or [])
    values = [_safe_int(item.get('count')) for item in series]
    if not series or not any(values):
        return empty_state(empty)

    width, height = 400, 236
    left, right, top, bottom = 34, 8, 24, 38
    plot_w = width - left - right
    plot_h = height - top - bottom
    axis_top, step = nice_ceiling(max(values))
    slot = plot_w / len(series)
    bar_w = min(slot * 0.62, 34)

    summary = ', '.join(
        f'{item.get("label", "")} {item.get("year", "")}: {fmt(v)}'.replace(
            ' :', ':')
        for item, v in zip(series, values)
    )
    parts = [
        f'<svg class="bb-chart bb-chart--bars" viewBox="0 0 {width} {height}" '
        'role="img" aria-labelledby="mc-title mc-desc">',
        f'<title id="mc-title">Verified {escape(unit)} per month</title>',
        f'<desc id="mc-desc">{escape(summary)}</desc>',
    ]
    # Gridlines and y labels.
    for i in range(0, axis_top // step + 1):
        value = i * step
        y = top + plot_h - plot_h * value / axis_top
        parts.append(
            f'<line class="bb-axis{" bb-axis--base" if i == 0 else ""}" '
            f'x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="bb-ylabel" x="{left - 6}" y="{y + 3.5:.1f}" '
            f'text-anchor="end">{escape(compact(value))}</text>'
        )
    # Bars, value labels and x labels.
    for index, (item, value) in enumerate(zip(series, values)):
        cx = left + slot * index + slot / 2
        h = plot_h * value / axis_top
        x = cx - bar_w / 2
        label = escape(item.get('label', ''))
        year = item.get('year')
        title = f'{item.get("label", "")} {year or ""}'.strip()
        is_last = index == len(series) - 1
        cls = 'bb-bar' + (' bb-bar--now' if is_last else '')
        if value:
            parts.append(
                f'<rect class="{cls}" x="{x:.1f}" y="{top + plot_h - h:.1f}" '
                f'width="{bar_w:.1f}" height="{h:.1f}" rx="4">'
                f'<title>{escape(title)}: {fmt(value)} {escape(unit)}</title>'
                '</rect>'
            )
            parts.append(
                f'<text class="bb-vlabel" x="{cx:.1f}" '
                f'y="{top + plot_h - h - 5:.1f}" text-anchor="middle">'
                f'{escape(compact(value))}</text>'
            )
        else:
            parts.append(
                f'<rect class="bb-bar bb-bar--zero" x="{x:.1f}" '
                f'y="{top + plot_h - 2:.1f}" width="{bar_w:.1f}" height="2" '
                f'rx="1"><title>{escape(title)}: 0 {escape(unit)}</title></rect>'
            )
        parts.append(
            f'<text class="bb-xlabel" x="{cx:.1f}" y="{height - bottom + 15}" '
            f'text-anchor="middle">{label}</text>'
        )
        if year and (index == 0 or item.get('month') == 1):
            parts.append(
                f'<text class="bb-xyear" x="{cx:.1f}" y="{height - bottom + 28}" '
                f'text-anchor="middle">{escape(year)}</text>'
            )
    parts.append('</svg>')
    return mark_safe(''.join(parts))


# -------------------------------------------------------- horizontal bars

def hbar_rows(items, empty='No data yet.', min_percent=3):
    """Ranked horizontal bars as HTML. Items: ``name``, ``count`` and
    optionally ``url`` (makes the name a link) and ``display`` (text shown
    instead of the count)."""
    items = list(items or [])
    counts = [_safe_int(i.get('count')) for i in items]
    if not items or not any(counts):
        return empty_state(empty, 'list')
    top = max(counts)
    rows = []
    for item, count in zip(items, counts):
        name = escape(item.get('name', ''))
        pct = max(count * 100 / top, min_percent) if count else 0
        label = name
        if item.get('url'):
            label = f'<a href="{escape(item["url"])}">{name}</a>'
        shown = escape(item.get('display') or fmt(count))
        rows.append(
            '<div class="bb-hbar bb-hbar--rank" role="listitem">'
            f'<span class="bb-hbar__label" title="{name}">{label}</span>'
            '<span class="bb-hbar__track" aria-hidden="true">'
            f'<span style="width:{pct:.1f}%"></span></span>'
            f'<span class="bb-hbar__value">{shown}</span></div>'
        )
    return mark_safe(f'<div class="bb-hbars" role="list">{"".join(rows)}</div>')


# ------------------------------------------------------------------ donut

def donut(items, center_label='donations', empty='No verified donations yet.'):
    """Share-of-total ring. Returns ``{'svg', 'legend', 'empty'}``.

    Items need ``label`` and ``count``; slice colours follow item order, so
    keep the order stable between renders.
    """
    items = list(items or [])
    counts = [_safe_int(i.get('count')) for i in items]
    total = sum(counts)
    if not total:
        return {'svg': empty_state(empty, 'pie-chart'), 'legend': '',
                'empty': True, 'total': 0}

    radius, stroke = 70, 28
    circumference = TWO_PI * radius
    summary = ', '.join(
        f'{i.get("label", "")}: {fmt(c)}' for i, c in zip(items, counts) if c
    )
    parts = [
        '<svg class="bb-chart bb-chart--donut" viewBox="0 0 200 200" '
        'role="img" aria-labelledby="dn-title dn-desc">',
        f'<title id="dn-title">Share of {escape(center_label)}</title>',
        f'<desc id="dn-desc">{escape(summary)}</desc>',
        f'<circle class="bb-dn-track" cx="100" cy="100" r="{radius}" '
        f'fill="none" stroke-width="{stroke}"/>',
        '<g transform="rotate(-90 100 100)">',
    ]
    visible = sum(1 for c in counts if c)
    offset = 0.0
    for index, (item, count) in enumerate(zip(items, counts)):
        if not count:
            continue
        length = circumference * count / total
        gap = 1.5 if visible > 1 and length > 4 else 0
        parts.append(
            f'<circle class="bb-dn bb-dn--{index % 8}" cx="100" cy="100" '
            f'r="{radius}" fill="none" stroke-width="{stroke}" '
            f'stroke-dasharray="{max(length - gap, 0.01):.2f} '
            f'{circumference - max(length - gap, 0.01):.2f}" '
            f'stroke-dashoffset="{-offset:.2f}">'
            f'<title>{escape(item.get("label", ""))}: {fmt(count)} '
            f'({count * 100 / total:.0f}%)</title></circle>'
        )
        offset += length
    parts.append('</g>')
    parts.append(
        f'<text class="bb-dn__total" x="100" y="102" text-anchor="middle">'
        f'{escape(compact(total) if total >= 100000 else fmt(total))}</text>'
        f'<text class="bb-dn__sub" x="100" y="122" text-anchor="middle">'
        f'{escape(center_label)}</text></svg>'
    )

    legend = []
    for index, (item, count) in enumerate(zip(items, counts)):
        share = f'{count * 100 / total:.0f}%'
        zero = ' class="is-zero"' if not count else ''
        legend.append(
            f'<li{zero}>'
            f'<span class="bb-swatch bb-swatch--{index % 8}" '
            'aria-hidden="true"></span>'
            f'<span class="bb-legend__name">{escape(item.get("label", ""))}'
            f'</span><span class="bb-legend__val">{fmt(count)}'
            f'<small>{share}</small></span></li>'
        )
    return {
        'svg': mark_safe(''.join(parts)),
        'legend': mark_safe(f'<ul class="bb-legend">{"".join(legend)}</ul>'),
        'empty': False,
        'total': total,
    }


# ------------------------------------------------------ division tile map

# A stylised map of Bangladesh: each division is one tile, placed roughly
# where it sits on the real map (north at the top).
DIVISION_TILES = {
    'Rangpur': (0, 0), 'Mymensingh': (1, 0), 'Sylhet': (2, 0),
    'Rajshahi': (0, 1), 'Dhaka': (1, 1), 'Chattogram': (2, 1),
    'Khulna': (0, 2), 'Barishal': (1, 2),
}
MAP_LEVELS = 4


def _level(count, top):
    if not count or not top:
        return 0
    return min(MAP_LEVELS, max(1, math.ceil(count / top * MAP_LEVELS)))


def division_map(divisions, empty='No verified donations yet.'):
    """Tile-grid map. `divisions`: dicts with ``name`` and ``count``.
    Returns ``{'svg', 'legend', 'empty'}``; unknown division names are ignored.
    """
    counts = {}
    for item in divisions or []:
        if item.get('name') in DIVISION_TILES:
            counts[item['name']] = _safe_int(item.get('count'))
    total = sum(counts.values())
    if not total:
        return {'svg': empty_state(empty, 'map'), 'legend': '', 'empty': True}

    top = max(counts.values())
    tile_w, tile_h, gap = 112, 84, 8
    width = 3 * tile_w + 2 * gap
    height = 3 * tile_h + 2 * gap
    summary = ', '.join(
        f'{name}: {fmt(counts.get(name, 0))}' for name in DIVISION_TILES
    )
    parts = [
        f'<svg class="bb-chart bb-chart--map" viewBox="0 0 {width} {height}" '
        'role="img" aria-labelledby="mp-title mp-desc">',
        '<title id="mp-title">Verified donations by division</title>',
        f'<desc id="mp-desc">{escape(summary)}</desc>',
    ]
    for name, (col, row) in DIVISION_TILES.items():
        count = counts.get(name, 0)
        level = _level(count, top)
        x = col * (tile_w + gap)
        y = row * (tile_h + gap)
        share = f'{count * 100 / total:.0f}%'
        parts.append(
            f'<g class="bb-map__tile bb-map__tile--l{level}">'
            f'<title>{escape(name)}: {fmt(count)} donations ({share})</title>'
            f'<rect x="{x}" y="{y}" width="{tile_w}" height="{tile_h}" rx="14"/>'
            f'<text class="bb-map__name" x="{x + tile_w / 2}" y="{y + 34}" '
            f'text-anchor="middle">{escape(name)}</text>'
            f'<text class="bb-map__count" x="{x + tile_w / 2}" y="{y + 62}" '
            f'text-anchor="middle">{escape(compact(count))}</text></g>'
        )
    parts.append('</svg>')
    swatches = ''.join(
        f'<i class="bb-map__sw bb-map__tile--l{lv}" aria-hidden="true"></i>'
        for lv in range(MAP_LEVELS + 1)
    )
    legend = (
        '<div class="bb-map-legend"><span>Fewer</span>'
        f'{swatches}<span>More donations</span></div>'
    )
    return {'svg': mark_safe(''.join(parts)), 'legend': mark_safe(legend),
            'empty': False}


# ------------------------------------------------------ supply vs demand

def supply_demand_rows(rows, empty='No data yet.'):
    """Per blood group: units still needed against donors listed. Both bars
    share one scale, so a longer red bar than blue means a gap."""
    rows = list(rows or [])
    top = max(
        [_safe_int(r.get('need')) for r in rows]
        + [_safe_int(r.get('donors')) for r in rows] + [0]
    )
    if not rows or not top:
        return empty_state(empty, 'droplet')
    pills = {
        'gap': ('bb-pill--bad', 'Needs donors'),
        'covered': ('bb-pill--ok', 'Covered'),
        'quiet': ('', 'No open requests'),
    }
    out = []
    for row in rows:
        need = _safe_int(row.get('need'))
        donors = _safe_int(row.get('donors'))
        group = escape(row.get('group', ''))
        cls, text = pills.get(row.get('status'), pills['quiet'])
        out.append(
            f'<div class="bb-sd__row bb-sd__row--{escape(row.get("status", "quiet"))}"'
            ' role="listitem">'
            f'<span class="bb-bloodtag bb-bloodtag--sm"><span>{group}</span></span>'
            '<div class="bb-sd__bars">'
            '<div class="bb-sd__bar"><span class="bb-sd__track">'
            f'<span class="bb-sd__fill bb-sd__fill--need" style="width:'
            f'{(max(need * 100 / top, 3) if need else 0):.1f}%"></span></span>'
            f'<span class="bb-sd__txt">{fmt(need)} needed</span></div>'
            '<div class="bb-sd__bar"><span class="bb-sd__track">'
            f'<span class="bb-sd__fill bb-sd__fill--supply" style="width:'
            f'{(max(donors * 100 / top, 3) if donors else 0):.1f}%"></span></span>'
            f'<span class="bb-sd__txt">{fmt(donors)} listed</span></div>'
            '</div>'
            f'<span class="bb-pill {cls}">{escape(text)}</span></div>'
        )
    return mark_safe(f'<div class="bb-sd" role="list">{"".join(out)}</div>')
