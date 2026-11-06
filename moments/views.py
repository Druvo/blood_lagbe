from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from core import ratelimit
from moments import services
from moments.models import ImpactUpdate, ThankNote
from rewards.models import BloodRequest

User = get_user_model()

PULSE_CACHE_SECONDS = 10
PULSE_LIMIT = 30  # polls per minute for one person (or address)
THANKS_PER_HOUR = 20  # note submissions per hour (each can be rejected)
THANKS_PAGE_SIZE = 20


def _back(request, fallback):
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return fallback


# ------------------------------------------------------------ live pulse

def pulse(request, pk):
    """Counts only (people responding, bags pledged, still needed): no names.
    Cached for a few seconds and rate limited per person or address."""
    ident = (
        f'u{request.user.pk}' if request.user.is_authenticated
        else f'ip{ratelimit.client_ip(request)}'
    )
    if ratelimit.is_limited('moments-pulse', ident, PULSE_LIMIT):
        response = JsonResponse({'error': 'slow_down'}, status=429)
        response['Retry-After'] = '60'
        return response
    ratelimit.hit('moments-pulse', ident, window=60)

    key = f'moments:pulse:{pk}'
    data = cache.get(key)
    if data is None:
        blood_request = get_object_or_404(BloodRequest, pk=pk)
        data = services.pulse_counts(blood_request)
        data['open'] = blood_request.status == BloodRequest.Status.OPEN
        cache.set(key, data, PULSE_CACHE_SECONDS)
    response = JsonResponse(data)
    response['Cache-Control'] = f'private, max-age={PULSE_CACHE_SECONDS}'
    return response


# ------------------------------------------------------------ thank notes

@login_required
@require_POST
def thank_send(request, pk):
    blood_request = get_object_or_404(BloodRequest, pk=pk)
    back = reverse('request-detail', args=[pk])
    if request.user.pk != blood_request.requester_id:
        raise PermissionDenied
    if blood_request.status != BloodRequest.Status.CLOSED:
        messages.error(request, 'You can send thanks once the request is closed.')
        return redirect(back)
    if ratelimit.is_limited('moments-thank', request.user.pk, THANKS_PER_HOUR):
        messages.error(request, 'Slow down a little. Please try again later.')
        return redirect(back)
    ratelimit.hit('moments-thank', request.user.pk, window=3600)

    created, error = services.send_thanks(
        blood_request, request.user,
        request.POST.get('recipient', ''), request.POST.get('text', ''),
    )
    if error:
        messages.error(request, error)
    elif created == 1:
        messages.success(request, 'Your thank-you note was sent.')
    else:
        messages.success(
            request, f'Your thank-you note was sent to {created} people.',
        )
    return redirect(back)


@login_required
def thanks_wall(request):
    paginator = Paginator(services.thanks_for(request.user), THANKS_PAGE_SIZE)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'moments/thanks.html', {'page': page})


@login_required
@require_POST
def note_hide(request, pk):
    # Only the recipient may hide a note; anyone else gets a 404.
    note = get_object_or_404(ThankNote, pk=pk, recipient=request.user)
    if not note.hidden:
        note.hidden = True
        note.save(update_fields=['hidden'])
    messages.success(request, 'Note hidden.')
    return redirect(_back(request, reverse('moments:thanks')))


# ------------------------------------------------------ hub interactions

@login_required
@require_POST
def update_dismiss(request, pk):
    update = get_object_or_404(ImpactUpdate, pk=pk, user=request.user)
    if not update.dismissed:
        update.dismissed = True
        update.save(update_fields=['dismissed'])
    return redirect(_back(request, reverse('log-donation')))


@login_required
@require_POST
def prefs(request):
    pref = services.get_pref(request.user)
    wants_email = bool(
        request.POST.get('email_enabled') or request.POST.get('impact_email')
    )
    if wants_email and not request.user.email:
        messages.error(
            request, 'Add an email address in Profile & privacy first.',
        )
        return redirect(_back(request, reverse('log-donation')))
    pref.email_enabled = bool(request.POST.get('email_enabled'))
    pref.impact_email = bool(request.POST.get('impact_email'))
    pref.save(update_fields=['email_enabled', 'impact_email', 'updated_at'])
    messages.success(request, 'Email preferences saved.')
    return redirect(_back(request, reverse('log-donation')))


@login_required
@require_POST
def freeze(request):
    _, error = services.activate_freeze(request.user)
    if error:
        messages.error(request, error)
    else:
        messages.success(
            request,
            'Streak freeze used. It does not change when you can donate '
            'again.',
        )
    return redirect(_back(request, reverse('log-donation')))


# ------------------------------------------------------ one-click email off

@require_http_methods(['GET', 'POST'])
def reminder_off(request, token):
    """Switch one kind of email off from a signed link. No login needed, and
    switching off is harmless to repeat, so GET works for old email links."""
    parsed = services.read_off_token(token)
    if parsed is None:
        raise Http404('This link is not valid.')
    user_id, kind = parsed
    pref = services.get_pref(get_object_or_404(User, pk=user_id))
    field = 'email_enabled' if kind == 'ready' else 'impact_email'
    if getattr(pref, field):
        setattr(pref, field, False)
        pref.save(update_fields=[field, 'updated_at'])
    return render(request, 'moments/reminder_off.html', {'kind': kind})
