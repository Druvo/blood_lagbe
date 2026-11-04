from django.conf import settings


def donation_interval(request):
    days = settings.DONATION_INTERVAL_DAYS
    months = round(days / 30)
    return {
        'donation_interval_days': days,
        'donation_interval_label': (
            f'{months} month{"s" if months != 1 else ""}' if days % 30 == 0
            else f'{days} days'
        ),
    }
