# flake8: noqa: E501 (long literal lines in tables of URLs and payloads)
"""A shared cast of people for the privacy, security and accessibility audits.

Every person gets a distinctive phone number, email address and name, so a
leak is a plain substring search. Not a test module (no ``test`` prefix).
"""
import datetime
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from blogapp.models import Author, Category, Post
from rewards.models import (
    BloodRequest,
    ContactRequest,
    Donation,
    RequestReport,
    RequestResponse,
    RequestUpdate,
)
from squads.models import Squad, SquadMember
from stories.models import Story

User = get_user_model()
PASSWORD = 'testpass123'
HOSPITAL = 'Audit Hospital Alpha'

# key: (first name, last name, phone)
PEOPLE = {
    'stranger': ('Stella', 'Strangerton', '01955500001'),
    'owner': ('Olivia', 'Ownerson', '01955500002'),
    'responder': ('Rhea', 'Responderi', '01955500003'),
    'flagger': ('Flagg', 'Flaggerson', '01955500004'),
    'directory': ('Dara', 'Directorson', '01955500005'),
    'asker_ok': ('Xanthe', 'Acceptsen', '01955500006'),
    'asker_wait': ('Xavier', 'Pendingsen', '01955500007'),
    'asker_no': ('Xena', 'Declinesen', '01955500008'),
    'anon_author': ('Anonia', 'Authorsen', '01955500009'),
    'optout_author': ('Opal', 'Outsiderson', '01955500010'),
    'pending_author': ('Penny', 'Pendingstory', '01955500011'),
    'hidden': ('Hidalgo', 'Hiddenson', '01955500012'),
    'private_card': ('Priva', 'Cardsen', '01955500013'),
    'visible': ('Vivian', 'Visiblesen', '01955500014'),
    'squad_owner': ('Sqwad', 'Ownerson', '01955500015'),
    'muted': ('Mute', 'Memberson', '01955500016'),
    'reporter1': ('Repo', 'Reporterone', '01955500017'),
    'reporter2': ('Reno', 'Reportertwo', '01955500018'),
    'reporter3': ('Reva', 'Reporterthree', '01955500019'),
    'staff': ('Stan', 'Staffer', '01955500020'),
    'blogger': ('Blaise', 'Bloggerson', '01955500021'),
}

# People whose own name must not show up for anyone else, except where a
# rule says so (an opted-out or anonymous person, a requester, a responder,
# a directory donor and the people who asked them).
NAME_PROTECTED = (
    'stranger', 'owner', 'responder', 'flagger', 'directory', 'asker_ok',
    'asker_wait', 'asker_no', 'anon_author', 'optout_author',
    'pending_author', 'hidden', 'squad_owner', 'muted', 'reporter1',
    'reporter2', 'reporter3', 'staff',
)

CONTACT_PHONES = {
    'open': '01966600001',
    'flagged': '01966600002',
    'closed': '01966600003',
    'expired': '01966600004',
}
PATIENT = 'Patientia'
FLAGGED_HOSPITAL = 'Reported Hospital Zeta'
PENDING_TITLE = 'Quietly Waiting Pendingtitle'
PRIVATE_SQUAD = 'Secretive Squadname'
BODY = 'This is a plain true story about giving blood and meeting kind people.'


def email_for(key):
    first, last, _phone = PEOPLE[key]
    return f'{first}.{last}@audit.test'.lower()


def _donation(user, status=Donation.Status.APPROVED, days_ago=200,
              request=None, location=HOSPITAL):
    return Donation.objects.create(
        donor=user, blood_group='A+', district='Dhaka', location=location,
        donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
        status=status, blood_request=request,
    )


def _request(user, kind, **extra):
    fields = dict(
        blood_group='A+', hospital=HOSPITAL, district='Dhaka', units_needed=2,
        needed_by=timezone.localdate() + datetime.timedelta(days=3),
        contact_phone=CONTACT_PHONES[kind],
    )
    fields.update(extra)
    return BloodRequest.objects.create(requester=user, **fields)


def build_cast():
    """Create everyone and everything. Returns a namespace of objects."""
    cast = SimpleNamespace(users={}, passwords=PASSWORD)
    for key, (first, last, phone) in PEOPLE.items():
        user = User.objects.create_user(phone=phone, password=PASSWORD)
        user.first_name, user.last_name = first, last
        user.email = email_for(key)
        user.save()
        cast.users[key] = user

    def profile(key, **fields):
        prof = cast.users[key].profile
        for name, value in fields.items():
            setattr(prof, name, value)
        prof.save()

    cast.users['staff'].staff = True
    cast.users['staff'].save()

    for key in ('directory',):
        profile(key, in_directory=True, blood_group='O+', district='Dhaka',
                show_on_leaderboard=False, public_badge_card=False)
    for key in ('owner', 'optout_author', 'hidden', 'squad_owner', 'muted'):
        profile(key, show_on_leaderboard=False)
    profile('private_card', public_badge_card=False)
    profile('visible', blood_group='A+', district='Dhaka')

    # Requests ---------------------------------------------------------
    owner = cast.users['owner']
    cast.req_open = _request(owner, 'open', patient_name=PATIENT,
                             note='Ward five, bed two')
    RequestResponse.objects.create(
        blood_request=cast.req_open, donor=cast.users['responder'],
    )
    RequestUpdate.objects.create(
        blood_request=cast.req_open, author=owner,
        kind=RequestUpdate.Kind.NOTE, text='Two donors found, one to go',
    )
    cast.req_flagged = _request(
        cast.users['flagger'], 'flagged', hospital=FLAGGED_HOSPITAL,
    )
    for key in ('reporter1', 'reporter2', 'reporter3'):
        RequestReport.objects.create(
            blood_request=cast.req_flagged, reporter=cast.users[key],
            reason=RequestReport.Reason.SPAM,
        )
    cast.req_closed = _request(
        owner, 'closed', status=BloodRequest.Status.CLOSED,
        closed_at=timezone.now(),
    )
    cast.req_expired = _request(
        owner, 'expired',
        needed_by=timezone.localdate() - datetime.timedelta(days=2),
    )

    # Donations --------------------------------------------------------
    cast.owner_pending = _donation(
        owner, Donation.Status.PENDING, days_ago=1,
    )
    cast.owner_approved = _donation(owner, days_ago=300)
    for key, count in (('hidden', 2), ('private_card', 1), ('visible', 2),
                       ('muted', 1)):
        for n in range(count):
            _donation(cast.users[key], days_ago=200 + 150 * n)

    # Directory contact requests --------------------------------------
    directory = cast.users['directory']
    cast.contact_ok = ContactRequest.objects.create(
        sender=cast.users['asker_ok'], donor=directory,
        status=ContactRequest.Status.ACCEPTED, answered_at=timezone.now(),
    )
    cast.contact_wait = ContactRequest.objects.create(
        sender=cast.users['asker_wait'], donor=directory,
    )
    cast.contact_no = ContactRequest.objects.create(
        sender=cast.users['asker_no'], donor=directory,
        status=ContactRequest.Status.DECLINED, answered_at=timezone.now(),
    )

    # Squads -----------------------------------------------------------
    cast.squad = Squad.objects.create(
        name='Audit Public Squad', owner=cast.users['squad_owner'],
        is_public=True, district='Dhaka',
    )
    cast.private_squad = Squad.objects.create(
        name=PRIVATE_SQUAD, owner=cast.users['visible'], is_public=False,
    )
    SquadMember.objects.create(
        squad=cast.squad, user=cast.users['squad_owner'],
        role=SquadMember.Role.OWNER,
    )
    SquadMember.objects.create(squad=cast.squad, user=cast.users['visible'])
    SquadMember.objects.create(squad=cast.squad, user=cast.users['muted'])
    SquadMember.objects.create(
        squad=cast.private_squad, user=cast.users['visible'],
        role=SquadMember.Role.OWNER,
    )

    # Stories ----------------------------------------------------------
    def story(key, title, status, **fields):
        item = Story.objects.create(
            author=cast.users[key], title=title, body=BODY, status=status,
            district='Dhaka', **fields,
        )
        if status == Story.Status.APPROVED:
            item.approve()
        return item

    cast.story_anon = story(
        'anon_author', 'An anonymous tale', Story.Status.APPROVED,
        anonymous=True,
    )
    cast.story_optout = story(
        'optout_author', 'A quiet opt-out tale', Story.Status.APPROVED,
    )
    cast.story_pending = story(
        'pending_author', PENDING_TITLE, Story.Status.PENDING,
    )

    # Blog -------------------------------------------------------------
    author = Author.objects.create(author=cast.users['blogger'])
    category = Category.objects.create(name='Health')
    cast.post = Post.objects.create(
        title='Audit blog post', author=author, overview='Body text.',
    )
    cast.post.categories.add(category)
    return cast


def crawl_paths(cast):
    """(url name, path) for every GET page and data endpoint."""
    c = cast
    u = cast.users
    paths = [
        ('robots', reverse('robots')),
        ('sitemap', reverse('sitemap')),
        ('manifest', reverse('manifest')),
        ('service-worker', reverse('service-worker')),
        ('home', reverse('home')),
        ('learn', reverse('learn')),
        ('about', reverse('about')),
        ('faq', reverse('faq')),
        ('offline', reverse('offline')),
        ('signup', reverse('signup')),
        ('login', reverse('login')),
        ('privacy', reverse('privacy')),
        ('password_reset', reverse('password_reset')),
        ('password_reset_done', reverse('password_reset_done')),
        ('password_reset_complete', reverse('password_reset_complete')),
        ('welcome', reverse('welcome')),
        ('blogapp:blog-view', reverse('blogapp:blog-view')),
        ('post-view', reverse('post-view', kwargs={'id': c.post.pk})),
        ('log-donation', reverse('log-donation')),
        ('donation-export', reverse('donation-export')),
        ('donation-history', reverse('donation-history')),
        ('donor-card', reverse('donor-card')),
        ('donation-delete', reverse('donation-delete', args=[c.owner_pending.pk])),
        ('donation-delete', reverse('donation-delete', args=[c.owner_approved.pk])),
        ('leaderboard', reverse('leaderboard')),
        ('leaderboard', reverse('leaderboard') + '?period=month&district=Dhaka'),
        ('request-list', reverse('request-list')),
        ('request-list', reverse('request-list') + '?blood_group=A%2B&when=week'),
        ('request-create', reverse('request-create')),
        ('request-mine', reverse('request-mine')),
        ('donor-directory', reverse('donor-directory')),
        ('donor-directory', reverse('donor-directory') + '?group=O%2B&compatible=1&available=1'),
        ('contact-inbox', reverse('contact-inbox')),
        ('squads:list', reverse('squads:list')),
        ('squads:create', reverse('squads:create')),
        ('squads:leaderboard', reverse('squads:leaderboard')),
        ('squads:join', reverse('squads:join')),
        ('squads:join-code', reverse('squads:join-code', args=[c.squad.join_code])),
        ('squads:join-code', reverse('squads:join-code', args=['NOSUCHCODE'])),
        ('squads:edit', reverse('squads:edit', args=[c.squad.pk])),
        ('impact', reverse('impact')),
        ('impact', reverse('impact') + '?period=12m'),
        ('impact-data', reverse('impact-data')),
        ('impact-export', reverse('impact-export')),
        ('impact-district', reverse('impact-district', args=['Dhaka'])),
        ('stories', reverse('stories')),
        ('stories', reverse('stories') + '?kind=donor&district=Dhaka'),
        ('story-create', reverse('story-create')),
        ('story-mine', reverse('story-mine')),
    ]
    for req in (c.req_open, c.req_flagged, c.req_closed, c.req_expired):
        paths.append(('request-detail', reverse('request-detail', args=[req.pk])))
    for key in ('hidden', 'private_card', 'visible', 'owner', 'directory'):
        paths.append(('badge-card', reverse('badge-card', args=[u[key].pk])))
    for squad in (c.squad, c.private_squad):
        paths.append(('squads:detail', reverse('squads:detail', args=[squad.pk])))
    for story in (c.story_anon, c.story_optout, c.story_pending):
        paths.append(('story-detail', reverse('story-detail', args=[story.pk])))
    return paths
