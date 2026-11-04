"""Leaderboard page: district filter, podium, ties, "your rank", privacy."""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.leaderboard import (
    get_leaderboard,
    get_request_heroes,
    get_user_standing,
    initials,
)
from rewards.models import BloodRequest, Donation
from rewards.tests import make_user

_n = iter(range(1, 100000))


def donor(name, **kwargs):
    return make_user(phone=f'0181{next(_n):07d}', first_name=name, **kwargs)


def give(user, district='Dhaka', days_ago=0, status=Donation.Status.APPROVED,
         blood_request=None):
    return Donation.objects.create(
        donor=user, blood_group='A+', district=district, location='Bank',
        donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
        status=status, blood_request=blood_request,
    )


def urgent_request(requester):
    return BloodRequest.objects.create(
        requester=requester, blood_group='A+', hospital='H', district='Dhaka',
        units_needed=10,
        needed_by=timezone.localdate() + datetime.timedelta(days=3),
        contact_phone='01712345678',
    )


class DistrictFilterTests(TestCase):
    def setUp(self):
        self.dhaka = donor('Dhakaite')
        self.sylhet = donor('Sylheti')
        self.both = donor('Traveller')
        give(self.dhaka, 'Dhaka')
        give(self.sylhet, 'Sylhet')
        give(self.both, 'Dhaka')
        give(self.both, 'Sylhet', days_ago=1)
        give(self.both, 'Sylhet', days_ago=2)

    def names(self, **kw):
        return [e['name'] for e in get_leaderboard(**kw)]

    def test_no_district_ranks_everyone(self):
        self.assertEqual(
            set(self.names(period='all')),
            {'Dhakaite', 'Sylheti', 'Traveller'},
        )

    def test_district_counts_only_donations_made_there(self):
        entries = get_leaderboard('all', district='Sylhet')
        self.assertEqual([e['name'] for e in entries], ['Traveller', 'Sylheti'])
        self.assertEqual(entries[0]['donations'], 2)
        self.assertEqual(entries[0]['points'], 20)  # no streak bonus here

    def test_donor_without_donations_in_district_is_absent(self):
        self.assertNotIn('Sylheti', self.names(period='all', district='Dhaka'))

    def test_unapproved_donations_do_not_count_in_district(self):
        ghost = donor('Pending')
        give(ghost, 'Khulna', status=Donation.Status.PENDING)
        give(ghost, 'Khulna', days_ago=3, status=Donation.Status.REJECTED)
        self.assertEqual(self.names(period='all', district='Khulna'), [])

    def test_district_respects_leaderboard_opt_out(self):
        self.both.profile.show_on_leaderboard = False
        self.both.profile.save()
        self.assertEqual(self.names(period='all', district='Sylhet'), ['Sylheti'])

    def test_district_and_month_combine(self):
        old = donor('Old')
        give(old, 'Sylhet', days_ago=90)
        self.assertNotIn('Old', self.names(period='month', district='Sylhet'))
        self.assertIn('Old', self.names(period='all', district='Sylhet'))

    def test_page_filters_by_district(self):
        response = self.client.get(reverse('leaderboard'), {'district': 'Dhaka'})
        self.assertEqual(response.context['district'], 'Dhaka')
        names = [e['name'] for e in response.context['entries']]
        self.assertEqual(set(names), {'Dhakaite', 'Traveller'})

    def test_page_ignores_unknown_district(self):
        response = self.client.get(reverse('leaderboard'), {'district': 'Narnia'})
        self.assertEqual(response.context['district'], '')
        self.assertEqual(len(response.context['entries']), 3)

    def test_empty_district_shows_friendly_state(self):
        response = self.client.get(reverse('leaderboard'), {'district': 'Bhola'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No verified donations in Bhola')


class PodiumTests(TestCase):
    def url(self, **params):
        return reverse('leaderboard') + (
            '?' + '&'.join(f'{k}={v}' for k, v in params.items()) if params else ''
        )

    def test_no_donors_has_empty_state_and_no_podium(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['podium'], [])
        self.assertContains(response, 'Nobody on the board yet')
        self.assertNotContains(response, 'cm-podium')

    def test_one_donor_makes_a_podium_of_one(self):
        give(donor('Solo'))
        response = self.client.get(self.url())
        self.assertEqual([e['name'] for e in response.context['podium']], ['Solo'])
        self.assertEqual(response.context['rest'], [])
        self.assertContains(response, 'Solo')
        self.assertContains(response, 'Room for you')

    def test_two_donors(self):
        give(donor('One'))
        give(donor('Two'), days_ago=1)
        response = self.client.get(self.url(period='month'))
        self.assertEqual(len(response.context['podium']), 2)

    def test_more_than_three_splits_podium_and_rest(self):
        for i in range(5):
            u = donor(f'D{i}')
            for _ in range(5 - i):
                give(u)
        response = self.client.get(self.url())
        self.assertEqual(len(response.context['podium']), 3)
        self.assertEqual(len(response.context['rest']), 2)
        self.assertEqual(
            [e['rank'] for e in response.context['rest']], [4, 5],
        )

    def test_page_shows_at_most_twenty(self):
        for i in range(25):
            give(donor(f'N{i}'))
        response = self.client.get(self.url())
        self.assertEqual(len(response.context['entries']), 20)


class TieTests(TestCase):
    def test_equal_points_and_donations_share_a_rank(self):
        a, b, c = donor('A'), donor('B'), donor('C')
        give(a)
        give(b)
        give(c, days_ago=2)
        give(c, days_ago=3)
        entries = get_leaderboard('month')
        ranks = {e['name']: e['rank'] for e in entries}
        self.assertEqual(ranks, {'C': 1, 'A': 2, 'B': 2})
        self.assertTrue(all(e['tied'] for e in entries if e['name'] in 'AB'))
        self.assertFalse(entries[0]['tied'])

    def test_rank_after_a_tie_skips(self):
        a, b, c = donor('A'), donor('B'), donor('C')
        for u in (a, b):
            give(u)
            give(u, days_ago=1)
        give(c)
        entries = get_leaderboard('month')
        self.assertEqual([e['rank'] for e in entries], [1, 1, 3])

    def test_gap_to_next_rank(self):
        a, b = donor('A'), donor('B')
        for i in range(3):
            give(a, days_ago=i)
        give(b)
        entries = get_leaderboard('month')
        self.assertEqual(entries[0]['gap'], 0)
        self.assertEqual(entries[1]['gap'], 20)


class MyRankTests(TestCase):
    def setUp(self):
        self.me = donor('Me')
        self.url = reverse('leaderboard')

    def test_anonymous_has_no_rank_card(self):
        response = self.client.get(self.url)
        self.assertIsNone(response.context['my_entry'])
        self.assertNotContains(response, 'Your rank')

    def test_ranked_user_sees_rank(self):
        give(self.me)
        self.client.force_login(self.me)
        response = self.client.get(self.url)
        self.assertContains(response, 'Your rank')
        self.assertEqual(response.context['my_entry']['rank'], 1)
        self.assertContains(response, "You're leading")

    def test_not_yet_on_board_message(self):
        self.client.force_login(self.me)
        response = self.client.get(self.url)
        self.assertContains(response, "You're not on the board yet")

    def test_hidden_user_gets_message_and_privacy_link(self):
        give(self.me)
        self.me.profile.show_on_leaderboard = False
        self.me.profile.save()
        self.client.force_login(self.me)
        response = self.client.get(self.url)
        self.assertContains(response, "You're hidden from the leaderboard")
        self.assertContains(response, reverse('privacy'))
        self.assertNotContains(response, 'Your rank')
        self.assertIsNone(response.context['my_entry'])

    def test_user_outside_the_pool_still_gets_standing(self):
        for i in range(52):
            u = donor(f'P{i}')
            give(u)
            give(u, days_ago=1)
        give(self.me)
        self.client.force_login(self.me)
        response = self.client.get(self.url, {'period': 'month'})
        self.assertIsNone(response.context['my_entry'])
        self.assertEqual(response.context['my_standing']['donations'], 1)
        self.assertContains(response, 'outside the top 50')

    def test_standing_counts_only_approved(self):
        give(self.me)
        give(self.me, days_ago=1, status=Donation.Status.PENDING)
        self.assertEqual(
            get_user_standing(self.me, 'month')['donations'], 1,
        )


class HeroesAndPrivacyTests(TestCase):
    def test_request_heroes_rank_by_request_donations(self):
        requester = donor('Needy')
        req = urgent_request(requester)
        a, b = donor('Hero'), donor('Regular')
        give(a, blood_request=req)
        give(a, days_ago=1, blood_request=req)
        give(b, blood_request=req)
        give(b, days_ago=1)
        heroes = get_request_heroes('all')
        self.assertEqual([h['name'] for h in heroes], ['Hero', 'Regular'])
        self.assertEqual(heroes[0]['count'], 2)

    def test_heroes_exclude_opted_out_and_unverified(self):
        req = urgent_request(donor('Needy'))
        hidden, pending = donor('Secret'), donor('Waiting')
        give(hidden, blood_request=req)
        give(pending, blood_request=req, status=Donation.Status.PENDING)
        hidden.profile.show_on_leaderboard = False
        hidden.profile.save()
        self.assertEqual(get_request_heroes('all'), [])

    def test_page_never_renders_phone_numbers(self):
        u = make_user(phone='01899990001', first_name='')
        give(u)
        response = self.client.get(reverse('leaderboard'))
        self.assertContains(response, f'Donor #{u.pk}')
        self.assertNotContains(response, '01899990001')

    def test_opted_out_donor_absent_from_every_view_of_the_page(self):
        u = donor('Invisible')
        give(u, 'Sylhet')
        u.profile.show_on_leaderboard = False
        u.profile.save()
        for params in ({}, {'period': 'month'}, {'district': 'Sylhet'}):
            response = self.client.get(reverse('leaderboard'), params)
            self.assertNotContains(response, 'Invisible')


class InitialsTests(TestCase):
    def test_initials(self):
        self.assertEqual(initials('Rina Das'), 'RD')
        self.assertEqual(initials('Rina'), 'R')
        self.assertEqual(initials('Donor #12'), 'D')
        self.assertEqual(initials('#12'), '?')
        self.assertEqual(initials('নুসরাত জাহান'), 'নজ')
