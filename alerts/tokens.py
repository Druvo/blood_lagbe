"""Signed one-click "turn email off" links. They work without logging in and
carry only the member's id, signed with the site secret."""
from django.core import signing

SALT = 'alerts.email-off'


def make_email_off_token(user):
    return signing.dumps({'u': user.pk}, salt=SALT, compress=True)


def read_email_off_token(token):
    """The user id in the token, or None if it is forged or damaged."""
    try:
        data = signing.loads(token, salt=SALT)
        return int(data['u'])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
