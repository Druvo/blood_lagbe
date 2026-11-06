from django.apps import AppConfig


class GrowthConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'growth'

    def ready(self):
        from growth import signals  # noqa: F401 (connects the receivers)
