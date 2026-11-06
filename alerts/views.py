from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
from django.utils.translation import gettext

from alerts.forms import AlertPreferenceForm
from alerts.matching import get_preference
from alerts.models import AlertPreference, Notification
from alerts.service import mark_all_read
from alerts.tokens import read_email_off_token
from core import ratelimit

User = get_user_model()

PAGE_SIZE = 20
WRITE_LIMIT = 60  # inbox and settings writes per 15 minutes per member


def _limited(request):
    if ratelimit.is_limited('alerts-write', request.user.pk, WRITE_LIMIT):
        messages.error(request, gettext('That is a lot of clicks. Please wait a few minutes.'))
        return True
    ratelimit.hit('alerts-write', request.user.pk)
    return False


def _back_to_inbox(request):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ) and target.startswith('/alerts/'):
        return redirect(target)
    return redirect('alerts:inbox')


@login_required
def inbox(request):
    # Unread first, then newest first.
    qs = Notification.objects.filter(user=request.user).order_by(
        F('read_at').asc(nulls_first=True), '-created_at', '-pk',
    )
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'alerts/inbox.html', {
        'page': page,
        'notifications': page.object_list,
    })


@login_required
@require_POST
def mark_read(request, pk):
    """Mark one of your own notifications read. With ``go`` it then opens
    the linked page. Someone else's notification is a 404."""
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    if notification.read_at is None:
        Notification.objects.filter(pk=pk, read_at__isnull=True).update(
            read_at=timezone.now(),
        )
    if request.POST.get('go') and notification.url:
        return redirect(notification.url)
    return _back_to_inbox(request)


@login_required
@require_POST
def mark_all(request):
    if not _limited(request):
        mark_all_read(request.user)
    return _back_to_inbox(request)


@login_required
def settings_view(request):
    user = request.user
    profile = getattr(user, 'profile', None)
    prefs = AlertPreference.objects.filter(user=user).first()
    if request.method == 'POST':
        if _limited(request):
            return redirect('alerts:settings')
        prefs = get_preference(user)
        form = AlertPreferenceForm(request.POST, instance=prefs, user=user)
        if form.is_valid():
            form.save()
            messages.success(request, gettext('Your alert settings are saved.'))
            return redirect('alerts:settings')
    else:
        form = AlertPreferenceForm(
            instance=prefs or AlertPreference(user=user), user=user,
        )
    return render(request, 'alerts/settings.html', {
        'form': form,
        'has_email': bool(user.email),
        'profile_ready': bool(
            profile and profile.blood_group and profile.district,
        ),
    })


@require_http_methods(['GET', 'POST'])
def email_off(request, token):
    """One-click email off from a link in an alert email. No login needed:
    the signed token names the member. Turning email off is harmless, so a
    mail scanner opening the link does no damage."""
    user_id = read_email_off_token(token)
    if user_id is None or not User.objects.filter(pk=user_id).exists():
        return render(request, 'alerts/email_off.html', {'ok': False}, status=404)
    AlertPreference.objects.filter(user_id=user_id).update(email_enabled=False)
    return render(request, 'alerts/email_off.html', {
        'ok': True,
        'signed_in': request.user.is_authenticated,
    })
