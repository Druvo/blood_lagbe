from django.conf import settings
from django.contrib import admin, messages
from django.core.mail import send_mail
from django.db.models import Count
from django.utils import timezone

from stories.models import Story, StoryReaction, StoryReport


def _notify_author(story, approved):
    email = story.author.email
    if not email:
        return
    site = settings.SITE_NAME
    if approved:
        subject = f'Your story is now live on {site}'
        message = (
            f'Thank you for sharing "{story.title}". It has been reviewed '
            f'and is now visible to the {site} community.'
        )
    else:
        subject = f'About your story on {site}'
        message = (
            f'Thank you for sharing "{story.title}". We could not publish '
            f'it this time.'
        )
        if story.moderation_note:
            message += f'\n\nNote from our team: {story.moderation_note}'
    send_mail(
        subject=subject, message=message, from_email=None,
        recipient_list=[email], fail_silently=True,
    )


@admin.action(description='Approve selected stories')
def approve_stories(modeladmin, request, queryset):
    stories = list(
        queryset.exclude(status=Story.Status.APPROVED)
        .select_related('author')
    )
    for story in stories:
        story.approve()
        _notify_author(story, approved=True)
    modeladmin.message_user(
        request, f'{len(stories)} {"story" if len(stories) == 1 else "stories"} approved.',
        messages.SUCCESS,
    )


@admin.action(description='Reject selected stories')
def reject_stories(modeladmin, request, queryset):
    stories = list(
        queryset.exclude(status=Story.Status.REJECTED).select_related('author')
    )
    for story in stories:
        story.reject()
        _notify_author(story, approved=False)
    modeladmin.message_user(
        request, f'{len(stories)} {"story" if len(stories) == 1 else "stories"} rejected.',
        messages.SUCCESS,
    )


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'author', 'kind', 'district', 'anonymous', 'status',
        'report_count', 'created_at', 'approved_at',
    )
    list_filter = ('status', 'kind', 'anonymous', 'district')
    search_fields = (
        'title', 'body', 'author__first_name', 'author__last_name',
        'author__phone', 'author__email',
    )
    readonly_fields = ('created_at', 'approved_at')
    raw_id_fields = ('author', 'donation')
    actions = [approve_stories, reject_stories]
    date_hierarchy = 'created_at'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('author').annotate(
            _reports=Count('reports', distinct=True),
        )

    @admin.display(description='Reports', ordering='_reports')
    def report_count(self, obj):
        return obj._reports

    def save_model(self, request, obj, form, change):
        # Keep approved_at honest when status is flipped in the edit form.
        was = None
        if change:
            was = Story.objects.filter(pk=obj.pk).values_list(
                'status', flat=True,
            ).first()
        if obj.status == Story.Status.APPROVED and was != obj.status:
            obj.approved_at = timezone.now()
        elif obj.status != Story.Status.APPROVED:
            obj.approved_at = None
        super().save_model(request, obj, form, change)
        if change and was != obj.status and obj.status in (
            Story.Status.APPROVED, Story.Status.REJECTED,
        ):
            _notify_author(obj, approved=obj.is_approved)


@admin.register(StoryReport)
class StoryReportAdmin(admin.ModelAdmin):
    list_display = ('story', 'reporter', 'reason', 'created_at')
    list_filter = ('reason',)
    raw_id_fields = ('story', 'reporter')


@admin.register(StoryReaction)
class StoryReactionAdmin(admin.ModelAdmin):
    list_display = ('story', 'user', 'kind', 'created_at')
    list_filter = ('kind',)
    raw_id_fields = ('story', 'user')
