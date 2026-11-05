"""Directory cards, inbox and share-card data, profile preview."""
import datetime

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from rewards.models import (
    CONTACT_RETRY_DAYS,
    MAX_PENDING_CONTACTS_PER_USER,
    ContactRequest,
    Donation,
)
from rewards.test_directory import listed
from rewards.tests import make_user

_n = iter(range(1, 100000))


def member(name='Asker', **profile):
    user = make_user(phone=f'0192{next(_n):07d}', first_name=name)
    for key, value in profile.items():
        setattr(user.profile, key, value)
    user.profile.save()
    return user


class DirectoryCardTests(TestCase):
    def setUp(self):
        self.me = member('Me')
        self.client.force_login(self.me)
        self.url = reverse('donor-directory')

    def donors(self, **params):
        return {d['name']: d for d in self.client.get(self.url, params).context['donors']}

    def test_ask_form_shown_for_free_donor(self):
        donor = listed(name='Free')
        response = self.client.get(self.url)
        self.assertContains(response, reverse('contact-donor', args=[donor.pk]))
        self.assertContains(response, 'Ask for help')

    def test_pending_explains_why_not_askable(self):
        donor = listed(name='Waiting')
        ContactRequest.objects.create(sender=self.me, donor=donor)
        response = self.client.get(self.url)
        self.assertContains(response, 'Waiting for an answer')
        self.assertNotContains(response, reverse('contact-donor', args=[donor.pk]))

    def test_recent_decline_explains_retry_date(self):
        donor = listed(name='Declined')
        answered = timezone.now() - datetime.timedelta(days=2)
        ContactRequest.objects.create(
            sender=self.me, donor=donor, status='declined', answered_at=answered,
        )
        row = self.donors()['Declined']
        expected = (answered + datetime.timedelta(days=CONTACT_RETRY_DAYS))
        self.assertEqual(
            row['retry_on'], timezone.localtime(expected).date(),
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'You can ask again after')
        self.assertNotContains(response, reverse('contact-donor', args=[donor.pk]))

    def test_old_decline_lets_you_ask_again(self):
        donor = listed(name='Old')
        ContactRequest.objects.create(
            sender=self.me, donor=donor, status='declined',
            answered_at=timezone.now() - datetime.timedelta(days=CONTACT_RETRY_DAYS + 1),
        )
        self.assertContains(
            self.client.get(self.url), reverse('contact-donor', args=[donor.pk]),
        )

    def test_pending_cap_disables_asking_everyone_new(self):
        for i in range(MAX_PENDING_CONTACTS_PER_USER):
            ContactRequest.objects.create(sender=self.me, donor=listed(name=f'Q{i}'))
        fresh = listed(name='Fresh')
        response = self.client.get(self.url)
        self.assertTrue(response.context['cap_reached'])
        self.assertContains(response, 'requests waiting for an answer')
        self.assertNotContains(response, reverse('contact-donor', args=[fresh.pk]))
        self.assertTrue(self.donors()['Fresh']['reason'])

    def test_unavailable_donor_labelled_but_askable(self):
        donor = listed(name='Away', available_to_donate=False)
        response = self.client.get(self.url)
        self.assertContains(response, 'Not available now')
        self.assertContains(response, reverse('contact-donor', args=[donor.pk]))

    def test_compatible_only_pill(self):
        listed(group='O-', name='Universal')
        listed(group='A+', name='Exact')
        rows = self.donors(group='A+', compatible='1')
        self.assertTrue(rows['Universal']['compatible_only'])
        self.assertFalse(rows['Exact']['compatible_only'])

    def test_blood_group_chips_rendered(self):
        response = self.client.get(self.url, {'group': 'B+'})
        for code in ('A+', 'AB-', 'O-'):
            self.assertContains(response, f'name="group" value="{code}"')
        self.assertContains(response, 'value="B+" checked')

    def test_empty_state_with_filters_offers_compatible(self):
        response = self.client.get(self.url, {'group': 'AB-', 'district': 'Bhola'})
        self.assertContains(response, 'No donors match these filters')
        self.assertContains(response, 'Include compatible groups')

    def test_empty_state_without_filters(self):
        self.assertContains(
            self.client.get(self.url), 'Nobody has joined the directory yet',
        )

    def test_pagination_query_has_no_duplicate_page(self):
        for i in range(22):
            listed(name=f'P{i}')
        response = self.client.get(self.url, {'page': '2', 'group': 'O+'})
        self.assertNotContains(response, 'page=2&amp;page=')
        self.assertEqual(response.context['query'], 'group=O%2B')
        response = self.client.get(self.url, {'group': 'O+'})
        self.assertContains(response, 'group=O%2B&amp;page=2')

    def test_accepted_contact_shows_phone_only_to_asker(self):
        donor = listed(name='Friend')
        ContactRequest.objects.create(
            sender=self.me, donor=donor, status='accepted',
            answered_at=timezone.now(),
        )
        self.assertContains(self.client.get(self.url), donor.phone)
        other = member('Other')
        self.client.force_login(other)
        self.assertNotContains(self.client.get(self.url), donor.phone)

    def test_not_listed_notice_links_to_privacy(self):
        response = self.client.get(self.url)
        self.assertContains(response, "You're not listed")
        self.assertContains(response, reverse('privacy'))

    def test_listed_viewer_has_no_notice(self):
        viewer = listed(name='Viewer')
        self.client.force_login(viewer)
        self.assertNotContains(self.client.get(self.url), "You're not listed")


class InboxTests(TestCase):
    def setUp(self):
        self.donor = member('Donor')
        self.asker = member('Asker')
        self.url = reverse('contact-inbox')

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_pending_listed_before_answered(self):
        old = ContactRequest.objects.create(
            sender=member('Answered'), donor=self.donor, status='declined',
            answered_at=timezone.now(),
        )
        pending = ContactRequest.objects.create(
            sender=self.asker, donor=self.donor,
        )
        self.client.force_login(self.donor)
        received = self.client.get(self.url).context['received']
        self.assertEqual([r['req'].pk for r in received], [pending.pk, old.pk])
        self.assertEqual(self.client.get(self.url).context['pending_count'], 1)

    def test_accept_and_decline_buttons_only_for_pending(self):
        pending = ContactRequest.objects.create(sender=self.asker, donor=self.donor)
        done = ContactRequest.objects.create(
            sender=member('Done'), donor=self.donor, status='declined',
            answered_at=timezone.now(),
        )
        self.client.force_login(self.donor)
        response = self.client.get(self.url)
        self.assertContains(response, reverse('contact-respond', args=[pending.pk]))
        self.assertNotContains(response, reverse('contact-respond', args=[done.pk]))
        self.assertContains(response, 'name="answer" value="accept"')
        self.assertContains(response, 'name="answer" value="decline"')

    def test_only_own_requests_visible(self):
        ContactRequest.objects.create(
            sender=self.asker, donor=self.donor, message='Secret note xyz',
        )
        self.client.force_login(member('Stranger'))
        response = self.client.get(self.url)
        self.assertNotContains(response, 'Secret note xyz')
        self.assertEqual(response.context['received'], [])
        self.assertEqual(response.context['sent'], [])

    def test_message_is_escaped(self):
        ContactRequest.objects.create(
            sender=self.asker, donor=self.donor, message='<script>alert(1)</script>',
        )
        self.client.force_login(self.donor)
        response = self.client.get(self.url)
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')

    def test_phone_hidden_until_accepted(self):
        req = ContactRequest.objects.create(sender=self.asker, donor=self.donor)
        self.client.force_login(self.donor)
        self.assertNotContains(self.client.get(self.url), self.asker.phone)
        req.status = 'accepted'
        req.answered_at = timezone.now()
        req.save()
        self.assertContains(self.client.get(self.url), self.asker.phone)

    def test_sent_decline_shows_retry_date_not_phone(self):
        ContactRequest.objects.create(
            sender=self.asker, donor=self.donor, status='declined',
            answered_at=timezone.now(),
        )
        self.client.force_login(self.asker)
        response = self.client.get(self.url)
        self.assertContains(response, 'You can ask again after')
        self.assertNotContains(response, self.donor.phone)

    def test_empty_states(self):
        self.client.force_login(self.donor)
        response = self.client.get(self.url)
        self.assertContains(response, 'Nobody has asked yet')
        self.assertContains(response, "You haven't asked anyone yet")

    def test_cannot_answer_someone_elses_request(self):
        req = ContactRequest.objects.create(sender=self.asker, donor=self.donor)
        self.client.force_login(self.asker)
        response = self.client.post(
            reverse('contact-respond', args=[req.pk]), {'answer': 'accept'},
        )
        self.assertEqual(response.status_code, 404)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')


class ShareCardTests(TestCase):
    def setUp(self):
        self.donor = member('Share', blood_group='A+')
        Donation.objects.create(
            donor=self.donor, blood_group='A+', location='Bank',
            donation_date=timezone.localdate(), status='approved',
        )

    def test_badge_card_has_site_name_and_no_phone(self):
        response = self.client.get(reverse('badge-card', args=[self.donor.pk]))
        card = response.context['share_card']
        self.assertEqual(card['siteName'], settings.SITE_NAME)
        self.assertNotIn(self.donor.phone, str(card))
        self.assertNotContains(response, self.donor.phone)
        self.assertNotIn('Blood Lagbe', response.content.decode())

    @override_settings(SITE_NAME='Lifeline')
    def test_site_name_follows_settings(self):
        response = self.client.get(reverse('badge-card', args=[self.donor.pk]))
        self.assertEqual(response.context['share_card']['siteName'], 'Lifeline')
        self.assertContains(response, 'on Lifeline')
        self.assertIn('Lifeline', response.context['share_text'])

    def test_badge_card_page_has_canvas_preview_hook(self):
        response = self.client.get(reverse('badge-card', args=[self.donor.pk]))
        self.assertContains(response, 'data-share-preview')
        self.assertContains(response, 'js/share-card.js')

    def test_share_script_tolerates_missing_site_name(self):
        # The request page does not pass siteName; the JS must fall back.
        with open('staticfiles/js/share-card.js', encoding='utf-8') as f:
            source = f.read()
        self.assertIn("(d && d.siteName) || 'BloodBond'", source)
        self.assertNotIn('Blood Lagbe', source)
        self.assertNotIn('d.phone', source)
        self.assertNotIn('contactPhone', source)


class ProfilePreviewTests(TestCase):
    def setUp(self):
        self.user = member('Preview', blood_group='B+', district='Dhaka')
        self.client.force_login(self.user)
        self.url = reverse('privacy')

    def test_preview_uses_display_name_not_phone(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['preview']['name'], 'Preview')
        self.assertNotContains(response, self.user.phone)

    def test_nameless_user_previews_as_donor_number(self):
        nameless = make_user(phone='01933333333')
        self.client.force_login(nameless)
        response = self.client.get(self.url)
        self.assertContains(response, f'Donor #{nameless.pk}')
        self.assertNotContains(response, '01933333333')

    def test_blood_group_chips_with_current_selected(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="blood_group" value="B+" checked')
        self.assertContains(response, 'name="blood_group" value="AB-"')

    def test_chip_choice_saves(self):
        self.client.post(self.url, {
            'blood_group': 'O-', 'district': 'Sylhet', 'in_directory': 'on',
            'available_to_donate': 'on', 'show_on_leaderboard': 'on',
        })
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.blood_group, 'O-')
        self.assertTrue(self.user.profile.in_directory)

    def test_directory_needs_group_and_district(self):
        self.user.profile.blood_group = ''
        self.user.profile.district = ''
        self.user.profile.save()
        response = self.client.post(self.url, {'in_directory': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Needed so people can find you')
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.in_directory)

    def test_explains_what_each_toggle_exposes(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Phone: only if you accept')
        self.assertContains(response, 'Phone: never')
        self.assertContains(response, 'id="pv-dir"')
