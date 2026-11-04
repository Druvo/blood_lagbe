"""Bangladesh's 8 divisions and 64 districts, as grouped Django choices.

The stored value is the English district name, so it also matches the
free-text values older records already hold.
"""

DIVISIONS = {
    'Barishal': [
        'Barguna', 'Barishal', 'Bhola', 'Jhalokati', 'Patuakhali', 'Pirojpur',
    ],
    'Chattogram': [
        'Bandarban', 'Brahmanbaria', 'Chandpur', 'Chattogram', 'Cumilla',
        "Cox's Bazar", 'Feni', 'Khagrachhari', 'Lakshmipur', 'Noakhali',
        'Rangamati',
    ],
    'Dhaka': [
        'Dhaka', 'Faridpur', 'Gazipur', 'Gopalganj', 'Kishoreganj',
        'Madaripur', 'Manikganj', 'Munshiganj', 'Narayanganj', 'Narsingdi',
        'Rajbari', 'Shariatpur', 'Tangail',
    ],
    'Khulna': [
        'Bagerhat', 'Chuadanga', 'Jashore', 'Jhenaidah', 'Khulna', 'Kushtia',
        'Magura', 'Meherpur', 'Narail', 'Satkhira',
    ],
    'Mymensingh': ['Jamalpur', 'Mymensingh', 'Netrokona', 'Sherpur'],
    'Rajshahi': [
        'Bogura', 'Chapainawabganj', 'Joypurhat', 'Naogaon', 'Natore',
        'Pabna', 'Rajshahi', 'Sirajganj',
    ],
    'Rangpur': [
        'Dinajpur', 'Gaibandha', 'Kurigram', 'Lalmonirhat', 'Nilphamari',
        'Panchagarh', 'Rangpur', 'Thakurgaon',
    ],
    'Sylhet': ['Habiganj', 'Moulvibazar', 'Sunamganj', 'Sylhet'],
}

DISTRICT_CHOICES = [
    (division, [(name, name) for name in districts])
    for division, districts in DIVISIONS.items()
]

DISTRICT_NAMES = [name for districts in DIVISIONS.values() for name in districts]
DISTRICT_TO_DIVISION = {
    name: division
    for division, districts in DIVISIONS.items() for name in districts
}
