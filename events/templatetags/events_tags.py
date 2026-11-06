from django import template

register = template.Library()


# Extension points. The shared templates call these three tags; each
# renders the matching template (empty by default). Fill in the
# context and the template; do not edit the shared templates.


@register.inclusion_tag('events/_hub.html', takes_context=True)
def events_hub(context):
    """Panel on the signed-in donor hub (My hub)."""
    return {}


@register.inclusion_tag('events/_home.html', takes_context=True)
def events_home(context):
    """Section on the public home page."""
    return {}


@register.inclusion_tag('events/_request.html', takes_context=True)
def events_request(context):
    """Panel on a blood request page (context has blood_request)."""
    return {}
