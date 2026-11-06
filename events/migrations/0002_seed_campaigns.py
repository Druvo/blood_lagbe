from django.db import migrations

# Inactive by default: staff switch a campaign on (and, for movable dates
# such as Ramadan, set its dates) in the admin. Yearly ones repeat on their
# month and day and last `duration_days`.
SEEDS = [
    {
        'slug': 'world-blood-donor-day',
        'name': 'World Blood Donor Day',
        'recurring_month': 6, 'recurring_day': 14, 'duration_days': 3,
        'headline': 'World Blood Donor Day: say thank you with a donation',
        'body': 'Every 14 June the world thanks people who give blood. Join '
                'a drive, or book your next donation if you are due.',
        'theme': 'red', 'icon': 'droplet',
        'badge_name': 'World Blood Donor Day', 'badge_icon': 'globe',
    },
    {
        'slug': 'thalassemia-awareness',
        'name': 'Thalassemia Awareness',
        'recurring_month': 5, 'recurring_day': 8, 'duration_days': 3,
        'headline': 'Thalassemia awareness: regular blood saves children',
        'body': 'Many children with thalassemia need blood every few weeks. '
                'Steady donors make that possible.',
        'theme': 'violet', 'icon': 'heart',
        'badge_name': 'Thalassemia Ally', 'badge_icon': 'heart',
    },
    {
        'slug': 'victory-day',
        'name': 'Victory Day',
        'recurring_month': 12, 'recurring_day': 16, 'duration_days': 3,
        'headline': 'Victory Day: give the gift of life',
        'body': 'Mark Victory Day by standing together for others. Join a '
                'drive near you.',
        'theme': 'green', 'icon': 'flag',
        'badge_name': 'Victory Day Giver', 'badge_icon': 'flag',
    },
    {
        'slug': 'ramadan',
        'name': 'Ramadan Giving',
        'recurring_month': None, 'recurring_day': None, 'duration_days': 1,
        'headline': 'Ramadan: a season of giving',
        'body': 'Blood stocks run low during Ramadan. Only donate when you '
                'feel well, and follow the blood bank\'s advice on timing.',
        'theme': 'amber', 'icon': 'moon',
        'badge_name': 'Ramadan Giver', 'badge_icon': 'moon',
    },
]


def seed(apps, schema_editor):
    Campaign = apps.get_model('events', 'Campaign')
    for row in SEEDS:
        Campaign.objects.get_or_create(
            slug=row['slug'], defaults=dict(row, is_active=False),
        )


def unseed(apps, schema_editor):
    Campaign = apps.get_model('events', 'Campaign')
    Campaign.objects.filter(
        slug__in=[r['slug'] for r in SEEDS], is_active=False,
        awards__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0001_initial'),
    ]

    operations = [migrations.RunPython(seed, unseed)]
