"""Campaign badges: a donor gets a campaign's badge once, for an approved
donation dated inside its window.

Three paths, all idempotent (a unique constraint plus ``get_or_create``):
the ``post_save`` signal on Donation, ``sync_user`` (called when a member
opens their hub, so donations the admin approved in bulk are caught: a
queryset ``update()`` sends no signal) and ``sync_all`` (the
``award_campaign_badges`` command). Only active campaigns award badges.
"""
from django.db import IntegrityError, transaction

from events.models import Campaign, CampaignBadgeAward
from rewards.models import Donation


def _award(user_id, campaign, donation):
    try:
        with transaction.atomic():
            award, created = CampaignBadgeAward.objects.get_or_create(
                user_id=user_id, campaign=campaign,
                defaults={'donation': donation},
            )
    except IntegrityError:  # a concurrent request got there first
        return None
    return award if created else None


def award_for_donation(donation):
    """Award every active campaign this approved donation falls in."""
    if donation.status != Donation.Status.APPROVED:
        return []
    awards = []
    for campaign in Campaign.objects.filter(is_active=True):
        if campaign.contains(donation.donation_date):
            award = _award(donation.donor_id, campaign, donation)
            if award:
                awards.append(award)
    return awards


def award_on_donation_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    award_for_donation(instance)


def sync_user(user):
    """Catch up one member's badges from their approved donations."""
    campaigns = list(Campaign.objects.filter(is_active=True).exclude(
        awards__user=user,
    ))
    if not campaigns:
        return []
    donations = list(Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
    ).order_by('donation_date'))
    awards = []
    for campaign in campaigns:
        for donation in donations:
            if campaign.contains(donation.donation_date):
                award = _award(user.pk, campaign, donation)
                if award:
                    awards.append(award)
                break
    return awards


def sync_all(dry_run=False):
    """Award every active campaign to all eligible donors. Returns the
    number of badges awarded (or that would be)."""
    approved = Donation.objects.filter(
        status=Donation.Status.APPROVED,
    ).order_by()
    first = approved.order_by('donation_date').values_list(
        'donation_date', flat=True,
    ).first()
    last = approved.order_by('-donation_date').values_list(
        'donation_date', flat=True,
    ).first()
    if first is None:
        return 0
    total = 0
    for campaign in Campaign.objects.filter(is_active=True):
        have = set(campaign.awards.values_list('user_id', flat=True))
        seen = set()
        for start, end in campaign.windows_between(first, last):
            rows = approved.filter(
                donation_date__range=(start, end),
            ).order_by('donation_date', 'pk')
            for donation in rows:
                if donation.donor_id in have or donation.donor_id in seen:
                    continue
                seen.add(donation.donor_id)
                if dry_run:
                    total += 1
                elif _award(donation.donor_id, campaign, donation):
                    total += 1
    return total
