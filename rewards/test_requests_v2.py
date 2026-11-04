"""Urgent requests, patient side: board filters, posting, detail page,
timeline, updates, close outcomes, reports, privacy and notifications."""
import datetime
import re

from django.core import mail
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from rewards.models import (
    REPORT_FLAG_THRESHOLD,
    BloodRequest,
    Donation,
    RequestReport,
    RequestResponse,
    RequestUpdate,
)
from rewards.request_views import MAX_UPDATES_PER_REQUEST, build_timeline
from rewards.test_game_loop import donate, make_request
from rewards.tests import make_user

REQ_PHONE = '01712345678'
DONOR_PHONE = '01722222222'


def set_profile(user, **fields):
    for key, value in fields.items():
        setattr(user.profile, key, value)
    user.profile.save()


class BoardTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.today = timezone.localdate()

    def names(self, **params):
        response = self.client.get(reverse('request-list'), params)
        self.assertEqual(response.status_code, 200)
        return [r.hospital for r in response.context['blood_requests']]

    def test_sorted_by_urgency(self):
        make_request(self.owner, days_ahead=6, hospital='Later')
        make_request(self.owner, days_ahead=0, hospital='Today')
        make_request(self.owner, days_ahead=2, hospital='Soon')
        self.assertEqual(self.names(), ['Today', 'Soon', 'Later'])

    def test_filter_by_group_and_district(self):
        make_request(self.owner, blood_group='O-', district='Sylhet', hospital='SylO')
        make_request(self.owner, blood_group='O-', district='Dhaka', hospital='DhkO')
        make_request(self.owner, blood_group='A+', district='Sylhet', hospital='SylA')
        self.assertEqual(self.names(district='Sylhet', blood_group='O-'), ['SylO'])
        self.assertCountEqual(self.names(district='Sylhet'), ['SylO', 'SylA'])
        # Unknown values are ignored, not an error.
        self.assertEqual(len(self.names(district='Atlantis', blood_group='ZZ')), 3)

    def test_urgency_filter(self):
        make_request(self.owner, days_ahead=0, hospital='Today')
        make_request(self.owner, days_ahead=5, hospital='Week')
        make_request(self.owner, days_ahead=20, hospital='Far')
        self.assertEqual(self.names(when='today'), ['Today'])
        self.assertEqual(self.names(when='week'), ['Today', 'Week'])
        self.assertEqual(len(self.names(when='all')), 3)

    def test_only_still_needed_requests(self):
        donor = make_user('01722222222')
        done = make_request(self.owner, hospital='Done')
        donate(donor, blood_request=done)
        make_request(self.owner, hospital='Closed', status='closed')
        make_request(self.owner, hospital='Open')
        self.assertEqual(self.names(), ['Open'])

    def test_pagination(self):
        for i in range(15):
            make_request(self.owner, hospital=f'H{i:02d}', days_ahead=i % 5)
        first = self.client.get(reverse('request-list'))
        self.assertEqual(len(first.context['blood_requests']), 12)
        self.assertContains(first, 'page=2')
        second = self.client.get(reverse('request-list'), {'page': 2})
        self.assertEqual(len(second.context['blood_requests']), 3)
        # Garbage page numbers fall back instead of erroring.
        self.assertEqual(
            self.client.get(reverse('request-list'), {'page': 'x'}).status_code,
            200,
        )

    def test_empty_state_and_cta(self):
        response = self.client.get(reverse('request-list'))
        self.assertContains(response, 'No one is waiting for blood')
        self.assertContains(response, reverse('request-create'))
        filtered = self.client.get(reverse('request-list'), {'blood_group': 'B+'})
        self.assertContains(filtered, 'No open requests match')

    def test_card_shows_progress_and_urgency(self):
        make_request(self.owner, units=3, days_ahead=0)
        response = self.client.get(reverse('request-list'))
        self.assertContains(response, 'Needed today')
        self.assertContains(response, '3</strong> of 3 bags still needed')
        self.assertContains(response, 'bb-progress')

    def test_board_never_shows_phone_numbers(self):
        make_request(self.owner)
        self.assertNotContains(self.client.get(reverse('request-list')), REQ_PHONE)

    def test_flagged_hidden_from_public_but_not_staff(self):
        req = make_request(self.owner, hospital='Shady')
        make_request(self.owner, hospital='Fine')
        for i in range(REPORT_FLAG_THRESHOLD):
            RequestReport.objects.create(
                blood_request=req, reporter=make_user(f'0188000000{i}'),
                reason='spam',
            )
        self.assertEqual(self.names(), ['Fine'])
        staff = make_user('01799999999', staff=True)
        self.client.force_login(staff)
        self.assertCountEqual(self.names(), ['Shady', 'Fine'])
        self.assertContains(self.client.get(reverse('request-list')), 'Reported')

    def test_below_threshold_stays_visible(self):
        req = make_request(self.owner, hospital='Okay')
        for i in range(REPORT_FLAG_THRESHOLD - 1):
            RequestReport.objects.create(
                blood_request=req, reporter=make_user(f'0188000000{i}'),
                reason='spam',
            )
        self.assertEqual(self.names(), ['Okay'])

    def test_cleared_reports_do_not_hide(self):
        req = make_request(self.owner, hospital='Cleared')
        for i in range(REPORT_FLAG_THRESHOLD):
            RequestReport.objects.create(
                blood_request=req, reporter=make_user(f'0188000000{i}'),
                reason='spam', cleared=True,
            )
        self.assertEqual(self.names(), ['Cleared'])

    def test_query_count_does_not_grow_with_requests(self):
        donor = make_user('01722222222')
        for i in range(2):
            req = make_request(self.owner, hospital=f'A{i}', units=3)
            RequestResponse.objects.create(blood_request=req, donor=donor)
        with CaptureQueriesContext(connection) as small:
            self.client.get(reverse('request-list'))
        for i in range(10):
            req = make_request(self.owner, hospital=f'B{i}', units=3)
            RequestResponse.objects.create(blood_request=req, donor=donor)
        with CaptureQueriesContext(connection) as large:
            self.client.get(reverse('request-list'))
        self.assertEqual(len(small), len(large))
        self.assertLessEqual(len(large), 6)

    def test_counts_not_inflated_by_pledges_and_reports(self):
        """Joins must not multiply the received/pledge counts."""
        donor_a, donor_b = make_user('01722222221'), make_user('01722222222')
        req = make_request(self.owner, units=5)
        for d in (donor_a, donor_b):
            RequestResponse.objects.create(blood_request=req, donor=d)
            RequestReport.objects.create(
                blood_request=req, reporter=d, reason='other',
            )
        donate(donor_a, blood_request=req)
        response = self.client.get(reverse('request-list'))
        card = response.context['blood_requests'][0]
        self.assertEqual(card.received, 1)
        self.assertEqual(card.pledges, 2)
        self.assertEqual(card.report_total, 2)


class CreateTests(TestCase):
    def setUp(self):
        self.user = make_user('01733333333')
        self.client.force_login(self.user)

    def data(self, **overrides):
        data = {
            'blood_group': 'B+', 'district': 'Dhaka',
            'hospital': 'Square Hospital', 'units_needed': 2,
            'needed_by': (timezone.localdate()
                          + datetime.timedelta(days=1)).isoformat(),
            'contact_phone': '01798765432', 'note': 'Ward 4',
            'patient_name': 'Rafi',
        }
        data.update(overrides)
        return data

    def test_form_renders_with_prefills(self):
        set_profile(self.user, district='Sylhet')
        response = self.client.get(reverse('request-create'))
        self.assertContains(response, 'Who needs blood?')
        self.assertContains(response, 'js-place-text')
        self.assertContains(response, 'place-select.js')
        form = response.context['form']
        self.assertEqual(form.fields['contact_phone'].initial, '01733333333')
        self.assertEqual(form.fields['district'].initial, 'Sylhet')
        # Eight blood groups as radios, no blank choice.
        self.assertContains(response, 'type="radio"', count=8)

    def test_form_never_asks_for_medical_record_numbers(self):
        response = self.client.get(reverse('request-create'))
        self.assertNotContains(response, 'record number</label>')
        self.assertContains(response, 'Never enter medical record numbers')

    def test_post_redirects_to_detail_with_message(self):
        response = self.client.post(
            reverse('request-create'), self.data(), follow=True,
        )
        created = BloodRequest.objects.get(hospital='Square Hospital')
        self.assertRedirects(
            response, reverse('request-detail', args=[created.pk]),
        )
        self.assertContains(response, 'Your request is posted')
        self.assertEqual(created.patient_name, 'Rafi')

    def test_patient_name_rejects_ids(self):
        response = self.client.post(
            reverse('request-create'), self.data(patient_name='MRN 123456'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BloodRequest.objects.exists())

    def test_patient_name_is_optional(self):
        self.client.post(reverse('request-create'), self.data(patient_name=''))
        self.assertEqual(BloodRequest.objects.get().patient_name, '')

    def test_bags_limited_to_ten(self):
        response = self.client.post(
            reverse('request-create'), self.data(units_needed=11),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BloodRequest.objects.exists())

    def test_cap_blocks_post_and_warns_on_form(self):
        for i in range(3):
            make_request(self.user, hospital=f'H{i}')
        self.assertContains(
            self.client.get(reverse('request-create')),
            'You already have 3 open requests',
        )
        response = self.client.post(reverse('request-create'), self.data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BloodRequest.objects.filter(requester=self.user).count(), 3)

    def test_closed_requests_do_not_count_toward_cap(self):
        for i in range(3):
            make_request(self.user, hospital=f'H{i}', status='closed')
        response = self.client.post(reverse('request-create'), self.data())
        self.assertEqual(response.status_code, 302)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('request-create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


class DetailTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111', email='owner@example.com')
        self.donor = make_user('01722222222', first_name='Nadia')
        self.req = make_request(self.owner, units=2)
        self.url = reverse('request-detail', args=[self.req.pk])

    def test_header_content(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Dhaka Medical')
        self.assertContains(response, 'bb-bloodtag')
        self.assertContains(response, '0</strong> of 2 bags covered')
        self.assertContains(response, 'Open')

    def test_phone_hidden_for_anonymous_visible_when_logged_in(self):
        self.assertNotContains(self.client.get(self.url), REQ_PHONE)
        self.client.force_login(self.donor)
        self.assertContains(self.client.get(self.url), REQ_PHONE)

    def test_phone_never_in_share_card_or_meta(self):
        self.client.force_login(self.donor)
        response = self.client.get(self.url)
        self.assertNotIn(REQ_PHONE, str(response.context['share_card']))
        self.assertNotIn(REQ_PHONE, response.context['share_text'])

    def test_patient_name_only_for_logged_in(self):
        self.req.patient_name = 'Rafi'
        self.req.save()
        self.assertNotContains(self.client.get(self.url), 'Rafi')
        self.client.force_login(self.donor)
        self.assertContains(self.client.get(self.url), 'Rafi')

    def test_responders_visible_only_to_owner_and_staff(self):
        RequestResponse.objects.create(blood_request=self.req, donor=self.donor)
        self.assertNotContains(self.client.get(self.url), DONOR_PHONE)
        stranger = make_user('01733333333')
        self.client.force_login(stranger)
        response = self.client.get(self.url)
        self.assertNotContains(response, DONOR_PHONE)
        self.assertNotContains(response, 'Nadia')
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, DONOR_PHONE)
        self.assertContains(response, 'Nadia')
        self.assertTrue(response.context['responses'][0]['when'])
        staff = make_user('01744444444', staff=True)
        self.client.force_login(staff)
        self.assertContains(self.client.get(self.url), DONOR_PHONE)

    def test_pledge_button_states(self):
        self.client.force_login(self.donor)
        self.assertContains(self.client.get(self.url), 'I can help')
        self.client.post(reverse('request-respond', args=[self.req.pk]))
        response = self.client.get(self.url)
        self.assertContains(response, "You're on the list")
        self.assertNotContains(response, 'I can help')

    def test_owner_has_no_pledge_button(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.url), 'I can help')

    def test_cooldown_donor_sees_date_not_button(self):
        donate(self.donor, days_ago=10)
        self.client.force_login(self.donor)
        response = self.client.get(self.url)
        self.assertContains(response, 'You can donate again on')
        self.assertNotContains(response, 'I can help')

    def test_anonymous_sees_login_cta(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Log in to help')

    def test_share_panel_only_while_active(self):
        self.assertContains(self.client.get(self.url), 'data-share-root')
        self.req.close('')
        self.assertNotContains(self.client.get(self.url), 'data-share-root')

    def test_fulfilled_and_expired_states(self):
        donate(self.donor, blood_request=self.req)
        donate(make_user('01755555555'), blood_request=self.req)
        response = self.client.get(self.url)
        self.assertContains(response, 'This request is fulfilled')
        self.assertContains(response, 'bb-progress--ok')
        self.assertNotContains(response, 'data-share-root')
        old = make_request(self.owner, hospital='Old')
        BloodRequest.objects.filter(pk=old.pk).update(
            needed_by=timezone.localdate() - datetime.timedelta(days=1),
        )
        self.assertContains(
            self.client.get(reverse('request-detail', args=[old.pk])),
            'This request has expired',
        )

    def test_closed_shows_outcome(self):
        self.req.close(BloodRequest.Outcome.FOUND_ELSEWHERE)
        self.assertContains(self.client.get(self.url), 'found blood elsewhere')

    def test_missing_request_404(self):
        self.assertEqual(
            self.client.get(reverse('request-detail', args=[9999])).status_code,
            404,
        )


class FlaggedDetailTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.req = make_request(self.owner)
        for i in range(REPORT_FLAG_THRESHOLD):
            RequestReport.objects.create(
                blood_request=self.req, reporter=make_user(f'0188000000{i}'),
                reason='fake',
            )
        self.url = reverse('request-detail', args=[self.req.pk])
        self.donor = make_user('01722222222')

    def test_strangers_see_review_notice_and_no_phone_or_actions(self):
        self.client.force_login(self.donor)
        response = self.client.get(self.url)
        self.assertContains(response, 'being reviewed')
        self.assertNotContains(response, REQ_PHONE)
        self.assertNotContains(response, 'I can help')
        self.assertNotContains(response, 'data-share-root')

    def test_cannot_pledge_to_flagged_request(self):
        self.client.force_login(self.donor)
        self.client.post(reverse('request-respond', args=[self.req.pk]))
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_owner_still_sees_everything(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, REQ_PHONE)
        self.assertContains(response, 'Reported (3)')


class PledgeRuleTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.donor = make_user('01722222222')
        self.req = make_request(self.owner)
        self.url = reverse('request-respond', args=[self.req.pk])

    def test_cannot_pledge_to_own_request(self):
        self.client.force_login(self.owner)
        self.client.post(self.url)
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_cooldown_blocks_pledge(self):
        donate(self.donor, days_ago=30)
        self.client.force_login(self.donor)
        self.client.post(self.url)
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_pending_donation_also_blocks_pledge(self):
        donate(self.donor, days_ago=5, status=Donation.Status.PENDING)
        self.client.force_login(self.donor)
        self.client.post(self.url)
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_old_donation_does_not_block(self):
        donate(self.donor, days_ago=400)
        self.client.force_login(self.donor)
        self.client.post(self.url)
        self.assertEqual(RequestResponse.objects.count(), 1)

    def test_closed_request_rejects_pledge(self):
        self.req.close('')
        self.client.force_login(self.donor)
        self.client.post(self.url)
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_fulfilled_request_rejects_pledge(self):
        donate(make_user('01733333333'), blood_request=self.req)
        self.client.force_login(self.donor)
        self.client.post(self.url)
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_expired_request_rejects_pledge(self):
        BloodRequest.objects.filter(pk=self.req.pk).update(
            needed_by=timezone.localdate() - datetime.timedelta(days=1),
        )
        self.client.force_login(self.donor)
        self.client.post(self.url)
        self.assertEqual(RequestResponse.objects.count(), 0)


class TimelineTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.req = make_request(self.owner, units=3)

    def titles(self):
        return [e['title'] for e in build_timeline(
            BloodRequest.objects.get(pk=self.req.pk))]

    def test_new_request_has_only_posted_event(self):
        self.assertEqual(self.titles(), ['Request posted'])

    def test_pledge_milestones(self):
        for i in range(3):
            RequestResponse.objects.create(
                blood_request=self.req, donor=make_user(f'0177000000{i}'),
            )
        titles = self.titles()
        self.assertIn('1 donor offered to help', titles)
        self.assertIn('3 donors offered to help', titles)
        self.assertNotIn('5 donors offered to help', titles)

    def test_only_approved_donations_appear(self):
        donate(make_user('01777777771'), blood_request=self.req)
        donate(make_user('01777777772'), blood_request=self.req,
               status=Donation.Status.PENDING)
        donate(make_user('01777777773'), blood_request=self.req,
               status=Donation.Status.REJECTED)
        events = build_timeline(BloodRequest.objects.get(pk=self.req.pk))
        verified = [e for e in events if e['title'] == 'A donation was verified']
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]['text'], '1 of 3 bags covered')

    def test_updates_and_close_with_outcome_newest_first(self):
        RequestUpdate.objects.create(
            blood_request=self.req, author=self.owner,
            kind='found_donor', text='Found one donor',
        )
        self.req.close(BloodRequest.Outcome.FULFILLED_HERE)
        events = build_timeline(BloodRequest.objects.get(pk=self.req.pk))
        self.assertEqual(
            [e['title'] for e in events],
            ['Closed by the requester', 'Found a donor', 'Request posted'],
        )
        self.assertEqual(events[0]['text'], 'Got blood through here')
        self.assertEqual(events[1]['text'], 'Found one donor')

    def test_timeline_renders_without_names_or_phones(self):
        donor = make_user('01777777771', first_name='Secretname')
        RequestResponse.objects.create(blood_request=self.req, donor=donor)
        donate(donor, blood_request=self.req)
        response = self.client.get(reverse('request-detail', args=[self.req.pk]))
        self.assertContains(response, '1 donor offered to help')
        self.assertContains(response, 'A donation was verified')
        self.assertNotContains(response, 'Secretname')
        self.assertNotContains(response, '01777777771')

    def test_legacy_closed_request_without_timestamp(self):
        BloodRequest.objects.filter(pk=self.req.pk).update(status='closed')
        self.assertEqual(self.titles()[0], 'Closed by the requester')


class UpdateTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.other = make_user('01722222222')
        self.req = make_request(self.owner)
        self.url = reverse('request-update', args=[self.req.pk])

    def test_owner_can_post_update(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self.url, {'kind': 'need_more', 'text': 'Need one more bag'},
            follow=True,
        )
        update = RequestUpdate.objects.get()
        self.assertEqual(update.kind, 'need_more')
        self.assertEqual(update.author, self.owner)
        self.assertContains(response, 'Need one more bag')

    def test_others_and_anonymous_cannot_post(self):
        self.client.post(self.url, {'kind': 'note', 'text': 'x'})
        self.client.force_login(self.other)
        self.client.post(self.url, {'kind': 'note', 'text': 'x'})
        self.assertEqual(RequestUpdate.objects.count(), 0)

    def test_staff_cannot_post_as_requester(self):
        staff = make_user('01744444444', staff=True)
        self.client.force_login(staff)
        self.client.post(self.url, {'kind': 'note', 'text': 'x'})
        self.assertEqual(RequestUpdate.objects.count(), 0)

    def test_get_not_allowed(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_validation(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {'kind': 'note', 'text': '   '})
        self.client.post(self.url, {'kind': 'bogus', 'text': 'hi'})
        self.client.post(self.url, {'kind': 'note', 'text': 'a' * 301})
        self.assertEqual(RequestUpdate.objects.count(), 0)
        self.client.post(self.url, {'kind': 'note', 'text': 'a' * 300})
        self.assertEqual(RequestUpdate.objects.count(), 1)

    def test_cannot_update_closed_request(self):
        self.req.close('')
        self.client.force_login(self.owner)
        self.client.post(self.url, {'kind': 'note', 'text': 'late'})
        self.assertEqual(RequestUpdate.objects.count(), 0)

    def test_update_cap(self):
        self.client.force_login(self.owner)
        for i in range(MAX_UPDATES_PER_REQUEST + 3):
            self.client.post(self.url, {'kind': 'note', 'text': f'u{i}'})
        self.assertEqual(RequestUpdate.objects.count(), MAX_UPDATES_PER_REQUEST)

    def test_update_form_only_for_owner(self):
        detail = reverse('request-detail', args=[self.req.pk])
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(detail), 'Post an update')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(detail), 'Post an update')


class CloseTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.other = make_user('01722222222')
        self.req = make_request(self.owner)
        self.url = reverse('request-close', args=[self.req.pk])

    def test_owner_closes_with_outcome(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {'outcome': 'found_elsewhere'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'closed')
        self.assertEqual(self.req.outcome, 'found_elsewhere')
        self.assertIsNotNone(self.req.closed_at)

    def test_each_outcome_is_stored(self):
        for value in BloodRequest.Outcome.values:
            req = make_request(self.owner, hospital=value)
            self.client.force_login(self.owner)
            self.client.post(reverse('request-close', args=[req.pk]),
                             {'outcome': value})
            req.refresh_from_db()
            self.assertEqual(req.outcome, value)

    def test_invalid_outcome_does_not_close(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {'outcome': 'nope'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'open')

    def test_other_user_cannot_close_or_set_outcome(self):
        self.client.force_login(self.other)
        self.client.post(self.url, {'outcome': 'fulfilled_here'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'open')
        self.assertEqual(self.req.outcome, '')

    def test_anonymous_cannot_close(self):
        self.client.post(self.url, {'outcome': 'fulfilled_here'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'open')

    def test_staff_can_close(self):
        self.client.force_login(make_user('01744444444', staff=True))
        self.client.post(self.url, {'outcome': 'no_longer_needed'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'closed')

    def test_close_without_outcome_still_works(self):
        self.client.force_login(self.owner)
        self.client.post(self.url)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'closed')
        self.assertEqual(self.req.outcome, '')

    def test_closing_twice_keeps_first_outcome(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {'outcome': 'fulfilled_here'})
        self.client.post(self.url, {'outcome': 'no_longer_needed'})
        self.req.refresh_from_db()
        self.assertEqual(self.req.outcome, 'fulfilled_here')

    def test_get_not_allowed(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_next_redirect_is_same_site_only(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self.url, {'outcome': 'fulfilled_here', 'next': '/requests/mine/'},
        )
        self.assertEqual(response.url, '/requests/mine/')
        req = make_request(self.owner, hospital='Two')
        response = self.client.post(
            reverse('request-close', args=[req.pk]),
            {'outcome': 'fulfilled_here', 'next': 'https://evil.example/'},
        )
        self.assertEqual(response.url, reverse('request-detail', args=[req.pk]))


class ReportTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.req = make_request(self.owner)
        self.url = reverse('request-report', args=[self.req.pk])

    def test_member_can_report_once(self):
        user = make_user('01722222222')
        self.client.force_login(user)
        self.client.post(self.url, {'reason': 'spam', 'text': 'looks odd'})
        self.client.post(self.url, {'reason': 'fake'})
        report = RequestReport.objects.get()
        self.assertEqual(report.reason, 'spam')
        self.assertEqual(report.text, 'looks odd')

    def test_cannot_report_own_request(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {'reason': 'spam'})
        self.assertEqual(RequestReport.objects.count(), 0)

    def test_anonymous_cannot_report(self):
        response = self.client.post(self.url, {'reason': 'spam'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RequestReport.objects.count(), 0)

    def test_invalid_reason_rejected(self):
        self.client.force_login(make_user('01722222222'))
        self.client.post(self.url, {'reason': 'because'})
        self.client.post(self.url, {})
        self.assertEqual(RequestReport.objects.count(), 0)

    def test_get_not_allowed(self):
        self.client.force_login(make_user('01722222222'))
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_threshold_flags_request(self):
        for i in range(REPORT_FLAG_THRESHOLD):
            self.client.force_login(make_user(f'0188000000{i}'))
            fresh = BloodRequest.objects.get(pk=self.req.pk)
            self.assertFalse(fresh.is_flagged)
            self.client.post(self.url, {'reason': 'spam'})
        self.assertTrue(BloodRequest.objects.get(pk=self.req.pk).is_flagged)
        self.assertEqual(BloodRequest.objects.public().count(), 0)
        self.assertEqual(BloodRequest.objects.flagged().count(), 1)

    def test_report_form_hidden_from_owner_and_anonymous(self):
        detail = reverse('request-detail', args=[self.req.pk])
        self.assertNotContains(self.client.get(detail), 'Report this request')
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(detail), 'Report this request')
        self.client.force_login(make_user('01722222222'))
        self.assertContains(self.client.get(detail), 'Report this request')
        self.client.post(self.url, {'reason': 'spam'})
        self.assertContains(self.client.get(detail), 'You reported this request')


class AdminActionTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.staff = make_user('01799999999', staff=True, admin=True)
        self.client.force_login(self.staff)
        self.req = make_request(self.owner)
        for i in range(REPORT_FLAG_THRESHOLD):
            RequestReport.objects.create(
                blood_request=self.req, reporter=make_user(f'0188000000{i}'),
                reason='spam',
            )

    def test_clear_reports_action_unflags(self):
        self.client.post(reverse('admin:rewards_bloodrequest_changelist'), {
            'action': 'clear_reports', '_selected_action': [self.req.pk],
        })
        self.assertEqual(BloodRequest.objects.flagged().count(), 0)
        self.assertEqual(RequestReport.objects.filter(cleared=True).count(), 3)
        # Cleared reporters cannot just report again (one per user).
        self.assertEqual(RequestReport.objects.count(), 3)

    def test_close_action_notifies_responders(self):
        donor = make_user('01722222222', email='d@example.com')
        RequestResponse.objects.create(blood_request=self.req, donor=donor)
        self.client.post(reverse('admin:rewards_bloodrequest_changelist'), {
            'action': 'close_requests', '_selected_action': [self.req.pk],
        })
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'closed')
        self.assertIsNotNone(self.req.closed_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_changelist_and_change_page_render(self):
        RequestUpdate.objects.create(
            blood_request=self.req, author=self.owner, text='hello')
        response = self.client.get(reverse('admin:rewards_bloodrequest_changelist'))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(
            reverse('admin:rewards_bloodrequest_change', args=[self.req.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hello')
        for flt in ('yes', 'any'):
            self.assertEqual(self.client.get(
                reverse('admin:rewards_bloodrequest_changelist'),
                {'flagged': flt}).status_code, 200)


class MineTests(TestCase):
    def setUp(self):
        self.user = make_user('01711111111')
        self.other = make_user('01722222222')
        self.url = reverse('request-mine')

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_lists_only_own_requests_with_details(self):
        mine = make_request(self.user, hospital='Mine', units=3)
        make_request(self.other, hospital='NotMine')
        RequestResponse.objects.create(blood_request=mine, donor=self.other)
        donate(self.other, blood_request=mine)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'Mine')
        self.assertNotContains(response, 'NotMine')
        self.assertContains(response, '1 offered')
        self.assertContains(response, '1</strong> of 3 bags covered')
        self.assertContains(response, reverse('request-close', args=[mine.pk]))

    def test_open_first_then_closed(self):
        make_request(self.user, hospital='ClosedOne', status='closed')
        make_request(self.user, hospital='OpenOne')
        self.client.force_login(self.user)
        names = [r.hospital for r in self.client.get(self.url).context['my_requests']]
        self.assertEqual(names, ['OpenOne', 'ClosedOne'])

    def test_closed_has_no_close_form_and_shows_outcome(self):
        req = make_request(self.user)
        req.close('found_elsewhere')
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertNotContains(response, reverse('request-close', args=[req.pk]))
        self.assertContains(response, 'Found blood elsewhere')

    def test_quick_close_returns_to_mine_page(self):
        req = make_request(self.user)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('request-close', args=[req.pk]),
            {'outcome': 'fulfilled_here', 'next': self.url},
        )
        self.assertRedirects(response, self.url)
        req.refresh_from_db()
        self.assertEqual(req.status, 'closed')

    def test_empty_state(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.url), 'You have not posted')

    def test_limit_notice(self):
        for i in range(3):
            make_request(self.user, hospital=f'H{i}')
        self.client.force_login(self.user)
        self.assertTrue(self.client.get(self.url).context['at_limit'])


class DonorsWhoCanHelpTests(TestCase):
    def setUp(self):
        self.owner = make_user('01711111111')
        self.req = make_request(
            self.owner, blood_group='A-', district='Dhaka', hospital='DMC',
        )
        self.url = reverse('request-detail', args=[self.req.pk])

    def donor(self, phone, group, district, listed=True, **kwargs):
        user = make_user(phone, **kwargs)
        set_profile(user, blood_group=group, district=district,
                    in_directory=listed)
        return user

    def test_counts_respect_compatibility_district_and_optin(self):
        self.donor('01800000001', 'A-', 'Dhaka', first_name='Zed')
        self.donor('01800000002', 'O-', 'Dhaka')
        self.donor('01800000003', 'O-', 'Sylhet')
        self.donor('01800000004', 'A+', 'Dhaka')      # incompatible with A-
        self.donor('01800000005', 'O-', 'Dhaka', listed=False)  # not opted in
        self.donor('01800000006', 'AB-', 'Dhaka')     # incompatible
        self.client.force_login(self.owner)
        panel = self.client.get(self.url).context['help_panel']
        self.assertEqual(panel['total'], 3)
        self.assertEqual(panel['nearby'], 2)

    def test_link_to_directory(self):
        self.client.force_login(self.owner)
        panel = self.client.get(self.url).context['help_panel']
        self.assertEqual(
            panel['url'], '/donors/?group=A-&district=Dhaka&compatible=1',
        )
        self.assertContains(self.client.get(self.url), 'Donors who can help')

    def test_panel_has_counts_only(self):
        self.donor('01800000001', 'A-', 'Dhaka', first_name='Zedname')
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('Zedname', html)
        self.assertNotIn('01800000001', html)

    def test_owner_is_not_counted(self):
        set_profile(self.owner, blood_group='A-', district='Dhaka',
                    in_directory=True)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).context['help_panel']['total'], 0)

    def test_panel_hidden_from_other_members_and_inactive_requests(self):
        self.client.force_login(make_user('01733333333'))
        self.assertNotContains(self.client.get(self.url), 'Donors who can help')
        self.client.force_login(self.owner)
        self.req.close('')
        self.assertNotContains(self.client.get(self.url), 'Donors who can help')

    def test_inactive_users_not_counted(self):
        user = self.donor('01800000001', 'A-', 'Dhaka')
        user.is_active = False
        user.save()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).context['help_panel']['total'], 0)


class NotificationTests(TestCase):
    def setUp(self):
        self.owner = make_user(
            '01711111111', email='owner@example.com', first_name='Karim',
        )
        self.req = make_request(self.owner, contact_phone=REQ_PHONE)

    def test_pledge_emails_requester_without_phone(self):
        donor = make_user('01722222222', email='d@example.com',
                          first_name='Nadia', last_name='Rahman')
        self.client.force_login(donor)
        self.client.post(reverse('request-respond', args=[self.req.pk]))
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['owner@example.com'])
        self.assertIn('Nadia Rahman', msg.body)
        self.assertIn(f'/requests/{self.req.pk}/', msg.body)
        self.assertIn('BloodBond', msg.subject)
        self.assertNotIn('Blood Lagbe', msg.subject + msg.body)
        self.assertIsNone(re.search(r'\d{10,}', msg.subject + msg.body))

    def test_repeated_pledge_sends_one_email(self):
        donor = make_user('01722222222')
        self.client.force_login(donor)
        for _ in range(2):
            self.client.post(reverse('request-respond', args=[self.req.pk]))
        self.assertEqual(len(mail.outbox), 1)

    def test_no_email_when_requester_has_none(self):
        owner = make_user('01733333333')
        req = make_request(owner)
        self.client.force_login(make_user('01722222222'))
        self.client.post(reverse('request-respond', args=[req.pk]))
        self.assertEqual(len(mail.outbox), 0)

    def test_pledge_without_name_uses_anonymous_label_not_phone(self):
        donor = make_user('01722222222')
        self.client.force_login(donor)
        self.client.post(reverse('request-respond', args=[self.req.pk]))
        body = mail.outbox[0].body
        self.assertIn(f'Donor #{donor.pk}', body)
        self.assertNotIn('01722222222', body)

    def test_close_emails_responders_with_email_only(self):
        a = make_user('01722222221', email='a@example.com')
        b = make_user('01722222222', email='b@example.com')
        c = make_user('01722222223')  # no email
        for d in (a, b, c):
            RequestResponse.objects.create(blood_request=self.req, donor=d)
        self.client.force_login(self.owner)
        self.client.post(reverse('request-close', args=[self.req.pk]),
                         {'outcome': 'fulfilled_here'})
        self.assertEqual(
            sorted(m.to[0] for m in mail.outbox), ['a@example.com', 'b@example.com'],
        )
        body = mail.outbox[0].body
        self.assertIn('Got blood through here', body)
        self.assertNotIn(REQ_PHONE, body)
        self.assertIsNone(re.search(r'\d{10,}', body))

    def test_close_twice_does_not_resend(self):
        d = make_user('01722222222', email='d@example.com')
        RequestResponse.objects.create(blood_request=self.req, donor=d)
        self.client.force_login(self.owner)
        url = reverse('request-close', args=[self.req.pk])
        self.client.post(url, {'outcome': 'fulfilled_here'})
        self.client.post(url, {'outcome': 'fulfilled_here'})
        self.assertEqual(len(mail.outbox), 1)

    def test_close_with_no_responders_sends_nothing(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('request-close', args=[self.req.pk]),
                         {'outcome': 'no_longer_needed'})
        self.assertEqual(len(mail.outbox), 0)


class DetailQueryTests(TestCase):
    def test_query_count_flat_in_pledges(self):
        owner = make_user('01711111111')
        req = make_request(owner, units=5)
        viewer = make_user('01722222222')
        self.client.force_login(viewer)
        url = reverse('request-detail', args=[req.pk])
        with CaptureQueriesContext(connection) as few:
            self.client.get(url)
        for i in range(6):
            d = make_user(f'0177000000{i}')
            RequestResponse.objects.create(blood_request=req, donor=d)
            donate(d, blood_request=req) if i < 3 else None
        with CaptureQueriesContext(connection) as many:
            self.client.get(url)
        self.assertEqual(len(few), len(many))
        self.assertLessEqual(len(many), 14)
