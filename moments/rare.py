"""Rare-group spotlight, computed on the fly: no Badge rows are created.

Rare here means a blood group that is scarce in the donor pool, so a verified
donor with one is worth a thank-you. It is a label, never a nudge to donate
sooner than the interval.
"""
from rewards.models import approved_donation_count

RARE_GROUPS = frozenset({'O-', 'AB-'})
RARE_LABEL = 'Rare hero'


def is_rare(blood_group):
    return (blood_group or '').strip().upper() in RARE_GROUPS


def rare_label(user):
    """"Rare hero" for an approved donor whose profile group is rare, else
    an empty string."""
    if not getattr(user, 'is_authenticated', True):
        return ''
    profile = getattr(user, 'profile', None)
    if profile is None or not is_rare(profile.blood_group):
        return ''
    if approved_donation_count(user) < 1:
        return ''
    return RARE_LABEL
