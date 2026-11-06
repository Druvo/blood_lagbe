from django import template

from habits import services, titles

register = template.Library()


def _member(context):
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return user
    return None


# Extension points. The shared templates call these three tags; each
# renders the matching template.


@register.inclusion_tag('habits/_hub.html', takes_context=True)
def habits_hub(context):
    """Panel on the signed-in donor hub (My hub)."""
    user = _member(context)
    if user is None:
        return {}
    info = services.summary(user)
    all_titles = titles.all_titles(user)
    pending = [t for t in all_titles if not t.earned]
    return {
        'member': user,
        'done_count': info['done_count'],
        'total': info['total'],
        'streak': info['streak'],
        'earned_titles': [t for t in all_titles if t.earned],
        'next_title': max(pending, key=lambda t: t.percent) if pending else None,
    }


@register.inclusion_tag('habits/_home.html', takes_context=True)
def habits_home(context):
    """Teaser on the public home page. Signed-in visitors only."""
    user = _member(context)
    if user is None:
        return {}
    info = services.summary(user)
    return {
        'member': user,
        'done_count': info['done_count'],
        'total': info['total'],
        'streak': info['streak'],
    }


@register.inclusion_tag('habits/_request.html', takes_context=True)
def habits_request(context):
    """Panel on a blood request page (context has blood_request). Nothing to
    show: sharing is tracked through the Today page's link."""
    return {}
