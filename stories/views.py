from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from rewards.geo import DISTRICT_NAMES, DIVISIONS
from stories.forms import StoryForm
from stories.models import (
    MAX_PENDING_PER_AUTHOR,
    MAX_REPORTS_PER_DAY,
    MAX_STORIES_PER_DAY,
    REPORT_THRESHOLD,
    Story,
    StoryReaction,
    StoryReport,
)

PER_PAGE = 9

REACTION_ICONS = {
    StoryReaction.Kind.HEART: 'feather-heart',
    StoryReaction.Kind.THANKS: 'feather-sun',
    StoryReaction.Kind.PROUD: 'feather-award',
}


def with_counts(queryset):
    """One query for the author, profile and all three reaction totals."""
    return queryset.select_related('author', 'author__profile').annotate(
        **{
            f'{kind}_count': Count(
                'reactions', filter=Q(reactions__kind=kind), distinct=True,
            )
            for kind in StoryReaction.Kind.values
        }
    )


def attach_reactions(stories, user):
    """Give each story a ready-to-render `reaction_buttons` list."""
    stories = list(stories)
    mine = set()
    if user.is_authenticated and stories:
        mine = set(
            StoryReaction.objects.filter(
                user=user, story__in=[s.pk for s in stories],
            ).values_list('story_id', 'kind')
        )
    for story in stories:
        story.reaction_buttons = [
            {
                'kind': kind,
                'label': label,
                'icon': REACTION_ICONS[kind],
                'count': getattr(story, f'{kind}_count', 0),
                'active': (story.pk, kind) in mine,
            }
            for kind, label in StoryReaction.Kind.choices
        ]
    return stories


def _is_ajax(request):
    return (
        request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('accept', '')
    )


def _safe_next(request, fallback):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return fallback


def story_list(request):
    kind = request.GET.get('kind', '')
    district = request.GET.get('district', '')
    if kind not in Story.Kind.values:
        kind = ''
    if district not in DISTRICT_NAMES:
        district = ''

    queryset = with_counts(Story.objects.approved())
    if kind:
        queryset = queryset.filter(kind=kind)
    if district:
        queryset = queryset.filter(district=district)
    queryset = queryset.order_by('-approved_at', '-id')

    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get('page'))
    stories = attach_reactions(page.object_list, request.user)

    filters = {k: v for k, v in (('kind', kind), ('district', district)) if v}
    return render(request, 'stories/story_list.html', {
        'stories': stories,
        'page': page,
        'kind': kind,
        'district': district,
        'kinds': Story.Kind.choices,
        'divisions': DIVISIONS,
        'filters_query': urlencode(filters),
        'has_filters': bool(filters),
    })


def story_detail(request, pk):
    story = get_object_or_404(
        with_counts(Story.objects.visible_to(request.user)), pk=pk,
    )
    attach_reactions([story], request.user)
    already_reported = (
        request.user.is_authenticated
        and StoryReport.objects.filter(
            reporter=request.user, story=story,
        ).exists()
    )
    return render(request, 'stories/story_detail.html', {
        'story': story,
        'is_author': request.user.is_authenticated
        and story.author_id == request.user.pk,
        'already_reported': already_reported,
        'report_reasons': StoryReport.Reason.choices,
        'share_url': request.build_absolute_uri(
            reverse('story-detail', args=[story.pk])
        ),
    })


@login_required
def story_create(request):
    form = StoryForm(request.POST or None, user=request.user)
    pending = Story.objects.filter(
        author=request.user, status=Story.Status.PENDING,
    ).count()
    if request.method == 'POST' and form.is_valid():
        if pending >= MAX_PENDING_PER_AUTHOR:
            form.add_error(None, (
                f'You already have {pending} stories waiting for review. '
                'Please wait until they are looked at before sharing more.'
            ))
        elif Story.objects.filter(
            author=request.user, title=form.instance.title,
            body=form.instance.body,
        ).exists():
            # What a double click on "Share" would otherwise create twice.
            form.add_error(None, 'You already shared this story.')
        elif Story.objects.filter(
            author=request.user,
            created_at__gte=timezone.now() - timedelta(days=1),
        ).count() >= MAX_STORIES_PER_DAY:
            form.add_error(None, (
                'You have shared several stories today. Please come back '
                'tomorrow to share more.'
            ))
        else:
            form.save()
            messages.success(
                request,
                'Thank you for sharing. Your story is pending review and '
                'will appear once our team has read it.',
            )
            return redirect('story-mine')
    elif request.method == 'GET':
        profile = getattr(request.user, 'profile', None)
        if profile is not None:
            form.initial.setdefault('district', profile.district)
            form.initial.setdefault('blood_group', profile.blood_group)
    return render(request, 'stories/story_form.html', {
        'form': form,
    })


@login_required
def story_mine(request):
    stories = with_counts(
        Story.objects.filter(author=request.user)
    ).order_by('-created_at', '-id')
    return render(request, 'stories/story_mine.html', {
        'stories': list(stories),
    })


@login_required
@require_POST
def story_react(request, pk):
    story = get_object_or_404(Story.objects.approved(), pk=pk)
    kind = request.POST.get('kind', '')
    if kind not in StoryReaction.Kind.values:
        if _is_ajax(request):
            return JsonResponse({'error': 'Unknown reaction.'}, status=400)
        messages.error(request, 'That reaction is not available.')
        return redirect(_safe_next(request, story.get_absolute_url()))

    existing = StoryReaction.objects.filter(
        user=request.user, story=story, kind=kind,
    )
    if existing.exists():
        existing.delete()
        active = False
    else:
        StoryReaction.objects.get_or_create(
            user=request.user, story=story, kind=kind,
        )
        active = True

    if _is_ajax(request):
        return JsonResponse({
            'kind': kind,
            'active': active,
            'count': StoryReaction.objects.filter(
                story=story, kind=kind,
            ).count(),
        })
    return redirect(_safe_next(request, story.get_absolute_url()))


@login_required
@require_POST
def story_report(request, pk):
    story = get_object_or_404(Story.objects.approved(), pk=pk)
    detail = story.get_absolute_url()
    if story.author_id == request.user.pk:
        messages.error(request, 'You cannot report your own story.')
        return redirect(detail)

    reason = request.POST.get('reason', '')
    if reason not in StoryReport.Reason.values:
        reason = StoryReport.Reason.OTHER
    if StoryReport.objects.filter(
        reporter=request.user,
        created_at__gte=timezone.now() - timedelta(days=1),
    ).count() >= MAX_REPORTS_PER_DAY:
        messages.error(
            request,
            'You have filed a lot of reports today. Please try again tomorrow.',
        )
        return redirect(detail)
    _, created = StoryReport.objects.get_or_create(
        reporter=request.user, story=story, defaults={'reason': reason},
    )
    if not created:
        messages.info(request, 'You have already reported this story. Thank you.')
        return redirect(detail)

    recent = StoryReport.objects.filter(story=story)
    if story.approved_at:
        # Reports made before the last approval were already reviewed.
        recent = recent.filter(created_at__gte=story.approved_at)
    count = recent.count()
    if count >= REPORT_THRESHOLD:
        Story.objects.filter(pk=story.pk, status=Story.Status.APPROVED).update(
            status=Story.Status.PENDING,
            moderation_note=f'Sent back for review after {count} reports.',
        )
    messages.success(
        request, 'Thank you. Our team will take a look at this story.',
    )
    return redirect(detail if count < REPORT_THRESHOLD else reverse('stories'))


@login_required
@require_POST
def story_delete(request, pk):
    # Scoped to the author: someone else's story is a plain 404.
    story = get_object_or_404(Story, pk=pk, author=request.user)
    story.delete()
    messages.success(request, 'Your story has been deleted.')
    return redirect('story-mine')
