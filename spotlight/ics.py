"""A minimal, valid iCalendar (RFC 5545) file with one all-day event.

Lines end in CRLF, text values are escaped and long lines are folded at 75
octets without splitting a character.
"""
import datetime

CRLF = '\r\n'
BACKSLASH = '\\'


def escape_text(value):
    value = (value or '').replace(BACKSLASH, BACKSLASH * 2)
    value = value.replace(';', BACKSLASH + ';').replace(',', BACKSLASH + ',')
    for newline in ('\r\n', '\n', '\r'):
        value = value.replace(newline, BACKSLASH + 'n')
    return value


def fold(line):
    if len(line.encode('utf-8')) <= 75:
        return line
    parts, current, limit = [], b'', 75
    for ch in line:
        encoded = ch.encode('utf-8')
        if len(current) + len(encoded) > limit:
            parts.append(current.decode('utf-8'))
            current, limit = b'', 74  # continuation lines start with a space
        current += encoded
    parts.append(current.decode('utf-8'))
    return (CRLF + ' ').join(parts)


def all_day_calendar(*, uid, day, summary, description, site_name, now):
    """One all-day event on ``day`` (a date), as ICS text."""
    nxt = day + datetime.timedelta(days=1)
    stamp = now.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        f'PRODID:-//{escape_text(site_name)}//Spotlight//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        f'UID:{uid}',
        f'DTSTAMP:{stamp}',
        f'DTSTART;VALUE=DATE:{day:%Y%m%d}',
        f'DTEND;VALUE=DATE:{nxt:%Y%m%d}',
        f'SUMMARY:{escape_text(summary)}',
        f'DESCRIPTION:{escape_text(description)}',
        'TRANSP:TRANSPARENT',
        'END:VEVENT',
        'END:VCALENDAR',
    ]
    return CRLF.join(fold(line) for line in lines) + CRLF
