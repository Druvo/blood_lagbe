"""A minimal, valid iCalendar (RFC 5545) file for one event.

Times are written in UTC (``...Z``), so every calendar app shows them at the
right local time, Asia/Dhaka included. Text values are escaped and long
lines folded at 75 octets.
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
    """Fold a content line to at most 75 octets per physical line, never
    splitting a multi-byte character."""
    if len(line.encode('utf-8')) <= 75:
        return line
    parts = []
    current = b''
    limit = 75
    for ch in line:
        encoded = ch.encode('utf-8')
        if len(current) + len(encoded) > limit:
            parts.append(current.decode('utf-8'))
            current = b''
            limit = 74  # continuation lines start with a space
        current += encoded
    parts.append(current.decode('utf-8'))
    return (CRLF + ' ').join(parts)


def _utc(value):
    return value.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def build_calendar(event, url, uid_domain, site_name, now):
    status = 'CANCELLED' if event.status == 'cancelled' else 'CONFIRMED'
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        f'PRODID:-//{escape_text(site_name)}//Events//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        f'UID:event-{event.pk}@{uid_domain}',
        f'DTSTAMP:{_utc(now)}',
        f'DTSTART:{_utc(event.starts_at)}',
        f'DTEND:{_utc(event.effective_end)}',
        f'SUMMARY:{escape_text(event.title)}',
        f'LOCATION:{escape_text(event.where)}',
        f'STATUS:{status}',
        f'URL:{url}',
    ]
    note = 'Eligibility and the donation interval are reminders, not medical advice.'
    description = event.description.strip()
    description = f'{description}\n\n{note}' if description else note
    lines.append(f'DESCRIPTION:{escape_text(description)}')
    lines += ['END:VEVENT', 'END:VCALENDAR']
    return CRLF.join(fold(line) for line in lines) + CRLF
