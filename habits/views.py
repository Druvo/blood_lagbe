"""Today, the tracked share link, settings, titles and the year in review."""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.translation import gettext

from habits import digest, services, titles, year as year_review
from habits.models import DigestPref
from rewards.leaderboard import display_name
from rewards.models import BloodRequest, get_eligibility

User = get_user_model()

TASK_META = {
    services.SHARE: ('send', 'Share an urgent request'),
    services.AVAILABILITY: ('check-circle', 'Confirm your availability'),
    services.FACT: ('book-open', 'Read one fact'),
    services.QUIZ: ('help-circle', 'Myth or fact?'),
}
SAFETY_NOTE = (
    "Reminders, not medical advice. The blood bank's screening decides "
    'eligibility.'
)


def _today_url(task=''):
    url = reverse('habits:today')
    return f'{url}#task-{task}' if task else url


class AvailabilityForm(forms.Form):
    """Reads one choice, writes one existing Profile field. Nothing else on
    the profile can be changed through it."""

    available = forms.ChoiceField(choices=[('yes', 'Yes'), ('no', 'Not right now')])


# ------------------------------------------------------------------- today

@login_required
def today(request):
    user = request.user
    plan = services.todays_plan(user)
    day = plan['day']
    done = plan['done']
    profile = user.profile

    tasks = []
    for key in plan['tasks']:
        icon, title = TASK_META[key]
        tasks.append({'key': key, 'icon': icon, 'title': title, 'done': key in done})

    quiz_rows = quiz_score = None
    if services.QUIZ in plan['tasks']:
        completion = user.daily_completions.filter(
            day=day, task=services.QUIZ,
        ).first()
        answers = services.parse_answers(completion.detail) if completion else None
        quiz_rows, quiz_score = services.quiz_review(
            user, day, answers if completion else None,
        )

    eligibility = get_eligibility(user)
    return render(request, 'habits/today.html', {
        'tasks': tasks,
        'plan': plan,
        'done_count': plan['done_count'],
        'total': plan['total'],
        'all_done': plan['done_count'] >= plan['total'],
        'share_request': plan['share_request'],
        'fact': services.fact_for(user, day),
        'quiz_rows': quiz_rows,
        'quiz_score': quiz_score,
        'quiz_done': services.QUIZ in done,
        'available': profile.available_to_donate,
        'resting': not eligibility.eligible,
        'streak': services.streak_info(user, day),
        'next_title': titles.next_title(user),
        'safety_note': SAFETY_NOTE,
    })


@login_required
@require_POST
def availability(request):
    user = request.user
    if services.write_limited(user):
        messages.error(request, gettext('Too many taps just now. Please try again in a few minutes.'))
        return redirect(_today_url(services.AVAILABILITY))
    if not services.can_do(user, services.AVAILABILITY):
        messages.info(request, gettext('That one is not on your list today.'))
        return redirect(_today_url())
    form = AvailabilityForm(request.POST)
    if not form.is_valid():
        messages.error(request, gettext('Please choose one of the two options.'))
        return redirect(_today_url(services.AVAILABILITY))
    profile = user.profile
    profile.available_to_donate = form.cleaned_data['available'] == 'yes'
    profile.save(update_fields=['available_to_donate', 'last_updated'])
    services.complete_task(user, services.AVAILABILITY)
    if profile.available_to_donate:
        messages.success(request, gettext('Thanks. We have noted that you are available.'))
    else:
        messages.success(request, gettext('Thanks. We have noted that you are resting right now. That is perfectly fine.'))
    return redirect(_today_url(services.AVAILABILITY))


@login_required
@require_POST
def read_fact(request):
    user = request.user
    if services.write_limited(user):
        messages.error(request, gettext('Too many taps just now. Please try again in a few minutes.'))
        return redirect(_today_url(services.FACT))
    if not services.can_do(user, services.FACT):
        messages.info(request, gettext('That one is not on your list today.'))
        return redirect(_today_url())
    services.complete_task(user, services.FACT)
    return redirect(_today_url(services.FACT))


@login_required
@require_POST
def quiz(request):
    user = request.user
    if services.write_limited(user):
        messages.error(request, gettext('Too many taps just now. Please try again in a few minutes.'))
        return redirect(_today_url(services.QUIZ))
    if not services.can_do(user, services.QUIZ):
        messages.info(request, gettext('That one is not on your list today.'))
        return redirect(_today_url())
    answers = [request.POST.get(f'q{i}', '') for i in range(services.QUIZ_QUESTIONS)]
    parsed = services.parse_answers(','.join(answers))
    if parsed is None:
        messages.error(request, gettext('Pick myth or fact for all three questions.'))
        return redirect(_today_url(services.QUIZ))
    _, created = services.complete_task(user, services.QUIZ, detail=','.join(parsed))
    if not created:
        messages.info(request, gettext('You already did today\'s quiz. Here are the answers.'))
    return redirect(_today_url(services.QUIZ))


# ----------------------------------------------------------- tracked share

def share(request, request_pk):
    """Count a share, then send the member to the request. The click is
    recorded once per member, request and day. Anonymous visitors, requests
    that are closed, flagged or the member's own are passed through without
    counting."""
    blood_request = get_object_or_404(
        BloodRequest.objects.with_report_count(), pk=request_pk,
    )
    user = request.user
    countable = (
        user.is_authenticated
        and blood_request.requester_id != user.pk
        and blood_request.is_active
        and not blood_request.is_flagged
    )
    if countable and not services.write_limited(user, 'habits-share'):
        if services.record_share(user, blood_request):
            messages.success(
                request, gettext('Thank you for sharing. Use the buttons to pass it on.'),
            )
        if services.can_do(user, services.SHARE):
            services.complete_task(
                user, services.SHARE, detail=str(blood_request.pk),
            )
    return redirect(reverse('request-detail', args=[blood_request.pk]) + '#share')


# ------------------------------------------------------------------ titles

@login_required
def titles_page(request):
    all_titles = titles.all_titles(request.user)
    return render(request, 'habits/titles.html', {
        'earned': [t for t in all_titles if t.earned],
        'locked': [t for t in all_titles if not t.earned],
    })


# ---------------------------------------------------------------- settings

@login_required
def settings_page(request):
    pref, _ = DigestPref.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        if services.write_limited(request.user, 'habits-settings'):
            messages.error(request, gettext('Too many changes just now. Please try again in a few minutes.'))
            return redirect('habits:settings')
        pref.enabled = request.POST.get('enabled') == 'on'
        pref.save(update_fields=['enabled', 'updated_at'])
        messages.success(
            request,
            'The weekly email is on. You can turn it off any time.'
            if pref.enabled else 'The weekly email is off.',
        )
        return redirect('habits:settings')
    return render(request, 'habits/settings.html', {
        'pref': pref,
        'has_email': bool(request.user.email),
    })


@csrf_exempt
def unsubscribe(request, token):
    """One click, no login. GET (a link in the email) and POST (mail apps
    that support one-click unsubscribe) both turn the digest off."""
    user_id = digest.read_token(token)
    user = User.objects.filter(pk=user_id).first() if user_id else None
    if user is None:
        return render(request, 'habits/unsubscribed.html', {'ok': False}, status=400)
    pref, _ = DigestPref.objects.get_or_create(user=user)
    if pref.enabled:
        pref.enabled = False
        pref.save(update_fields=['enabled', 'updated_at'])
    return render(request, 'habits/unsubscribed.html', {'ok': True})


# ------------------------------------------------------------ year in review

def _valid_year(year):
    if not (year_review.FIRST_YEAR <= year <= timezone.localdate().year):
        raise Http404('No review for that year.')
    return year


@login_required
def year_default(request):
    """Current year, or the latest year with donations if this one has none."""
    current = timezone.localdate().year
    years = year_review.donation_years(request.user)
    target = current if (not years or current in years) else years[0]
    return redirect('habits:year-detail', year=target)


@login_required
def year_detail(request, year):
    _valid_year(year)
    user = request.user
    data = year_review.review(user, year)
    years = sorted(
        set(year_review.donation_years(user)) | {timezone.localdate().year},
        reverse=True,
    )
    return render(request, 'habits/year.html', {
        'review': data,
        'year': year,
        'years': years,
        'profile': user.profile,
        'card_url': reverse('habits:year-card', args=[year]),
        'safety_note': SAFETY_NOTE,
    })


def _card_response(request, owner, year):
    data = year_review.review(owner, year)
    name = display_name(owner)
    path = reverse('habits:year-card-user', args=[year, owner.pk])
    share_url = request.build_absolute_uri(path)
    site = settings.SITE_NAME
    share_text = (
        f'{name}: {data["count"]} verified donation'
        f'{"s" if data["count"] != 1 else ""} on {site} in {year}, an '
        f'estimated {data["lives"]} people helped at most. Join us!'
    )
    return render(request, 'habits/year_card.html', {
        'review': data,
        'year': year,
        'display_name': name,
        'is_owner': request.user.is_authenticated and request.user.pk == owner.pk,
        'is_public': (
            owner.profile.public_badge_card and owner.profile.show_on_leaderboard
        ),
        'share_url': share_url,
        'share_text': share_text,
        'share_title': f'My {year} on {site}',
        'share_filename': f'{slugify(site) or "donor"}-{year}.png',
    })


@login_required
def year_card(request, year):
    """The member's own card. Always visible to them."""
    _valid_year(year)
    return _card_response(request, request.user, year)


def year_card_user(request, year, user_id):
    """The shareable link. Public only when the owner allows badge-card
    sharing and has not hidden themselves from public lists (the card shows
    their name); otherwise only the owner and staff can open it."""
    _valid_year(year)
    owner = get_object_or_404(User.objects.select_related('profile'), pk=user_id)
    viewer = request.user
    profile = owner.profile
    is_owner = viewer.is_authenticated and viewer.pk == owner.pk
    allowed = is_owner or (viewer.is_authenticated and viewer.is_staff) or (
        profile.public_badge_card and profile.show_on_leaderboard
    )
    if not allowed:
        raise Http404('This card is private.')
    # A stranger sees nothing for a year with no approved donations.
    if not is_owner and not year_review.approved_in_year(owner, year).exists():
        raise Http404('No card for that year.')
    return _card_response(request, owner, year)
