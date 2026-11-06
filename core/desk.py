"""Staff desk: one page that says what needs a moderator's attention.

Counts only, with links into the admin changelists. No member data is shown
here, so the page cannot leak anything the admin does not already show.
"""
import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone
from django.shortcuts import render

from accounts.models import Profile
from events.models import Event
from rewards.models import BloodRequest, Donation, RequestReport
from stories.models import Story


def _admin(name, query=''):
    return reverse(f'admin:{name}_changelist') + query


@login_required
def desk(request):
    if not request.user.is_staff:
        raise PermissionDenied
    now = timezone.now()
    week_ago = now - datetime.timedelta(days=7)
    today = timezone.localdate()

    queue = [
        {
            'label': 'Donations to verify',
            'count': Donation.objects.filter(
                status=Donation.Status.PENDING).count(),
            'url': _admin('rewards_donation', '?status__exact=pending'),
            'hint': 'Only verified donations count for points and tiers.',
        },
        {
            'label': 'Open request reports',
            'count': RequestReport.objects.filter(cleared=False).count(),
            'url': _admin('rewards_requestreport', '?cleared__exact=0'),
            'hint': 'Three open reports hide a request from the board.',
        },
        {
            'label': 'Stories to review',
            'count': Story.objects.filter(
                status=Story.Status.PENDING).count(),
            'url': _admin('stories_story', '?status__exact=pending'),
            'hint': 'Stories appear publicly only once approved.',
        },
        {
            'label': 'Blood drives to approve',
            'count': Event.objects.filter(
                status=Event.Status.PENDING).count(),
            'url': _admin('events_event', '?status__exact=pending'),
            'hint': 'Drives stay private until approved.',
        },
    ]
    pulse = [
        {'label': 'New members this week',
         'value': Profile.objects.filter(created_at__gte=week_ago).count()},
        {'label': 'Verified donations this week',
         'value': Donation.objects.filter(
             status=Donation.Status.APPROVED,
             donation_date__gte=today - datetime.timedelta(days=7),
         ).count()},
        {'label': 'Requests still needing blood',
         'value': BloodRequest.objects.still_needed().count()},
        {'label': 'Upcoming approved drives',
         'value': Event.objects.filter(
             status=Event.Status.APPROVED, starts_at__gte=now).count()},
    ]
    return render(request, 'core/desk.html', {
        'queue': queue,
        'pulse': pulse,
        'waiting': sum(item['count'] for item in queue),
        'admin_url': reverse('admin:index'),
    })
