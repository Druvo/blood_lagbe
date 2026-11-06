from django import template

from atlas import data, geo, layers, render as svg

register = template.Library()


# Extension points. The shared templates call these three tags; each
# renders the matching template (empty by default). Fill in the
# context and the template; do not edit the shared templates.


@register.inclusion_tag('atlas/_hub.html', takes_context=True)
def atlas_hub(context):
    """Panel on the signed-in donor hub (My hub): the member's district."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    profile = getattr(user, 'profile', None)
    district = getattr(profile, 'district', '')
    if district not in geo.TILES:
        return {}
    from atlas.views import _drives
    gap = data.gap(district)
    group = profile.blood_group
    return {
        'name': district,
        'slug': geo.SLUGS[district],
        'requests': data.demand_for(district)['requests'],
        'drives': len(_drives(district)),
        'group': group,
        'group_short': bool(group) and group in gap['short'],
    }


@register.inclusion_tag('atlas/_home.html', takes_context=True)
def atlas_home(context):
    """Section on the public home page: a small demand map and a link."""
    need = data.demand()
    requests = need['national']['requests']
    ctx = {'requests': requests, 'districts': len(need['districts'])}
    if requests:
        ctx['mini'] = svg.tile_map(layers.build(layers.DEMAND), compact=True)
    return ctx


@register.inclusion_tag('atlas/_request.html', takes_context=True)
def atlas_request(context):
    """Panel on a blood request page (context has blood_request): a line that
    points to the other open requests in the same district."""
    blood_request = context.get('blood_request')
    if blood_request is None or blood_request.district not in geo.TILES:
        return {}
    active = context.get('active')
    if active is None:
        active = blood_request.is_active
    if not active:
        return {}
    from atlas.views import count_other_open
    others = count_other_open(blood_request)
    if others <= 0:
        return {}
    return {
        'name': blood_request.district,
        'slug': geo.SLUGS[blood_request.district],
        'others': others,
    }
