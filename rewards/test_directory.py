"""Donor directory and consent-based contact requests."""
import datetime

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.forms import PrivacyForm
from rewards.blood import COMPATIBLE_DONORS, donor_groups_for
from rewards.models import (
    CONTACT_RETRY_DAYS,
    MAX_PENDING_CONTACTS_PER_USER,
    ContactRequest,
    Donation,
)
from rewards.tests import make_user

_phone = iter(range(1, 10000))


def listed(group='O+', district='Dhaka', name='Listed', **profile):
    """A donor who opted into the directory."""
    user = make_user(
        phone=f'0171{next(_phone):07d}', first_name=name, email=f'{name}{next(_phone)}@x.com',
    )
    p = user.profile
    p.blood_group, p.district, p.in_directory = group, district, True
    for key, value in profile.items():
        setattr(p, key, value)
    p.save()
    return user


def donate(user, days_ago, status=Donation.Status.APPROVED):
    return Donation.objects.create(
        donor=user, blood_group=user.profile.blood_group or 'O+',
        donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
        location='Bank', status=status,
    )


class CompatibilityTests(TestCase):
    def test_o_negative_is_universal_donor(self):
        for recipient, donors in COMPATIBLE_DONORS.items():
            self.assertIn('O-', donors, recipient)

    def test_o_negative_recipient_only_from_o_negative(self):
        self.assertEqual(COMPATIBLE_DONORS['O-'], ['O-'])

    def test_ab_positive_receives_from_everyone(self):
        self.assertEqual(len(COMPATIBLE_DONORS['AB+']), 8)

    def test_exact_vs_compatible(self):
        self.assertEqual(donor_groups_for('B+'), ['B+'])
        self.assertEqual(
            sorted(donor_groups_for('B+', include_compatible=True)),
            ['B+', 'B-', 'O+', 'O-'],
        )
        self.assertEqual(donor_groups_for('nonsense'), [])


class DirectoryAccessTests(TestCase):
    def test_requires_login(self):
        response = self.client.get(reverse('donor-directory'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_only_opted_in_donors_listed(self):
        seen = listed(name='Visible')
        make_user(phone='01799990000', first_name='Hiddenone')
        viewer = make_user(phone='01799990001')
        self.client.force_login(viewer)
        response = self.client.get(reverse('donor-directory'))
        self.assertContains(response, 'Visible')
        self.assertNotContains(response, 'Hiddenone')
        self.assertEqual([d['user'] for d in response.context['donors']], [seen])

    def test_phone_never_in_listing(self):
        donor = listed(name='Private')
        self.client.force_login(make_user(phone='01799990002'))
        response = self.client.get(reverse('donor-directory'))
        self.assertNotContains(response, donor.phone)

    def test_viewer_not_listed_to_self(self):
        me = listed(name='Me')
        self.client.force_login(me)
        response = self.client.get(reverse('donor-directory'))
        self.assertEqual(response.context['donors'], [])

    def test_inactive_and_blank_group_excluded(self):
        gone = listed(name='Gone')
        gone.is_active = False
        gone.save()
        listed(group='', name='Nogroup')
        self.client.force_login(make_user(phone='01799990003'))
        response = self.client.get(reverse('donor-directory'))
        self.assertEqual(response.context['donors'], [])


class DirectoryFilterTests(TestCase):
    def setUp(self):
        self.o_neg = listed('O-', 'Dhaka', 'Oneg')
        self.b_pos = listed('B+', 'Dhaka', 'Bpos')
        self.a_pos = listed('A+', 'Sylhet', 'Apos')
        self.client.force_login(make_user(phone='01799990010'))

    def names(self, **params):
        response = self.client.get(reverse('donor-directory'), params)
        return sorted(d['name'] for d in response.context['donors'])

    def test_no_filter_lists_all(self):
        self.assertEqual(self.names(), ['Apos', 'Bpos', 'Oneg'])

    def test_exact_group(self):
        self.assertEqual(self.names(group='B+'), ['Bpos'])

    def test_compatible_includes_universal_donor(self):
        self.assertEqual(
            self.names(group='B+', compatible='1'), ['Bpos', 'Oneg'],
        )

    def test_compatible_ignored_without_group(self):
        self.assertEqual(self.names(compatible='1'), ['Apos', 'Bpos', 'Oneg'])

    def test_district(self):
        self.assertEqual(self.names(district='Sylhet'), ['Apos'])

    def test_group_and_district(self):
        self.assertEqual(self.names(group='B+', district='Sylhet'), [])

    def test_garbage_filters_ignored(self):
        self.assertEqual(
            self.names(group="'; DROP", district='Atlantis'),
            ['Apos', 'Bpos', 'Oneg'],
        )


class AvailabilityTests(TestCase):
    def setUp(self):
        self.viewer = make_user(phone='01799990020')
        self.client.force_login(self.viewer)

    def rows(self, **params):
        response = self.client.get(reverse('donor-directory'), params)
        return {d['name']: d for d in response.context['donors']}

    def test_fresh_donor_available(self):
        listed(name='Fresh')
        self.assertTrue(self.rows()['Fresh']['available'])

    def test_switched_off_unavailable(self):
        listed(name='Off', available_to_donate=False)
        self.assertFalse(self.rows()['Off']['available'])

    def test_recent_donation_makes_unavailable(self):
        donor = listed(name='Recent')
        donate(donor, days_ago=10)
        self.assertFalse(self.rows()['Recent']['available'])

    def test_pending_donation_also_counts(self):
        donor = listed(name='Pending')
        donate(donor, days_ago=5, status=Donation.Status.PENDING)
        self.assertFalse(self.rows()['Pending']['available'])

    def test_rejected_donation_ignored(self):
        donor = listed(name='Rej')
        donate(donor, days_ago=5, status=Donation.Status.REJECTED)
        self.assertTrue(self.rows()['Rej']['available'])

    def test_old_donation_available_again(self):
        donor = listed(name='Old')
        donate(donor, days_ago=400)
        self.assertTrue(self.rows()['Old']['available'])

    def test_available_listed_first_then_by_donations(self):
        busy = listed(name='Busy')
        donate(busy, days_ago=5)
        veteran = listed(name='Veteran')
        donate(veteran, days_ago=500)
        donate(veteran, days_ago=700)
        listed(name='Newbie')
        response = self.client.get(reverse('donor-directory'))
        self.assertEqual(
            [d['name'] for d in response.context['donors']],
            ['Veteran', 'Newbie', 'Busy'],
        )

    def test_available_only_filter(self):
        listed(name='Free')
        busy = listed(name='Busy')
        donate(busy, days_ago=5)
        self.assertEqual(list(self.rows(available='1')), ['Free'])


class ContactRequestTests(TestCase):
    def setUp(self):
        self.donor = listed(name='Helper')
        self.asker = make_user(phone='01788880001', first_name='Asker')
        self.client.force_login(self.asker)
        self.url = reverse('contact-donor', args=[self.donor.pk])

    def ask(self, **extra):
        return self.client.post(self.url, {'message': 'Need O+ today', **extra})

    def test_requires_login(self):
        self.client.logout()
        response = self.ask()
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertFalse(ContactRequest.objects.exists())

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_creates_pending_request(self):
        self.ask()
        req = ContactRequest.objects.get()
        self.assertEqual(
            (req.sender, req.donor, req.status, req.message),
            (self.asker, self.donor, 'pending', 'Need O+ today'),
        )

    def test_email_sent_without_phone_numbers(self):
        self.ask()
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn('Asker', body)
        self.assertNotIn(self.asker.phone, body)
        self.assertNotIn(self.donor.phone, body)

    def test_cannot_contact_self(self):
        self.client.force_login(self.donor)
        self.ask()
        self.assertFalse(ContactRequest.objects.exists())

    def test_db_forbids_self_contact(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContactRequest.objects.create(sender=self.donor, donor=self.donor)

    def test_cannot_contact_donor_not_in_directory(self):
        self.donor.profile.in_directory = False
        self.donor.profile.save()
        self.ask()
        self.assertFalse(ContactRequest.objects.exists())

    def test_unknown_user_404(self):
        response = self.client.post(reverse('contact-donor', args=[99999]))
        self.assertEqual(response.status_code, 404)

    def test_no_duplicate_pending(self):
        self.ask()
        self.ask()
        self.assertEqual(ContactRequest.objects.count(), 1)

    def test_pending_cap(self):
        for _ in range(MAX_PENDING_CONTACTS_PER_USER):
            ContactRequest.objects.create(sender=self.asker, donor=listed())
        self.ask()
        self.assertEqual(
            ContactRequest.objects.filter(donor=self.donor).count(), 0,
        )

    def test_answered_requests_free_up_the_cap(self):
        for _ in range(MAX_PENDING_CONTACTS_PER_USER):
            ContactRequest.objects.create(
                sender=self.asker, donor=listed(), status='accepted',
            )
        self.ask()
        self.assertEqual(
            ContactRequest.objects.filter(donor=self.donor).count(), 1,
        )

    def test_recent_decline_blocks_reask(self):
        ContactRequest.objects.create(
            sender=self.asker, donor=self.donor, status='declined',
            answered_at=timezone.now(),
        )
        self.ask()
        self.assertEqual(ContactRequest.objects.count(), 1)

    def test_old_decline_allows_reask(self):
        ContactRequest.objects.create(
            sender=self.asker, donor=self.donor, status='declined',
            answered_at=timezone.now()
            - datetime.timedelta(days=CONTACT_RETRY_DAYS + 1),
        )
        self.ask()
        self.assertEqual(ContactRequest.objects.count(), 2)

    def test_message_too_long_rejected(self):
        self.ask(message='x' * 201)
        self.assertFalse(ContactRequest.objects.exists())

    def test_redirect_next_must_be_local(self):
        response = self.ask(next='https://evil.example/steal')
        self.assertEqual(response.url, reverse('donor-directory'))
        response = self.ask(next='/donors/?group=O%2B')
        self.assertEqual(response.url, '/donors/?group=O%2B')


class PhoneRevealTests(TestCase):
    def setUp(self):
        self.donor = listed(name='Helper')
        self.asker = make_user(phone='01788880002', first_name='Asker')
        self.req = ContactRequest.objects.create(
            sender=self.asker, donor=self.donor, message='Please help',
        )

    def inbox(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('contact-inbox'))

    def respond(self, user, answer, pk=None):
        self.client.force_login(user)
        return self.client.post(
            reverse('contact-respond', args=[pk or self.req.pk]),
            {'answer': answer},
        )

    def test_nothing_revealed_while_pending(self):
        self.assertNotContains(self.inbox(self.donor), self.asker.phone)
        self.assertNotContains(self.inbox(self.asker), self.donor.phone)
        client_directory = self.client.get(reverse('donor-directory'))
        self.assertNotContains(client_directory, self.donor.phone)

    def test_accept_reveals_both_numbers(self):
        self.respond(self.donor, 'accept')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'accepted')
        self.assertIsNotNone(self.req.answered_at)
        self.assertContains(self.inbox(self.donor), self.asker.phone)
        self.assertContains(self.inbox(self.asker), self.donor.phone)
        self.client.force_login(self.asker)
        self.assertContains(
            self.client.get(reverse('donor-directory')), self.donor.phone,
        )

    def test_decline_reveals_nothing(self):
        self.respond(self.donor, 'decline')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'declined')
        self.assertNotContains(self.inbox(self.donor), self.asker.phone)
        self.assertNotContains(self.inbox(self.asker), self.donor.phone)

    def test_only_the_donor_can_answer(self):
        for user in (self.asker, make_user(phone='01788880003')):
            response = self.respond(user, 'accept')
            self.assertEqual(response.status_code, 404)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'pending')

    def test_cannot_answer_twice(self):
        self.respond(self.donor, 'decline')
        self.respond(self.donor, 'accept')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'declined')

    def test_bad_answer_ignored(self):
        self.respond(self.donor, 'maybe')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'pending')

    def test_respond_requires_post_and_login(self):
        url = reverse('contact-respond', args=[self.req.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(self.donor)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_pending_count_in_nav(self):
        response = self.inbox(self.donor)
        self.assertEqual(response.context['contact_pending_count'], 1)

    def test_inbox_requires_login(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse('contact-inbox')).status_code, 302,
        )

    def test_sender_name_never_falls_back_to_phone(self):
        nameless = make_user(phone='01788880004')
        ContactRequest.objects.create(sender=nameless, donor=self.donor)
        self.assertNotContains(self.inbox(self.donor), nameless.phone)


class ProfileFormTests(TestCase):
    def setUp(self):
        self.profile = make_user(phone='01788880010').profile

    def form(self, **data):
        return PrivacyForm(data, instance=self.profile)

    def test_joining_needs_group_and_district(self):
        form = self.form(in_directory='on')
        self.assertFalse(form.is_valid())
        self.assertIn('blood_group', form.errors)
        self.assertIn('district', form.errors)

    def test_joining_with_both_is_valid(self):
        form = self.form(
            in_directory='on', blood_group='A+', district='Dhaka',
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_not_joining_needs_nothing(self):
        self.assertTrue(self.form().is_valid())

    def test_directory_is_opt_in_by_default(self):
        self.assertFalse(self.profile.in_directory)

    def test_view_saves_profile(self):
        self.client.force_login(self.profile.user)
        self.client.post(reverse('privacy'), {
            'blood_group': 'AB-', 'district': 'Khulna',
            'in_directory': 'on', 'available_to_donate': 'on',
        })
        self.profile.refresh_from_db()
        self.assertEqual(
            (self.profile.blood_group, self.profile.district,
             self.profile.in_directory),
            ('AB-', 'Khulna', True),
        )
