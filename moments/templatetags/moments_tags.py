from django import template

from moments import services
from rewards.leaderboard import display_name
from rewards.models import BloodRequest

register = template.Library()


# Extension points. The shared templates call these three tags; each
# renders the matching template (empty by default). Fill in the
# context and the template; do not edit the shared templates.


@register.inclusion_tag('moments/_hub.html', takes_context=True)
def moments_hub(context):
    """Panel on the signed-in donor hub (My hub)."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    known = {
        key: context[key]
        for key in ('current_badge', 'approved_count', 'eligibility')
        if key in context
    }
    panel = services.hub_panel(user, known)
    if panel is None:
        return {}
    panel.update({
        'show': True,
        'csrf_token': context.get('csrf_token'),
        'hub_user': user,
        'next_url': request.path,
    })
    return panel


@register.inclusion_tag('moments/_home.html', takes_context=True)
def moments_home(context):
    """Section on the public home page."""
    return {}


@register.inclusion_tag('moments/_request.html', takes_context=True)
def moments_request(context):
    """Panel on a blood request page (context has blood_request)."""
    request = context.get('request')
    blood_request = context.get('blood_request')
    if blood_request is None:
        return {}
    user = getattr(request, 'user', None)
    is_open = blood_request.status == BloodRequest.Status.OPEN
    ctx = {
        'show': False,
        'csrf_token': context.get('csrf_token'),
        'blood_request': blood_request,
    }
    if is_open:
        ctx.update({
            'show': True,
            'pulse': services.pulse_counts(
                blood_request, people=context.get('response_count'),
            ),
        })
    elif user is not None and services.can_thank(user, blood_request):
        done = services.thanked_ids(blood_request)
        responders = [
            {
                'id': donor.pk, 'name': display_name(donor),
                'thanked': donor.pk in done,
            }
            for donor in services.responders_of(blood_request)
        ]
        ctx.update({
            'show': True,
            'thank': {
                'responders': responders,
                'open_count': sum(1 for r in responders if not r['thanked']),
                'sent_count': len(done),
            },
        })
    return ctx
