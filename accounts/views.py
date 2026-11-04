from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetView
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from rewards.models import BloodRequest, Donation, RequestResponse

from .forms import LoginForm, PrivacyForm, RegistrationForm


def safe_next_url(request):
    """The `next` target, only if it stays on this site."""
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return ''


class SitePasswordResetView(PasswordResetView):
    """The stock reset view, plus the site name for the email templates
    (emails are rendered without a request, so context processors do not run)."""

    @property
    def extra_email_context(self):
        return {'SITE_NAME': settings.SITE_NAME}


@never_cache
def login_view(request):
    next_url = safe_next_url(request)
    if request.user.is_authenticated:
        return redirect(next_url or 'home')

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            login(request, form.user)
            return redirect(next_url or 'home')
    else:
        form = LoginForm()

    return render(request, 'login.html', {
        'login_form': form,
        'next': next_url,
    })


def logout_view(request):
    was_logged_in = request.user.is_authenticated
    logout(request)
    if was_logged_in:
        messages.info(request, 'You are logged out. See you soon.')
    return redirect('home')


@never_cache
def signup_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            form.save(commit=True)
            account = authenticate(
                phone=form.cleaned_data['phone'],
                password=form.cleaned_data['password1'],
            )
            if account:
                login(request, account)
                if form.directory_ignored:
                    messages.info(
                        request,
                        'You are not in the donor directory yet. Add your '
                        'blood group and district to join it.',
                    )
                return redirect('welcome')
            return redirect('login')
    else:
        form = RegistrationForm()

    return render(request, 'signup.html', {'registration_form': form})


@login_required
def welcome_view(request):
    user = request.user
    profile = user.profile
    open_matching = 0
    if profile.blood_group:
        open_matching = BloodRequest.objects.still_needed().filter(
            blood_group=profile.blood_group,
        ).count()
    open_total = BloodRequest.objects.still_needed().count()

    profile_done = bool(
        user.first_name and profile.blood_group and profile.district
    )
    directory_done = profile.in_directory
    requests_done = (
        RequestResponse.objects.filter(donor=user).exists()
        or BloodRequest.objects.filter(requester=user).exists()
    )
    donation_done = Donation.objects.filter(donor=user).exclude(
        status=Donation.Status.REJECTED,
    ).exists()
    guide_done = bool(request.session.get('read_guide'))

    steps = [
        {
            'key': 'profile', 'done': profile_done,
            'title': 'Complete your profile',
            'hint': (
                'Name, blood group and district are set.' if profile_done
                else 'Add your blood group and district so we can match you.'
            ),
            'url_name': 'privacy', 'cta': 'Review' if profile_done else 'Add details',
            'icon': 'feather-user',
        },
        {
            'key': 'directory', 'done': directory_done,
            'title': 'Join the donor directory',
            'hint': (
                'You are listed. People ask first; your number is shared only if you accept.'
                if directory_done else
                'Optional. Members can ask you for help, and you choose to accept or decline.'
            ),
            'url_name': 'privacy', 'cta': 'Manage' if directory_done else 'Join',
            'icon': 'feather-users',
        },
        {
            'key': 'requests', 'done': requests_done,
            'title': 'See urgent requests',
            'hint': (
                f'{open_matching} open request{"s" if open_matching != 1 else ""} '
                f'need your blood group.' if open_matching else
                (f'{open_total} open request{"s" if open_total != 1 else ""} right now.'
                 if open_total else 'No open requests right now.')
            ),
            'url_name': 'request-list', 'cta': 'Open board',
            'icon': 'feather-droplet',
        },
        {
            'key': 'donation', 'done': donation_done,
            'title': 'Log your first donation',
            'hint': (
                'Done. A volunteer verifies each donation before it counts.'
                if donation_done else
                'Donated before? Add it. A volunteer verifies it before points count.'
            ),
            'url_name': 'log-donation', 'cta': 'Open my hub',
            'icon': 'feather-award',
        },
        {
            'key': 'guide', 'done': guide_done,
            'title': 'Read the donor guide',
            'hint': 'Who can donate, what to expect, and how to look after yourself.',
            'url_name': 'learn', 'cta': 'Read guide',
            'icon': 'feather-book-open',
        },
    ]
    done = sum(1 for step in steps if step['done'])
    return render(request, 'welcome.html', {
        'steps': steps,
        'done_count': done,
        'total_count': len(steps),
        'percent': int(done / len(steps) * 100),
        'all_done': done == len(steps),
    })


@login_required
def privacy_view(request):
    # Not `request.POST or None`: unchecking every box posts an empty dict,
    # which is falsy, and the opt-out would be silently ignored.
    form = PrivacyForm(
        request.POST if request.method == 'POST' else None,
        instance=request.user.profile,
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Privacy settings saved.')
        return redirect('privacy')
    return render(request, 'privacy.html', {
        'form': form,
        'preview': _privacy_preview(request.user),
    })


def _privacy_preview(user):
    """What other members see of this user. Never the phone number: the
    name is the same one the leaderboard and directory use."""
    from rewards.leaderboard import display_name, initials
    from rewards.models import get_donor_badge_progress, get_donor_points

    tier, _next, count, _percent = get_donor_badge_progress(user)
    name = display_name(user)
    return {
        'name': name,
        'initials': initials(name),
        'donations': count,
        'tier': tier,
        'points': get_donor_points(user),
    }
