from django.db import migrations

DEFAULT_BADGES = [
    {
        'name': 'Bronze Donor', 'icon': 'award', 'color': '#cd7f32',
        'min_donations': 1,
        'description': 'Completed your first verified donation. Thank you!',
    },
    {
        'name': 'Silver Donor', 'icon': 'star', 'color': '#a8a8a8',
        'min_donations': 5,
        'description': '5 verified donations. You\'re a regular lifesaver.',
    },
    {
        'name': 'Gold Donor', 'icon': 'zap', 'color': '#d4af37',
        'min_donations': 10,
        'description': '10 verified donations. Outstanding commitment.',
    },
    {
        'name': 'Platinum Donor', 'icon': 'shield', 'color': '#7c4dff',
        'min_donations': 25,
        'description': '25 verified donations. A true blood donation hero.',
    },
]


def seed_badges(apps, schema_editor):
    Badge = apps.get_model('rewards', 'Badge')
    for data in DEFAULT_BADGES:
        Badge.objects.get_or_create(
            min_donations=data['min_donations'], defaults=data,
        )


def remove_badges(apps, schema_editor):
    Badge = apps.get_model('rewards', 'Badge')
    Badge.objects.filter(
        min_donations__in=[b['min_donations'] for b in DEFAULT_BADGES],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0002_badge'),
    ]

    operations = [
        migrations.RunPython(seed_badges, remove_badges),
    ]
