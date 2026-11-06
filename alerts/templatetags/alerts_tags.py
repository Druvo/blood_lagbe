from django import template

from alerts import matching
from alerts.models import Notification

register = template.Library()


def _user(context):
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return user
    return None


@register.inclusion_tag('alerts/_hub.html', takes_context=True)
def alerts_hub(context):
    """Panel on the signed-in donor hub (My hub)."""
    user = _user(context)
    if user is None:
        return {}
    return {
        'show': True,
        'latest': list(Notification.objects.filter(user=user)[:3]),
        'unread': context.get('alerts_unread', 0),
    }


@register.inclusion_tag('alerts/_home.html', takes_context=True)
def alerts_home(context):
    """Section on the public home page (nothing to show here)."""
    return {}


@register.inclusion_tag('alerts/_request.html', takes_context=True)
def alerts_request(context):
    """Panel on a blood request page (context has blood_request)."""
    user = _user(context)
    blood_request = context.get('blood_request')
    if user is None or blood_request is None:
        return {}
    if user.pk == blood_request.requester_id:
        if blood_request.status != blood_request.Status.OPEN:
            return {}
        return {
            'mode': 'owner',
            'notified': matching.alerts_sent_for(blood_request),
        }
    if not blood_request.is_active or blood_request.is_flagged:
        return {}
    # Opted-in members get the alerts themselves, so only invite the others.
    # The page already worked out the cooldown for the signed-in viewer.
    if matching.viewer_would_match(
        blood_request, user, eligibility=context.get('eligibility'),
    ):
        return {'mode': 'invite'}
    return {}
