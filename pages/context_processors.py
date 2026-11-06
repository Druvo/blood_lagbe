from django.conf import settings
from django.utils.translation import gettext, gettext_noop

# The default tagline is translated; a custom SITE_TAGLINE from the
# environment has no translation and is shown as written.
gettext_noop('Give blood. Build bonds. Save lives.')


def site(request):
    return {
        'SITE_NAME': settings.SITE_NAME,
        'SITE_TAGLINE': gettext(settings.SITE_TAGLINE),
        'SITE_CONTACT_EMAIL': settings.SITE_CONTACT_EMAIL,
    }
