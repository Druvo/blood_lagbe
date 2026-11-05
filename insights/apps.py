from django.apps import AppConfig


class InsightsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'insights'

    def ready(self):
        """Bump the stats cache whenever the data behind it changes, so a
        newly approved donation shows up without waiting for the TTL."""
        from django.db.models.signals import post_delete, post_save

        from accounts.models import Profile
        from insights import stats
        from rewards.models import (
            BloodRequest, Donation, RequestReport, RequestResponse,
        )

        def bust(sender, **kwargs):
            stats.clear_cache()

        for model in (
            Donation, BloodRequest, RequestResponse, RequestReport, Profile,
        ):
            for signal in (post_save, post_delete):
                signal.connect(
                    bust, sender=model,
                    dispatch_uid=f'insights-bust-{signal.__class__.__name__}'
                                 f'-{model.__name__}',
                    weak=False,
                )
