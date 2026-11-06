from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'events'

    def ready(self):
        from django.db.models.signals import post_save

        from events.awards import award_on_donation_saved
        from rewards.models import Donation

        post_save.connect(
            award_on_donation_saved, sender=Donation,
            dispatch_uid='events-campaign-badge-award',
        )
