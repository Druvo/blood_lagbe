"""Privacy opt-outs: leaderboard, home page, badge card, and phone leaks."""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.leaderboard import display_name, get_leaderboard, get_monthly_awards
from rewards.models import Donation, get_community_stats
from rewards.tests import approve, make_user


def donate(user, n=1):
    for i in range(n):
        donation = Donation.objects.create(
            donor=user, blood_group='A+',
            donation_date=timezone.localdate() - datetime.timedelta(days=i),
            location='Bank',
        )
        approve(donation)


class DisplayNameTests(TestCase):
    def test_never_falls_back_to_phone(self):
        user = make_user(phone='01712345678')
        self.assertNotIn('01712345678', display_name(user))
        self.assertEqual(display_name(user), f'Donor #{user.pk}')

    def test_uses_real_name_when_set(self):
        user = make_user(phone='01712345679', first_name='Rina', last_name='Das')
        self.assertEqual(display_name(user), 'Rina Das')

    def test_phone_not_on_public_pages(self):
        user = make_user(phone='01712345680')
        donate(user)
        for name in ('home', 'leaderboard'):
            response = self.client.get(reverse(name))
            self.assertNotContains(response, '01712345680')
        response = self.client.get(reverse('badge-card', args=[user.pk]))
        self.assertNotContains(response, '01712345680')


class LeaderboardOptOutTests(TestCase):
    def setUp(self):
        self.shown = make_user(phone='01711111111', first_name='Shown')
        self.hidden = make_user(phone='01722222222', first_name='Hidden')
        donate(self.shown, 1)
        donate(self.hidden, 3)  # would win if visible
        self.hidden.profile.show_on_leaderboard = False
        self.hidden.profile.save()

    def test_hidden_donor_not_ranked(self):
        self.assertEqual([e['name'] for e in get_leaderboard('all')], ['Shown'])
        self.assertEqual([e['name'] for e in get_leaderboard('month')], ['Shown'])

    def test_hidden_donor_not_awarded(self):
        awards = get_monthly_awards()
        self.assertEqual([a['name'] for a in awards['awards']], ['Shown'])

    def test_hidden_donor_still_in_aggregate_totals(self):
        self.assertEqual(get_community_stats()['donations'], 4)
        self.assertEqual(get_monthly_awards()['donations'], 4)

    def test_hidden_donor_not_on_home_or_leaderboard_page(self):
        for name in ('home', 'leaderboard'):
            response = self.client.get(reverse(name))
            self.assertNotContains(response, 'Hidden')
            self.assertContains(response, 'Shown')

    def test_hidden_donor_told_they_are_hidden(self):
        self.client.force_login(self.hidden)
        response = self.client.get(reverse('leaderboard'))
        self.assertContains(response, "You're hidden from the leaderboard")


class BadgeCardPrivacyTests(TestCase):
    def setUp(self):
        self.donor = make_user(phone='01733333333', first_name='Private')
        self.donor.profile.public_badge_card = False
        self.donor.profile.save()
        self.url = reverse('badge-card', args=[self.donor.pk])

    def test_anonymous_gets_404(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_other_user_gets_404(self):
        self.client.force_login(make_user(phone='01744444444'))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_owner_can_view(self):
        self.client.force_login(self.donor)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_staff_can_view(self):
        staff = make_user(phone='01755555555')
        staff.staff = True
        staff.save()
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_leaderboard_name_not_linked_when_card_private(self):
        donate(self.donor)
        response = self.client.get(reverse('leaderboard'))
        self.assertContains(response, 'Private')
        self.assertNotContains(response, self.url)


class PrivacyViewTests(TestCase):
    def setUp(self):
        self.user = make_user(phone='01766666666')
        self.url = reverse('privacy')

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_defaults_are_on(self):
        self.assertTrue(self.user.profile.show_on_leaderboard)
        self.assertTrue(self.user.profile.public_badge_card)

    def test_can_opt_out(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {})  # unchecked boxes
        self.assertEqual(response.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.show_on_leaderboard)
        self.assertFalse(self.user.profile.public_badge_card)

    def test_can_opt_back_in(self):
        self.user.profile.show_on_leaderboard = False
        self.user.profile.save()
        self.client.force_login(self.user)
        self.client.post(self.url, {'show_on_leaderboard': 'on'})
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.show_on_leaderboard)
