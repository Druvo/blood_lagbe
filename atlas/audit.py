"""Paths of the atlas pages for the privacy and accessibility crawls.

Not a test module (no ``test`` prefix). ``paths(cast)`` takes the shared cast
of `rewards.audit_fixtures.build_cast` and returns ``[(url_name, path), ...]``
for every GET page. It builds a few extra rows with ``get_or_create`` so the
aggregates have something to show: an approved donation this month by an
opted-out member, and an open request in a second district.

URL names that are POST-only or need a token: none. Every atlas URL is a plain
GET. Query keys used by these pages: ``layer`` (map) and ``district`` (gap).
"""
import datetime

from django.urls import reverse
from django.utils import timezone

from rewards.models import BloodRequest, Donation


def paths(cast):
    from rewards.audit_fixtures import HOSPITAL

    u = cast.users
    today = timezone.localdate()
    Donation.objects.get_or_create(
        donor=u['hidden'], donation_date=today, status=Donation.Status.APPROVED,
        defaults=dict(
            blood_group='A+', district='Dhaka', location=HOSPITAL,
        ),
    )
    BloodRequest.objects.get_or_create(
        requester=u['owner'], hospital=HOSPITAL, district='Sylhet',
        defaults=dict(
            blood_group='O+', units_needed=1, contact_phone='01966600009',
            needed_by=today + datetime.timedelta(days=2),
        ),
    )
    out = [
        ('atlas:map', reverse('atlas:map')),
        ('atlas:gap', reverse('atlas:gap')),
        ('atlas:gap', reverse('atlas:gap') + '?district=Dhaka'),
        ('atlas:divisions', reverse('atlas:divisions')),
        ('atlas:data-csv', reverse('atlas:data-csv')),
    ]
    for layer in ('demand', 'donors', 'donations', 'gap'):
        out.append(('atlas:map', f'{reverse("atlas:map")}?layer={layer}'))
    for slug in ('dhaka', 'sylhet', 'coxs-bazar'):
        out.append(('atlas:district', reverse('atlas:district', args=[slug])))
    return out
