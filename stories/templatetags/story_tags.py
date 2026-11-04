from django import template
from django.db.models import Count

from stories.models import Story

register = template.Library()


@register.simple_tag
def latest_stories(limit=3):
    """The newest approved stories, in one query (author and profile are
    joined so names and the leaderboard opt-out cost nothing extra).

    Usage: {% load story_tags %}{% latest_stories as stories %}
    """
    return list(
        Story.objects.approved()
        .select_related('author', 'author__profile')
        .annotate(reaction_total=Count('reactions', distinct=True))
        .order_by('-approved_at', '-id')[:limit]
    )
