"""Blood groups and red-cell compatibility. Pure data, no Django imports, so
both the accounts and rewards apps can use it."""

BLOOD_GROUP_CHOICES = [
    ('A+', 'A+'), ('A-', 'A-'),
    ('B+', 'B+'), ('B-', 'B-'),
    ('AB+', 'AB+'), ('AB-', 'AB-'),
    ('O+', 'O+'), ('O-', 'O-'),
]

# Recipient group -> donor groups whose red cells they can generally
# receive. A search aid only: the hospital's own cross-matching decides.
COMPATIBLE_DONORS = {
    'A+': ['A+', 'A-', 'O+', 'O-'],
    'A-': ['A-', 'O-'],
    'B+': ['B+', 'B-', 'O+', 'O-'],
    'B-': ['B-', 'O-'],
    'AB+': ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'],
    'AB-': ['A-', 'B-', 'AB-', 'O-'],
    'O+': ['O+', 'O-'],
    'O-': ['O-'],
}


def donor_groups_for(recipient_group, include_compatible=False):
    if include_compatible:
        return COMPATIBLE_DONORS.get(recipient_group, [])
    return [recipient_group] if recipient_group in COMPATIBLE_DONORS else []
