"""Tests for the game loop: cooldown, lives, urgent requests, hero badges,
leaderboard/awards and shareable cards."""
import datetime

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.leaderboard import get_leaderboard, get_monthly_awards
from rewards.models import (
    DONATION_CYCLE_DAYS,
    MAX_OPEN_REQUESTS_PER_USER,
    BloodRequest,
    Donation,
    RequestResponse,
    get_community_stats,
    get_donor_badge_snapshot,
    get_donor_points,
    get_earned_hero_badges,
    get_eligibility,
    get_newly_unlocked_badges,
    lives_saved,
    request_donation_count,
)
from rewards.tests import approve, make_user

User = get_user_model()


def donate(user, days_ago=0, status=Donation.Status.APPROVED,
           blood_request=None, blood_group='A+'):
    return Donation.objects.create(
        donor=user, blood_group=blood_group,
        donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
        location='Test Bank', status=status, blood_request=blood_request,
    )


def make_request(requester, days_ahead=3, units=1, **kwargs):
    defaults = dict(
        blood_group='A+', hospital='Dhaka Medical', district='Dhaka',
        units_needed=units, contact_phone='01712345678',
        needed_by=timezone.localdate() + datetime.timedelta(days=days_ahead),
    )
    defaults.update(kwargs)
    return BloodRequest.objects.create(requester=requester, **defaults)


class EligibilityTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_never_donated_is_eligible(self):
        e = get_eligibility(self.user)
        self.assertTrue(e.eligible)
        self.assertIsNone(e.next_date)
        self.assertEqual(e.percent, 100)

    def test_recent_donation_starts_cooldown(self):
        donate(self.user, days_ago=10)
        e = get_eligibility(self.user)
        self.assertFalse(e.eligible)
        self.assertEqual(e.days_left, DONATION_CYCLE_DAYS - 10)
        self.assertEqual(
            e.next_date, timezone.localdate() + datetime.timedelta(days=DONATION_CYCLE_DAYS - 10),
        )
        self.assertEqual(e.percent, int(10 / DONATION_CYCLE_DAYS * 100))

    def test_eligible_again_after_interval(self):
        donate(self.user, days_ago=DONATION_CYCLE_DAYS)
        e = get_eligibility(self.user)
        self.assertTrue(e.eligible)
        self.assertEqual(e.days_left, 0)

    def test_pending_donation_also_counts_for_safety(self):
        donate(self.user, days_ago=5, status=Donation.Status.PENDING)
        self.assertFalse(get_eligibility(self.user).eligible)

    def test_rejected_donation_is_ignored(self):
        donate(self.user, days_ago=5, status=Donation.Status.REJECTED)
        self.assertTrue(get_eligibility(self.user).eligible)


class LivesAndCommunityTests(TestCase):
    def test_lives_saved(self):
        self.assertEqual(lives_saved(0), 0)
        self.assertEqual(lives_saved(4), 12)

    def test_community_stats_count_only_approved(self):
        a, b = make_user('01711111111'), make_user('01722222222')
        donate(a)
        donate(a, days_ago=100)
        donate(b)
        donate(b, status=Donation.Status.PENDING)
        stats = get_community_stats()
        self.assertEqual(stats['donations'], 3)
        self.assertEqual(stats['donors'], 2)
        self.assertEqual(stats['lives'], 9)


class BloodRequestModelTests(TestCase):
    def setUp(self):
        self.requester = make_user('01711111111')
        self.donor = make_user('01722222222')

    def test_active_open_future_request(self):
        self.assertTrue(make_request(self.requester).is_active)

    def test_expired_request_not_active(self):
        req = make_request(self.requester, days_ahead=1)
        BloodRequest.objects.filter(pk=req.pk).update(
            needed_by=timezone.localdate() - datetime.timedelta(days=1),
        )
        req.refresh_from_db()
        self.assertTrue(req.is_expired)
        self.assertFalse(req.is_active)

    def test_closed_request_not_active(self):
        req = make_request(self.requester, status=BloodRequest.Status.CLOSED)
        self.assertFalse(req.is_active)

    def test_only_approved_linked_donations_count_as_received(self):
        req = make_request(self.requester, units=2)
        donate(self.donor, blood_request=req)
        donate(self.donor, days_ago=100, blood_request=req,
               status=Donation.Status.PENDING)
        self.assertEqual(req.units_received, 1)
        self.assertEqual(req.units_remaining, 1)
        self.assertFalse(req.is_fulfilled)

    def test_fulfilled_when_enough_verified_donations(self):
        req = make_request(self.requester, units=1)
        donate(self.donor, blood_request=req)
        self.assertTrue(req.is_fulfilled)
        self.assertFalse(req.is_active)

    def test_still_needed_excludes_fulfilled_expired_and_closed(self):
        open_req = make_request(self.requester, hospital='Open')
        fulfilled = make_request(self.requester, hospital='Fulfilled')
        donate(self.donor, blood_request=fulfilled)
        make_request(self.requester, hospital='Closed',
                     status=BloodRequest.Status.CLOSED)
        needed = list(BloodRequest.objects.still_needed())
        self.assertEqual([r.pk for r in needed], [open_req.pk])


class BloodRequestViewTests(TestCase):
    def setUp(self):
        self.requester = make_user('01711111111')
        self.donor = make_user('01722222222')
        self.req = make_request(self.requester)

    def post_data(self, **overrides):
        data = {
            'blood_group': 'O-', 'hospital': 'Square Hospital',
            'district': 'Dhaka', 'units_needed': 2,
            'needed_by': (timezone.localdate()
                          + datetime.timedelta(days=2)).isoformat(),
            'contact_phone': '01798765432', 'note': 'Urgent',
        }
        data.update(overrides)
        return data

    def test_list_is_public_and_filters_by_blood_group(self):
        make_request(self.requester, blood_group='O-', hospital='OnlyO')
        response = self.client.get(reverse('request-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['blood_requests']), 2)
        response = self.client.get(reverse('request-list'), {'blood_group': 'O-'})
        self.assertEqual(
            [r.hospital for r in response.context['blood_requests']], ['OnlyO'],
        )

    def test_list_ignores_unknown_blood_group_filter(self):
        response = self.client.get(
            reverse('request-list'), {'blood_group': 'XX'},
        )
        self.assertEqual(response.context['selected'], '')

    def test_list_hides_fulfilled_requests(self):
        donate(self.donor, blood_request=self.req)
        response = self.client.get(reverse('request-list'))
        self.assertEqual(len(response.context['blood_requests']), 0)

    def test_contact_phone_hidden_from_anonymous(self):
        url = reverse('request-detail', args=[self.req.pk])
        self.assertNotContains(self.client.get(url), '01712345678')
        self.client.force_login(self.donor)
        self.assertContains(self.client.get(url), '01712345678')

    def test_create_requires_login(self):
        response = self.client.post(reverse('request-create'), self.post_data())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_create_sets_requester(self):
        self.client.force_login(self.donor)
        response = self.client.post(reverse('request-create'), self.post_data())
        self.assertEqual(response.status_code, 302)
        created = BloodRequest.objects.get(hospital='Square Hospital')
        self.assertEqual(created.requester, self.donor)

    def test_create_rejects_past_date(self):
        self.client.force_login(self.donor)
        past = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
        response = self.client.post(
            reverse('request-create'), self.post_data(needed_by=past),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BloodRequest.objects.filter(hospital='Square Hospital').exists(),
        )

    def test_create_blocked_over_open_request_limit(self):
        for i in range(MAX_OPEN_REQUESTS_PER_USER):
            make_request(self.donor, hospital=f'H{i}')
        self.client.force_login(self.donor)
        response = self.client.post(reverse('request-create'), self.post_data())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BloodRequest.objects.filter(hospital='Square Hospital').exists(),
        )

    def test_respond_requires_login(self):
        response = self.client.post(reverse('request-respond', args=[self.req.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_respond_requires_post(self):
        self.client.force_login(self.donor)
        response = self.client.get(reverse('request-respond', args=[self.req.pk]))
        self.assertEqual(response.status_code, 405)

    def test_eligible_donor_can_respond_once(self):
        self.client.force_login(self.donor)
        url = reverse('request-respond', args=[self.req.pk])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(RequestResponse.objects.filter(
            blood_request=self.req, donor=self.donor).count(), 1)

    def test_donor_in_cooldown_cannot_respond(self):
        donate(self.donor, days_ago=10)
        self.client.force_login(self.donor)
        self.client.post(reverse('request-respond', args=[self.req.pk]))
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_cannot_respond_to_own_request(self):
        self.client.force_login(self.requester)
        self.client.post(reverse('request-respond', args=[self.req.pk]))
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_cannot_respond_to_closed_request(self):
        self.req.status = BloodRequest.Status.CLOSED
        self.req.save()
        self.client.force_login(self.donor)
        self.client.post(reverse('request-respond', args=[self.req.pk]))
        self.assertEqual(RequestResponse.objects.count(), 0)

    def test_only_owner_can_close(self):
        url = reverse('request-close', args=[self.req.pk])
        self.client.force_login(self.donor)
        self.client.post(url)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, BloodRequest.Status.OPEN)
        self.client.force_login(self.requester)
        self.client.post(url)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, BloodRequest.Status.CLOSED)

    def test_only_owner_sees_responders(self):
        RequestResponse.objects.create(blood_request=self.req, donor=self.donor)
        url = reverse('request-detail', args=[self.req.pk])
        other = make_user('01733333333')
        self.client.force_login(other)
        self.assertNotContains(self.client.get(url), '01722222222')
        self.client.force_login(self.requester)
        self.assertContains(self.client.get(url), '01722222222')


class RequestDonationLinkTests(TestCase):
    def setUp(self):
        self.requester = make_user('01711111111', email='req@example.com')
        self.donor = make_user('01722222222', email='donor@example.com')
        self.req = make_request(self.requester)

    def log_donation(self, **extra):
        data = {
            'blood_group': 'A+',
            'donation_date': timezone.localdate().isoformat(),
            'location': 'Bank',
        }
        data.update(extra)
        return self.client.post(reverse('log-donation'), data)

    def test_form_only_offers_requests_donor_pledged_to(self):
        other = make_request(self.requester, hospital='Other')
        RequestResponse.objects.create(blood_request=self.req, donor=self.donor)
        self.client.force_login(self.donor)
        response = self.client.get(reverse('log-donation'))
        queryset = response.context['form'].fields['blood_request'].queryset
        self.assertEqual(list(queryset), [self.req])
        self.assertNotIn(other, queryset)

    def test_cannot_link_donation_to_unpledged_request(self):
        self.client.force_login(self.donor)
        response = self.log_donation(blood_request=self.req.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Donation.objects.count(), 0)

    def test_pledged_donation_links_to_request(self):
        RequestResponse.objects.create(blood_request=self.req, donor=self.donor)
        self.client.force_login(self.donor)
        self.log_donation(blood_request=self.req.pk)
        self.assertEqual(Donation.objects.get().blood_request, self.req)

    def test_bonus_points_only_once_verified(self):
        donation = donate(self.donor, blood_request=self.req,
                          status=Donation.Status.PENDING)
        self.assertEqual(get_donor_points(self.donor), 0)
        approve(donation)
        # 10 for the donation + 5 streak + 20 request bonus
        self.assertEqual(get_donor_points(self.donor), 35)

    def test_first_request_donation_unlocks_hero_badge(self):
        before = get_donor_badge_snapshot(self.donor)
        donate(self.donor, blood_request=self.req)
        names = [b.name for b in get_newly_unlocked_badges(before, self.donor)]
        self.assertIn('First Responder', names)
        self.assertEqual(request_donation_count(self.donor), 1)
        self.assertEqual(
            [b.name for b in get_earned_hero_badges(1)], ['First Responder'],
        )

    def test_hero_badge_email_on_admin_approval(self):
        admin = User.objects.create_superuser(
            phone='01900000000', password='adminpass123',
        )
        donation = donate(self.donor, blood_request=self.req,
                          status=Donation.Status.PENDING)
        self.client.force_login(admin)
        self.client.post(reverse('admin:rewards_donation_changelist'), {
            'action': 'approve_donations',
            '_selected_action': [donation.pk],
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('First Responder', mail.outbox[0].body)


class LeaderboardTests(TestCase):
    def setUp(self):
        self.a = make_user('01711111111', first_name='Alice')
        self.b = make_user('01722222222', first_name='Bob')
        self.c = make_user('01733333333', first_name='Carol')
        requester = make_user('01744444444')
        req = make_request(requester, units=5)
        for _ in range(3):
            donate(self.a)
        donate(self.b, blood_request=req)
        donate(self.c, days_ago=60)

    def test_month_ranking_uses_points_and_breaks_ties_by_donations(self):
        entries = get_leaderboard('month')
        # Alice 3*10 = 30, Bob 10+20 = 30 (tie -> more donations wins)
        self.assertEqual([e['name'] for e in entries], ['Alice', 'Bob'])
        self.assertEqual([e['rank'] for e in entries], [1, 2])
        self.assertEqual(entries[1]['request_donations'], 1)

    def test_all_time_includes_older_donors_and_streak_bonus(self):
        entries = get_leaderboard('all')
        self.assertEqual({e['name'] for e in entries}, {'Alice', 'Bob', 'Carol'})
        alice = next(e for e in entries if e['name'] == 'Alice')
        # 3 donations (30), all dated today, so the streak is 3 (15)
        self.assertEqual(alice['points'], 45)
        self.assertEqual(alice['lives'], 9)

    def test_pending_and_rejected_never_rank(self):
        d = make_user('01755555555', first_name='Dan')
        donate(d, status=Donation.Status.PENDING)
        donate(d, days_ago=100, status=Donation.Status.REJECTED)
        self.assertNotIn('Dan', [e['name'] for e in get_leaderboard('all')])

    def test_limit_applies(self):
        self.assertEqual(len(get_leaderboard('all', limit=1)), 1)

    def test_monthly_awards(self):
        monthly = get_monthly_awards()
        titles = {a['title']: a['name'] for a in monthly['awards']}
        self.assertEqual(titles['Donor of the Month'], 'Alice')
        self.assertEqual(titles['Lifeline Hero'], 'Bob')
        self.assertEqual(monthly['donations'], 4)
        self.assertEqual(monthly['lives'], 12)
        self.assertEqual(monthly['new_donors'], 2)

    def test_no_hero_award_without_request_donations(self):
        Donation.objects.exclude(blood_request=None).delete()
        titles = [a['title'] for a in get_monthly_awards()['awards']]
        self.assertNotIn('Lifeline Hero', titles)

    def test_leaderboard_page_is_public(self):
        response = self.client.get(reverse('leaderboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Alice')
        self.assertContains(response, 'Donor of the Month')

    def test_invalid_period_falls_back_to_all_time(self):
        response = self.client.get(reverse('leaderboard'), {'period': 'bogus'})
        self.assertEqual(response.context['period'], 'all')

    def test_logged_in_donor_sees_own_rank(self):
        self.client.force_login(self.b)
        response = self.client.get(reverse('leaderboard'), {'period': 'month'})
        self.assertEqual(response.context['my_entry']['rank'], 2)


class DonationsOnDifferentDatesTests(TestCase):
    """Donation's default ordering must not split donors in GROUP BY."""

    def setUp(self):
        self.karim = make_user('01711111111', first_name='Karim')
        self.fatima = make_user('01722222222', first_name='Fatima')
        for days in (200, 100, 40, 10, 2):
            donate(self.karim, days_ago=days)
        for days in (150, 20):
            donate(self.fatima, days_ago=days)

    def test_each_donor_appears_once_with_all_donations(self):
        entries = get_leaderboard('all')
        self.assertEqual([e['name'] for e in entries], ['Karim', 'Fatima'])
        self.assertEqual([e['donations'] for e in entries], [5, 2])
        self.assertEqual([e['lives'] for e in entries], [15, 6])
        # 5 donations are all in the Bronze tier (needs 5 for Silver)
        self.assertEqual(entries[0]['badge'].name, 'Silver Donor')

    def test_month_view_counts_one_row_per_donor(self):
        donate(self.karim, days_ago=0)
        names = [e['name'] for e in get_leaderboard('month')]
        self.assertEqual(len(names), len(set(names)))

    def test_community_donor_count_counts_people_not_dates(self):
        stats = get_community_stats()
        self.assertEqual(stats['donors'], 2)
        self.assertEqual(stats['donations'], 7)
        self.assertEqual(stats['lives'], 21)

    def test_new_donors_counts_people_not_dates(self):
        make_user('01733333333', first_name='Newbie')
        newbie = User.objects.get(phone='01733333333')
        donate(newbie, days_ago=0)
        donate(newbie, days_ago=0)
        self.assertEqual(get_monthly_awards()['new_donors'], 1)


class ShareCardTests(TestCase):
    def setUp(self):
        self.donor = make_user(
            '01711111111', first_name='Karim', last_name='Rahman',
        )
        for i in range(2):
            donate(self.donor, days_ago=i * 30)

    def test_badge_card_has_social_preview_and_share_links(self):
        response = self.client.get(reverse('badge-card', args=[self.donor.id]))
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, 'Karim Rahman')
        self.assertContains(response, 'wa.me/?text=')
        self.assertContains(response, 'facebook.com/sharer/sharer.php?u=')
        self.assertContains(response, 'id="share-data"')

    def test_badge_card_data_for_the_image(self):
        response = self.client.get(reverse('badge-card', args=[self.donor.id]))
        card = response.context['share_card']
        self.assertEqual(card['type'], 'badge')
        self.assertEqual(card['donations'], 2)
        self.assertEqual(card['lives'], 6)
        self.assertEqual(card['tier'], 'Bronze Donor')

    def test_request_detail_has_social_preview_and_card_data(self):
        req = make_request(self.donor, hospital='Square Hospital')
        response = self.client.get(reverse('request-detail', args=[req.pk]))
        self.assertContains(
            response, 'Urgent: A+ blood needed at Square Hospital',
        )
        card = response.context['share_card']
        self.assertEqual(card['type'], 'request')
        self.assertEqual(card['remaining'], 1)
        # The shareable image must never carry the contact number.
        self.assertNotIn('01712345678', str(card))

    def test_closed_request_hides_share_panel(self):
        req = make_request(self.donor, status=BloodRequest.Status.CLOSED)
        response = self.client.get(reverse('request-detail', args=[req.pk]))
        self.assertNotContains(response, 'data-share-root')

    def test_home_page_shows_community_and_requests(self):
        make_request(self.donor, hospital='Home Hospital')
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Home Hospital')
        self.assertContains(response, 'Est. people helped')
        self.assertEqual(response.context['community']['donations'], 2)
