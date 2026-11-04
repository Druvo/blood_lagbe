"""Tests for the donor hub: levels, quests, CSV export, printable pages and
deleting your own pending donations."""
import csv
import datetime
import io

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.exports import CSV_HEADER, csv_safe
from rewards.models import BloodRequest, Donation, RequestResponse
from rewards.quests import (
    LEVELS,
    QuestFacts,
    build_quests,
    get_level,
    get_quests,
)
from rewards.tests import approve, make_user

PHONE_A = '01755500001'
PHONE_B = '01755500002'


def donate(user, days_ago=0, status=Donation.Status.APPROVED, **kwargs):
    data = {
        'donor': user, 'blood_group': 'A+', 'district': 'Dhaka',
        'donation_date': timezone.localdate() - datetime.timedelta(days=days_ago),
        'location': 'Test Bank', 'status': status,
    }
    data.update(kwargs)
    return Donation.objects.create(**data)


def facts(**overrides):
    base = dict(
        blood_group='', district='', in_directory=False, logged_count=0,
        approved_count=0, answered_request=False, request_donations=0,
        streak=0,
    )
    base.update(overrides)
    return QuestFacts(**base)


def quest_map(**overrides):
    return {q.key: q for q in build_quests(facts(**overrides))}


class LevelTests(SimpleTestCase):
    def test_zero_points_is_first_level(self):
        info = get_level(0)
        self.assertEqual(info.level.name, 'Spark')
        self.assertEqual(info.next_level.name, 'Helper')
        self.assertEqual(info.progress_percent, 0)
        self.assertFalse(info.is_max)

    def test_there_are_about_eight_increasing_levels(self):
        self.assertEqual(len(LEVELS), 8)
        thresholds = [level.min_points for level in LEVELS]
        self.assertEqual(thresholds, sorted(set(thresholds)))
        self.assertEqual(thresholds[0], 0)

    def test_boundaries_every_level(self):
        for index, level in enumerate(LEVELS):
            with self.subTest(level=level.name):
                self.assertEqual(get_level(level.min_points).level, level)
                if level.min_points:
                    below = get_level(level.min_points - 1).level
                    self.assertEqual(below, LEVELS[index - 1])

    def test_progress_midway(self):
        helper, guardian = LEVELS[1], LEVELS[2]
        mid = (helper.min_points + guardian.min_points) // 2
        info = get_level(mid)
        self.assertEqual(info.level, helper)
        self.assertEqual(info.next_level, guardian)
        self.assertEqual(info.points_to_next, guardian.min_points - mid)
        self.assertTrue(0 < info.progress_percent < 100)

    def test_progress_never_reaches_100_before_next_level(self):
        for level, nxt in zip(LEVELS, LEVELS[1:]):
            info = get_level(nxt.min_points - 1)
            self.assertLess(info.progress_percent, 100)
            self.assertEqual(info.level, level)

    def test_top_level_is_capped(self):
        top = LEVELS[-1]
        for points in (top.min_points, top.min_points + 5000):
            info = get_level(points)
            self.assertEqual(info.level, top)
            self.assertIsNone(info.next_level)
            self.assertEqual(info.progress_percent, 100)
            self.assertEqual(info.points_to_next, 0)
            self.assertTrue(info.is_max)

    def test_negative_or_none_points_are_zero(self):
        self.assertEqual(get_level(-5).level, LEVELS[0])
        self.assertEqual(get_level(None).level, LEVELS[0])


class QuestLogicTests(TestCase):
    def test_fresh_donor_has_nothing_done(self):
        quests = build_quests(facts())
        self.assertGreaterEqual(len(quests), 6)
        self.assertFalse(any(q.done for q in quests))

    def test_every_quest_has_content_and_a_link(self):
        for quest in build_quests(facts()):
            with self.subTest(quest=quest.key):
                self.assertTrue(quest.title and quest.description and quest.reward)
                self.assertTrue(quest.url.startswith('/'))

    def test_profile_needs_both_blood_group_and_district(self):
        self.assertFalse(quest_map(blood_group='A+')['profile'].done)
        self.assertFalse(quest_map(district='Dhaka')['profile'].done)
        self.assertTrue(
            quest_map(blood_group='A+', district='Dhaka')['profile'].done,
        )

    def test_directory_quest(self):
        self.assertFalse(quest_map()['directory'].done)
        self.assertTrue(quest_map(in_directory=True)['directory'].done)

    def test_first_donation_counts_pending_too(self):
        self.assertFalse(quest_map(logged_count=0)['first'].done)
        self.assertTrue(quest_map(logged_count=1)['first'].done)

    def test_streak_quest_progress_is_capped(self):
        quest = quest_map(streak=1)['streak']
        self.assertFalse(quest.done)
        self.assertEqual((quest.progress, quest.target), (1, 2))
        quest = quest_map(streak=9)['streak']
        self.assertTrue(quest.done)
        self.assertEqual(quest.progress, quest.target)

    def test_three_donations_boundary(self):
        self.assertFalse(quest_map(approved_count=2)['three'].done)
        self.assertTrue(quest_map(approved_count=3)['three'].done)

    def test_request_quests(self):
        self.assertTrue(quest_map(answered_request=True)['respond'].done)
        self.assertFalse(quest_map(answered_request=True)['request_donation'].done)
        self.assertTrue(quest_map(request_donations=1)['request_donation'].done)

    def test_get_quests_reads_the_database(self):
        user = make_user(PHONE_A)
        self.assertFalse(any(q.done for q in get_quests(user)))

        profile = user.profile
        profile.blood_group, profile.district = 'O+', 'Dhaka'
        profile.in_directory = True
        profile.save()
        donate(user, status=Donation.Status.PENDING)
        done = {q.key for q in get_quests(user) if q.done}
        self.assertEqual(done, {'profile', 'directory', 'first'})

    def test_responding_to_a_request_completes_that_quest(self):
        user, requester = make_user(PHONE_A), make_user(PHONE_B)
        req = BloodRequest.objects.create(
            requester=requester, blood_group='A+', hospital='H', district='Dhaka',
            needed_by=timezone.localdate() + datetime.timedelta(days=3),
            contact_phone='01711111111',
        )
        RequestResponse.objects.create(blood_request=req, donor=user)
        done = {q.key for q in get_quests(user) if q.done}
        self.assertIn('respond', done)
        self.assertNotIn('request_donation', done)


class CsvSafeTests(SimpleTestCase):
    def test_formula_prefixes_are_quoted(self):
        for bad in ('=1+1', '+cmd', '-2', '@SUM(A1)', '\t=x', ' =1+1'):
            with self.subTest(bad=bad):
                self.assertEqual(csv_safe(bad), "'" + bad)

    def test_ordinary_text_untouched(self):
        for ok in ('Square Hospitals', 'ঢাকা মেডিকেল', 'A-', '2024-01-02', ''):
            with self.subTest(ok=ok):
                self.assertEqual(csv_safe(ok), ok)

    def test_none_becomes_empty(self):
        self.assertEqual(csv_safe(None), '')


class CsvExportTests(TestCase):
    def setUp(self):
        self.user = make_user(PHONE_A, first_name='Rina', last_name='Akter')
        self.other = make_user(PHONE_B)
        self.url = reverse('donation-export')

    def get_rows(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        text = response.content.decode('utf-8')
        return response, list(csv.reader(io.StringIO(text.lstrip('﻿'))))

    def test_url_is_export_csv(self):
        self.assertEqual(self.url, '/donations/export.csv')

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_headers_and_bom(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('utf-8', response['Content-Type'].lower())
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="donation-history.csv"',
        )

    def test_header_row_and_bangla_roundtrip(self):
        donate(self.user, location='ঢাকা মেডিকেল কলেজ হাসপাতাল')
        self.client.force_login(self.user)
        _, rows = self.get_rows()
        self.assertEqual(rows[0], CSV_HEADER)
        self.assertEqual(rows[1][3], 'ঢাকা মেডিকেল কলেজ হাসপাতাল')

    def test_all_statuses_included_with_label(self):
        donate(self.user, 10, Donation.Status.APPROVED)
        donate(self.user, 20, Donation.Status.PENDING)
        donate(self.user, 30, Donation.Status.REJECTED)
        self.client.force_login(self.user)
        _, rows = self.get_rows()
        self.assertEqual(
            [r[4] for r in rows[1:]], ['Approved', 'Pending', 'Rejected'],
        )

    def test_only_own_rows(self):
        donate(self.user, location='Mine')
        donate(self.other, location='Not Mine Secret')
        self.client.force_login(self.user)
        response, rows = self.get_rows()
        self.assertEqual(len(rows), 2)
        self.assertNotIn('Not Mine Secret', response.content.decode())

    def test_formula_injection_is_neutralised(self):
        donate(self.user, location='=HYPERLINK("http://evil","x")')
        donate(self.user, 5, location='@cmd', district='Dhaka')
        donate(self.user, 9, location='-1+1')
        self.client.force_login(self.user)
        _, rows = self.get_rows()
        places = [r[3] for r in rows[1:]]
        self.assertEqual(len(places), 3)
        for place in places:
            self.assertTrue(place.startswith("'"), place)

    def test_no_phone_number(self):
        donate(self.user)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertNotIn(PHONE_A, response.content.decode())

    def test_empty_history_still_has_header(self):
        self.client.force_login(self.user)
        _, rows = self.get_rows()
        self.assertEqual(rows, [CSV_HEADER])


class PrintPageTests(TestCase):
    def setUp(self):
        self.user = make_user(PHONE_A, first_name='Rina', last_name='Akter')
        self.user.profile.blood_group = 'B+'
        self.user.profile.save()
        self.other = make_user(PHONE_B, first_name='Other', last_name='Person')

    def test_history_requires_login(self):
        response = self.client.get(reverse('donation-history'))
        self.assertEqual(response.status_code, 302)

    def test_card_requires_login(self):
        response = self.client.get(reverse('donor-card'))
        self.assertEqual(response.status_code, 302)

    def test_history_summary_and_no_phone(self):
        donate(self.user, 10, location='Square Hospitals')
        donate(self.user, 300, location='Old Camp')
        donate(self.user, 3, Donation.Status.PENDING, location='Pending Place')
        donate(self.other, 1, location='Other Persons Place')
        self.client.force_login(self.user)
        response = self.client.get(reverse('donation-history'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['verified_count'], 2)
        self.assertEqual(response.context['total_count'], 3)
        self.assertEqual(
            response.context['last_verified'],
            timezone.localdate() - datetime.timedelta(days=10),
        )
        self.assertContains(response, 'Rina Akter')
        self.assertContains(response, 'Square Hospitals')
        self.assertContains(response, 'Pending Place')
        self.assertContains(response, 'window.print()')
        self.assertContains(response, 'self-service')
        self.assertNotContains(response, 'Other Persons Place')
        self.assertNotContains(response, PHONE_A)

    def test_history_verified_only_filter(self):
        donate(self.user, 10, location='Verified Place')
        donate(self.user, 3, Donation.Status.PENDING, location='Pending Place')
        self.client.force_login(self.user)
        response = self.client.get(reverse('donation-history') + '?only=verified')
        self.assertContains(response, 'Verified Place')
        self.assertNotContains(response, 'Pending Place')

    def test_history_empty(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('donation-history'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No donations logged yet')
        self.assertIsNone(response.context['last_verified'])

    def test_history_escapes_html_in_place(self):
        donate(self.user, location='<script>alert(1)</script>')
        self.client.force_login(self.user)
        response = self.client.get(reverse('donation-history'))
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_card_content_and_no_phone(self):
        donate(self.user, 10)
        self.client.force_login(self.user)
        response = self.client.get(reverse('donor-card'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rina Akter')
        self.assertContains(response, 'B+')
        self.assertContains(response, 'Bronze Donor')
        self.assertContains(response, 'member since')
        self.assertEqual(response.context['verified_count'], 1)
        self.assertNotContains(response, PHONE_A)

    def test_card_does_not_count_unverified(self):
        donate(self.user, 10, Donation.Status.PENDING)
        self.client.force_login(self.user)
        response = self.client.get(reverse('donor-card'))
        self.assertEqual(response.context['verified_count'], 0)
        self.assertContains(response, 'Aspiring Donor')

    def test_card_without_name_never_uses_phone(self):
        self.client.force_login(self.other)
        self.other.first_name = self.other.last_name = ''
        self.other.save()
        response = self.client.get(reverse('donor-card'))
        self.assertContains(response, f'Donor #{self.other.pk}')
        self.assertNotContains(response, PHONE_B)


class DeleteDonationTests(TestCase):
    def setUp(self):
        self.user = make_user(PHONE_A)
        self.other = make_user(PHONE_B)
        self.client.force_login(self.user)

    def url(self, donation):
        return reverse('donation-delete', args=[donation.pk])

    def test_url_shape(self):
        donation = donate(self.user, status=Donation.Status.PENDING)
        self.assertEqual(self.url(donation), f'/donations/{donation.pk}/delete/')

    def test_owner_can_delete_pending(self):
        donation = donate(self.user, status=Donation.Status.PENDING)
        response = self.client.post(self.url(donation))
        self.assertRedirects(response, reverse('log-donation'))
        self.assertFalse(Donation.objects.filter(pk=donation.pk).exists())

    def test_get_shows_confirm_step_and_does_not_delete(self):
        donation = donate(self.user, status=Donation.Status.PENDING,
                          location='Confirm Me Bank')
        response = self.client.get(self.url(donation))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirm Me Bank')
        self.assertContains(response, 'method="post"')
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_cannot_delete_approved(self):
        donation = donate(self.user, status=Donation.Status.APPROVED)
        for method in (self.client.post, self.client.get):
            self.assertIn(method(self.url(donation)).status_code, (403, 404))
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_cannot_delete_rejected(self):
        donation = donate(self.user, status=Donation.Status.REJECTED)
        self.assertIn(self.client.post(self.url(donation)).status_code, (403, 404))
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_cannot_delete_someone_elses_pending(self):
        donation = donate(self.other, status=Donation.Status.PENDING)
        self.assertIn(self.client.post(self.url(donation)).status_code, (403, 404))
        self.assertIn(self.client.get(self.url(donation)).status_code, (403, 404))
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_anonymous_redirected_and_nothing_deleted(self):
        donation = donate(self.user, status=Donation.Status.PENDING)
        self.client.logout()
        response = self.client.post(self.url(donation))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_missing_donation_is_404(self):
        response = self.client.post(reverse('donation-delete', args=[99999]))
        self.assertEqual(response.status_code, 404)

    def test_other_methods_not_allowed(self):
        donation = donate(self.user, status=Donation.Status.PENDING)
        self.assertEqual(self.client.put(self.url(donation)).status_code, 405)
        self.assertTrue(Donation.objects.filter(pk=donation.pk).exists())

    def test_hub_shows_delete_only_for_pending(self):
        pending = donate(self.user, 40, Donation.Status.PENDING)
        approved = donate(self.user, 200, Donation.Status.APPROVED)
        rejected = donate(self.user, 300, Donation.Status.REJECTED)
        response = self.client.get(reverse('log-donation'))
        self.assertContains(response, self.url(pending))
        self.assertNotContains(response, self.url(approved))
        self.assertNotContains(response, self.url(rejected))

    def test_deleting_pending_restores_eligibility(self):
        donation = donate(self.user, 5, Donation.Status.PENDING)
        before = self.client.get(reverse('log-donation')).context['eligibility']
        self.assertFalse(before.eligible)
        self.client.post(self.url(donation))
        after = self.client.get(reverse('log-donation')).context['eligibility']
        self.assertTrue(after.eligible)


class HubViewTests(TestCase):
    def setUp(self):
        self.user = make_user(PHONE_A, first_name='Rina', last_name='Akter')
        self.url = reverse('log-donation')

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_fresh_user_renders(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Hi, Rina')
        self.assertContains(response, 'Ready to donate')
        self.assertContains(response, 'No donations logged yet')
        self.assertContains(response, 'Spark')
        self.assertContains(response, 'est. up to')
        self.assertEqual(response.context['level_info'].level.name, 'Spark')
        self.assertEqual(response.context['quests_done'], 0)
        self.assertNotContains(response, 'Recharging')

    def test_hub_links_to_exports_badge_and_privacy(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        for name in ('donation-export', 'donation-history', 'donor-card',
                     'privacy'):
            self.assertContains(response, reverse(name))
        self.assertContains(response, reverse('badge-card', args=[self.user.pk]))

    def test_safety_disclaimer_and_interval_present(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'not medical clearance')
        self.assertContains(response, '4 months')

    def test_recharging_state(self):
        donate(self.user, 10, Donation.Status.APPROVED)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'Recharging')
        self.assertContains(response, 'not medical clearance')
        self.assertNotContains(response, 'Ready to donate')
        self.assertFalse(response.context['eligibility'].eligible)

    def test_rich_user_renders(self):
        requester = make_user(PHONE_B)
        req = BloodRequest.objects.create(
            requester=requester, blood_group='A+', hospital='H', district='Dhaka',
            needed_by=timezone.localdate() + datetime.timedelta(days=3),
            contact_phone='01711111111',
        )
        for days in (400, 270, 150, 20):
            donate(self.user, days, location=f'Bank {days}')
        donate(self.user, 3, Donation.Status.PENDING, location='Pending Bank')
        donate(self.user, 600, Donation.Status.REJECTED, location='Bad Bank')
        approved = donate(self.user, 1, blood_request=req)
        RequestResponse.objects.create(blood_request=req, donor=self.user)
        self.assertEqual(approved.status, Donation.Status.APPROVED)

        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['approved_count'], 5)
        self.assertEqual(response.context['pending_count'], 1)
        self.assertEqual(response.context['current_badge'].name, 'Silver Donor')
        self.assertGreater(response.context['level_info'].level.number, 1)
        self.assertContains(response, 'Pending Bank')
        self.assertContains(response, 'Rejected')
        self.assertContains(response, 'Approved')
        self.assertNotContains(response, PHONE_A)

    def test_history_is_capped_with_see_all_link(self):
        for i in range(12):
            donate(self.user, i * 3, Donation.Status.REJECTED)
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['recent_donations']), 8)
        self.assertContains(response, 'See all 12')

    def test_blood_group_defaults_from_profile(self):
        self.user.profile.blood_group = 'AB-'
        self.user.profile.district = 'Sylhet'
        self.user.profile.save()
        self.client.force_login(self.user)
        form = self.client.get(self.url).context['form']
        self.assertEqual(form['blood_group'].value(), 'AB-')
        self.assertEqual(form['district'].value(), 'Sylhet')
        self.assertContains(self.client.get(self.url), 'bb-bloodtag')

    def test_no_default_blood_group_when_profile_blank(self):
        self.client.force_login(self.user)
        form = self.client.get(self.url).context['form']
        self.assertFalse(form['blood_group'].value())

    def test_form_keeps_place_select_hooks(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'js/place-select.js')
        self.assertContains(response, 'js-place-text')
        self.assertContains(response, 'name="place"')

    def test_invalid_post_rerenders_with_errors_and_creates_nothing(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {
            'blood_group': 'A+', 'donation_date': 'not-a-date',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Donation.objects.count(), 0)
        self.assertContains(response, 'errorlist')

    def test_urgent_prompt_only_when_relevant(self):
        requester = make_user(PHONE_B)
        BloodRequest.objects.create(
            requester=requester, blood_group='B+', hospital='H', district='Dhaka',
            needed_by=timezone.localdate() + datetime.timedelta(days=3),
            contact_phone='01711111111',
        )
        self.client.force_login(self.user)
        # No blood group on the profile: no prompt.
        self.assertEqual(self.client.get(self.url).context['urgent_matches'], 0)
        self.user.profile.blood_group = 'B+'
        self.user.profile.save()
        self.assertEqual(self.client.get(self.url).context['urgent_matches'], 1)
        # Different group: no prompt.
        self.user.profile.blood_group = 'O-'
        self.user.profile.save()
        self.assertEqual(self.client.get(self.url).context['urgent_matches'], 0)
        # Cooling down: no prompt.
        self.user.profile.blood_group = 'B+'
        self.user.profile.save()
        donate(self.user, 5, Donation.Status.PENDING)
        self.assertEqual(self.client.get(self.url).context['urgent_matches'], 0)

    def test_next_step_hidden_when_all_quests_done(self):
        self.client.force_login(self.user)
        self.assertTrue(self.client.get(self.url).context['open_quests'])
        self.assertContains(self.client.get(self.url), 'Next step')

    def test_query_count_is_bounded(self):
        for days in (400, 270, 150, 20):
            donate(self.user, days)
        donate(self.user, 3, Donation.Status.PENDING)
        self.client.force_login(self.user)
        self.client.get(self.url)  # warm caches (content types, session)
        with _ceiling(self, 45):
            self.client.get(self.url)

    def test_query_count_does_not_grow_with_history(self):
        self.client.force_login(self.user)
        donate(self.user, 400)
        counts = []
        for _ in range(2):
            with _Counter() as counter:
                self.client.get(self.url)
            counts.append(len(counter.queries))
            for i in range(10):
                donate(self.user, 500 + i * 3)
        self.assertLessEqual(counts[1], counts[0] + 1)


class _Counter:
    """Collect queries run inside a `with` block."""

    def __enter__(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self._ctx = CaptureQueriesContext(connection)
        self._ctx.__enter__()
        return self

    @property
    def queries(self):
        return self._ctx.captured_queries

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


class _ceiling:
    """Fail if the block runs more than `limit` queries."""

    def __init__(self, test, limit):
        self.test, self.limit = test, limit

    def __enter__(self):
        self.counter = _Counter().__enter__()
        return self

    def __exit__(self, *exc):
        self.counter.__exit__(*exc)
        if exc[0] is None:
            used = len(self.counter.queries)
            self.test.assertLessEqual(
                used, self.limit, f'{used} queries, ceiling {self.limit}',
            )
        return False


class ExistingPostFlowTests(TestCase):
    def test_post_logs_pending_donation_with_profile_default_group(self):
        user = make_user(PHONE_A)
        self.client.force_login(user)
        response = self.client.post(reverse('log-donation'), {
            'blood_group': 'O+',
            'donation_date': timezone.localdate().isoformat(),
            'district': 'Dhaka', 'location': 'Typed Bank',
        })
        self.assertRedirects(response, reverse('log-donation'))
        donation = Donation.objects.get(donor=user)
        self.assertEqual(donation.status, Donation.Status.PENDING)
        approve(donation)
        page = self.client.get(reverse('log-donation'))
        self.assertEqual(page.context['approved_count'], 1)
