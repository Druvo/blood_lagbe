from django import template

from rewards.leaderboard import display_name

register = template.Library()


@register.filter
def author_name(author):
    """Public author name. Author.__str__ is the login phone number, which
    must never appear on a public page."""
    return display_name(author.author)
