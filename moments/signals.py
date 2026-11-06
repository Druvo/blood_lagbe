import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from rewards.models import BloodRequest

logger = logging.getLogger(__name__)


@receiver(post_save, sender=BloodRequest, dispatch_uid='moments_impact')
def request_saved(sender, instance, raw=False, **kwargs):
    """A request closed as fulfilled: thank everyone who offered to help.

    Fires on saves (admin edit, ``BloodRequest.close()``). The close view
    updates rows in bulk, which sends no signal; those are caught by
    ``sync_impact_updates`` when the donor opens the hub and by the
    ``send_impact_updates`` command.
    """
    if raw:
        return
    from moments.services import deliver_impact_updates
    try:
        deliver_impact_updates(instance)
    except Exception:  # a failed thank-you must never block the save
        logger.exception('Impact updates failed for request %s', instance.pk)
