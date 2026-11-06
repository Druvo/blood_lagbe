"""Signals that connect growth to accounts and rewards without editing them.

* ``user_logged_in``: the sign-up view calls ``login()`` right after creating
  the account, while the session still holds the invite code, so this is the
  hook that links the new member to their inviter. Ordinary logins carry no
  code, or an account that is not new, and do nothing.
* ``post_save(Donation)``: the first approved donation of an invited member
  pays the inviter's Friend Maker reward (idempotent).
"""
import logging

from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver

from growth import services
from rewards.models import Donation

logger = logging.getLogger(__name__)


@receiver(user_logged_in, dispatch_uid='growth_attach_referral')
def link_invite_on_signup(sender, request, user, **kwargs):
    if request is None:
        return
    try:
        services.attach_referral(request, user)
    except Exception:  # never break signing in because of an invite
        logger.exception('growth: could not attach referral')


@receiver(post_save, sender=Donation, dispatch_uid='growth_reward_referral')
def reward_inviter_on_approval(sender, instance, **kwargs):
    if instance.status != Donation.Status.APPROVED:
        return
    try:
        services.reward_referral(instance)
    except Exception:  # never break the admin approval because of a reward
        logger.exception('growth: could not reward referral')
