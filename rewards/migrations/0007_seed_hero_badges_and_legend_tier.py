from django.db import migrations

HERO_BADGES = [
    {
        'name': 'First Responder', 'icon': 'life-buoy', 'color': '#e50202',
        'min_request_donations': 1,
        'description': 'Answered an urgent request with a verified donation.',
    },
    {
        'name': 'Lifeline Hero', 'icon': 'flag', 'color': '#ff7a18',
        'min_request_donations': 3,
        'description': '3 urgent requests answered. Families are counting on you.',
    },
    {
        'name': 'Guardian Angel', 'icon': 'sun', 'color': '#f5b700',
        'min_request_donations': 10,
        'description': '10 urgent requests answered. You are someone\'s miracle.',
    },
]

LEGEND_TIER = {
    'name': 'Legend Donor', 'icon': 'target', 'color': '#00b894',
    'min_donations': 50,
    'description': '50 verified donations. A legend of the community.',
}


def seed(apps, schema_editor):
    Badge = apps.get_model('rewards', 'Badge')
    for data in HERO_BADGES:
        Badge.objects.get_or_create(
            badge_type='hero',
            min_request_donations=data['min_request_donations'],
            defaults={**data, 'badge_type': 'hero'},
        )
    Badge.objects.get_or_create(
        badge_type='tier', min_donations=LEGEND_TIER['min_donations'],
        defaults={**LEGEND_TIER, 'badge_type': 'tier'},
    )


def remove(apps, schema_editor):
    Badge = apps.get_model('rewards', 'Badge')
    Badge.objects.filter(
        badge_type='hero',
        min_request_donations__in=[
            b['min_request_donations'] for b in HERO_BADGES
        ],
    ).delete()
    Badge.objects.filter(
        badge_type='tier', min_donations=LEGEND_TIER['min_donations'],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0006_game_loop_requests_hero_badges'),
    ]

    operations = [
        migrations.RunPython(seed, remove),
    ]
