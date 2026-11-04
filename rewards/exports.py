"""CSV export of a donor's own donation history.

Safe to open in Excel: the file starts with a UTF-8 byte order mark so Bangla
place names display correctly, and any cell that a spreadsheet could read as
a formula is prefixed with a quote. Phone numbers are never included.
"""
import csv

from django.http import HttpResponse

CSV_FILENAME = 'donation-history.csv'
CSV_HEADER = ['Date', 'Blood group', 'District', 'Place', 'Status']

# Characters that make Excel, Sheets or LibreOffice treat a cell as a formula.
_FORMULA_STARTS = ('=', '+', '-', '@', '\t', '\r', '\n')


def csv_safe(value):
    """Neutralise CSV/formula injection: a leading = + - @ (also after
    spaces, or a tab/newline) gets a quote prefix so it stays plain text."""
    text = '' if value is None else str(value)
    if text and (
        text.startswith(_FORMULA_STARTS)
        or text.lstrip().startswith(_FORMULA_STARTS)
    ):
        return "'" + text
    return text


def donation_row(donation):
    return [
        csv_safe(donation.donation_date.isoformat()),
        csv_safe(donation.blood_group),
        csv_safe(donation.district),
        csv_safe(donation.location),
        csv_safe(donation.get_status_display()),
    ]


def donations_csv_response(donations):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{CSV_FILENAME}"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(CSV_HEADER)
    for donation in donations:
        writer.writerow(donation_row(donation))
    return response
