# flake8: noqa: E501 (long literal lines in tables of URLs and payloads)
"""Security audit: CSRF, authorization (IDOR), mass assignment, open
redirects, XSS, CSV injection, abuse caps, sessions, password reset,
headers and production settings."""
import csv
import datetime
import io
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile
from accounts.validators import MAX_IMAGE_BYTES
from rewards.audit_fixtures import PEOPLE, build_cast, crawl_paths, email_for
from rewards.models import (
    MAX_CONTACTS_PER_DAY,
    MAX_OPEN_REQUESTS_PER_USER,
    MAX_PENDING_CONTACTS_PER_USER,
    MAX_REPORTS_PER_DAY,
    MAX_REQUESTS_PER_DAY,
    BloodRequest,
    ContactRequest,
    Donation,
    RequestReport,
    RequestResponse,
)
from squads.models import Squad, SquadMember
from squads.services import JOIN_FAIL_LIMIT
from stories.models import (
    MAX_STORIES_PER_DAY,
    Story,
    StoryReaction,
    StoryReport,
)

User = get_user_model()
EVIL = 'https://evil.example/phish'
XSS = '<img src=x onerror=alert(1)>'
BREAKOUT = '</script><script>alert(7)</script>'


class AuditBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cast = build_cast()
        cls.users = cls.cast.users

    def client_for(self, key, **extra):
        client = Client(**extra)
        if key:
            client.force_login(self.users[key])
        return client

    def location(self, response):
        self.assertIn(response.status_code, (301, 302), response.status_code)
        return response['Location']

    def assertLocal(self, response):
        target = self.location(response)
        self.assertTrue(target.startswith('/') and not target.startswith('//'),
                        target)
        self.assertNotIn('evil.example', target)


# ---------------------------------------------------------------- CSRF

class CsrfTests(AuditBase):
    def post_targets(self):
        c = self.cast
        return {
            'request-respond': reverse('request-respond', args=[c.req_open.pk]),
            'request-close': reverse('request-close', args=[c.req_open.pk]),
            'request-update': reverse('request-update', args=[c.req_open.pk]),
            'request-report': reverse('request-report', args=[c.req_open.pk]),
            'contact-donor': reverse(
                'contact-donor', args=[self.users['directory'].pk]),
            'contact-respond': reverse(
                'contact-respond', args=[c.contact_wait.pk]),
            'squads:join-public': reverse('squads:join-public', args=[c.squad.pk]),
            'squads:leave': reverse('squads:leave', args=[c.squad.pk]),
            'squads:delete': reverse('squads:delete', args=[c.squad.pk]),
            'squads:new-code': reverse('squads:new-code', args=[c.squad.pk]),
            'squads:kick': reverse(
                'squads:kick', args=[c.squad.pk, self.users['muted'].pk]),
            'story-react': reverse('story-react', args=[c.story_anon.pk]),
            'story-report': reverse('story-report', args=[c.story_anon.pk]),
            'story-delete': reverse('story-delete', args=[c.story_anon.pk]),
            # views that take a form: still POST, still need the token
            'log-donation': reverse('log-donation'),
            'donation-delete': reverse(
                'donation-delete', args=[c.owner_pending.pk]),
            'request-create': reverse('request-create'),
            'privacy': reverse('privacy'),
            'squads:create': reverse('squads:create'),
            'squads:join': reverse('squads:join'),
            'squads:edit': reverse('squads:edit', args=[c.squad.pk]),
            'story-create': reverse('story-create'),
            'login': reverse('login'),
            'signup': reverse('signup'),
            'password_reset': reverse('password_reset'),
            'logout': reverse('logout'),
        }

    def test_every_state_changing_view_demands_a_csrf_token(self):
        for name, url in self.post_targets().items():
            for viewer in ('squad_owner', None):
                with self.subTest(name=name, viewer=viewer):
                    client = self.client_for(viewer, enforce_csrf_checks=True)
                    response = client.post(url, {})
                    self.assertEqual(response.status_code, 403)

    def test_no_get_request_changes_data(self):
        models = (
            User, Profile, Donation, BloodRequest, RequestResponse,
            RequestReport, ContactRequest, Squad, SquadMember, Story,
            StoryReaction, StoryReport,
        )
        before = {m: list(m.objects.order_by('pk').values_list('pk', flat=True))
                  for m in models}
        statuses = {
            'donations': list(Donation.objects.values_list('pk', 'status')),
            'contacts': list(ContactRequest.objects.values_list('pk', 'status')),
            'requests': list(BloodRequest.objects.values_list('pk', 'status')),
        }
        for viewer in (None, 'owner', 'staff', 'squad_owner', 'directory'):
            client = self.client_for(viewer)
            for _name, path in crawl_paths(self.cast):
                client.get(path)
        for model in models:
            self.assertEqual(
                before[model],
                list(model.objects.order_by('pk').values_list('pk', flat=True)),
                model.__name__,
            )
        self.assertEqual(
            statuses['donations'],
            list(Donation.objects.values_list('pk', 'status')),
        )
        self.assertEqual(
            statuses['contacts'],
            list(ContactRequest.objects.values_list('pk', 'status')),
        )
        self.assertEqual(
            statuses['requests'],
            list(BloodRequest.objects.values_list('pk', 'status')),
        )

    def test_every_post_form_in_a_template_has_a_csrf_token(self):
        offenders = []
        root = Path(settings.BASE_DIR)
        for path in root.rglob('*.html'):
            if any(part in ('staticfiles', 'venv', 'env') for part in path.parts):
                continue
            text = path.read_text(encoding='utf-8')
            for match in re.finditer(r'<form\b[^>]*>', text, re.I):
                tag = match.group(0)
                if not re.search(r'method\s*=\s*["\']?post', tag, re.I):
                    continue
                end = text.find('</form>', match.end())
                if '{% csrf_token %}' not in text[match.end():end]:
                    line = text.count('\n', 0, match.start()) + 1
                    offenders.append(f'{path.relative_to(root)}:{line}')
        self.assertEqual(offenders, [])

    def test_logout_is_a_post_form_in_the_menu_and_get_is_harmless(self):
        client = self.client_for('stranger')
        page = client.get(reverse('home')).content.decode()
        match = re.search(
            r'<form method="post" action="/accounts/logout/"[^>]*>(.*?)</form>',
            page, re.S,
        )
        self.assertIsNotNone(match)
        self.assertIn('csrfmiddlewaretoken', match.group(1))
        # a GET still works for old bookmarks: it only ends your own session
        response = client.get(reverse('logout'))
        self.assertLocal(response)
        self.assertNotIn('_auth_user_id', client.session)
        # and a POST works too
        client = self.client_for('stranger')
        self.assertLocal(client.post(reverse('logout')))
        self.assertNotIn('_auth_user_id', client.session)


# ------------------------------------------------- authorization (IDOR)

class AuthorizationTests(AuditBase):
    def test_other_peoples_donations_cannot_be_deleted(self):
        url = reverse('donation-delete', args=[self.cast.owner_pending.pk])
        stranger = self.client_for('stranger')
        self.assertEqual(stranger.get(url).status_code, 404)
        self.assertEqual(stranger.post(url).status_code, 404)
        self.assertEqual(self.client_for('staff').post(url).status_code, 404)
        self.assertTrue(Donation.objects.filter(pk=self.cast.owner_pending.pk).exists())
        approved = reverse('donation-delete', args=[self.cast.owner_approved.pk])
        self.assertEqual(self.client_for('owner').post(approved).status_code, 403)
        self.assertTrue(Donation.objects.filter(pk=self.cast.owner_approved.pk).exists())

    def test_request_actions_belong_to_the_requester(self):
        req = self.cast.req_open
        stranger = self.client_for('stranger')
        stranger.post(reverse('request-close', args=[req.pk]),
                      {'outcome': 'no_longer_needed'})
        stranger.post(reverse('request-update', args=[req.pk]),
                      {'kind': 'note', 'text': 'hijack'})
        req.refresh_from_db()
        self.assertEqual(req.status, BloodRequest.Status.OPEN)
        self.assertFalse(req.updates.filter(text='hijack').exists())
        # staff may close, the requester may close
        self.client_for('staff').post(
            reverse('request-close', args=[req.pk]), {'outcome': 'no_longer_needed'})
        req.refresh_from_db()
        self.assertEqual(req.status, BloodRequest.Status.CLOSED)

    def test_staff_cannot_post_updates_as_the_requester(self):
        req = self.cast.req_open
        self.client_for('staff').post(
            reverse('request-update', args=[req.pk]),
            {'kind': 'note', 'text': 'from staff'})
        self.assertFalse(req.updates.filter(text='from staff').exists())

    def test_nobody_reports_their_own_request(self):
        req = self.cast.req_open
        self.client_for('owner').post(
            reverse('request-report', args=[req.pk]), {'reason': 'spam'})
        self.assertFalse(req.reports.filter(reporter=self.users['owner']).exists())

    def test_only_the_donor_answers_a_contact_request(self):
        contact = self.cast.contact_wait
        url = reverse('contact-respond', args=[contact.pk])
        for viewer in ('stranger', 'asker_wait', 'staff'):
            response = self.client_for(viewer).post(url, {'answer': 'accept'})
            self.assertEqual(response.status_code, 404, viewer)
        contact.refresh_from_db()
        self.assertEqual(contact.status, ContactRequest.Status.PENDING)
        self.client_for('directory').post(url, {'answer': 'accept'})
        contact.refresh_from_db()
        self.assertEqual(contact.status, ContactRequest.Status.ACCEPTED)

    def test_squad_actions_belong_to_the_owner(self):
        squad = self.cast.squad
        member = self.client_for('visible')
        outsider = self.client_for('stranger')
        muted_pk = self.users['muted'].pk
        for client in (member, outsider):
            client.post(reverse('squads:delete', args=[squad.pk]))
            client.post(reverse('squads:new-code', args=[squad.pk]))
            client.post(reverse('squads:kick', args=[squad.pk, muted_pk]))
            client.post(reverse('squads:edit', args=[squad.pk]), {
                'name': 'Taken over', 'description': '', 'district': '',
                'is_public': 'on',
            })
        old_code = squad.join_code
        squad.refresh_from_db()
        self.assertEqual(squad.join_code, old_code)
        self.assertEqual(squad.name, 'Audit Public Squad')
        self.assertTrue(SquadMember.objects.filter(
            squad=squad, user_id=muted_pk).exists())

    def test_private_squad_actions_are_404_for_outsiders(self):
        squad = self.cast.private_squad
        outsider = self.client_for('stranger')
        for name in ('squads:edit', 'squads:delete', 'squads:new-code'):
            response = outsider.post(reverse(name, args=[squad.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.assertEqual(
            outsider.post(reverse('squads:join-public', args=[squad.pk])).status_code,
            404,
        )
        self.assertFalse(SquadMember.objects.filter(
            squad=squad, user=self.users['stranger']).exists())

    def test_story_actions_belong_to_the_author(self):
        story = self.cast.story_anon
        self.assertEqual(
            self.client_for('stranger').post(
                reverse('story-delete', args=[story.pk])).status_code, 404)
        self.assertEqual(
            self.client_for('staff').post(
                reverse('story-delete', args=[story.pk])).status_code, 404)
        self.assertTrue(Story.objects.filter(pk=story.pk).exists())
        # unpublished stories take no reactions or reports
        pending = self.cast.story_pending
        client = self.client_for('stranger')
        self.assertEqual(client.post(
            reverse('story-react', args=[pending.pk]), {'kind': 'heart'}
        ).status_code, 404)
        self.assertEqual(client.post(
            reverse('story-report', args=[pending.pk]), {'reason': 'spam'}
        ).status_code, 404)

    def test_exports_hold_only_the_viewers_own_rows(self):
        Donation.objects.create(
            donor=self.users['stranger'], blood_group='B+', district='Dhaka',
            location='Stella Private Clinic', status='approved',
            donation_date=timezone.localdate() - datetime.timedelta(days=500),
        )
        mine = self.client_for('stranger').get(reverse('donation-export'))
        self.assertIn('Stella Private Clinic', mine.content.decode())
        theirs = self.client_for('owner').get(reverse('donation-export'))
        self.assertNotIn('Stella Private Clinic', theirs.content.decode())
        history = self.client_for('owner').get(reverse('donation-history'))
        self.assertNotContains(history, 'Stella Private Clinic')

    def test_private_pages_need_a_login(self):
        for name in ('log-donation', 'donation-export', 'donation-history',
                     'donor-card', 'request-create', 'request-mine',
                     'donor-directory', 'contact-inbox', 'squads:list',
                     'squads:create', 'story-create', 'story-mine', 'privacy',
                     'welcome'):
            response = Client().get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn('/accounts/login/', response['Location'], name)

    def test_the_admin_is_for_staff(self):
        self.assertEqual(Client().get('/admin/').status_code, 302)
        response = self.client_for('stranger').get('/admin/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])


# -------------------------------------------------- mass assignment

class MassAssignmentTests(AuditBase):
    def test_privacy_form_cannot_grant_staff(self):
        user = self.users['stranger']
        self.client_for('stranger').post(reverse('privacy'), {
            'blood_group': 'A+', 'district': 'Dhaka', 'staff': 'on',
            'admin': 'on', 'is_active': '', 'user': self.users['owner'].pk,
            'is_staff': 'on', 'is_superuser': 'on',
        })
        user.refresh_from_db()
        self.assertFalse(user.staff or user.admin)
        self.assertTrue(user.is_active)
        self.assertEqual(user.profile.user_id, user.pk)

    def test_donation_form_cannot_self_approve_or_pick_a_donor(self):
        owner = self.users['owner']
        self.client_for('stranger').post(reverse('log-donation'), {
            'blood_group': 'A+', 'district': 'Dhaka', 'location': 'Mass Clinic',
            'donation_date': (timezone.localdate() - datetime.timedelta(days=3)).isoformat(),
            'status': 'approved', 'verified_by': owner.pk, 'donor': owner.pk,
            'admin_note': 'pre-approved',
        })
        donation = Donation.objects.get(location='Mass Clinic')
        self.assertEqual(donation.status, Donation.Status.PENDING)
        self.assertEqual(donation.donor, self.users['stranger'])
        self.assertIsNone(donation.verified_by)
        self.assertEqual(donation.admin_note, '')

    def test_donation_cannot_be_linked_to_a_request_never_pledged_to(self):
        self.client_for('stranger').post(reverse('log-donation'), {
            'blood_group': 'A+', 'district': 'Dhaka', 'location': 'Link Clinic',
            'donation_date': (timezone.localdate() - datetime.timedelta(days=3)).isoformat(),
            'blood_request': self.cast.req_open.pk,
        })
        self.assertFalse(Donation.objects.filter(location='Link Clinic').exists())

    def test_request_form_cannot_set_status_or_requester(self):
        owner = self.users['owner']
        self.client_for('stranger').post(reverse('request-create'), {
            'blood_group': 'O-', 'district': 'Dhaka', 'hospital': 'Mass Hospital',
            'units_needed': 1,
            'needed_by': (timezone.localdate() + datetime.timedelta(days=2)).isoformat(),
            'contact_phone': '01977700001', 'status': 'closed',
            'requester': owner.pk, 'outcome': 'fulfilled_here',
        })
        req = BloodRequest.objects.get(hospital='Mass Hospital')
        self.assertEqual(req.requester, self.users['stranger'])
        self.assertEqual(req.status, BloodRequest.Status.OPEN)
        self.assertEqual(req.outcome, '')
        self.assertIsNone(req.closed_at)

    def test_squad_forms_cannot_set_owner_or_code(self):
        client = self.client_for('stranger')
        client.post(reverse('squads:create'), {
            'name': 'Mass Squad', 'description': '', 'district': '',
            'owner': self.users['owner'].pk, 'join_code': 'AAAAAAAA',
        })
        squad = Squad.objects.get(name='Mass Squad')
        self.assertEqual(squad.owner, self.users['stranger'])
        self.assertNotEqual(squad.join_code, 'AAAAAAAA')
        client.post(reverse('squads:edit', args=[squad.pk]), {
            'name': 'Mass Squad', 'description': '', 'district': '',
            'owner': self.users['owner'].pk, 'join_code': 'BBBBBBBB',
        })
        squad.refresh_from_db()
        self.assertEqual(squad.owner, self.users['stranger'])
        self.assertNotEqual(squad.join_code, 'BBBBBBBB')

    def test_story_form_cannot_publish_itself(self):
        self.client_for('stranger').post(reverse('story-create'), {
            'kind': 'donor', 'title': 'Mass story title',
            'body': 'A perfectly fine true story about donating blood once.',
            'status': 'approved', 'approved_at': '2020-01-01 00:00:00',
            'author': self.users['owner'].pk, 'moderation_note': 'ok',
        })
        story = Story.objects.get(title='Mass story title')
        self.assertEqual(story.status, Story.Status.PENDING)
        self.assertEqual(story.author, self.users['stranger'])
        self.assertIsNone(story.approved_at)
        self.assertEqual(story.moderation_note, '')

    def test_signup_cannot_create_staff(self):
        Client().post(reverse('signup'), {
            'first_name': 'Sly', 'phone': '01988800001', 'staff': 'on',
            'admin': 'on', 'is_active': 'on',
            'password1': 'Sturdy-pass-4411', 'password2': 'Sturdy-pass-4411',
        })
        user = User.objects.get(phone='01988800001')
        self.assertFalse(user.staff or user.admin)


# ------------------------------------------------------ open redirects

class RedirectTests(AuditBase):
    BAD = (EVIL, '//evil.example/x', '/\\evil.example', 'javascript:alert(1)',
           'http:evil.example', ' //evil.example', '\\\\evil.example')

    def test_login_and_signup_ignore_foreign_next(self):
        for bad in self.BAD:
            with self.subTest(bad):
                response = Client().post(reverse('login'), {
                    'phone': PEOPLE['stranger'][2], 'password': 'testpass123',
                    'next': bad,
                })
                self.assertLocal(response)
                response = Client().get(reverse('login') + '?next=' + bad)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'evil.example')

    def test_login_keeps_a_local_next(self):
        response = Client().post(reverse('login'), {
            'phone': PEOPLE['stranger'][2], 'password': 'testpass123',
            'next': '/requests/',
        })
        self.assertEqual(response['Location'], '/requests/')

    def test_signup_ignores_foreign_next(self):
        for n, bad in enumerate(self.BAD):
            with self.subTest(bad):
                response = Client().post(reverse('signup'), {
                    'first_name': 'Nxt', 'phone': f'0199990{n:04d}',
                    'next': bad, 'password1': 'Sturdy-pass-4411',
                    'password2': 'Sturdy-pass-4411',
                })
                self.assertLocal(response)

    def test_post_actions_ignore_foreign_next(self):
        c = self.cast
        for bad in self.BAD:
            with self.subTest(bad):
                client = self.client_for('stranger')
                response = client.post(
                    reverse('story-react', args=[c.story_anon.pk]),
                    {'kind': 'heart', 'next': bad})
                self.assertLocal(response)
                response = self.client_for('owner').post(
                    reverse('request-close', args=[c.req_expired.pk]),
                    {'outcome': 'no_longer_needed', 'next': bad})
                self.assertLocal(response)
                response = self.client_for('stranger').post(
                    reverse('contact-donor', args=[self.users['directory'].pk]),
                    {'message': '', 'next': bad})
                self.assertLocal(response)

    def test_invite_link_login_redirect_stays_on_site(self):
        response = Client().post(
            reverse('squads:join-code', args=[self.cast.squad.join_code]))
        self.assertLocal(response)


# ---------------------------------------------------------------- XSS

class XssTests(AuditBase):
    def test_user_text_is_escaped_everywhere_it_is_shown(self):
        owner = self.users['owner']
        c = self.cast
        req = BloodRequest.objects.create(
            requester=owner, blood_group='A+', district='Dhaka',
            hospital=XSS + BREAKOUT, units_needed=1,
            needed_by=timezone.localdate() + datetime.timedelta(days=2),
            contact_phone='01977700002', patient_name='Pat', note=XSS,
        )
        req.updates.create(author=owner, kind='note', text=XSS)
        Donation.objects.create(
            donor=owner, blood_group='A+', district='Dhaka', location=XSS,
            status='approved',
            donation_date=timezone.localdate() - datetime.timedelta(days=700),
        )
        c.squad.description = XSS
        c.squad.save()
        Squad.objects.create(name=XSS, owner=owner, is_public=True)
        directory = self.users['directory']
        ContactRequest.objects.create(
            sender=owner, donor=directory, message=XSS,
        )
        pages = [
            (None, reverse('request-list')),
            (None, reverse('request-detail', args=[req.pk])),
            (None, reverse('home')),
            (None, reverse('impact-district', args=['Dhaka'])),
            ('owner', reverse('donation-history')),
            ('owner', reverse('log-donation')),
            ('owner', reverse('request-mine')),
            ('owner', reverse('squads:list')),
            ('directory', reverse('contact-inbox')),
            (None, reverse('squads:detail', args=[c.squad.pk])),
        ]
        for viewer, url in pages:
            with self.subTest(url=url, viewer=viewer):
                body = self.client_for(viewer).get(url).content.decode()
                self.assertNotIn('<img src=x', body)
                self.assertNotIn('</script><script>alert', body)
        detail = self.client_for(None).get(
            reverse('request-detail', args=[req.pk])).content.decode()
        self.assertIn('&lt;img src=x', detail)
        # the share-card JSON cannot close its own script tag
        shared = self.client_for('stranger').get(
            reverse('request-detail', args=[req.pk])).content.decode()
        self.assertIn('\\u003C/script\\u003E', shared)

    def test_stories_refuse_markup(self):
        client = self.client_for('stranger')
        response = client.post(reverse('story-create'), {
            'kind': 'donor', 'title': 'Hello <b>there</b>',
            'body': 'A fine story. <script>alert(1)</script> and more words here.',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Story.objects.filter(author=self.users['stranger']).exists())

    def test_template_filters_do_not_mark_user_text_safe(self):
        offenders = []
        root = Path(settings.BASE_DIR)
        for path in list(root.rglob('*.html')) + list(root.rglob('*.py')):
            if any(part in ('staticfiles', 'venv', 'env', 'migrations')
                   for part in path.parts) or path.name.startswith('test_'):
                continue
            text = path.read_text(encoding='utf-8')
            for token in ('|safe', 'autoescape off'):
                if token in text:
                    offenders.append(f'{path.relative_to(root)}: {token}')
        # the password reset mail is plain text and fed only by Django
        self.assertEqual(
            offenders,
            ['accounts\\templates\\registration\\password_reset_email.html: autoescape off'
             if os.sep == '\\' else
             'accounts/templates/registration/password_reset_email.html: autoescape off'],
        )

    def test_javascript_never_writes_user_data_with_innerhtml(self):
        """innerHTML is only used with static strings or escaped text."""
        root = Path(settings.BASE_DIR)
        allowed = {
            'staticfiles/js/bb-learn.js': 5,   # esc()-wrapped / static markup
            'staticfiles/js/share-card.js': 3,  # restores its own button label
        }
        found = {}
        files = [root / 'staticfiles/js/bloodbond.js',
                 root / 'staticfiles/js/bb-auth.js',
                 root / 'staticfiles/js/place-select.js',
                 root / 'staticfiles/js/bb-learn.js',
                 root / 'staticfiles/js/share-card.js',
                 root / 'stories/static/stories/stories.js']
        for path in files:
            text = path.read_text(encoding='utf-8')
            count = len(re.findall(r'innerHTML|insertAdjacentHTML|document\.write', text))
            if count:
                found[path.relative_to(root).as_posix()] = count
        for name, count in found.items():
            self.assertLessEqual(count, allowed.get(name, 0), name)


# ---------------------------------------------------- CSV and queries

class CsvAndQueryTests(AuditBase):
    def test_csv_cells_cannot_become_formulas(self):
        owner = self.users['owner']
        evil = ['=HYPERLINK("http://evil.example","x")', '+1+1', '-2+3',
                '@SUM(1)', '  =1+1', '\t=1+1']
        for n, text in enumerate(evil):
            Donation.objects.create(
                donor=owner, blood_group='A+', district='Dhaka', location=text,
                status='approved',
                donation_date=timezone.localdate() - datetime.timedelta(days=900 + n),
            )
        response = self.client_for('owner').get(reverse('donation-export'))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        places = [row[3] for row in rows[1:]]
        for text in evil:
            self.assertIn("'" + text, places)
        for place in places:
            self.assertNotRegex(place, r'^\s*[=+\-@\t]')

    def test_odd_query_values_never_crash_or_break_the_database(self):
        payloads = ["' OR 1=1 --", '"; DROP TABLE accounts_user;--', '%00',
                    'A' * 5000, '../../etc/passwd', '1; SELECT 1']
        urls = [
            reverse('request-list') + '?blood_group={}&district={}&when={}&page={}',
            reverse('donor-directory') + '?group={}&district={}&available={}&page={}',
            reverse('leaderboard') + '?period={}&district={}',
            reverse('stories') + '?kind={}&district={}&page={}',
            reverse('impact') + '?period={}',
            reverse('squads:list') + '?district={}',
            reverse('squads:leaderboard') + '?period={}',
        ]
        client = self.client_for('stranger')
        for template in urls:
            for payload in payloads:
                filled = template.format(*([payload] * template.count('{}')))
                self.assertLess(client.get(filled).status_code, 500, filled)
        for payload in payloads:
            for name in ('impact-district',):
                response = client.get(reverse(name, args=['Dhaka']) + '?x=' + payload)
                self.assertLess(response.status_code, 500)
            self.assertEqual(
                client.get('/impact/district/' + payload.replace('/', '') + '/').status_code, 404)
            self.assertEqual(
                client.get('/squads/join/' + 'ZZ' + '/').status_code, 200)
        self.assertEqual(User.objects.count(), len(PEOPLE))


# ------------------------------------------------------ abuse limits

class AbuseCapTests(AuditBase):
    def test_login_guessing_is_throttled(self):
        phone = PEOPLE['stranger'][2]
        for _ in range(8):
            response = Client().post(reverse('login'), {
                'phone': phone, 'password': 'wrong-password-1'})
            self.assertEqual(response.status_code, 200)
        client = Client()
        locked = client.post(reverse('login'), {
            'phone': phone, 'password': 'testpass123'})
        self.assertEqual(locked.status_code, 429)
        self.assertContains(locked, 'Too many tries', status_code=429)
        self.assertNotIn('_auth_user_id', client.session)
        # a different address is not locked out by someone else's guesses
        other = Client(REMOTE_ADDR='10.9.8.7').post(reverse('login'), {
            'phone': phone, 'password': 'testpass123'})
        self.assertEqual(other.status_code, 302)

    def test_successful_login_clears_the_failure_count(self):
        phone = PEOPLE['stranger'][2]
        for _ in range(5):
            Client().post(reverse('login'), {
                'phone': phone, 'password': 'wrong-password-1'})
        ok = Client().post(reverse('login'), {
            'phone': phone, 'password': 'testpass123'})
        self.assertEqual(ok.status_code, 302)
        for _ in range(5):
            Client().post(reverse('login'), {
                'phone': phone, 'password': 'wrong-password-1'})
        again = Client().post(reverse('login'), {
            'phone': phone, 'password': 'testpass123'})
        self.assertEqual(again.status_code, 302)

    def test_account_creation_is_capped_per_connection(self):
        from accounts.views import SIGNUPS_PER_IP_PER_HOUR
        for n in range(SIGNUPS_PER_IP_PER_HOUR):
            response = Client().post(reverse('signup'), {
                'first_name': 'Bulk', 'phone': f'0198{n:07d}',
                'password1': 'Sturdy-pass-4411', 'password2': 'Sturdy-pass-4411'})
            self.assertEqual(response.status_code, 302)
        response = Client().post(reverse('signup'), {
            'first_name': 'Bulk', 'phone': '01980009999',
            'password1': 'Sturdy-pass-4411', 'password2': 'Sturdy-pass-4411'})
        self.assertEqual(response.status_code, 429)
        self.assertFalse(User.objects.filter(phone='01980009999').exists())
        # another connection is unaffected
        other = Client(REMOTE_ADDR='10.1.2.3').post(reverse('signup'), {
            'first_name': 'Bulk', 'phone': '01980009998',
            'password1': 'Sturdy-pass-4411', 'password2': 'Sturdy-pass-4411'})
        self.assertEqual(other.status_code, 302)

    def test_password_reset_mail_is_capped_without_revealing_accounts(self):
        mail.outbox.clear()
        statuses = set()
        for _ in range(8):
            response = Client().post(
                reverse('password_reset'), {'email': email_for('owner')})
            statuses.add((response.status_code, response['Location']))
        self.assertEqual(statuses, {(302, reverse('password_reset_done'))})
        self.assertEqual(len(mail.outbox), 5)
        # an unknown address gets the very same answer
        response = Client().post(
            reverse('password_reset'), {'email': 'nobody@audit.test'})
        self.assertEqual(response['Location'], reverse('password_reset_done'))

    def test_contact_requests_have_pending_and_daily_caps(self):
        sender = self.users['stranger']
        donors = []
        for n in range(MAX_PENDING_CONTACTS_PER_USER + 1):
            donor = User.objects.create_user(
                phone=f'0197{n:07d}', password='x')
            donor.profile.in_directory = True
            donor.profile.blood_group = 'A+'
            donor.profile.district = 'Dhaka'
            donor.profile.save()
            donors.append(donor)
        client = self.client_for('stranger')
        for donor in donors:
            client.post(reverse('contact-donor', args=[donor.pk]), {'message': ''})
        self.assertEqual(
            ContactRequest.objects.filter(sender=sender).count(),
            MAX_PENDING_CONTACTS_PER_USER,
        )
        # daily cap, even when every earlier request was answered
        ContactRequest.objects.filter(sender=sender).update(
            status=ContactRequest.Status.DECLINED,
            answered_at=timezone.now() - datetime.timedelta(days=40),
        )
        extra = []
        for n in range(MAX_CONTACTS_PER_DAY):
            donor = User.objects.create_user(
                phone=f'0196{n:07d}', password='x')
            extra.append(ContactRequest(sender=sender, donor=donor, status='declined',
                                        answered_at=timezone.now()))
        ContactRequest.objects.bulk_create(extra)
        fresh = donors[0]
        ContactRequest.objects.filter(sender=sender, donor=fresh).delete()
        before = ContactRequest.objects.filter(sender=sender).count()
        client.post(reverse('contact-donor', args=[fresh.pk]), {'message': ''})
        self.assertEqual(ContactRequest.objects.filter(sender=sender).count(), before)

    def test_join_code_guessing_is_capped(self):
        client = self.client_for('stranger')
        for _ in range(JOIN_FAIL_LIMIT):
            client.post(reverse('squads:join'), {'code': 'WRONGCODE'})
        response = client.post(
            reverse('squads:join'), {'code': self.cast.squad.join_code})
        self.assertEqual(response.status_code, 200)  # refused, not joined
        self.assertFalse(SquadMember.objects.filter(
            squad=self.cast.squad, user=self.users['stranger']).exists())

    def test_invite_link_guessing_without_an_account_is_capped(self):
        client = Client()
        for n in range(30):
            client.get(reverse('squads:join-code', args=[f'GUESS{n:03d}']))
        response = client.get(
            reverse('squads:join-code', args=[self.cast.squad.join_code]))
        self.assertNotContains(response, 'Audit Public Squad')

    def test_request_reports_are_capped_per_day(self):
        reporter = self.users['stranger']
        others = []
        for n in range(MAX_REPORTS_PER_DAY):
            owner = User.objects.create_user(phone=f'0195{n:07d}', password='x')
            others.append(BloodRequest.objects.create(
                requester=owner, blood_group='A+', district='Dhaka',
                hospital='H', units_needed=1, contact_phone='01912345678',
                needed_by=timezone.localdate() + datetime.timedelta(days=2)))
        client = self.client_for('stranger')
        for req in others:
            client.post(reverse('request-report', args=[req.pk]), {'reason': 'spam'})
        self.assertEqual(
            RequestReport.objects.filter(reporter=reporter).count(),
            MAX_REPORTS_PER_DAY)
        client.post(reverse('request-report', args=[self.cast.req_open.pk]),
                    {'reason': 'spam'})
        self.assertEqual(
            RequestReport.objects.filter(reporter=reporter).count(),
            MAX_REPORTS_PER_DAY)

    def test_request_posting_has_open_and_daily_caps(self):
        user = self.users['stranger']
        client = self.client_for('stranger')

        def post(hospital):
            return client.post(reverse('request-create'), {
                'blood_group': 'A+', 'district': 'Dhaka', 'hospital': hospital,
                'units_needed': 1,
                'needed_by': (timezone.localdate() + datetime.timedelta(days=2)).isoformat(),
                'contact_phone': '01977700003',
            })
        for n in range(MAX_OPEN_REQUESTS_PER_USER + 1):
            post(f'Cap Hospital {n}')
        self.assertEqual(
            BloodRequest.objects.filter(requester=user).count(),
            MAX_OPEN_REQUESTS_PER_USER)
        BloodRequest.objects.filter(requester=user).update(status='closed')
        for n in range(MAX_REQUESTS_PER_DAY):
            BloodRequest.objects.create(
                requester=user, blood_group='A+', district='Dhaka', hospital='X',
                units_needed=1, contact_phone='01977700004', status='closed',
                needed_by=timezone.localdate() + datetime.timedelta(days=2))
        count = BloodRequest.objects.filter(requester=user).count()
        post('One too many today')
        self.assertEqual(BloodRequest.objects.filter(requester=user).count(), count)

    def test_story_posting_and_reporting_are_capped(self):
        user = self.users['stranger']
        client = self.client_for('stranger')
        for n in range(MAX_STORIES_PER_DAY):
            Story.objects.create(
                author=user, title=f'Earlier story {n}', status='approved',
                body='A perfectly fine true story about donating blood once.')
        client.post(reverse('story-create'), {
            'kind': 'donor', 'title': 'Story number six',
            'body': 'A perfectly fine true story about donating blood once.'})
        self.assertFalse(Story.objects.filter(title='Story number six').exists())
        for n in range(10):
            other = Story.objects.create(
                author=self.users['owner'], title=f'Reportable {n}',
                status='approved',
                body='A perfectly fine true story about donating blood once.')
            client.post(reverse('story-report', args=[other.pk]), {'reason': 'spam'})
        self.assertEqual(StoryReport.objects.filter(reporter=user).count(), 10)
        other = Story.objects.create(
            author=self.users['owner'], title='Reportable late', status='approved',
            body='A perfectly fine true story about donating blood once.')
        client.post(reverse('story-report', args=[other.pk]), {'reason': 'spam'})
        self.assertEqual(StoryReport.objects.filter(reporter=user).count(), 10)

    def test_pledging_is_capped_per_day(self):
        from rewards.models import MAX_PLEDGES_PER_DAY
        user = self.users['stranger']
        for n in range(MAX_PLEDGES_PER_DAY):
            owner = User.objects.create_user(phone=f'0194{n:07d}', password='x')
            req = BloodRequest.objects.create(
                requester=owner, blood_group='A+', district='Dhaka',
                hospital='H', units_needed=1, contact_phone='01912345678',
                needed_by=timezone.localdate() + datetime.timedelta(days=2))
            RequestResponse.objects.create(blood_request=req, donor=user)
        before = RequestResponse.objects.filter(donor=user).count()
        self.client_for('stranger').post(
            reverse('request-respond', args=[self.cast.req_open.pk]))
        self.assertEqual(RequestResponse.objects.filter(donor=user).count(), before)


# --------------------------------------- sessions, passwords, headers

class SessionAndPasswordTests(AuditBase):
    def test_login_rotates_the_session_key(self):
        client = Client()
        session = client.session
        session['planted'] = 'yes'
        session.save()
        old_key = session.session_key
        client.cookies['sessionid'] = old_key
        response = client.post(reverse('login'), {
            'phone': PEOPLE['stranger'][2], 'password': 'testpass123'})
        self.assertEqual(response.status_code, 302)
        new_key = client.session.session_key
        self.assertNotEqual(old_key, new_key)
        self.assertFalse(Session.objects.filter(session_key=old_key).exists())

    def test_logout_destroys_the_session(self):
        client = self.client_for('stranger')
        key = client.session.session_key
        client.post(reverse('logout'))
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    def test_weak_passwords_are_refused_at_signup(self):
        for weak in ('12345678', 'password', 'short1', '01933300009',
                     'Sophiaaaa'):
            with self.subTest(weak):
                response = Client().post(reverse('signup'), {
                    'first_name': 'Sophiaaaa', 'phone': '01933300009',
                    'password1': weak, 'password2': weak})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(User.objects.filter(phone='01933300009').exists())

    def test_password_validators_are_the_django_defaults(self):
        names = [v['NAME'].rsplit('.', 1)[1] for v in settings.AUTH_PASSWORD_VALIDATORS]
        self.assertEqual(names, [
            'UserAttributeSimilarityValidator', 'MinimumLengthValidator',
            'CommonPasswordValidator', 'NumericPasswordValidator'])
        minimum = [v for v in settings.AUTH_PASSWORD_VALIDATORS
                   if v['NAME'].endswith('MinimumLengthValidator')][0]
        self.assertGreaterEqual(minimum['OPTIONS']['min_length'], 8)

    def test_password_reset_link_works_once(self):
        mail.outbox.clear()
        Client().post(reverse('password_reset'), {'email': email_for('stranger')})
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r'https?://\S+/accounts/reset/\S+', mail.outbox[0].body).group(0)
        path = '/' + link.split('/', 3)[3]
        client = Client()
        first = client.get(path)
        self.assertEqual(first.status_code, 302)  # token moves into the session
        self.assertIn('set-password', first['Location'])
        self.assertNotIn(path.rstrip('/').rsplit('/', 1)[1], first['Location'])
        done = client.post(first['Location'], {
            'new_password1': 'Brand-new-pass-90', 'new_password2': 'Brand-new-pass-90'})
        self.assertEqual(done.status_code, 302)
        self.assertTrue(Client().login(
            phone=PEOPLE['stranger'][2], password='Brand-new-pass-90'))
        reused = Client().get(path, follow=True)
        self.assertFalse(reused.context['validlink'])

    def test_bad_reset_token_is_refused(self):
        response = Client().get('/accounts/reset/MQ/not-a-real-token/', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['validlink'])

    def test_security_headers_are_present(self):
        for viewer in (None, 'stranger'):
            response = self.client_for(viewer).get(reverse('home'))
            self.assertEqual(response['X-Frame-Options'], 'DENY')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response['Referrer-Policy'], 'same-origin')

    def test_pages_for_signed_in_people_are_not_shared_cacheable(self):
        response = self.client_for('stranger').get(reverse('request-list'))
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-cache', response['Cache-Control'])
        # views that set their own policy keep it
        feed = self.client_for('stranger').get(reverse('impact-data'))
        self.assertIn('public', feed['Cache-Control'])
        # anonymous pages are untouched
        self.assertFalse(Client().get(reverse('about')).has_header('Cache-Control'))
        # login and signup are never cached
        self.assertIn('no-cache', Client().get(reverse('login'))['Cache-Control'])

    def test_session_cookie_flags(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, 'Lax')
        self.assertEqual(settings.X_FRAME_OPTIONS, 'DENY')
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)

    def test_robots_keeps_private_areas_out_of_search(self):
        body = Client().get(reverse('robots')).content.decode()
        for path in ('/admin/', '/accounts/', '/donations/', '/donors/'):
            self.assertIn(f'Disallow: {path}', body)

    def test_invite_and_private_pages_are_noindex(self):
        for url in (
            reverse('squads:join-code', args=[self.cast.squad.join_code]),
            reverse('squads:detail', args=[self.cast.squad.pk]),
            reverse('login'),
        ):
            body = self.client_for(None).get(url).content.decode()
            self.assertIn('name="robots" content="noindex"', body, url)


# ------------------------------------------------------- file uploads

class UploadTests(TestCase):
    def png(self, size=64):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (size, size), 'red').save(buffer, 'PNG')
        return buffer.getvalue()

    def clean(self, upload):
        return Profile._meta.get_field('image').formfield().clean(upload)

    def test_a_small_png_is_accepted(self):
        self.clean(SimpleUploadedFile('me.png', self.png(), 'image/png'))

    def test_wrong_extension_is_refused(self):
        with self.assertRaises(ValidationError):
            self.clean(SimpleUploadedFile('me.svg', self.png(), 'image/png'))
        with self.assertRaises(ValidationError):
            self.clean(SimpleUploadedFile('me.php', self.png(), 'image/png'))

    def test_text_posing_as_an_image_is_refused(self):
        with self.assertRaises(ValidationError):
            self.clean(SimpleUploadedFile('me.png', b'<?php echo 1;', 'image/png'))

    def test_oversized_image_is_refused(self):
        from accounts.validators import validate_image_size

        class Big:
            size = MAX_IMAGE_BYTES + 1
        with self.assertRaises(ValidationError):
            validate_image_size(Big())

    def test_no_public_page_accepts_a_file(self):
        offenders = []
        root = Path(settings.BASE_DIR)
        for path in root.rglob('*.html'):
            if any(part in ('staticfiles', 'venv', 'env') for part in path.parts):
                continue
            text = path.read_text(encoding='utf-8')
            if 'multipart/form-data' in text or 'type="file"' in text:
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])


# ----------------------------------------------- production settings

class ProductionSettingsTests(TestCase):
    """Boots Django in a child process with DJANGO_DEBUG=False."""

    def run_check(self, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith('DJANGO_')}
        base.update(env)
        return subprocess.run(
            [sys.executable, 'manage.py', 'check', '--deploy'],
            cwd=settings.BASE_DIR, env=base, capture_output=True, text=True,
            timeout=120,
        )

    def test_production_refuses_the_public_development_key(self):
        # Import the settings directly: `manage.py check` on Django 3.2.0
        # hides this error behind AppRegistryNotReady.
        base = {k: v for k, v in os.environ.items() if not k.startswith('DJANGO_')}
        base.update(DJANGO_DEBUG='False', DJANGO_SETTINGS_MODULE='blood_lagbe.settings')
        result = subprocess.run(
            [sys.executable, '-c',
             'from django.conf import settings; settings.INSTALLED_APPS'],
            cwd=settings.BASE_DIR, env=base, capture_output=True, text=True,
            timeout=120,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY', result.stderr)

    def test_production_defaults_pass_the_deploy_checklist(self):
        result = self.run_check(
            DJANGO_DEBUG='False', DJANGO_SECRET_KEY='k' * 12 + 'x7Qz!' * 12,
            DJANGO_ALLOWED_HOSTS='bloodbond.example',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        warnings = set(re.findall(r'security\.W\d+', result.stdout + result.stderr))
        # only the HSTS preload list is left, a deliberate opt-in
        self.assertLessEqual(warnings, {'security.W021'})

    @unittest.skipIf(
        os.environ.get('DJANGO_DEBUG', 'True').lower() in ('false', '0', 'no'),
        'running with production settings',
    )
    def test_development_defaults_stay_friendly(self):
        self.assertFalse(settings.SECURE_SSL_REDIRECT)
        self.assertFalse(settings.SESSION_COOKIE_SECURE)
        self.assertEqual(settings.SECURE_HSTS_SECONDS, 0)
