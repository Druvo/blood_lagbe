"""Template tags for BloodBond's illustration system.

`{% badge_art badge 56 %}` draws a layered metallic medal for a Badge. The
shape (not just the colour) says which tier it is, so the medal reads without
colour: circle, hexagon, scalloped seal, gem and crowned shield for the five
tiers, plus a squircle for streaks and a plain shield for hero badges. The
metal comes from the Badge's own `color` field. That value is user-editable
data, so it is only used after it matches a strict hex pattern; anything else
falls back to the tier's default metal. Nothing from the database is placed in
the SVG as-is, and the optional label is escaped.

`{% progress_ring pct 72 %}` draws a circular progress indicator (XP,
recovery).
"""
import itertools
import math
import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

_HEX = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')
_ids = itertools.count(1)

DEFAULT_COLORS = {
    'bronze': '#cd7f32',
    'silver': '#9aa0a6',
    'gold': '#d4af37',
    'platinum': '#7c4dff',
    'legend': '#00b894',
    'streak': '#e8590c',
    'hero': '#d8213a',
    'plain': '#cd7f32',
}


def _safe_hex(value, fallback):
    """A 6-digit lowercase hex colour, or `fallback` when `value` is not one."""
    value = (value or '').strip() if isinstance(value, str) else ''
    if not _HEX.match(value):
        value = fallback
    value = value.lower()
    if len(value) == 4:
        value = '#' + ''.join(ch * 2 for ch in value[1:])
    return value


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _mix(hex_color, other, amount):
    """Blend `hex_color` towards `other` (0..255 channels) by `amount`."""
    r, g, b = _rgb(hex_color)
    o = other
    return '#%02x%02x%02x' % tuple(
        int(round(c + (o - c) * amount)) for c in (r, g, b))


def _luminance(hex_color):
    def lin(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _tier_key(badge):
    """Which medal shape a badge gets."""
    if isinstance(badge, str):
        name, kind, count = badge, 'tier', None
    elif isinstance(badge, dict):
        name = badge.get('name') or badge.get('tier') or ''
        kind = badge.get('badge_type') or 'tier'
        count = badge.get('min_donations')
    else:
        name = getattr(badge, 'name', '') or ''
        kind = getattr(badge, 'badge_type', '') or 'tier'
        count = getattr(badge, 'min_donations', None)
    if kind == 'streak':
        return 'streak'
    if kind == 'hero':
        return 'hero'
    lowered = str(name).lower()
    for key in ('bronze', 'silver', 'gold', 'platinum', 'legend'):
        if key in lowered:
            return key
    if isinstance(count, int):
        for key, floor in (('legend', 50), ('platinum', 25), ('gold', 10), ('silver', 5)):
            if count >= floor:
                return key
        return 'bronze'
    return 'plain'


def _badge_color(badge):
    if isinstance(badge, dict):
        return badge.get('color')
    return getattr(badge, 'color', None)


def _seal_path(points=12, outer=30.0, inner=26.0, cx=32.0, cy=32.0):
    coords = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = math.pi * i / points - math.pi / 2
        coords.append('%.1f %.1f' % (cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return 'M' + ' L'.join(coords) + 'Z'


_SEAL = _seal_path()
_SEAL_INNER = _seal_path(outer=24.0, inner=21.0)

# Outer shape and an inset shape drawn in the light face colour (the "bevel").
_SHAPES = {
    'bronze': ('<circle cx="32" cy="32" r="29"/>', '<circle cx="32" cy="32" r="23"/>'),
    'silver': ('<path d="M32 2.5 57.5 17v30L32 61.500 6.500 47V17Z"/>',
               '<path d="M32 9 51.800 20.500v23L32 55 12.200 43.500v-23Z"/>'),
    'gold': ('<path d="%s"/>' % _SEAL, '<path d="%s"/>' % _SEAL_INNER),
    'platinum': ('<path d="M20 6h24l15 16-27 37L5 22Z"/>',
                 '<path d="M23 12h18l10.500 11.500L32 50 12.500 23.500Z"/>'),
    'legend': ('<path d="M32 15 51 19.500v14C51 46 42.500 55 32 61 21.500 55 13 46 13 33.500v-14Z"/>',
               '<path d="M32 21 45 24v10c0 8.500-5.500 15-13 19.500C24.500 49 19 42.500 19 34V24Z"/>'),
    'streak': ('<rect x="5" y="5" width="54" height="54" rx="19"/>',
               '<rect x="11" y="11" width="42" height="42" rx="14"/>'),
    'hero': ('<path d="M32 3 55 11v20c0 14-9.500 24-23 30C18.500 55 9 45 9 31V11Z"/>',
             '<path d="M32 10 49 16v15c0 10.500-7 18.500-17 23.500C22 49.500 15 41.500 15 31V16Z"/>'),
    'plain': ('<circle cx="32" cy="32" r="29"/>', '<circle cx="32" cy="32" r="23"/>'),
}

# Engraved emblem drawn in the centre (64 x 64 space).
_DROP = '<path d="M32 17c0 0-9.500 10.500-9.500 18a9.500 9.500 0 0 0 19 0C41.500 27.500 32 17 32 17Z"/>'
_HEART = ('<path d="M32 46c-8.500-5.600-13-9.800-13-14.600a6.600 6.600 0 0 1 13-2 '
          '6.600 6.600 0 0 1 13 2C45 36.200 40.500 40.400 32 46Z"/>')
_FLAME = ('<path d="M33 14c1 6-3 8.500-5 12-2.500 4.200-.5 9.500 2 11-.5-3 1.500-5 3-6.500 '
          '2 2 5.500 4.500 2.500 10.500 6-2 9-8.500 6-14.500-1.500-3-4-4-5-7-.5-2-1.500-4-3.500-5.500Z" '
          'transform="translate(-4.500 1.500)"/>')
_CROWN = '<path d="M20 27l5 5 7-9 7 9 5-5-2 10H22Z" transform="translate(0 4)"/>'
_PLUS = '<path d="M29 22h6v7h7v6h-7v7h-6v-7h-7v-6h7Z"/>'
_EMBLEMS = {
    'bronze': _DROP,
    'silver': _DROP,
    'gold': _DROP,
    'platinum': _DROP,
    'legend': _CROWN,
    'streak': _FLAME,
    'hero': _HEART,
    'plain': _DROP,
}
# Small extra marks that make neighbouring tiers differ at a glance.
_PIPS = {
    'bronze': 1, 'silver': 2, 'gold': 3, 'platinum': 4, 'legend': 5,
}


def _pips(key, ink):
    count = _PIPS.get(key)
    if not count or key == 'legend':
        return ''
    start = 32 - (count - 1) * 3.5
    dots = ''.join(
        '<circle cx="%.1f" cy="48" r="1.5"/>' % (start + i * 7) for i in range(count))
    return '<g fill="%s" opacity=".85">%s</g>' % (ink, dots)


@register.simple_tag
def badge_art(badge, size=48, label=''):
    """Inline SVG medal for a Badge (or a tier name such as "Gold")."""
    key = _tier_key(badge)
    base = _safe_hex(_badge_color(badge), DEFAULT_COLORS[key])
    try:
        px = max(16, min(int(size), 320))
    except (TypeError, ValueError):
        px = 48
    uid = next(_ids)
    light = _mix(base, 255, .62)
    sheen = _mix(base, 255, .3)
    dark = _mix(base, 0, .32)
    deep = _mix(base, 0, .55)
    # Engraving contrasts with the face: dark on light metals, pale on dark ones.
    ink = _mix(base, 0, .66) if _luminance(base) > .2 else _mix(base, 255, .92)
    outer, inner = _SHAPES[key]
    ribbon = ''
    if key in ('bronze', 'gold'):
        ribbon = ('<path d="M20 50 14 63l7-3 3 6 5-14Z" fill="%s"/>'
                  '<path d="M44 50 50 63l-7-3-3 6-5-14Z" fill="%s"/>' % (dark, deep))
    label = (label or '').strip()
    a11y = ('role="img" aria-label="%s"' % escape(label)) if label else 'aria-hidden="true" focusable="false"'
    svg = (
        '<svg class="bb-medal bb-medal--%(key)s" xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 64 68" width="%(px)d" height="%(h)d" %(a11y)s>'
        '<defs>'
        '<linearGradient id="bm%(uid)dr" x1="8" y1="2" x2="56" y2="64" gradientUnits="userSpaceOnUse">'
        '<stop offset="0" stop-color="%(light)s"/><stop offset=".35" stop-color="%(base)s"/>'
        '<stop offset=".6" stop-color="%(sheen)s"/><stop offset="1" stop-color="%(deep)s"/></linearGradient>'
        '<linearGradient id="bm%(uid)df" x1="14" y1="8" x2="50" y2="58" gradientUnits="userSpaceOnUse">'
        '<stop offset="0" stop-color="%(sheen)s"/><stop offset=".45" stop-color="%(base)s"/>'
        '<stop offset="1" stop-color="%(dark)s"/></linearGradient>'
        '</defs>'
        '%(ribbon)s'
        '<g fill="url(#bm%(uid)dr)" stroke="%(deep)s" stroke-width="1.2" stroke-linejoin="round">%(outer)s</g>'
        '<g fill="url(#bm%(uid)df)" stroke="%(light)s" stroke-opacity=".7" stroke-width="1">%(inner)s</g>'
        '<g fill="%(ink)s" stroke="%(light)s" stroke-opacity=".55" stroke-width=".6">%(emblem)s</g>'
        '%(pips)s'
        '</svg>'
    ) % {
        'key': key, 'px': px, 'h': round(px * 68 / 64), 'a11y': a11y, 'uid': uid,
        'light': light, 'base': base, 'sheen': sheen, 'dark': dark, 'deep': deep,
        'ink': ink, 'ribbon': ribbon, 'outer': outer, 'inner': inner,
        'emblem': _EMBLEMS[key], 'pips': _pips(key, ink),
    }
    return mark_safe(svg)


@register.simple_tag
def progress_ring(percent, size=88, label=''):
    """Circular progress. `percent` is clamped to 0..100; `label` is escaped."""
    try:
        pct = max(0, min(100, int(float(percent))))
    except (TypeError, ValueError):
        pct = 0
    try:
        px = max(32, min(int(size), 240))
    except (TypeError, ValueError):
        px = 88
    radius = 42
    circumference = 2 * math.pi * radius
    offset = circumference * (1 - pct / 100)
    uid = next(_ids)
    text = escape(label) if label else '%d%%' % pct
    svg = (
        '<svg class="bb-ring" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
        'width="%(px)d" height="%(px)d" aria-hidden="true" focusable="false">'
        '<defs><linearGradient id="br%(uid)d" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" style="stop-color:var(--bb-rose)"/><stop offset="1" style="stop-color:var(--bb-red)"/>'
        '</linearGradient></defs>'
        '<circle class="bb-ring__track" cx="50" cy="50" r="42" fill="none" stroke-width="9"/>'
        '<circle class="bb-ring__bar" cx="50" cy="50" r="42" fill="none" stroke-width="9" '
        'stroke="url(#br%(uid)d)" stroke-linecap="round" transform="rotate(-90 50 50)" '
        'stroke-dasharray="%(c).2f" stroke-dashoffset="%(o).2f" style="--ring-c:%(c).2f"/>'
        '<text class="bb-ring__text" x="50" y="50" text-anchor="middle" dominant-baseline="central">%(t)s</text>'
        '</svg>'
    ) % {'px': px, 'uid': uid, 'c': circumference, 'o': offset, 't': text}
    return mark_safe(svg)
