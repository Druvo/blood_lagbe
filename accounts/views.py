from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetView
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _, gettext_lazy, ngettext
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from core import ratelimit
from rewards.models import BloodRequest, Donation, RequestResponse

from .forms import (
    LoginForm, PrivacyForm, RegistrationForm, normalize_phone,
)


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


# Abuse limits. Failed logins are counted per phone number + address (so a
# stranger cannot lock the real owner out from elsewhere) and per address.
LOGIN_FAIL_LIMIT_PER_PHONE = 8
LOGIN_FAIL_LIMIT_PER_IP = 40
RESET_MAIL_LIMIT_PER_IP = 5
RESET_WINDOW = 60 * 60
SIGNUPS_PER_IP_PER_HOUR = 20
TOO_MANY_LOGINS = gettext_lazy(
    'Too many tries. Please wait about 15 minutes before trying again.'
)


class SitePasswordResetView(PasswordResetView):
    """The stock reset view, plus the site name for the email templates
    (emails are rendered without a request, so context processors do not run).

    Capped per address so the form cannot be used to flood someone's inbox.
    Over the cap it behaves exactly as if the mail went out (same page), so
    the cap reveals nothing about which emails have accounts."""

    @property
    def extra_email_context(self):
        return {'SITE_NAME': settings.SITE_NAME}

    def form_valid(self, form):
        ip = ratelimit.client_ip(self.request)
        if ratelimit.is_limited('reset-ip', ip, RESET_MAIL_LIMIT_PER_IP):
            return redirect(self.get_success_url())
        ratelimit.hit('reset-ip', ip, RESET_WINDOW)
        return super().form_valid(form)


@never_cache
def login_view(request):
    next_url = safe_next_url(request)
    if request.user.is_authenticated:
        return redirect(next_url or 'home')

    if request.method == 'POST':
        form = LoginForm(request.POST)
        ip = ratelimit.client_ip(request)
        phone_id = f'{ip}|{normalize_phone(request.POST.get("phone", ""))}'
        if (
            ratelimit.is_limited('login-phone', phone_id, LOGIN_FAIL_LIMIT_PER_PHONE)
            or ratelimit.is_limited('login-ip', ip, LOGIN_FAIL_LIMIT_PER_IP)
        ):
            # Checked before the password is even looked at.
            form.add_error(None, TOO_MANY_LOGINS)
            return render(request, 'login.html', {
                'login_form': form, 'next': next_url,
            }, status=429)
        if form.is_valid():
            ratelimit.reset('login-phone', phone_id)
            # login() starts a fresh session key, so a session id planted
            # before logging in is useless afterwards.
            login(request, form.user)
            return redirect(next_url or 'home')
        ratelimit.hit('login-phone', phone_id)
        ratelimit.hit('login-ip', ip)
    else:
        form = LoginForm()

    return render(request, 'login.html', {
        'login_form': form,
        'next': next_url,
    })


@never_cache
@require_http_methods(['GET', 'HEAD', 'POST'])
def logout_view(request):
    """Logging out is harmless, so a plain link (GET) still works for old
    bookmarks. The site's own menu uses a POST form with a CSRF token."""
    was_logged_in = request.user.is_authenticated
    logout(request)
    if was_logged_in:
        messages.info(request, _('You are logged out. See you soon.'))
    return redirect('home')


@never_cache
def signup_view(request):
    next_url = safe_next_url(request)
    if request.user.is_authenticated:
        return redirect(next_url or 'home')

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        ip = ratelimit.client_ip(request)
        if ratelimit.is_limited('signup-ip', ip, SIGNUPS_PER_IP_PER_HOUR):
            form.add_error(None, _(
                'Too many new accounts from this connection. '
                'Please try again in an hour.'))
            return render(request, 'signup.html', {
                'registration_form': form, 'next': next_url,
            }, status=429)
        if form.is_valid():
            ratelimit.hit('signup-ip', ip, RESET_WINDOW)
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
                        _('You are not in the donor directory yet. Add your '
                          'blood group and district to join it.'),
                    )
                # Someone who came from an invite or a request link goes
                # back there; everyone else gets the onboarding checklist.
                return redirect(next_url or 'welcome')
            return redirect('login')
    else:
        form = RegistrationForm()

    return render(request, 'signup.html', {
        'registration_form': form, 'next': next_url,
    })


@login_required
def welcome_view(request):
    user = request.user
    profile = user.profile
    open_matching = 0
    if profile.blood_group:
        open_matching = BloodRequest.objects.still_needed().public().filter(
            blood_group=profile.blood_group,
        ).count()
    open_total = BloodRequest.objects.still_needed().public().count()

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
            'title': _('Complete your profile'),
            'hint': (
                _('Name, blood group and district are set.') if profile_done
                else _('Add your blood group and district so we can match you.')
            ),
            'url_name': 'privacy', 'cta': _('Review') if profile_done else _('Add details'),
            'icon': 'feather-user',
        },
        {
            'key': 'directory', 'done': directory_done,
            'title': _('Join the donor directory'),
            'hint': (
                _('You are listed. People ask first; your number is shared only if you accept.')
                if directory_done else
                _('Optional. Members can ask you for help, and you choose to accept or decline.')
            ),
            'url_name': 'privacy', 'cta': _('Manage') if directory_done else _('Join'),
            'icon': 'feather-users',
        },
        {
            'key': 'requests', 'done': requests_done,
            'title': _('See urgent requests'),
            'hint': (
                ngettext(
                    '%(n)s open request need your blood group.',
                    '%(n)s open requests need your blood group.',
                    open_matching,
                ) % {'n': open_matching} if open_matching else
                (ngettext(
                    '%(n)s open request right now.',
                    '%(n)s open requests right now.',
                    open_total,
                ) % {'n': open_total}
                 if open_total else _('No open requests right now.'))
            ),
            'url_name': 'request-list', 'cta': _('Open board'),
            'icon': 'feather-droplet',
        },
        {
            'key': 'donation', 'done': donation_done,
            'title': _('Log your first donation'),
            'hint': (
                _('Done. A volunteer verifies each donation before it counts.')
                if donation_done else
                _('Donated before? Add it. A volunteer verifies it before points count.')
            ),
            'url_name': 'log-donation', 'cta': _('Open my hub'),
            'icon': 'feather-award',
        },
        {
            'key': 'guide', 'done': guide_done,
            'title': _('Read the donor guide'),
            'hint': _('Who can donate, what to expect, and how to look after yourself.'),
            'url_name': 'learn', 'cta': _('Read guide'),
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
        messages.success(request, _('Privacy settings saved.'))
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
