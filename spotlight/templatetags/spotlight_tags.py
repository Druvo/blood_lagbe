from django import template

from rewards.models import Donation
from spotlight import heroes, pulse, styles
from spotlight.views import month_label

register = template.Library()

CERTIFICATES_ON_HUB = 3


# Extension points. The shared templates call these three tags; each
# renders the matching template. The request panel stays empty on purpose.


def _user(context):
    request = context.get('request')
    user = getattr(request, 'user', None) or context.get('user')
    return user if user is not None and user.is_authenticated else None


@register.inclusion_tag('spotlight/_hub.html', takes_context=True)
def spotlight_hub(context):
    """Panel on the signed-in donor hub (My hub): style, last award,
    certificate links and the ready-again calendar button."""
    user = _user(context)
    if user is None:
        return {}
    # One query: recent donations that are not rejected. Verified ones get a
    # certificate link; any of them means a ready-again date exists.
    recent = list(
        Donation.objects.filter(donor=user)
        .exclude(status=Donation.Status.REJECTED)
        .order_by('-donation_date', '-pk')[:30]
    )
    certificates = [
        d for d in recent if d.status == Donation.Status.APPROVED
    ][:CERTIFICATES_ON_HUB]
    award = heroes.last_award_for(user)
    return {
        'show': True,
        'style': styles.style_for(user, use_cache=True),
        'certificates': certificates,
        'has_donation': bool(recent),
        'award': award,
        'award_title': heroes.KIND_INFO[award.kind]['title'] if award else '',
        'award_label': month_label(award.year, award.month) if award else '',
    }


@register.inclusion_tag('spotlight/_home.html', takes_context=True)
def spotlight_home(context):
    """Public home page: the live feed (three or more events) and this
    month's heroes."""
    events = pulse.pulse()
    entries = heroes.current_heroes()
    for entry in entries:
        entry['title'] = heroes.KIND_INFO[entry['kind']]['title']
    return {
        'events': events,
        'show_feed': pulse.show_panel(events),
        'heroes': entries,
        'refresh': pulse.CACHE_SECONDS,
    }


@register.inclusion_tag('spotlight/_request.html', takes_context=True)
def spotlight_request(context):
    """Panel on a blood request page. Intentionally empty."""
    return {}
