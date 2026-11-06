"""Share links for WhatsApp, Messenger and Facebook, with proper encoding.

Messages never carry a phone number or email. Request messages carry only
what the request page already shows to everyone: blood group, hospital,
district and the needed-by date, plus the page URL.
"""
import re
from urllib.parse import quote

from django.conf import settings
from django.urls import reverse

# Anything that looks like a phone number is dropped from free text.
_PHONE_LIKE = re.compile(r'\+?\d[\d\s\-().]{6,}\d')
_EMAIL_LIKE = re.compile(r'\S+@\S+')


def _scrub(text):
    text = _PHONE_LIKE.sub('', text or '')
    text = _EMAIL_LIKE.sub('', text)
    return ' '.join(text.split())


def _q(value):
    return quote(value, safe='')


# ----------------------------------------------------------- generic links

def whatsapp_link(text):
    return f'https://wa.me/?text={_q(text)}'


def facebook_link(url):
    return f'https://www.facebook.com/sharer/sharer.php?u={_q(url)}'


def messenger_link(url, redirect_to=''):
    """Messenger has no keyless web share. With ``FACEBOOK_APP_ID`` set in
    settings, use the web send dialog; otherwise the mobile deep link, which
    opens the Messenger app on phones."""
    app_id = getattr(settings, 'FACEBOOK_APP_ID', '')
    if app_id:
        return (
            'https://www.facebook.com/dialog/send'
            f'?app_id={_q(str(app_id))}&link={_q(url)}'
            f'&redirect_uri={_q(redirect_to or url)}'
        )
    return f'fb-messenger://share/?link={_q(url)}'


# --------------------------------------------------------- blood requests

def request_url(request, blood_request):
    return request.build_absolute_uri(
        reverse('request-detail', args=[blood_request.pk]),
    )


def request_message(blood_request, url):
    """Bangla and English side by side, so it reads well for anyone."""
    needed = blood_request.needed_by
    lines = [
        'জরুরি রক্ত দরকার | Urgent blood needed',
        f'রক্তের গ্রুপ / Blood group: {blood_request.blood_group}',
        'হাসপাতাল / Hospital: '
        f'{_scrub(blood_request.hospital)}, {blood_request.district}',
        f'কবে লাগবে / Needed by: {needed.day} {needed:%b} {needed.year}',
        f'সাহায্য করুন / Please help: {url}',
    ]
    return '\n'.join(lines)


def whatsapp_url(request, blood_request):
    url = request_url(request, blood_request)
    return whatsapp_link(request_message(blood_request, url))


def messenger_url(request, blood_request):
    return messenger_link(request_url(request, blood_request))


def facebook_url(request, blood_request):
    return facebook_link(request_url(request, blood_request))


def request_share(request, blood_request):
    """Everything the request panel needs, in one call."""
    url = request_url(request, blood_request)
    message = request_message(blood_request, url)
    return {
        'url': url,
        'message': message,
        'whatsapp': whatsapp_link(message),
        'messenger': messenger_link(url),
        'facebook': facebook_link(url),
    }


# ------------------------------------------------------------------ invites

def invite_share(request, invite):
    from growth.services import invite_message, invite_path
    url = request.build_absolute_uri(invite_path(invite))
    message = f'{invite_message()} {url}'
    return {
        'url': url,
        'message': message,
        'whatsapp': whatsapp_link(message),
        'messenger': messenger_link(url),
        'facebook': facebook_link(url),
    }
