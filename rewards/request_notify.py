"""Email notifications for urgent requests.

Always fail_silently and only for people who have an email on file. Messages
carry names and a link, never a phone number: details are behind the login.
"""
from django.conf import settings
from django.core.mail import send_mass_mail
from django.utils import timezone

from rewards.leaderboard import display_name
from rewards.models import BloodRequest


def _site():
    return getattr(settings, 'SITE_NAME', 'BloodBond')


def _where(blood_request):
    return f'{blood_request.hospital}, {blood_request.district}'


def notify_pledge(blood_request, donor, url):
    """Tell the requester a donor offered to help. Name only, no phone."""
    requester = blood_request.requester
    if not requester.email:
        return 0
    site = _site()
    body = (
        f'{display_name(donor)} said "I can help" on your request for '
        f'{blood_request.blood_group} blood at {_where(blood_request)}.\n\n'
        f'Log in and open your request to see who has offered and how to '
        f'reach them:\n{url}\n\n'
        'Please confirm everything with the hospital blood bank. The '
        f'hospital decides who can donate.\n\n{site}'
    )
    return send_mass_mail(
        [(
            f'{site}: a donor offered to help with your request',
            body, settings.DEFAULT_FROM_EMAIL, [requester.email],
        )],
        fail_silently=True,
    )


def notify_closed(blood_request, url):
    """Tell everyone who pledged that the request is closed."""
    site = _site()
    outcome = (
        blood_request.get_outcome_display() if blood_request.outcome else ''
    )
    recipients = list(
        blood_request.responses
        .exclude(donor__email__isnull=True).exclude(donor__email='')
        .values_list('donor__email', flat=True)
    )
    messages = []
    for email in recipients:
        body = (
            f'The request for {blood_request.blood_group} blood at '
            f'{_where(blood_request)} has been closed.\n'
        )
        if outcome:
            body += f'Outcome: {outcome}.\n'
        body += (
            '\nThank you for offering to help. If you already donated for '
            'it, you can log the donation so it counts toward your '
            f'badges.\n{url}\n\n{site}'
        )
        messages.append((
            f'{site}: a request you offered to help with is closed',
            body, settings.DEFAULT_FROM_EMAIL, [email],
        ))
    if not messages:
        return 0
    return send_mass_mail(messages, fail_silently=True)


def close_and_notify(blood_request, outcome, url):
    """Close a request and email responders, once."""
    # Decide in the UPDATE itself so two simultaneous closes (a double click,
    # or the owner and an admin) email the responders only once.
    now = timezone.now()
    changed = BloodRequest.objects.filter(pk=blood_request.pk).exclude(
        status=BloodRequest.Status.CLOSED,
    ).update(
        status=BloodRequest.Status.CLOSED, outcome=outcome, closed_at=now,
    )
    if not changed:
        return False
    blood_request.status = BloodRequest.Status.CLOSED
    blood_request.outcome = outcome
    blood_request.closed_at = now
    notify_closed(blood_request, url)
    return True
