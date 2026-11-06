from urllib.parse import parse_qsl, urlencode

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def language_next(context):
    """Where the language switcher sends the visitor back to: the current
    path with its query string, so filters and pages are kept.

    Two things are deliberately not echoed into the page: a `next` query
    parameter (it may carry an unsafe address), and the path of a page that
    does not exist (error pages stay generic); those go back to the home page.
    """
    request = context.get('request')
    if request is None or getattr(request, 'resolver_match', None) is None:
        return '/'
    query = [(k, v) for k, v in parse_qsl(request.META.get('QUERY_STRING', ''),
                                          keep_blank_values=True) if k != 'next']
    path = request.path
    return path + ('?' + urlencode(query) if query else '')
