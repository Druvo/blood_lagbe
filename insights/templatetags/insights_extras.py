from django import template

from insights import charts

register = template.Library()


@register.filter
def commas(value):
    """12345 -> 12,345"""
    return charts.fmt(value)


@register.filter
def absval(value):
    try:
        return abs(int(value))
    except (TypeError, ValueError):
        return 0
