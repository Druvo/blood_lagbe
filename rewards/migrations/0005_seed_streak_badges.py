from django.db import migrations

STREAK_BADGES = [
    {
        'name': 'Consistent Donor', 'icon': 'repeat', 'color': '#2e9e5b',
        'min_streak': 2,
        'description': '2 donation cycles in a row. Consistency saves lives.',
    },
    {
        'name': 'Reliable Donor', 'icon': 'trending-up', 'color': '#1e7fd6',
        'min_streak': 4,
        'description': '4 donation cycles in a row. You never miss a beat.',
    },
    {
        'name': 'Iron Donor', 'icon': 'activity', 'color': '#c0392b',
        'min_streak': 8,
        'description': '8 donation cycles in a row. Unstoppable.',
    },
]


def seed(apps, schema_editor):
    Badge = apps.get_model('rewards', 'Badge')
    for data in STREAK_BADGES:
        Badge.objects.get_or_create(
            badge_type='streak', min_streak=data['min_streak'], defaults=data,
        )


def remove(apps, schema_editor):
    Badge = apps.get_model('rewards', 'Badge')
    Badge.objects.filter(
        badge_type='streak',
        min_streak__in=[b['min_streak'] for b in STREAK_BADGES],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0004_auto_20260921_1351'),
    ]

    operations = [
        migrations.RunPython(seed, remove),
    ]
