from django.apps import AppConfig


class MomentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'moments'

    def ready(self):
        from moments import signals  # noqa: F401
