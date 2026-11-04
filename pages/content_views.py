"""Learn hub, FAQ and About. Static content with a little real data."""
from django.shortcuts import render

from rewards.blood import BLOOD_GROUP_CHOICES, COMPATIBLE_DONORS

GROUPS = [code for code, _label in BLOOD_GROUP_CHOICES]


def compatibility_matrix():
    """Rows are recipients, columns are donors; `ok` marks red-cell matches.

    Also the inverse ("who can this group give to") for the group picker.
    """
    rows = []
    for recipient in GROUPS:
        donors = COMPATIBLE_DONORS[recipient]
        rows.append({
            'recipient': recipient,
            'cells': [
                {'donor': donor, 'ok': donor in donors} for donor in GROUPS
            ],
            'can_receive_from': donors,
            'can_give_to': [
                other for other in GROUPS if recipient in COMPATIBLE_DONORS[other]
            ],
        })
    return rows


def learn(request):
    # Remember that a signed-in member opened the guide, for the welcome
    # checklist. Only written once, so reads do not touch the session.
    if request.user.is_authenticated and not request.session.get('read_guide'):
        request.session['read_guide'] = True
    return render(request, 'pages/learn.html', {
        'compat_groups': GROUPS,
        'compat_rows': compatibility_matrix(),
    })


def about(request):
    return render(request, 'pages/about.html')


def faq(request):
    return render(request, 'pages/faq.html')
