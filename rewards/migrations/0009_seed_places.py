from django.db import migrations

# A starter list of large, well-known institutions so the select is not empty.
# Deliberately short: staff extend it in the admin, and donors can type a
# name for anything missing. Check names against current sources before
# relying on it.
PLACES = [
    ('Dhaka Medical College Hospital', 'Dhaka', 'hospital'),
    ('Bangabandhu Sheikh Mujib Medical University (BSMMU)', 'Dhaka', 'hospital'),
    ('Sir Salimullah Medical College & Mitford Hospital', 'Dhaka', 'hospital'),
    ('Shaheed Suhrawardy Medical College Hospital', 'Dhaka', 'hospital'),
    ('National Institute of Cardiovascular Diseases', 'Dhaka', 'hospital'),
    ('Square Hospitals', 'Dhaka', 'hospital'),
    ('United Hospital', 'Dhaka', 'hospital'),
    ('Evercare Hospital Dhaka', 'Dhaka', 'hospital'),
    ('Bangladesh Red Crescent Blood Centre', 'Dhaka', 'blood_bank'),
    ('Shaheed Tajuddin Ahmad Medical College Hospital', 'Gazipur', 'hospital'),
    ('Chattogram Medical College Hospital', 'Chattogram', 'hospital'),
    ('Cumilla Medical College Hospital', 'Cumilla', 'hospital'),
    ('Sylhet MAG Osmani Medical College Hospital', 'Sylhet', 'hospital'),
    ('Rajshahi Medical College Hospital', 'Rajshahi', 'hospital'),
    ('Shaheed Ziaur Rahman Medical College Hospital', 'Bogura', 'hospital'),
    ('Khulna Medical College Hospital', 'Khulna', 'hospital'),
    ('Sher-e-Bangla Medical College Hospital', 'Barishal', 'hospital'),
    ('Rangpur Medical College Hospital', 'Rangpur', 'hospital'),
    ('Mymensingh Medical College Hospital', 'Mymensingh', 'hospital'),
]


def seed(apps, schema_editor):
    Place = apps.get_model('rewards', 'Place')
    for name, district, kind in PLACES:
        Place.objects.get_or_create(
            name=name, district=district, defaults={'kind': kind},
        )


def unseed(apps, schema_editor):
    Place = apps.get_model('rewards', 'Place')
    for name, district, _ in PLACES:
        Place.objects.filter(
            name=name, district=district,
            donations__isnull=True, blood_requests__isnull=True,
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0008_places_and_districts'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
