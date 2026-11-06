"""Tests for the alerts lane: notification service, inbox, settings, matched
request alerts (every rule), request and hub panels, signed email-off link and
the prune command."""
import datetime
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from alerts import matching
from alerts.context_processors import unread_alerts
from alerts.models import AlertPreference, Notification
from alerts.service import notify
from alerts.tokens import make_email_off_token
from rewards.models import BloodRequest, Donation

User = get_user_model()
PASSWORD = 'testpass123'
REQUESTER_PHONE = '01966600001'
DONOR_EMAIL = 'donor.alerts@example.test'
REQUESTER_EMAIL = 'requester.alerts@example.test'
CONTACT_PHONE = '01977700001'
HOSPITAL = 'Alerts Test Hospital'

_counter = [0]


def make_user(group='O+', district='Dhaka', phone=None, email=None,
              available=True, **profile):
    _counter[0] += 1
    phone = phone or f'0196660{_counter[0]:04d}'
    user = User.objects.create_user(phone=phone, password=PASSWORD)
    if email:
        user.email = email
        user.save()
    p = user.profile
    p.blood_group = group
    p.district = district
    p.available_to_donate = available
    for key, value in profile.items():
        setattr(p, key, value)
    p.save()
    return user


def opt_in(user, **kwargs):
    kwargs.setdefault('enabled', True)
    return AlertPreference.objects.update_or_create(
        user=user, defaults=kwargs,
    )[0]


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = make_user('A+', 'Dhaka', phone=REQUESTER_PHONE,
                                  email=REQUESTER_EMAIL)

    def create_request(self, group='A+', district='Dhaka', **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return BloodRequest.objects.create(
                requester=kwargs.pop('requester', self.requester),
                blood_group=group, hospital=HOSPITAL, district=district,
                needed_by=timezone.localdate() + datetime.timedelta(days=3),
                contact_phone=CONTACT_PHONE, patient_name='Secret Patient',
                note='Call 01988800001', **kwargs,
            )

    def matches(self, user):
        return Notification.objects.filter(user=user, kind='match')


class NotifyServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_creates_notification(self):
        n = notify(self.user, 'info', 'Hello', 'Body', url='/alerts/')
        self.assertEqual(n.user, self.user)
        self.assertIsNone(n.read_at)
        self.assertEqual(n.url, '/alerts/')

    def test_dedupe_key_is_idempotent_per_user(self):
        a = notify(self.user, 'info', 'One', 'x', dedupe_key='k1')
        b = notify(self.user, 'info', 'Two', 'y', dedupe_key='k1')
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    def test_same_key_for_other_user_is_separate(self):
        other = make_user()
        notify(self.user, 'info', 'One', 'x', dedupe_key='k1')
        notify(other, 'info', 'One', 'x', dedupe_key='k1')
        self.assertEqual(Notification.objects.filter(dedupe_key='k1').count(), 2)

    def test_no_key_creates_each_time(self):
        notify(self.user, 'info', 'One', 'x')
        notify(self.user, 'info', 'One', 'x')
        self.assertEqual(Notification.objects.count(), 2)

    def test_unsafe_urls_are_dropped(self):
        for bad in ('https://evil.example/', '//evil.example', 'javascript:alert(1)',
                    '/\\evil.example'):
            n = notify(self.user, 'info', 'T', 'b', url=bad)
            self.assertEqual(n.url, '', bad)

    def test_long_text_is_clipped(self):
        n = notify(self.user, 'info', 'x' * 500, 'y' * 3000)
        self.assertLessEqual(len(n.title), 200)
        self.assertLessEqual(len(n.body), 1000)


class MatchingRuleTests(Base):
    """Every rule that decides who is alerted."""

    def donor(self, group='O+', district='Dhaka', prefs=None, **kwargs):
        user = make_user(group, district, email=DONOR_EMAIL if kwargs.pop(
            'with_email', False) else None, **kwargs)
        opt_in(user, **(prefs or {}))
        return user

    def test_matching_donor_is_alerted(self):
        donor = self.donor('A+')
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 1)

    def test_compatible_group_is_alerted(self):
        donor = self.donor('O-')
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 1)

    def test_incompatible_group_is_not_alerted(self):
        donor = self.donor('B+')
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 0)

    def test_wrong_group_not_alerted(self):
        # An AB+ donor cannot give to an O- patient; a donor with no group
        # set is never matched either.
        wrong = self.donor('AB+')
        blank = self.donor('')
        self.create_request('O-', 'Dhaka')
        self.assertEqual(self.matches(wrong).count(), 0)
        self.assertEqual(self.matches(blank).count(), 0)

    def test_wrong_district_not_alerted(self):
        donor = self.donor('A+', 'Sylhet')
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 0)

    def test_extra_district_is_alerted(self):
        donor = self.donor('A+', 'Sylhet',
                           prefs={'extra_districts': 'Gazipur,Dhaka'})
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 1)

    def test_extra_district_matches_whole_names_only(self):
        # "Dhaka" must not match an extra list holding only a longer name.
        donor = self.donor('A+', 'Sylhet',
                           prefs={'extra_districts': 'Dhakaa,NotDhaka'})
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 0)

    def test_extra_district_first_middle_last_position(self):
        for extras in ('Dhaka,Gazipur,Tangail', 'Gazipur,Dhaka,Tangail',
                       'Gazipur,Tangail,Dhaka'):
            donor = self.donor('A+', 'Sylhet', prefs={'extra_districts': extras})
            self.create_request('A+', 'Dhaka')
            self.assertEqual(self.matches(donor).count(), 1, extras)

    def test_not_available_not_alerted(self):
        donor = self.donor('A+', available=False)
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 0)

    def test_not_opted_in_not_alerted(self):
        silent = make_user('A+', 'Dhaka')
        off = self.donor('A+', prefs={'enabled': False})
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(silent).count(), 0)
        self.assertEqual(self.matches(off).count(), 0)

    def test_requester_is_never_alerted(self):
        opt_in(self.requester)
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(self.requester).count(), 0)

    def test_inactive_user_not_alerted(self):
        donor = self.donor('A+')
        donor.is_active = False
        donor.save()
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 0)

    def test_pending_donation_cooldown_blocks(self):
        donor = self.donor('A+')
        Donation.objects.create(
            donor=donor, blood_group='A+', location='Bank',
            donation_date=timezone.localdate() - datetime.timedelta(days=5),
            status=Donation.Status.PENDING,
        )
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 0)

    def test_approved_donation_cooldown_blocks(self):
        donor = self.donor('A+')
        Donation.objects.create(
            donor=donor, blood_group='A+', location='Bank',
            donation_date=timezone.localdate() - datetime.timedelta(days=30),
            status=Donation.Status.APPROVED,
        )
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 0)

    def test_old_donation_and_rejected_donation_do_not_block(self):
        old = self.donor('A+')
        Donation.objects.create(
            donor=old, blood_group='A+', location='Bank',
            donation_date=timezone.localdate() - datetime.timedelta(days=400),
            status=Donation.Status.APPROVED,
        )
        rejected = self.donor('A+')
        Donation.objects.create(
            donor=rejected, blood_group='A+', location='Bank',
            donation_date=timezone.localdate() - datetime.timedelta(days=3),
            status=Donation.Status.REJECTED,
        )
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(old).count(), 1)
        self.assertEqual(self.matches(rejected).count(), 1)

    def test_over_daily_cap_not_alerted(self):
        donor = self.donor('A+', prefs={'daily_cap': 2})
        for i in range(2):
            Notification.objects.create(
                user=donor, kind='match', title=f't{i}', dedupe_key=f'old-{i}',
            )
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 2)  # no third one

    def test_under_daily_cap_alerted_and_old_ones_do_not_count(self):
        donor = self.donor('A+', prefs={'daily_cap': 2})
        old = Notification.objects.create(
            user=donor, kind='match', title='old', dedupe_key='ancient',
        )
        Notification.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=30),
        )
        Notification.objects.create(
            user=donor, kind='match', title='recent', dedupe_key='recent',
        )
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 3)

    def test_other_kinds_do_not_count_toward_cap(self):
        donor = self.donor('A+', prefs={'daily_cap': 1})
        notify(donor, 'info', 'Something else', 'x')
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 1)

    def test_flagged_request_is_not_alerted(self):
        donor = self.donor('A+')
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            req = BloodRequest.objects.create(
                requester=self.requester, blood_group='A+', hospital=HOSPITAL,
                district='Dhaka',
                needed_by=timezone.localdate() + datetime.timedelta(days=2),
                contact_phone=CONTACT_PHONE,
            )
        self.assertEqual(len(callbacks), 1)
        for i in range(3):
            reporter = make_user()
            req.reports.create(reporter=reporter, reason='spam')
        for cb in callbacks:
            cb()
        self.assertEqual(self.matches(donor).count(), 0)

    def test_closed_or_expired_request_is_not_alerted(self):
        donor = self.donor('A+')
        req = self.create_request('A+', 'Dhaka')
        Notification.objects.all().delete()
        req.close()
        self.assertEqual(matching.send_match_alerts(req.pk), 0)
        self.assertEqual(self.matches(donor).count(), 0)

    def test_only_rare_groups_skips_common_requests(self):
        donor = self.donor('O-', prefs={'only_rare_groups': True})
        self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 0)

    def test_only_rare_groups_gets_rare_requests(self):
        donor = self.donor('O-', prefs={'only_rare_groups': True})
        self.create_request('O-', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 1)

    def test_duplicate_signal_firing_is_idempotent(self):
        donor = self.donor('A+', with_email=True,
                           prefs={'email_enabled': True})
        req = self.create_request('A+', 'Dhaka')
        self.assertEqual(self.matches(donor).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        # Fire again, directly and through the service.
        self.assertEqual(matching.send_match_alerts(req.pk), 0)
        from alerts.apps import _dispatch
        _dispatch(req.pk)
        self.assertEqual(self.matches(donor).count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_saving_an_existing_request_does_not_alert_again(self):
        donor = self.donor('A+')
        req = self.create_request('A+', 'Dhaka')
        Notification.objects.all().delete()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            req.note = 'edited'
            req.save()
        self.assertEqual(callbacks, [])
        self.assertEqual(self.matches(donor).count(), 0)

    def test_candidates_are_capped_and_ordered(self):
        users = [self.donor('A+') for _ in range(5)]
        with mock.patch.object(matching, 'MAX_CANDIDATES', 3):
            self.create_request('A+', 'Dhaka')
        got = [u for u in users if self.matches(u).exists()]
        self.assertEqual(got, users[:3])

    def test_failure_does_not_break_request_creation(self):
        with mock.patch.object(matching, 'send_match_alerts',
                               side_effect=RuntimeError('boom')):
            with self.assertLogs('alerts.apps', level='ERROR'):
                req = self.create_request('A+', 'Dhaka')
        self.assertTrue(BloodRequest.objects.filter(pk=req.pk).exists())


class AlertContentTests(Base):
    def setUp(self):
        self.donor = make_user('A+', 'Dhaka', email=DONOR_EMAIL)
        opt_in(self.donor, email_enabled=True)

    def test_title_body_and_url(self):
        req = self.create_request('A+', 'Dhaka')
        n = self.matches(self.donor).get()
        self.assertEqual(
            n.title, f'A A+ donor is needed at {HOSPITAL}, Dhaka',
        )
        self.assertIn('Needed by', n.body)
        self.assertIn('not medical clearance', n.body)
        self.assertIn('no pressure', n.body)
        self.assertEqual(n.url, reverse('request-detail', args=[req.pk]))
        self.assertEqual(n.dedupe_key, f'match-{req.pk}')

    def test_rare_group_title(self):
        universal = make_user('O-', 'Dhaka')
        opt_in(universal)
        self.create_request('O-', 'Dhaka')
        self.assertTrue(self.matches(universal).get().title.startswith(
            'Rare blood group'))

    def test_common_group_title_has_no_rare_label(self):
        self.create_request('A+', 'Dhaka')
        self.assertNotIn('Rare', self.matches(self.donor).get().title)

    def test_rare_uses_moments_when_available(self):
        with mock.patch.object(matching, '_moments_is_rare',
                               lambda group: group == 'A+'):
            self.assertTrue(matching.is_rare_group('A+'))
            self.assertFalse(matching.is_rare_group('O-'))

    def test_rare_falls_back_when_moments_missing_or_broken(self):
        with mock.patch.object(matching, '_moments_is_rare', None):
            self.assertTrue(matching.is_rare_group('AB-'))
            self.assertFalse(matching.is_rare_group('B+'))

        def broken(group):
            raise RuntimeError

        with mock.patch.object(matching, '_moments_is_rare', broken):
            self.assertTrue(matching.is_rare_group('O-'))

    def test_no_phone_or_email_leaks_in_notification(self):
        self.create_request('A+', 'Dhaka')
        n = self.matches(self.donor).get()
        blob = ' '.join((n.title, n.body, n.url))
        for secret in (REQUESTER_PHONE, REQUESTER_EMAIL, CONTACT_PHONE,
                       '01988800001', 'Secret Patient', DONOR_EMAIL):
            self.assertNotIn(secret, blob)

    def test_email_sent_only_when_enabled_and_has_no_leaks(self):
        quiet = make_user('A+', 'Dhaka', email='quiet.alerts@example.test')
        opt_in(quiet, email_enabled=False)
        self.create_request('A+', 'Dhaka')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [DONOR_EMAIL])
        text = message.subject + message.body
        for secret in (REQUESTER_PHONE, REQUESTER_EMAIL, CONTACT_PHONE,
                       '01988800001', 'Secret Patient',
                       'quiet.alerts@example.test'):
            self.assertNotIn(secret, text)
        self.assertIn('email-off', message.body)
        self.assertIn('not medical clearance', message.body)
        self.assertIn('BloodBond', message.subject)
        # Both donors still got the in-app alert.
        self.assertEqual(self.matches(quiet).count(), 1)

    def test_email_enabled_without_address_sends_nothing(self):
        nomail = make_user('A+', 'Dhaka')
        opt_in(nomail, email_enabled=True)
        self.create_request('A+', 'Dhaka')
        self.assertEqual(len(mail.outbox), 1)  # only self.donor
        self.assertEqual(self.matches(nomail).count(), 1)

    def test_email_link_turns_email_off_without_login(self):
        self.create_request('A+', 'Dhaka')
        link = [line for line in mail.outbox[0].body.splitlines()
                if '/alerts/email-off/' in line][0]
        path = '/alerts/email-off/' + link.split('/alerts/email-off/')[1]
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            AlertPreference.objects.get(user=self.donor).email_enabled)
        # In-app alerts stay on.
        self.assertTrue(AlertPreference.objects.get(user=self.donor).enabled)


class EmailOffTests(TestCase):
    def setUp(self):
        self.user = make_user(email='off.alerts@example.test')
        opt_in(self.user, email_enabled=True)

    def url(self, token=None):
        return reverse('alerts:email-off',
                       args=[token or make_email_off_token(self.user)])

    def test_get_turns_email_off(self):
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertFalse(
            AlertPreference.objects.get(user=self.user).email_enabled)

    def test_post_works_too(self):
        self.assertEqual(self.client.post(self.url()).status_code, 200)
        self.assertFalse(
            AlertPreference.objects.get(user=self.user).email_enabled)

    def test_forged_token_is_friendly_404_and_changes_nothing(self):
        for token in ('garbage', make_email_off_token(self.user)[:-3] + 'abc'):
            response = self.client.get(self.url(token))
            self.assertEqual(response.status_code, 404)
        self.assertTrue(
            AlertPreference.objects.get(user=self.user).email_enabled)

    def test_token_for_another_purpose_is_rejected(self):
        from django.core import signing
        token = signing.dumps({'u': self.user.pk}, salt='something.else')
        self.assertEqual(self.client.get(self.url(token)).status_code, 404)

    def test_token_only_affects_its_own_user(self):
        other = make_user()
        opt_in(other, email_enabled=True)
        self.client.get(self.url())
        self.assertTrue(AlertPreference.objects.get(user=other).email_enabled)

    def test_deleted_user_is_404(self):
        token = make_email_off_token(self.user)
        self.user.delete()
        self.assertEqual(self.client.get(self.url(token)).status_code, 404)


class InboxTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.other = make_user()
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.client.logout()
        for name in ('alerts:inbox', 'alerts:settings'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn('login', response['Location'])

    def test_empty_inbox(self):
        response = self.client.get(reverse('alerts:inbox'))
        self.assertContains(response, 'No alerts yet')

    def test_unread_first_then_newest(self):
        read = notify(self.user, 'info', 'Already read', 'x')
        read.read_at = timezone.now()
        read.save()
        unread_old = notify(self.user, 'info', 'Unread old', 'x')
        Notification.objects.filter(pk=unread_old.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=2))
        unread_new = notify(self.user, 'info', 'Unread new', 'x')
        response = self.client.get(reverse('alerts:inbox'))
        titles = [n.title for n in response.context['notifications']]
        self.assertEqual(titles, ['Unread new', 'Unread old', 'Already read'])
        self.assertEqual(unread_new.user, self.user)

    def test_only_own_notifications_shown(self):
        notify(self.other, 'info', 'Private to other', 'x')
        response = self.client.get(reverse('alerts:inbox'))
        self.assertNotContains(response, 'Private to other')

    def test_pagination(self):
        for i in range(25):
            notify(self.user, 'info', f'Item {i}', 'x')
        first = self.client.get(reverse('alerts:inbox'))
        self.assertEqual(len(first.context['notifications']), 20)
        second = self.client.get(reverse('alerts:inbox') + '?page=2')
        self.assertEqual(len(second.context['notifications']), 5)
        # A bad page number is not a 500.
        self.assertEqual(
            self.client.get(reverse('alerts:inbox') + '?page=zzz').status_code,
            200)

    def test_text_is_escaped(self):
        notify(self.user, 'info', '<script>alert(1)</script>', '<b>bold</b>')
        response = self.client.get(reverse('alerts:inbox'))
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<b>bold</b>')

    def test_mark_one_read(self):
        n = notify(self.user, 'info', 'T', 'x', url='/alerts/')
        response = self.client.post(reverse('alerts:read', args=[n.pk]))
        self.assertRedirects(response, reverse('alerts:inbox'))
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_open_marks_read_and_redirects_to_url(self):
        n = notify(self.user, 'info', 'T', 'x', url='/requests/')
        response = self.client.post(
            reverse('alerts:read', args=[n.pk]), {'go': '1'})
        self.assertRedirects(response, '/requests/',
                             fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_mark_read_is_idempotent_and_keeps_first_time(self):
        n = notify(self.user, 'info', 'T', 'x')
        self.client.post(reverse('alerts:read', args=[n.pk]))
        n.refresh_from_db()
        first = n.read_at
        self.client.post(reverse('alerts:read', args=[n.pk]))
        n.refresh_from_db()
        self.assertEqual(n.read_at, first)

    def test_cannot_mark_someone_elses_read(self):
        n = notify(self.other, 'info', 'T', 'x')
        response = self.client.post(reverse('alerts:read', args=[n.pk]))
        self.assertEqual(response.status_code, 404)
        n.refresh_from_db()
        self.assertIsNone(n.read_at)

    def test_get_does_not_mark_read(self):
        n = notify(self.user, 'info', 'T', 'x')
        response = self.client.get(reverse('alerts:read', args=[n.pk]))
        self.assertEqual(response.status_code, 405)
        n.refresh_from_db()
        self.assertIsNone(n.read_at)

    def test_anonymous_cannot_mark_read(self):
        n = notify(self.user, 'info', 'T', 'x')
        self.client.logout()
        response = self.client.post(reverse('alerts:read', args=[n.pk]))
        self.assertEqual(response.status_code, 302)
        n.refresh_from_db()
        self.assertIsNone(n.read_at)

    def test_mark_all_read_only_mine(self):
        mine = [notify(self.user, 'info', f'T{i}', 'x') for i in range(3)]
        theirs = notify(self.other, 'info', 'Other', 'x')
        response = self.client.post(reverse('alerts:read-all'))
        self.assertEqual(response.status_code, 302)
        for n in mine:
            n.refresh_from_db()
            self.assertIsNotNone(n.read_at)
        theirs.refresh_from_db()
        self.assertIsNone(theirs.read_at)

    def test_mark_all_is_post_only(self):
        self.assertEqual(
            self.client.get(reverse('alerts:read-all')).status_code, 405)

    def test_next_redirect_is_not_open(self):
        n = notify(self.user, 'info', 'T', 'x')
        response = self.client.post(
            reverse('alerts:read', args=[n.pk]),
            {'next': 'https://evil.example/alerts/'})
        self.assertRedirects(response, reverse('alerts:inbox'))

    def test_rate_limit_is_friendly(self):
        from alerts.views import WRITE_LIMIT
        for _ in range(WRITE_LIMIT):
            self.client.post(reverse('alerts:read-all'))
        notify(self.user, 'info', 'T', 'x')
        response = self.client.post(reverse('alerts:read-all'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Notification.objects.filter(
                user=self.user, read_at__isnull=True).count(), 1)


class UnreadBadgeTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.factory = RequestFactory()

    def make(self, user):
        request = self.factory.get('/')
        request.user = user
        return request

    def test_counts_unread_for_signed_in_user(self):
        notify(self.user, 'info', 'a', 'x')
        read = notify(self.user, 'info', 'b', 'x')
        read.read_at = timezone.now()
        read.save()
        notify(make_user(), 'info', 'not mine', 'x')
        self.assertEqual(
            unread_alerts(self.make(self.user)), {'alerts_unread': 1})

    def test_anonymous_gets_zero_without_a_query(self):
        from django.contrib.auth.models import AnonymousUser
        with self.assertNumQueries(0):
            self.assertEqual(unread_alerts(self.make(AnonymousUser())),
                             {'alerts_unread': 0})

    def test_cached_per_request(self):
        request = self.make(self.user)
        with self.assertNumQueries(1):
            unread_alerts(request)
            unread_alerts(request)
            unread_alerts(request)

    def test_available_in_templates(self):
        notify(self.user, 'info', 'a', 'x')
        self.client.force_login(self.user)
        response = self.client.get(reverse('alerts:inbox'))
        self.assertEqual(response.context['alerts_unread'], 1)


class SettingsTests(TestCase):
    def setUp(self):
        self.user = make_user(email='set.alerts@example.test')
        self.client.force_login(self.user)
        self.url = reverse('alerts:settings')

    def post(self, **data):
        base = {'daily_cap': '3'}
        base.update(data)
        return self.client.post(self.url, base)

    def test_defaults_are_off(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertFalse(form.instance.enabled)
        self.assertFalse(form.instance.email_enabled)
        self.assertEqual(form.instance.daily_cap, 3)
        # Viewing the page creates nothing.
        self.assertFalse(AlertPreference.objects.exists())

    def test_explains_opt_in_and_safety(self):
        text = self.client.get(self.url).content.decode()
        self.assertIn('off until you turn it on', text)
        self.assertIn('not medical clearance', text)

    def test_save_preferences(self):
        response = self.post(enabled='on', email_enabled='on',
                             only_rare_groups='on', daily_cap='5',
                             extra_districts=['Gazipur', 'Sylhet'])
        self.assertRedirects(response, self.url)
        p = AlertPreference.objects.get(user=self.user)
        self.assertTrue(p.enabled)
        self.assertTrue(p.email_enabled)
        self.assertTrue(p.only_rare_groups)
        self.assertEqual(p.daily_cap, 5)
        self.assertEqual(p.extra_district_list, ['Gazipur', 'Sylhet'])

    def test_replayed_post_creates_one_row(self):
        self.post(enabled='on')
        self.post(enabled='on')
        self.assertEqual(AlertPreference.objects.filter(user=self.user).count(), 1)

    def test_more_than_five_districts_rejected(self):
        districts = ['Dhaka', 'Gazipur', 'Sylhet', 'Khulna', 'Rangpur', 'Bogura']
        response = self.post(enabled='on', extra_districts=districts)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'at most 5')
        self.assertFalse(AlertPreference.objects.filter(
            user=self.user, enabled=True).exists())

    def test_unknown_district_rejected(self):
        response = self.post(enabled='on', extra_districts=['Atlantis'])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AlertPreference.objects.filter(
            user=self.user, enabled=True).exists())

    def test_daily_cap_bounds(self):
        for bad in ('0', '11', 'x'):
            self.assertEqual(self.post(enabled='on', daily_cap=bad).status_code, 200)
        self.assertFalse(AlertPreference.objects.filter(
            user=self.user, enabled=True).exists())

    def test_email_requires_an_address(self):
        self.user.email = None
        self.user.save()
        response = self.post(enabled='on', email_enabled='on')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Add an email address')

    def test_prefilled_districts_on_edit(self):
        opt_in(self.user, extra_districts='Gazipur,Sylhet')
        response = self.client.get(self.url)
        self.assertContains(response, 'value="Gazipur" selected')

    def test_warns_when_profile_incomplete(self):
        p = self.user.profile
        p.blood_group = ''
        p.save()
        self.assertContains(self.client.get(self.url), 'blood group and district')

    def test_settings_only_change_own_row(self):
        other = make_user()
        opt_in(other, enabled=False)
        self.post(enabled='on')
        self.assertFalse(AlertPreference.objects.get(user=other).enabled)

    def test_csrf_enforced(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(self.url, {'enabled': 'on', 'daily_cap': '3'})
        self.assertEqual(response.status_code, 403)


class RequestPanelTests(Base):
    def setUp(self):
        self.donor = make_user('A+', 'Dhaka')
        self.stranger = make_user('B+', 'Sylhet')

    def page(self, req, user=None):
        if user:
            self.client.force_login(user)
        else:
            self.client.logout()
        return self.client.get(reverse('request-detail', args=[req.pk]))

    def test_owner_sees_count_only(self):
        opt_in(self.donor)
        opt_in(make_user('A+', 'Dhaka'))
        req = self.create_request('A+', 'Dhaka')
        response = self.page(req, self.requester)
        self.assertContains(response, '2 matching donors were notified')
        self.assertNotContains(response, self.donor.phone)

    def test_owner_count_singular_and_zero(self):
        req = self.create_request('A+', 'Dhaka')
        self.assertContains(self.page(req, self.requester),
                            '0 matching donors were notified')
        opt_in(self.donor)
        req2 = self.create_request('A+', 'Dhaka')
        self.assertContains(self.page(req2, self.requester),
                            '1 matching donor was notified')

    def test_owner_panel_hidden_for_closed_request(self):
        req = self.create_request('A+', 'Dhaka')
        req.close()
        self.assertNotContains(self.page(req, self.requester),
                               'matching donor')

    def test_anonymous_sees_nothing(self):
        req = self.create_request('A+', 'Dhaka')
        response = self.page(req)
        self.assertNotContains(response, 'matching donor')
        self.assertNotContains(response, 'Get alerts for requests like this')

    def test_matching_viewer_not_opted_in_sees_prompt(self):
        req = self.create_request('A+', 'Dhaka')
        response = self.page(req, self.donor)
        self.assertContains(response, 'Get alerts for requests like this')
        self.assertContains(response, reverse('alerts:settings'))
        self.assertNotContains(response, 'matching donor')

    def test_opted_in_viewer_sees_no_prompt(self):
        opt_in(self.donor)
        req = self.create_request('A+', 'Dhaka')
        self.assertNotContains(self.page(req, self.donor),
                               'Get alerts for requests like this')

    def test_nonmatching_viewer_sees_no_prompt(self):
        req = self.create_request('A+', 'Dhaka')
        self.assertNotContains(self.page(req, self.stranger),
                               'Get alerts for requests like this')

    def test_viewer_in_cooldown_sees_no_prompt(self):
        Donation.objects.create(
            donor=self.donor, blood_group='A+', location='Bank',
            donation_date=timezone.localdate() - datetime.timedelta(days=2),
            status=Donation.Status.PENDING)
        req = self.create_request('A+', 'Dhaka')
        self.assertNotContains(self.page(req, self.donor),
                               'Get alerts for requests like this')

    def test_no_prompt_on_closed_request(self):
        req = self.create_request('A+', 'Dhaka')
        req.close()
        self.assertNotContains(self.page(req, self.donor),
                               'Get alerts for requests like this')

    def test_no_prompt_to_the_requester_role_of_another_request(self):
        req = self.create_request('A+', 'Dhaka')
        # The requester sees the owner panel, never the invitation.
        self.assertNotContains(self.page(req, self.requester),
                               'Get alerts for requests like this')


class HubPanelTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_hub_panel_shows_latest_three_and_unread(self):
        for i in range(5):
            notify(self.user, 'info', f'Hub item {i}', 'x')
        response = self.client.get(reverse('log-donation'))
        self.assertContains(response, 'Hub item 4')
        self.assertContains(response, 'Hub item 2')
        self.assertNotContains(response, 'Hub item 1')
        self.assertContains(response, '5 new')
        self.assertContains(response, reverse('alerts:inbox'))
        self.assertContains(response, reverse('alerts:settings'))

    def test_hub_panel_empty_state(self):
        response = self.client.get(reverse('log-donation'))
        self.assertContains(response, 'No alerts yet')

    def test_home_panel_is_blank_and_anonymous_hub_not_applicable(self):
        self.client.logout()
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'alerts/settings')


class PruneCommandTests(TestCase):
    def setUp(self):
        self.user = make_user()
        now = timezone.now()

        def make(title, age_days, read):
            n = Notification.objects.create(user=self.user, kind='info',
                                            title=title)
            Notification.objects.filter(pk=n.pk).update(
                created_at=now - datetime.timedelta(days=age_days),
                read_at=now if read else None)
            return n

        self.old_read = make('old read', 120, True)
        self.old_unread = make('old unread', 120, False)
        self.new_read = make('new read', 10, True)

    def run_cmd(self, *args):
        out = StringIO()
        call_command('prune_notifications', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_deletes_nothing(self):
        out = self.run_cmd('--dry-run')
        self.assertIn('Would delete 1', out)
        self.assertEqual(Notification.objects.count(), 3)

    def test_deletes_only_old_read(self):
        out = self.run_cmd()
        self.assertIn('Deleted 1', out)
        self.assertFalse(Notification.objects.filter(pk=self.old_read.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=self.old_unread.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=self.new_read.pk).exists())

    def test_days_option(self):
        self.run_cmd('--days', '5')
        self.assertEqual(Notification.objects.count(), 1)
        self.assertTrue(Notification.objects.filter(pk=self.old_unread.pk).exists())

    def test_idempotent(self):
        self.run_cmd()
        self.assertIn('Deleted 0', self.run_cmd())

    def test_rejects_bad_days(self):
        with self.assertRaises(CommandError):
            self.run_cmd('--days', '0')
