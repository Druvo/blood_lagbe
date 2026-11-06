# flake8: noqa: E501 (long literal lines in tables of URLs and payloads)
"""Privacy audit: crawls every public and logged-in URL as several kinds of
viewer and checks that phone numbers, emails and the names of opted-out or
anonymous people never show up, except where the rules allow it.

The rules, in one place:
* a request's contact phone (and the patient's first name) is shown to
  logged-in viewers on that request's detail page, while it is active and not
  flagged (flagged: only its owner and staff);
* the people who offered to help are shown, with phone, to the requester and
  staff on that request's page;
* a donor's phone is shown to a member only after the donor accepted that
  member's contact request, and a member's phone to the donor in the same
  case;
* every person sees their own details;
* an opted-out or anonymous person's name is shown to nobody else.
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from django.conf import settings
from django.core import mail
from django.test import Client, TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from insights import stats
from rewards.audit_fixtures import (
    CONTACT_PHONES,
    FLAGGED_HOSPITAL,
    NAME_PROTECTED,
    PATIENT,
    PENDING_TITLE,
    PEOPLE,
    PRIVATE_SQUAD,
    build_cast,
    crawl_paths,
    email_for,
    lanes_paths,
)
from rewards.models import Donation


def identity(key):
    first, last, phone = PEOPLE[key]
    return {phone, email_for(key), f'{first} {last}', last}


def phones_and_emails(key):
    return {PEOPLE[key][2], email_for(key)}


class Links(HTMLParser):
    """Every href/action in a page, and the names of GET form fields."""

    def __init__(self):
        super().__init__()
        self.urls = []
        self.get_fields = []
        self._form_is_get = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for attr in ('href', 'action', 'src'):
            if attrs.get(attr):
                self.urls.append(attrs[attr])
        if tag == 'form':
            self._form_is_get = (attrs.get('method') or 'get').lower() == 'get'
        if tag in ('input', 'select', 'textarea') and self._form_is_get:
            if attrs.get('name'):
                self.get_fields.append(attrs['name'])

    def handle_endtag(self, tag):
        if tag == 'form':
            self._form_is_get = False


def named_urls():
    """Every URL name reachable from the root URLconf, except the admin."""
    def walk(patterns, namespace=''):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                inner = namespace
                if pattern.namespace:
                    inner = f'{namespace}{pattern.namespace}:'
                yield from walk(pattern.url_patterns, inner)
            elif isinstance(pattern, URLPattern) and pattern.name:
                yield f'{namespace}{pattern.name}'
    return {
        name for name in walk(get_resolver().url_patterns)
        if not name.startswith('admin:')
    }


# URL names that only accept POST (state changes), or that need a token we
# cannot guess, or that end the session. The coverage test below keeps this
# list honest: a new URL name must be added to the crawl or to one of these.
POST_ONLY = {
    'request-respond', 'request-close', 'request-update', 'request-report',
    'contact-donor', 'contact-respond', 'contact-revoke', 'contact-block',
    'contact-unblock', 'squads:join-public', 'squads:leave',
    'squads:delete', 'squads:new-code', 'squads:kick', 'story-react',
    'story-report', 'story-delete',
    'alerts:read-all', 'alerts:read', 'events:cancel', 'events:rsvp',
    'events:rsvp-cancel', 'events:checkin', 'events:enter-squad',
    'events:withdraw-squad', 'habits:availability', 'habits:fact',
    'habits:quiz', 'moments:thank-send', 'moments:note-hide',
    'moments:update-dismiss', 'moments:freeze', 'set_language',
}
# Token links open without a login and change one setting each.
NOT_CRAWLED = {
    'logout', 'password_reset_confirm', 'alerts:email-off',
    'habits:unsubscribe', 'moments:reminder-off',
}

# Query parameters that may appear in links: filters and navigation only.
ALLOWED_QUERY_KEYS = {
    'next', 'period', 'district', 'group', 'compatible', 'available',
    'blood_group', 'when', 'page', 'kind', 'only',
    # staff desk links into filtered admin lists (status and report state)
    'status__exact', 'cleared__exact',
    # atlas map layer
    'layer',
}
# The eligibility self-check on /learn/ is hidden unless JavaScript runs and
# its submit is always intercepted, so those answers never reach a URL.
CHECKER_FIELDS = {
    'age', 'weight', 'pregnant', 'ill', 'travel', 'tattoo', 'meds', 'never',
    'last',
}
ALLOWED_GET_FIELDS = (
    ALLOWED_QUERY_KEYS | CHECKER_FIELDS | {'csrfmiddlewaretoken'}
)


class PrivacyAuditCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cast = build_cast()
        cls.users = cls.cast.users

    def client_for(self, key):
        client = Client()
        if key:
            client.force_login(self.users[key])
        return client

    def crawl_paths(self):
        return crawl_paths(self.cast)

    # ---- what each viewer may see ------------------------------------
    def always_forbidden(self):
        c = self.cast
        banned = set()
        for key in PEOPLE:
            banned |= phones_and_emails(key)
            if key in NAME_PROTECTED:
                banned |= identity(key)
        banned |= set(CONTACT_PHONES.values())
        banned |= {PATIENT, PENDING_TITLE, PRIVATE_SQUAD, FLAGGED_HOSPITAL,
                   c.squad.join_code, c.private_squad.join_code}
        return banned

    def allowed(self, viewer, name, path):
        """Strings `viewer` (a key, or None for anonymous) may see at `path`."""
        c = self.cast
        ok = set()
        if viewer:
            ok |= identity(viewer)
        logged_in = viewer is not None
        staff = viewer == 'staff'

        if viewer == 'flagger' or (staff and name == 'request-list'):
            ok.add(FLAGGED_HOSPITAL)  # theirs / the moderation view
        if name == 'request-detail':
            pk = int(path.strip('/').split('/')[-1])
            if pk == c.req_flagged.pk:
                ok.add(FLAGGED_HOSPITAL)  # still reachable by its link
            if pk == c.req_open.pk and logged_in:
                ok |= {CONTACT_PHONES['open'], PATIENT}
                if viewer in ('owner', 'staff'):
                    ok |= identity('responder') - {email_for('responder')}
            if pk == c.req_flagged.pk and viewer in ('flagger', 'staff'):
                ok.add(CONTACT_PHONES['flagged'])
        elif name == 'donor-directory' and logged_in:
            ok |= {PEOPLE['directory'][1], 'Dara Directorson'}
            if viewer == 'asker_ok':
                ok.add(PEOPLE['directory'][2])
        elif name == 'contact-inbox':
            askers = ('asker_ok', 'asker_wait', 'asker_no')
            if viewer in askers:
                ok |= {'Dara Directorson', PEOPLE['directory'][1]}
            if viewer == 'asker_ok':
                ok.add(PEOPLE['directory'][2])
            if viewer == 'directory':
                for key in askers:
                    ok |= {f'{PEOPLE[key][0]} {PEOPLE[key][1]}', PEOPLE[key][1]}
                ok.add(PEOPLE['asker_ok'][2])
        elif name in ('badge-card', 'growth:profile', 'habits:year-card-user',
                      'spotlight:style-card', 'spotlight:certificate') and staff:
            # staff may open any card, private or opted out: names only
            for key in NAME_PROTECTED:
                first, last, _phone = PEOPLE[key]
                ok |= {f'{first} {last}', last}
        elif name == 'story-detail':
            pk = int(path.strip('/').split('/')[-1])
            if staff and pk == c.story_pending.pk:
                # moderators see the pending story and who wrote it
                ok |= {PENDING_TITLE} | identity('pending_author') - phones_and_emails('pending_author')
        elif name == 'squads:join-code':
            ok |= {c.squad.join_code, c.private_squad.join_code}

        if viewer in ('visible', 'squad_owner', 'muted'):
            ok.add(c.squad.join_code)
        if viewer == 'visible':
            ok |= {c.private_squad.join_code, PRIVATE_SQUAD}
        if viewer == 'pending_author':
            ok.add(PENDING_TITLE)
        return ok

    # ---- the crawl ---------------------------------------------------
    VIEWERS = (
        None, 'stranger', 'owner', 'responder', 'flagger', 'directory',
        'asker_ok', 'asker_wait', 'asker_no', 'anon_author',
        'optout_author', 'pending_author', 'hidden', 'private_card',
        'visible', 'squad_owner', 'muted', 'staff',
    )

    def test_no_personal_data_leaks_to_any_viewer(self):
        banned = self.always_forbidden()
        problems = []
        link_problems = set()
        paths = self.crawl_paths()
        for viewer in self.VIEWERS:
            client = self.client_for(viewer)
            for name, path in paths:
                response = client.get(path)
                label = f'{viewer or "anonymous"} GET {path}'
                if response.status_code >= 500:
                    problems.append(f'{label}: status {response.status_code}')
                    continue
                if response.status_code in (301, 302):
                    body = response['Location']
                else:
                    body = response.content.decode('utf-8', 'replace')
                allowed = self.allowed(viewer, name, path)
                for secret in sorted(banned - allowed):
                    if secret in body:
                        problems.append(f'{label} leaks {secret!r}')
                if 'html' in response.get('Content-Type', ''):
                    links = Links()
                    links.feed(body)
                    for url in links.urls:
                        if urlsplit(url).scheme or urlsplit(url).netloc:
                            continue  # share links and fonts leave the site
                        for key, _ in parse_qsl(urlsplit(url).query):
                            if key not in ALLOWED_QUERY_KEYS:
                                link_problems.add(f'{label}: link {url!r}')
                    for field in links.get_fields:
                        if field not in ALLOWED_GET_FIELDS:
                            link_problems.add(f'{label}: GET field {field!r}')
        self.maxDiff = None
        self.assertEqual(problems, [])
        self.assertEqual(sorted(link_problems), [])

    def test_every_named_url_is_crawled_or_accounted_for(self):
        crawled = {name for name, _ in self.crawl_paths()}
        known = crawled | POST_ONLY | NOT_CRAWLED
        # Django's own auth views, not rendered by this site's templates.
        known |= {'password_change', 'password_change_done'}
        self.assertEqual(
            sorted(named_urls() - known), [],
            'Add the new URL name to crawl_paths() or POST_ONLY.',
        )

    def test_post_only_views_refuse_get(self):
        client = self.client_for('owner')
        c = self.cast
        lanes_paths(c)  # creates the drive, notice and thank-you note
        samples = {
            'request-respond': [c.req_open.pk],
            'request-close': [c.req_open.pk],
            'request-update': [c.req_open.pk],
            'request-report': [c.req_open.pk],
            'contact-donor': [self.users['directory'].pk],
            'contact-respond': [c.contact_wait.pk],
            'contact-revoke': [c.contact_ok.pk],
            'contact-block': [c.contact_ok.pk],
            'contact-unblock': [c.contact_ok.pk],
            'squads:join-public': [c.squad.pk],
            'squads:leave': [c.squad.pk],
            'squads:delete': [c.squad.pk],
            'squads:new-code': [c.squad.pk],
            'squads:kick': [c.squad.pk, self.users['muted'].pk],
            'story-react': [c.story_anon.pk],
            'story-report': [c.story_anon.pk],
            'story-delete': [c.story_anon.pk],
            'alerts:read-all': [],
            'alerts:read': [c.notification.pk],
            'events:cancel': [c.event.pk],
            'events:rsvp': [c.event.pk],
            'events:rsvp-cancel': [c.event.pk],
            'events:checkin': [c.event.pk, c.rsvp.pk],
            'events:enter-squad': [c.event.pk],
            'events:withdraw-squad': [c.event.pk, c.squad.pk],
            'habits:availability': [],
            'habits:fact': [],
            'habits:quiz': [],
            'moments:thank-send': [c.req_closed.pk],
            'moments:note-hide': [c.thank_note.pk],
            'moments:update-dismiss': [1],
            'moments:freeze': [],
            'set_language': [],
        }
        self.assertEqual(set(samples), POST_ONLY)
        for name, args in samples.items():
            with self.subTest(name):
                response = client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 405)

    # ---- rules checked one by one ------------------------------------
    def test_request_phone_rules(self):
        c = self.cast
        phone = CONTACT_PHONES['open']
        detail = reverse('request-detail', args=[c.req_open.pk])
        self.assertNotContains(self.client_for(None).get(detail), phone)
        self.assertContains(self.client_for('stranger').get(detail), phone)
        # flagged: hidden from strangers, visible to its owner and staff
        flagged = reverse('request-detail', args=[c.req_flagged.pk])
        number = CONTACT_PHONES['flagged']
        self.assertNotContains(self.client_for('stranger').get(flagged), number)
        self.assertContains(self.client_for('flagger').get(flagged), number)
        self.assertContains(self.client_for('staff').get(flagged), number)
        # closed and expired requests show no phone to anybody, owner included
        for req, key in ((c.req_closed, 'closed'), (c.req_expired, 'expired')):
            url = reverse('request-detail', args=[req.pk])
            for viewer in ('stranger', 'owner', 'staff'):
                self.assertNotContains(
                    self.client_for(viewer).get(url), CONTACT_PHONES[key],
                )

    def test_responders_are_visible_to_owner_and_staff_only(self):
        detail = reverse('request-detail', args=[self.cast.req_open.pk])
        phone = PEOPLE['responder'][2]
        for viewer, expected in (
            (None, False), ('stranger', False), ('responder', False),
            ('owner', True), ('staff', True), ('flagger', False),
        ):
            with self.subTest(viewer):
                body = self.client_for(viewer).get(detail).content.decode()
                self.assertEqual(phone in body, expected)

    def test_directory_phone_only_after_accept(self):
        directory_phone = PEOPLE['directory'][2]
        page = reverse('donor-directory')
        for viewer, expected in (
            ('asker_ok', True), ('asker_wait', False), ('asker_no', False),
            ('stranger', False), ('staff', False),
        ):
            with self.subTest(viewer):
                body = self.client_for(viewer).get(page).content.decode()
                self.assertEqual(directory_phone in body, expected)
        # the donor sees the asker's phone only for the accepted request
        inbox = self.client_for('directory').get(
            reverse('contact-inbox')).content.decode()
        self.assertIn(PEOPLE['asker_ok'][2], inbox)
        self.assertNotIn(PEOPLE['asker_wait'][2], inbox)
        self.assertNotIn(PEOPLE['asker_no'][2], inbox)

    def test_badge_card_rules(self):
        users = self.users
        hidden = reverse('badge-card', args=[users['hidden'].pk])
        private = reverse('badge-card', args=[users['private_card'].pk])
        visible = reverse('badge-card', args=[users['visible'].pk])
        for viewer, hidden_status, private_status in (
            (None, 404, 404), ('stranger', 404, 404),
            ('hidden', 200, 404), ('private_card', 404, 200),
            ('staff', 200, 200),
        ):
            with self.subTest(viewer):
                client = self.client_for(viewer)
                self.assertEqual(client.get(hidden).status_code, hidden_status)
                self.assertEqual(client.get(private).status_code, private_status)
        refused = self.client_for('stranger').get(private)
        self.assertNotContains(refused, 'Cardsen', status_code=404)
        # a public card carries the name and numbers, never phone or email
        page = self.client_for(None).get(visible)
        self.assertContains(page, 'Vivian Visiblesen')
        self.assertNotContains(page, PEOPLE['visible'][2])
        self.assertNotContains(page, email_for('visible'))

    def test_anonymous_and_opted_out_authors_stay_unnamed(self):
        c = self.cast
        for story, key in ((c.story_anon, 'anon_author'),
                           (c.story_optout, 'optout_author')):
            url = reverse('story-detail', args=[story.pk])
            for viewer in (None, 'stranger', 'staff'):
                with self.subTest(viewer=viewer, story=story.pk):
                    body = self.client_for(viewer).get(url).content.decode()
                    self.assertNotIn(PEOPLE[key][1], body)
                    self.assertIn('story', body.lower())
        listing = self.client_for(None).get(reverse('stories')).content.decode()
        self.assertNotIn('Authorsen', listing)
        self.assertNotIn('Outsiderson', listing)

    def test_pending_story_is_private(self):
        c = self.cast
        url = reverse('story-detail', args=[c.story_pending.pk])
        self.assertEqual(self.client_for(None).get(url).status_code, 404)
        self.assertEqual(self.client_for('stranger').get(url).status_code, 404)
        self.assertEqual(self.client_for('staff').get(url).status_code, 200)
        self.assertEqual(
            self.client_for('pending_author').get(url).status_code, 200,
        )
        self.assertNotContains(
            self.client_for(None).get(reverse('stories')), PENDING_TITLE,
        )

    def test_private_squad_is_invisible_to_outsiders(self):
        c = self.cast
        url = reverse('squads:detail', args=[c.private_squad.pk])
        self.assertEqual(self.client_for('stranger').get(url).status_code, 404)
        self.assertEqual(self.client_for('visible').get(url).status_code, 200)
        listing = self.client_for('stranger').get(reverse('squads:list'))
        self.assertNotContains(listing, PRIVATE_SQUAD)

    def test_public_squad_hides_opted_out_people(self):
        url = reverse('squads:detail', args=[self.cast.squad.pk])
        body = self.client_for(None).get(url).content.decode()
        self.assertIn('Vivian Visiblesen', body)
        self.assertNotIn('Ownerson', body)  # owner opted out
        self.assertNotIn('Memberson', body)  # member opted out
        self.assertIn('squad owner', body)

    # ---- data endpoints ----------------------------------------------
    def test_public_feeds_are_aggregates_only(self):
        banned = self.always_forbidden()
        for name in ('impact-data', 'impact-export', 'impact', 'sitemap'):
            body = self.client_for(None).get(reverse(name)).content.decode()
            for secret in banned:
                self.assertNotIn(secret, body, f'{name} leaks {secret!r}')
        payload = json.loads(
            self.client_for(None).get(reverse('impact-data')).content,
        )

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)
        for key in keys(payload):
            self.assertFalse(
                re.search(r'phone|email|name|user|donor_id|contact', key),
                f'unexpected key {key!r} in the public JSON',
            )

    def test_typed_places_that_look_like_contact_details_are_dropped(self):
        owner = self.users['owner']
        for text in ('01711 000 001', '+880 1711-000002', 'reach me a@b.example'):
            for n in range(3):
                Donation.objects.create(
                    donor=owner, blood_group='A+', district='Dhaka',
                    location=text, donation_date=Donation.objects.first()
                    .donation_date.replace(year=2000 + n),
                    status=Donation.Status.APPROVED,
                )
        stats.clear_cache()
        client = self.client_for(None)
        page = client.get(reverse('impact')).content.decode()
        for text in ('01711 000 001', '1711-000002', 'a@b.example'):
            self.assertNotIn(text, page)

    def test_stats_cache_is_shared_but_holds_no_viewer_data(self):
        stats.clear_cache()
        as_owner = self.client_for('owner').get(reverse('impact-data'))
        as_stranger = self.client_for('stranger').get(reverse('impact-data'))

        def strip(response):
            data = json.loads(response.content)
            data.pop('generated_at', None)
            return data
        self.assertEqual(strip(as_owner), strip(as_stranger))
        self.assertNotIn(
            b'Ownerson', self.client_for('stranger').get(reverse('impact')).content,
        )
        self.assertIn('public', as_owner['Cache-Control'])

    # ---- email -------------------------------------------------------
    def test_emails_never_contain_phone_numbers_or_addresses(self):
        c = self.cast
        directory = self.users['directory']
        directory.email = email_for('directory')
        directory.save()
        mail.outbox.clear()

        # a contact request mails the donor
        self.users['stranger'].profile.in_directory = False
        asker = self.client_for('stranger')
        asker.post(reverse('contact-donor', args=[directory.pk]),
                   {'message': 'Please help'})
        # a pledge mails the requester
        helper = self.client_for('visible')
        helper.post(reverse('request-respond', args=[c.req_open.pk]))
        # closing mails the people who pledged
        self.users['responder'].email = email_for('responder')
        self.users['responder'].save()
        self.client_for('owner').post(
            reverse('request-close', args=[c.req_open.pk]),
            {'outcome': 'fulfilled_here'},
        )
        # a password reset mails the account holder
        Client().post(reverse('password_reset'), {'email': email_for('owner')})

        self.assertGreaterEqual(len(mail.outbox), 3)
        secrets = set(CONTACT_PHONES.values())
        for key in PEOPLE:
            secrets |= {PEOPLE[key][2]}
        for message in mail.outbox:
            text = f'{message.subject}\n{message.body}'
            for secret in secrets:
                self.assertNotIn(secret, text, f'{message.subject!r} leaks {secret!r}')
            for key in PEOPLE:
                self.assertNotIn(email_for(key), message.body)
            self.assertIn(settings.SITE_NAME, message.subject)

    # ---- redirects and URLs -------------------------------------------
    def test_redirects_carry_no_personal_data(self):
        client = Client()
        response = client.post(reverse('login'), {
            'phone': PEOPLE['stranger'][2], 'password': 'testpass123',
            'next': reverse('request-list'),
        })
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(PEOPLE['stranger'][2], response['Location'])
        anonymous = Client().get(reverse('log-donation'))
        self.assertEqual(anonymous.status_code, 302)
        query = dict(parse_qsl(urlsplit(anonymous['Location']).query))
        self.assertEqual(list(query), ['next'])

    def test_failed_login_does_not_echo_the_password(self):
        response = Client().post(reverse('login'), {
            'phone': PEOPLE['stranger'][2], 'password': 'wrong-Secret-9',
        })
        self.assertNotContains(response, 'wrong-Secret-9')

    def test_signup_errors_do_not_reveal_other_accounts(self):
        response = Client().post(reverse('signup'), {
            'first_name': 'Dup', 'phone': PEOPLE['owner'][2],
            'password1': 'S3cure-pass-77', 'password2': 'S3cure-pass-77',
        })
        body = response.content.decode()
        self.assertNotIn('Olivia', body)
        self.assertNotIn(email_for('owner'), body)

    # ---- templates, models, assets ------------------------------------
    def test_templates_never_print_a_user_or_record_object(self):
        """str(user) is the phone number and str(donation) contains it, so a
        bare {{ user }} or {{ x.donor }} in a template would publish it."""
        bare = re.compile(
            r'\{\{\s*(?!form\.|cell\.)(?:[\w.]*\.)?'
            r'(user|donor|sender|requester|author|owner|reporter|donation|'
            r'blood_request|response|contact|membership|profile)\s*\}\}'
        )
        offenders = []
        root = Path(settings.BASE_DIR)
        for path in root.rglob('*.html'):
            if any(part in ('staticfiles', 'venv', 'env', 'node_modules')
                   for part in path.parts):
                continue
            for number, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(), start=1,
            ):
                if bare.search(line):
                    offenders.append(f'{path.relative_to(root)}:{number}')
        self.assertEqual(offenders, [])

    def test_public_model_labels_hide_the_login(self):
        from blogapp.models import Author
        author = Author.objects.get(author=self.users['blogger'])
        self.assertNotIn(PEOPLE['blogger'][2], str(author))
        self.assertIn('Bloggerson', str(author))

    def test_blog_pages_show_the_name_not_the_login(self):
        for url in (reverse('blogapp:blog-view'),
                    reverse('post-view', kwargs={'id': self.cast.post.pk})):
            body = self.client_for(None).get(url).content.decode()
            self.assertIn('Blaise Bloggerson', body)
            self.assertNotIn(PEOPLE['blogger'][2], body)
            self.assertNotIn(email_for('blogger'), body)

    def test_error_pages_are_generic(self):
        secret_path = f'/requests/99999/{PEOPLE["owner"][2]}/'
        response = self.client_for('owner').get(secret_path)
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, PEOPLE['owner'][2], status_code=404)
        # a 403 (deleting a donation that was already reviewed)
        forbidden = self.client_for('owner').get(
            reverse('donation-delete', args=[self.cast.owner_approved.pk]),
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertNotContains(forbidden, PEOPLE['owner'][2], status_code=403)

    def test_share_card_data_carries_no_contact_details(self):
        c = self.cast
        for path in (
            reverse('request-detail', args=[c.req_open.pk]),
            reverse('badge-card', args=[self.users['visible'].pk]),
        ):
            body = self.client_for('stranger').get(path).content.decode()
            match = re.search(
                r'<script id="share-data"[^>]*>(.*?)</script>', body, re.S,
            )
            self.assertIsNotNone(match, path)
            data = json.loads(match.group(1))
            text = json.dumps(data)
            self.assertNotRegex(text, r'\b01\d{9}\b')
            self.assertNotIn('@', text)

    def test_service_worker_never_caches_pages(self):
        response = self.client_for(None).get(reverse('service-worker'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn('no-cache', response['Cache-Control'])
        body = response.content.decode()
        self.assertIn("request.mode === 'navigate'", body)
        # navigations are network-only: nothing HTML is ever put in a cache
        navigate_branch = body.split("request.mode === 'navigate'")[1].split('return;')[0]
        self.assertNotIn('cache.put', navigate_branch)
        self.assertNotIn('/accounts/', body)
        self.assertNotIn('/donors/', body)
        self.assertEqual(reverse('service-worker'), '/sw.js')
