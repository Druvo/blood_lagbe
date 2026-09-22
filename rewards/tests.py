import datetime

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.models import (
    Badge,
    Donation,
    get_current_streak,
    get_donor_badge_progress,
    get_donor_badge_snapshot,
    get_donor_points,
    get_earned_streak_badges,
    get_newly_unlocked_badges,
    validate_not_future,
)

User = get_user_model()


def make_user(phone='01700000000', **kwargs):
    user = User.objects.create_user(phone=phone, password='testpass123')
    if kwargs:
        for field, value in kwargs.items():
            setattr(user, field, value)
        user.save()
    return user


def approve(donation):
    donation.status = Donation.Status.APPROVED
    donation.save()


class ValidateNotFutureTests(TestCase):
    def test_rejects_future_date(self):
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        with self.assertRaises(ValidationError):
            validate_not_future(tomorrow)

    def test_accepts_today_and_past(self):
        validate_not_future(timezone.localdate())
        validate_not_future(timezone.localdate() - datetime.timedelta(days=1))


class DonationCountTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_only_approved_donations_count(self):
        today = timezone.localdate()
        Donation.objects.create(
            donor=self.user, blood_group='A+', donation_date=today,
            location='Bank A', status=Donation.Status.PENDING,
        )
        Donation.objects.create(
            donor=self.user, blood_group='A+', donation_date=today,
            location='Bank B', status=Donation.Status.REJECTED,
        )
        Donation.objects.create(
            donor=self.user, blood_group='A+', donation_date=today,
            location='Bank C', status=Donation.Status.APPROVED,
        )
        _, _, count, _ = get_donor_badge_progress(self.user)
        self.assertEqual(count, 1)


class StreakTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def _donate(self, days_ago, status=Donation.Status.APPROVED):
        return Donation.objects.create(
            donor=self.user, blood_group='O+',
            donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
            location='Test Bank', status=status,
        )

    def test_no_donations_zero_streak(self):
        self.assertEqual(get_current_streak(self.user), 0)

    def test_single_recent_donation_streak_one(self):
        self._donate(10)
        self.assertEqual(get_current_streak(self.user), 1)

    def test_single_stale_donation_streak_zero(self):
        self._donate(150)  # older than 84 + 14 day threshold
        self.assertEqual(get_current_streak(self.user), 0)

    def test_consecutive_donations_within_threshold(self):
        self._donate(270)
        self._donate(180)
        self._donate(90)
        self._donate(20)
        self.assertEqual(get_current_streak(self.user), 4)

    def test_gap_breaks_streak(self):
        self._donate(300)  # isolated, gap to next is too large
        self._donate(90)
        self._donate(20)
        self.assertEqual(get_current_streak(self.user), 2)

    def test_pending_donations_dont_count_toward_streak(self):
        self._donate(90)
        self._donate(20, status=Donation.Status.PENDING)
        self.assertEqual(get_current_streak(self.user), 1)


class PointsTests(TestCase):
    def test_formula(self):
        user = make_user()
        Donation.objects.create(
            donor=user, blood_group='B+', donation_date=timezone.localdate(),
            location='Bank', status=Donation.Status.APPROVED,
        )
        # 1 approved donation (10 pts) + streak of 1 (5 pts)
        self.assertEqual(get_donor_points(user), 15)


class BadgeProgressTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def _approve_n_donations(self, n):
        for i in range(n):
            Donation.objects.create(
                donor=self.user, blood_group='A+',
                donation_date=timezone.localdate() - datetime.timedelta(days=i),
                location='Bank', status=Donation.Status.APPROVED,
            )

    def test_no_donations_no_current_badge(self):
        current, next_badge, count, progress = get_donor_badge_progress(self.user)
        self.assertIsNone(current)
        self.assertEqual(next_badge.min_donations, 1)
        self.assertEqual(count, 0)
        self.assertEqual(progress, 0)

    def test_bronze_tier_at_one_donation(self):
        self._approve_n_donations(1)
        current, next_badge, count, progress = get_donor_badge_progress(self.user)
        self.assertEqual(current.name, 'Bronze Donor')
        self.assertEqual(next_badge.min_donations, 5)

    def test_top_tier_has_no_next_badge_and_full_progress(self):
        self._approve_n_donations(25)
        current, next_badge, count, progress = get_donor_badge_progress(self.user)
        self.assertEqual(current.name, 'Platinum Donor')
        self.assertIsNone(next_badge)
        self.assertEqual(progress, 100)

    def test_streak_badges_earned_by_threshold(self):
        self.assertEqual(get_earned_streak_badges(0), [])
        self.assertEqual(len(get_earned_streak_badges(2)), 1)
        self.assertEqual(len(get_earned_streak_badges(4)), 2)
        self.assertEqual(len(get_earned_streak_badges(8)), 3)


class NewlyUnlockedBadgeTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_detects_new_tier_badge(self):
        before = get_donor_badge_snapshot(self.user)
        Donation.objects.create(
            donor=self.user, blood_group='A+', donation_date=timezone.localdate(),
            location='Bank', status=Donation.Status.APPROVED,
        )
        newly_unlocked = get_newly_unlocked_badges(before, self.user)
        self.assertEqual(len(newly_unlocked), 1)
        self.assertEqual(newly_unlocked[0].name, 'Bronze Donor')

    def test_no_new_badge_when_tier_unchanged(self):
        Donation.objects.create(
            donor=self.user, blood_group='A+', donation_date=timezone.localdate(),
            location='Bank', status=Donation.Status.APPROVED,
        )
        before = get_donor_badge_snapshot(self.user)
        # a second donation far enough back that it neither extends the
        # streak (past the cycle+grace threshold) nor crosses the next
        # tier (5)
        Donation.objects.create(
            donor=self.user, blood_group='A+',
            donation_date=timezone.localdate() - datetime.timedelta(days=200),
            location='Bank', status=Donation.Status.APPROVED,
        )
        self.assertEqual(get_newly_unlocked_badges(before, self.user), [])


class LogDonationViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.url = reverse('log-donation')

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_get_shows_no_tier_for_fresh_user(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['current_badge'])
        self.assertEqual(response.context['approved_count'], 0)

    def test_post_creates_pending_donation(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {
            'blood_group': 'A+',
            'donation_date': timezone.localdate().isoformat(),
            'location': 'Test Blood Bank',
        })
        self.assertEqual(response.status_code, 302)
        donation = Donation.objects.get(donor=self.user)
        self.assertEqual(donation.status, Donation.Status.PENDING)
        self.assertEqual(donation.location, 'Test Blood Bank')

    def test_post_rejects_future_date(self):
        self.client.force_login(self.user)
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        response = self.client.post(self.url, {
            'blood_group': 'A+',
            'donation_date': tomorrow.isoformat(),
            'location': 'Test Blood Bank',
        })
        self.assertEqual(response.status_code, 200)  # re-renders with errors
        self.assertFalse(Donation.objects.filter(donor=self.user).exists())


class BadgeCardViewTests(TestCase):
    def test_renders_for_existing_user(self):
        user = make_user(first_name='Card', last_name='Test')
        response = self.client.get(reverse('badge-card', args=[user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Card Test')

    def test_404_for_missing_user(self):
        response = self.client.get(reverse('badge-card', args=[999999]))
        self.assertEqual(response.status_code, 404)


class ApproveDonationAdminActionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            phone='01900000000', password='adminpass123',
        )
        self.donor = make_user(phone='01711111111', email='donor@example.com')
        self.client.force_login(self.admin)

    def _create_pending(self, days_ago=0):
        return Donation.objects.create(
            donor=self.donor, blood_group='A+',
            donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
            location='Bank', status=Donation.Status.PENDING,
        )

    def test_approve_action_sets_status_and_verifier(self):
        donation = self._create_pending()
        self.client.post(reverse('admin:rewards_donation_changelist'), {
            'action': 'approve_donations',
            '_selected_action': [donation.pk],
        })
        donation.refresh_from_db()
        self.assertEqual(donation.status, Donation.Status.APPROVED)
        self.assertEqual(donation.verified_by, self.admin)

    def test_approve_sends_badge_unlock_email(self):
        donation = self._create_pending()
        self.client.post(reverse('admin:rewards_donation_changelist'), {
            'action': 'approve_donations',
            '_selected_action': [donation.pk],
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.donor.email, mail.outbox[0].to)
        self.assertIn('badge', mail.outbox[0].subject.lower())

    def test_reject_action_sets_status(self):
        donation = self._create_pending()
        self.client.post(reverse('admin:rewards_donation_changelist'), {
            'action': 'reject_donations',
            '_selected_action': [donation.pk],
        })
        donation.refresh_from_db()
        self.assertEqual(donation.status, Donation.Status.REJECTED)
