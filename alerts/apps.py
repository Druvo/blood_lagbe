import logging

from django.apps import AppConfig
from django.db import transaction
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def _dispatch(request_id):
    from alerts.matching import send_match_alerts
    try:
        send_match_alerts(request_id)
    except Exception:  # never break posting a request over an alert
        logger.exception('Match alerts failed for request %s', request_id)


def alert_on_new_request(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    pk = instance.pk
    transaction.on_commit(lambda: _dispatch(pk))


class AlertsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'alerts'

    def ready(self):
        from rewards.models import BloodRequest
        post_save.connect(
            alert_on_new_request, sender=BloodRequest,
            dispatch_uid='alerts.new_request',
        )
