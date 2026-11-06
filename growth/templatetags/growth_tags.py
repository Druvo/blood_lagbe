from django import template
from django.conf import settings

from growth import services, share, trust

register = template.Library()


# Extension points. The shared templates call these three tags; each
# renders the matching template. Keep each panel compact.


def _user(context):
    request = context.get('request')
    user = getattr(request, 'user', None)
    return request, user if user is not None and user.is_authenticated else None


@register.inclusion_tag('growth/_hub.html', takes_context=True)
def growth_hub(context):
    """Panel on the signed-in donor hub (My hub): invite a friend."""
    request, user = _user(context)
    base = {'SITE_NAME': settings.SITE_NAME, 'user': user}
    if user is None:
        return base
    invite = services.get_or_create_invite(user)
    return dict(
        base,
        invite=invite,
        share=share.invite_share(request, invite),
        stats=services.referral_stats(user),
        public_profile=user.profile.public_profile,
    )


@register.inclusion_tag('growth/_home.html', takes_context=True)
def growth_home(context):
    """Section on the public home page: invite card for members, plus the
    install prompt script for everyone."""
    request, user = _user(context)
    base = {'SITE_NAME': settings.SITE_NAME, 'user': user}
    if user is None:
        return base
    invite = services.get_or_create_invite(user)
    return dict(base, invite=invite, share=share.invite_share(request, invite))


@register.inclusion_tag('growth/_request.html', takes_context=True)
def growth_request(context):
    """Panel on a blood request page (context has blood_request): the
    verified-hospital chip and the extra share options."""
    request = context.get('request')
    blood_request = context.get('blood_request')
    if blood_request is None or request is None or blood_request.is_flagged:
        return {}
    return {
        'SITE_NAME': settings.SITE_NAME,
        'verified': trust.verified_hospital_for(blood_request),
        # Sharing only makes sense while people can still help.
        'share': (
            share.request_share(request, blood_request)
            if blood_request.is_active else None
        ),
    }


@register.filter
def is_verified_hospital(blood_request):
    """{{ blood_request|is_verified_hospital }}"""
    return trust.is_verified_hospital(blood_request)
