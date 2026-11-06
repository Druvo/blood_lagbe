"""Habits: daily micro-actions, the visit streak, titles, the weekly digest
and the year in review. Privacy and safety first."""
import datetime
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from habits import content, digest, services, titles, year as year_review
from habits.models import DailyCompletion, DigestLog, DigestPref, ShareAction
from rewards.models import (
    Badge,
    BloodRequest,
    Donation,
    RequestResponse,
    get_current_streak,
    get_donor_points,
)
from rewards.tests import make_user
from squads.models import Squad, SquadMember
from stories.models import Story

_n = iter(range(1, 100000))
ALL_TASKS = list(services.TASK_ORDER)


def person(name='Pat', email=None, **profile):
    n = next(_n)
    user = make_user(phone=f'0176{n:07d}', first_name=name)
    if email is None:
        email = f'pat{n}@habits.test'
    user.email = email or None
    user.save()
    for key, value in profile.items():
        setattr(user.profile, key, value)
    if profile:
        user.profile.save()
    return user


def give(user, days_ago=0, status=Donation.Status.APPROVED, request=None,
         location='Test Bank', on=None):
    return Donation.objects.create(
        donor=user, blood_group='A+', district='Dhaka', location=location,
        status=status, blood_request=request,
        donation_date=on or (timezone.localdate() - datetime.timedelta(days=days_ago)),
    )


def make_request(requester, group='A+', district='Dhaka', days=3, **kwargs):
    data = dict(
        requester=requester, blood_group=group, hospital='Habits Hospital',
        district=district, units_needed=2,
        needed_by=timezone.localdate() + datetime.timedelta(days=days),
        contact_phone='01912345678',
    )
    data.update(kwargs)
    return BloodRequest.objects.create(**data)


def d(*args):
    """d(11, 6) is 2026-11-06; d(2025, 3, 1) is year, month, day."""
    if args[0] > 12:
        return datetime.date(*args)
    return datetime.date(2026, *args)


class ClearCacheMixin:
    def setUp(self):
        super().setUp()
        cache.clear()


# --------------------------------------------------------------- content

class ContentTests(SimpleTestCase):
    def test_enough_facts_and_questions(self):
        self.assertGreaterEqual(len(content.facts()), 30)
        self.assertGreaterEqual(len(content.quiz_bank()), 15)

    def test_facts_are_unique_and_hedged(self):
        facts = content.facts()
        self.assertEqual(len(set(facts)), len(facts))
        banned = ('cure', 'guarantee', 'dose', ' mg', 'diagnos', 'prescri')
        for text in facts + [q['explanation'] for q in content.quiz_bank()]:
            self.assertNotIn('{interval}', text)
            for word in banned:
                self.assertNotIn(word, text.lower())

    def test_footer_says_blood_bank_decides(self):
        self.assertIn('blood bank', content.FACT_FOOTER.lower())
        self.assertIn('screening', content.FACT_FOOTER.lower())

    def test_interval_follows_the_setting(self):
        from django.test import override_settings
        with override_settings(DONATION_INTERVAL_DAYS=90):
            self.assertEqual(content.interval_label(), '3 months')
            self.assertTrue(any('3 months' in f for f in content.facts()))
        with override_settings(DONATION_INTERVAL_DAYS=100):
            self.assertEqual(content.interval_label(), '100 days')

    def test_quiz_answers_are_myth_or_fact(self):
        for q in content.quiz_bank():
            self.assertIn(q['answer'], ('M', 'F'))
            self.assertTrue(q['explanation'])


# ---------------------------------------------------------------- the plan

class PlanTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = person()
        self.other = person()
        self.req = make_request(self.other)

    def test_three_tasks_a_day(self):
        for offset in range(8):
            day = d(11, 1) + datetime.timedelta(days=offset)
            tasks = services.assigned_tasks(self.user, day, self.req)
            self.assertEqual(len(tasks), services.DAILY_TASK_COUNT)
            self.assertEqual(len(set(tasks)), 3)

    def test_deterministic_for_a_user_and_date(self):
        day = d(11, 6)
        self.assertEqual(
            services.assigned_tasks(self.user, day, self.req),
            services.assigned_tasks(self.user, day, self.req),
        )
        self.assertEqual(
            services.fact_for(self.user, day), services.fact_for(self.user, day),
        )
        self.assertEqual(
            services.quiz_for(self.user, day), services.quiz_for(self.user, day),
        )

    def test_rotates_each_task_rests_once_in_four_days(self):
        rested = []
        for offset in range(4):
            day = d(11, 1) + datetime.timedelta(days=offset)
            tasks = services.assigned_tasks(self.user, day, self.req)
            rested.append(next(t for t in ALL_TASKS if t not in tasks))
        self.assertEqual(sorted(rested), sorted(ALL_TASKS))

    def test_users_differ(self):
        day = d(11, 6)
        a = services.fact_for(self.user, day)['text']
        b = services.fact_for(self.other, day)['text']
        self.assertNotEqual(a, b)

    def test_fact_changes_daily(self):
        seen = {
            services.fact_for(self.user, d(11, 1) + datetime.timedelta(days=i))['text']
            for i in range(10)
        }
        self.assertEqual(len(seen), 10)

    def test_no_request_means_the_other_three(self):
        tasks = services.assigned_tasks(self.user, d(11, 6), None)
        self.assertEqual(tasks, [
            services.AVAILABILITY, services.FACT, services.QUIZ,
        ])

    def test_quiz_has_three_distinct_questions(self):
        qs = services.quiz_for(self.user, d(11, 6))
        self.assertEqual(len(qs), 3)
        self.assertEqual(len({q['statement'] for q in qs}), 3)

    def test_share_request_never_own_closed_or_flagged(self):
        self.req.delete()
        mine = make_request(self.user)
        self.assertIsNone(services.pick_share_request(self.user))
        mine.delete()
        closed = make_request(self.other)
        closed.close()
        self.assertIsNone(services.pick_share_request(self.user))
        closed.delete()
        flagged = make_request(self.other)
        from rewards.models import RequestReport
        for i in range(3):
            RequestReport.objects.create(
                blood_request=flagged, reporter=person(), reason='spam',
            )
        self.assertIsNone(services.pick_share_request(self.user))

    def test_share_request_prefers_own_district(self):
        self.req.delete()
        self.user.profile.district = 'Sylhet'
        self.user.profile.save()
        make_request(self.other, district='Dhaka')
        near = make_request(self.other, district='Sylhet')
        self.assertEqual(services.pick_share_request(self.user), near)

    def test_complete_task_is_unique_and_idempotent(self):
        _, first = services.complete_task(self.user, services.FACT)
        _, second = services.complete_task(self.user, services.FACT)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(DailyCompletion.objects.count(), 1)

    def test_unique_constraint_in_database(self):
        DailyCompletion.objects.create(
            user=self.user, day=d(11, 6), task='fact',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DailyCompletion.objects.create(
                user=self.user, day=d(11, 6), task='fact',
            )


# ------------------------------------------------------------------ streaks

class StreakTests(SimpleTestCase):
    """2026-11-02 is a Monday, so Nov 2 to Nov 8 is one ISO week."""

    def test_plain_run(self):
        days = {d(11, 2), d(11, 3), d(11, 4)}
        self.assertEqual(services.streak_ending(days, d(11, 4)), 3)

    def test_empty(self):
        self.assertEqual(services.streak_ending(set(), d(11, 4), True), 0)

    def test_today_not_done_yet_keeps_streak(self):
        days = {d(11, 2), d(11, 3)}
        self.assertEqual(services.streak_ending(days, d(11, 4), True), 2)

    def test_one_missed_day_is_forgiven(self):
        days = {d(11, 2), d(11, 4)}
        self.assertEqual(services.streak_ending(days, d(11, 4)), 2)

    def test_two_missed_days_break_it(self):
        days = {d(11, 2), d(11, 5)}
        self.assertEqual(services.streak_ending(days, d(11, 5)), 1)

    def test_only_one_grace_per_iso_week(self):
        days = {d(11, 2), d(11, 4), d(11, 6)}
        self.assertEqual(services.streak_ending(days, d(11, 6)), 2)

    def test_grace_resets_each_week(self):
        days = {d(10, 30), d(11, 1), d(11, 3)}
        self.assertEqual(services.streak_ending(days, d(11, 3)), 3)

    def test_missing_yesterday_with_today_open_is_forgiven_once(self):
        days = {d(11, 2), d(11, 3)}
        self.assertEqual(services.streak_ending(days, d(11, 5), True), 2)

    def test_two_gaps_with_today_open_break(self):
        days = {d(11, 2)}
        self.assertEqual(services.streak_ending(days, d(11, 5), True), 0)

    def test_longest_streak(self):
        days = {d(1, 1), d(1, 2), d(1, 3), d(1, 4), d(2, 10), d(2, 11)}
        self.assertEqual(services.longest_streak(days), 4)
        self.assertEqual(services.longest_streak(set()), 0)


class StreakIsolationTests(ClearCacheMixin, TestCase):
    """The visit streak is cosmetic: it must never reach points or tiers."""

    def test_completions_do_not_change_points_or_donation_streak(self):
        user = person()
        give(user, days_ago=10)
        before = (get_donor_points(user), get_current_streak(user))
        for offset in range(5):
            DailyCompletion.objects.create(
                user=user, day=timezone.localdate() - datetime.timedelta(days=offset),
                task='fact',
            )
        self.assertEqual(
            (get_donor_points(user), get_current_streak(user)), before,
        )
        self.assertEqual(services.streak_info(user)['current'], 5)

    def test_streak_info_grace_flag(self):
        user = person()
        today = timezone.localdate()
        monday = today - datetime.timedelta(days=today.weekday())
        if today == monday:
            self.skipTest('needs a day later in the week')
        # Done Monday, missed yesterday-or-so, done today: grace is used.
        DailyCompletion.objects.create(user=user, day=monday, task='fact')
        DailyCompletion.objects.create(user=user, day=today, task='fact')
        if (today - monday).days == 2:
            self.assertTrue(services.streak_info(user)['grace_used'])


# ------------------------------------------------------------- today views

class TodayViewTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch(
            'habits.services.assigned_tasks', return_value=list(ALL_TASKS),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = person('Ayesha')
        self.requester = person('Requester')
        self.req = make_request(self.requester)
        self.client.force_login(self.user)

    def test_anonymous_goes_to_login(self):
        self.client.logout()
        for name in ('today', 'titles', 'settings', 'year'):
            r = self.client.get(reverse(f'habits:{name}'))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn('login', r['Location'])

    def test_anonymous_cannot_post(self):
        self.client.logout()
        for name in ('availability', 'fact', 'quiz'):
            r = self.client.post(reverse(f'habits:{name}'))
            self.assertEqual(r.status_code, 302)
            self.assertIn('login', r['Location'])
        self.assertEqual(DailyCompletion.objects.count(), 0)

    def test_page_renders_all_task_cards(self):
        r = self.client.get(reverse('habits:today'))
        self.assertEqual(r.status_code, 200)
        for anchor in ('task-share', 'task-availability', 'task-fact', 'task-quiz'):
            self.assertContains(r, f'id="{anchor}"')
        self.assertContains(r, "blood bank&#x27;s screening decides eligibility")

    def test_page_never_shows_phone_numbers(self):
        r = self.client.get(reverse('habits:today'))
        for phone in (self.requester.phone, self.req.contact_phone):
            self.assertNotContains(r, phone)

    def test_post_only_endpoints_reject_get(self):
        for name in ('availability', 'fact', 'quiz'):
            self.assertEqual(self.client.get(reverse(f'habits:{name}')).status_code, 405)

    def test_availability_sets_only_that_field(self):
        profile = self.user.profile
        profile.available_to_donate = True
        profile.in_directory = True
        profile.district = 'Dhaka'
        profile.save()
        r = self.client.post(reverse('habits:availability'), {'available': 'no'})
        self.assertEqual(r.status_code, 302)
        profile.refresh_from_db()
        self.assertFalse(profile.available_to_donate)
        self.assertTrue(profile.in_directory)
        self.assertEqual(profile.district, 'Dhaka')
        self.assertTrue(DailyCompletion.objects.filter(
            user=self.user, task='availability').exists())
        self.client.post(reverse('habits:availability'), {'available': 'yes'})
        profile.refresh_from_db()
        self.assertTrue(profile.available_to_donate)
        self.assertEqual(DailyCompletion.objects.filter(task='availability').count(), 1)

    def test_availability_ignores_extra_fields(self):
        self.client.post(reverse('habits:availability'), {
            'available': 'yes', 'in_directory': 'on', 'public_badge_card': '',
            'show_on_leaderboard': '',
        })
        profile = self.user.profile
        profile.refresh_from_db()
        self.assertFalse(profile.in_directory)
        self.assertTrue(profile.show_on_leaderboard)
        self.assertTrue(profile.public_badge_card)

    def test_availability_invalid_or_empty_changes_nothing(self):
        self.user.profile.available_to_donate = True
        self.user.profile.save()
        for payload in ({}, {'available': ''}, {'available': 'maybe'}):
            self.client.post(reverse('habits:availability'), payload)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.available_to_donate)
        self.assertEqual(DailyCompletion.objects.count(), 0)

    def test_fact_completion_is_once(self):
        for _ in range(3):
            self.client.post(reverse('habits:fact'))
        self.assertEqual(DailyCompletion.objects.filter(
            user=self.user, task='fact').count(), 1)

    def test_quiz_records_answers_and_reveals(self):
        rows, _ = services.quiz_review(self.user, timezone.localdate())
        data = {f'q{i}': row['answer'] for i, row in enumerate(rows)}
        r = self.client.post(reverse('habits:quiz'), data)
        self.assertEqual(r.status_code, 302)
        completion = DailyCompletion.objects.get(user=self.user, task='quiz')
        self.assertEqual(
            completion.detail, ','.join(row['answer'] for row in rows),
        )
        page = self.client.get(reverse('habits:today'))
        self.assertContains(page, '3 of 3')
        self.assertContains(page, rows[0]['statement'])

    def test_quiz_wrong_answers_still_complete_without_shame(self):
        rows, _ = services.quiz_review(self.user, timezone.localdate())
        flip = {'M': 'F', 'F': 'M'}
        data = {f'q{i}': flip[row['answer']] for i, row in enumerate(rows)}
        self.client.post(reverse('habits:quiz'), data)
        page = self.client.get(reverse('habits:today'))
        self.assertContains(page, '0 of 3')
        self.assertContains(page, rows[0]['explanation'].replace("'", '&#x27;'))

    def test_quiz_needs_all_three(self):
        self.client.post(reverse('habits:quiz'), {'q0': 'M', 'q1': 'F'})
        self.client.post(reverse('habits:quiz'), {'q0': 'M', 'q1': 'F', 'q2': 'x'})
        self.assertEqual(DailyCompletion.objects.count(), 0)

    def test_quiz_replay_keeps_first_answers(self):
        self.client.post(reverse('habits:quiz'), {'q0': 'M', 'q1': 'M', 'q2': 'M'})
        self.client.post(reverse('habits:quiz'), {'q0': 'F', 'q1': 'F', 'q2': 'F'})
        self.assertEqual(
            DailyCompletion.objects.get(task='quiz').detail, 'M,M,M',
        )

    def test_task_not_on_todays_list_is_refused_politely(self):
        with mock.patch(
            'habits.services.assigned_tasks', return_value=[services.QUIZ],
        ):
            r = self.client.post(reverse('habits:fact'), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(DailyCompletion.objects.count(), 0)

    def test_write_rate_limit(self):
        for _ in range(services.WRITE_LIMIT + 5):
            self.client.post(reverse('habits:availability'), {'available': 'yes'})
        # Still one completion and no server error.
        self.assertEqual(DailyCompletion.objects.count(), 1)
        r = self.client.post(reverse('habits:availability'), {'available': 'no'})
        self.assertEqual(r.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.available_to_donate)

    def test_csrf_is_enforced(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        r = strict.post(reverse('habits:fact'))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(DailyCompletion.objects.count(), 0)

    def test_resting_donor_gets_a_gentle_note(self):
        give(self.user, days_ago=5)
        r = self.client.get(reverse('habits:today'))
        self.assertContains(r, 'recovery gap')


# --------------------------------------------------------------------- share

class ShareTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = person()
        self.requester = person()
        self.req = make_request(self.requester)
        self.client.force_login(self.user)
        self.url = reverse('habits:share', args=[self.req.pk])

    def test_redirects_to_request_with_share_anchor(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            r['Location'], reverse('request-detail', args=[self.req.pk]) + '#share',
        )

    def test_records_once_per_user_request_day(self):
        for _ in range(4):
            self.client.get(self.url)
        self.assertEqual(ShareAction.objects.filter(user=self.user).count(), 1)

    def test_next_day_is_a_new_share(self):
        self.client.get(self.url)
        ShareAction.objects.update(day=timezone.localdate() - datetime.timedelta(days=1))
        self.client.get(self.url)
        self.assertEqual(ShareAction.objects.count(), 2)

    def test_unique_constraint_in_database(self):
        ShareAction.objects.create(
            user=self.user, blood_request=self.req, day=d(11, 6),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShareAction.objects.create(
                user=self.user, blood_request=self.req, day=d(11, 6),
            )

    def test_anonymous_is_passed_through_without_recording(self):
        self.client.logout()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/requests/', r['Location'])
        self.assertEqual(ShareAction.objects.count(), 0)

    def test_own_request_not_counted(self):
        self.client.force_login(self.requester)
        self.client.get(self.url)
        self.assertEqual(ShareAction.objects.count(), 0)

    def test_closed_request_not_counted(self):
        self.req.close(BloodRequest.Outcome.NO_LONGER_NEEDED)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ShareAction.objects.count(), 0)

    def test_flagged_request_not_counted(self):
        from rewards.models import RequestReport
        for _ in range(3):
            RequestReport.objects.create(
                blood_request=self.req, reporter=person(), reason='spam',
            )
        self.client.get(self.url)
        self.assertEqual(ShareAction.objects.count(), 0)

    def test_unknown_request_is_404(self):
        r = self.client.get(reverse('habits:share', args=[99999]))
        self.assertEqual(r.status_code, 404)

    def test_counts_as_the_daily_share_task(self):
        with mock.patch(
            'habits.services.assigned_tasks', return_value=list(ALL_TASKS),
        ):
            self.client.get(self.url)
        self.assertTrue(DailyCompletion.objects.filter(
            user=self.user, task='share').exists())

    def test_daily_cap(self):
        for _ in range(services.MAX_SHARES_PER_DAY + 3):
            other = make_request(self.requester)
            self.client.get(reverse('habits:share', args=[other.pk]))
        self.assertEqual(
            ShareAction.objects.filter(user=self.user).count(),
            services.MAX_SHARES_PER_DAY,
        )


# -------------------------------------------------------------------- titles

class TitleTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = person()
        self.other = person()

    def by_key(self, user=None):
        return {t.key: t for t in titles.all_titles(user or self.user)}

    def test_newcomer_has_no_titles_but_sees_progress(self):
        self.assertEqual(titles.titles_for(self.user), [])
        t = self.by_key()['champion']
        self.assertFalse(t.earned)
        self.assertEqual((t.current, t.target, t.percent), (0, 10, 0))

    def test_request_champion_needs_ten_distinct_requests(self):
        for i in range(9):
            ShareAction.objects.create(
                user=self.user, blood_request=make_request(self.other),
                day=d(11, 1),
            )
        self.assertFalse(self.by_key()['champion'].earned)
        self.assertEqual(self.by_key()['champion'].percent, 90)
        # The same request on another day is not a new request.
        ShareAction.objects.create(
            user=self.user, blood_request=ShareAction.objects.first().blood_request,
            day=d(11, 2),
        )
        self.assertFalse(self.by_key()['champion'].earned)
        ShareAction.objects.create(
            user=self.user, blood_request=make_request(self.other), day=d(11, 3),
        )
        self.assertTrue(self.by_key()['champion'].earned)
        self.assertIn('Request Champion', [t.name for t in titles.titles_for(self.user)])

    def test_connector_counts_only_fulfilled_closed_requests(self):
        def pledge(outcome, status=BloodRequest.Status.CLOSED):
            r = make_request(self.other)
            RequestResponse.objects.create(blood_request=r, donor=self.user)
            if status == BloodRequest.Status.CLOSED:
                r.close(outcome)
            return r
        for _ in range(4):
            pledge(BloodRequest.Outcome.FULFILLED_HERE)
        pledge(BloodRequest.Outcome.FOUND_ELSEWHERE)
        pledge(BloodRequest.Outcome.NO_LONGER_NEEDED)
        pledge('', status=BloodRequest.Status.OPEN)
        self.assertEqual(self.by_key()['connector'].current, 4)
        self.assertFalse(self.by_key()['connector'].earned)
        pledge(BloodRequest.Outcome.FULFILLED_HERE)
        self.assertTrue(self.by_key()['connector'].earned)

    def test_storyteller_only_when_approved(self):
        story = Story.objects.create(
            author=self.user, title='A kind night', body='x' * 60,
        )
        self.assertFalse(self.by_key()['storyteller'].earned)
        story.reject('no')
        self.assertFalse(self.by_key()['storyteller'].earned)
        story.approve()
        self.assertTrue(self.by_key()['storyteller'].earned)

    def test_squad_captain_needs_three_members_and_ownership(self):
        squad = Squad.objects.create(name='Cap', owner=self.user)
        SquadMember.objects.create(squad=squad, user=self.user, role='owner')
        SquadMember.objects.create(squad=squad, user=self.other)
        self.assertFalse(self.by_key()['captain'].earned)
        third = person()
        SquadMember.objects.create(squad=squad, user=third)
        self.assertTrue(self.by_key()['captain'].earned)
        # Members of someone else's squad are not captains.
        self.assertFalse(self.by_key(third)['captain'].earned)
        self.assertFalse(self.by_key(self.other)['captain'].earned)

    def test_early_bird_counts_tasks_before_nine(self):
        for i in range(6):
            DailyCompletion.objects.create(
                user=self.user, day=d(10, 1 + i), task='fact', hour=8,
            )
        DailyCompletion.objects.create(
            user=self.user, day=d(10, 20), task='fact', hour=9,
        )
        self.assertFalse(self.by_key()['early_bird'].earned)
        DailyCompletion.objects.create(
            user=self.user, day=d(10, 21), task='fact', hour=0,
        )
        self.assertTrue(self.by_key()['early_bird'].earned)

    def test_titles_never_change_points(self):
        give(self.user, days_ago=200)
        before = get_donor_points(self.user)
        Story.objects.create(
            author=self.user, title='A kind night', body='x' * 60,
            status=Story.Status.APPROVED,
        )
        self.assertEqual(get_donor_points(self.user), before)

    def test_titles_page_and_hub_panel(self):
        Story.objects.create(
            author=self.user, title='A kind night', body='x' * 60,
            status=Story.Status.APPROVED,
        )
        self.client.force_login(self.user)
        r = self.client.get(reverse('habits:titles'))
        self.assertContains(r, 'Storyteller')
        self.assertContains(r, 'Request Champion')
        hub = self.client.get(reverse('log-donation'))
        self.assertContains(hub, 'Storyteller')
        self.assertContains(hub, reverse('habits:today'))


# -------------------------------------------------------------------- digest

class DigestTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = person('Dina', email='dina@habits.test',
                           blood_group='A+', district='Dhaka')
        DigestPref.objects.create(user=self.user, enabled=True)

    def run_cmd(self, *args):
        out, err = StringIO(), StringIO()
        call_command('send_weekly_digest', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_opt_in_only(self):
        quiet = person('Quiet')
        DigestPref.objects.create(user=quiet, enabled=False)
        person('NoPref')
        self.run_cmd()
        self.assertEqual([m.to for m in mail.outbox], [['dina@habits.test']])

    def test_sends_one_plain_text_email_with_unsubscribe(self):
        self.run_cmd()
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.content_subtype, 'plain')
        self.assertNotIn('<', msg.body)
        self.assertIn('XP', msg.body)
        self.assertIn("blood bank's screening decides eligibility", msg.body)
        self.assertIn('/today/unsubscribe/', msg.body)
        self.assertIn('List-Unsubscribe', msg.extra_headers)
        self.assertIn(self.user.first_name, msg.body)

    def test_idempotent_per_iso_week(self):
        self.run_cmd()
        self.run_cmd()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(DigestLog.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            DigestPref.objects.get(user=self.user).last_sent_week,
            digest.week_key(),
        )

    def test_next_week_sends_again(self):
        self.run_cmd()
        later = timezone.localdate() + datetime.timedelta(days=7)
        with mock.patch('django.utils.timezone.localdate', return_value=later):
            self.run_cmd()
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(DigestLog.objects.filter(user=self.user).count(), 2)

    def test_dry_run_sends_and_logs_nothing(self):
        out, _ = self.run_cmd('--dry-run')
        self.assertEqual(mail.outbox, [])
        self.assertEqual(DigestLog.objects.count(), 0)
        self.assertIn('would send', out.lower())
        self.assertIn('Subject:', out)
        # A real run afterwards still sends.
        self.run_cmd()
        self.assertEqual(len(mail.outbox), 1)

    def test_user_option(self):
        other = person('Other', email='other@habits.test')
        DigestPref.objects.create(user=other, enabled=True)
        self.run_cmd('--user', str(other.pk))
        self.assertEqual([m.to for m in mail.outbox], [['other@habits.test']])

    def test_user_option_not_opted_in_errors(self):
        stranger = person('Stranger')
        with self.assertRaises(CommandError):
            self.run_cmd('--user', str(stranger.pk))
        self.assertEqual(mail.outbox, [])

    def test_no_email_address_is_skipped(self):
        silent = person('Silent', email='')
        DigestPref.objects.create(user=silent, enabled=True)
        self.run_cmd()
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(DigestLog.objects.filter(user=silent).exists())

    def test_inactive_user_is_skipped(self):
        self.user.is_active = False
        self.user.save()
        self.run_cmd()
        self.assertEqual(mail.outbox, [])

    def test_failed_send_is_retried_next_run(self):
        with mock.patch(
            'django.core.mail.message.EmailMessage.send',
            side_effect=OSError('smtp down'),
        ):
            _, err = self.run_cmd()
        self.assertIn('fail', err)
        self.assertEqual(DigestLog.objects.count(), 0)
        self.run_cmd()
        self.assertEqual(len(mail.outbox), 1)

    def test_xp_change_uses_previous_snapshot(self):
        DigestLog.objects.create(
            user=self.user, iso_year=2020, iso_week=1, points=0, level=1,
        )
        give(self.user, days_ago=30)
        body = digest.build_digest(self.user).body
        self.assertIn('+', body)
        self.assertIn('since your last email', body)
        self.assertIn('level 2', body)

    def test_open_requests_in_district_for_compatible_group_max_three(self):
        requester = person('Req')
        # A+ donors can give to A+ and AB+.
        for _ in range(4):
            make_request(requester, group='A+', district='Dhaka')
        make_request(requester, group='AB+', district='Dhaka', hospital='Ab Hospital')
        make_request(requester, group='O-', district='Dhaka', hospital='Wrong Group')
        make_request(requester, group='A+', district='Sylhet', hospital='Far Hospital')
        body = digest.build_digest(self.user).body
        self.assertEqual(body.count('/requests/'), 3)
        self.assertNotIn('Wrong Group', body)
        self.assertNotIn('Far Hospital', body)

    def test_compatible_group_is_included(self):
        requester = person('Req')
        make_request(requester, group='AB+', district='Dhaka', hospital='Ab Hospital')
        self.assertIn('Ab Hospital', digest.build_digest(self.user).body)

    def test_requests_hidden_while_resting_and_when_unavailable(self):
        make_request(person('Req'), group='A+', district='Dhaka')
        give(self.user, days_ago=5)
        body = digest.build_digest(self.user).body
        self.assertNotIn('/requests/', body)
        self.assertIn('resting', body)
        Donation.objects.all().delete()
        self.user.profile.available_to_donate = False
        self.user.profile.save()
        self.assertNotIn('/requests/', digest.build_digest(self.user).body)

    def test_already_pledged_and_own_requests_are_left_out(self):
        requester = person('Req')
        pledged = make_request(requester, hospital='Pledged Hospital')
        RequestResponse.objects.create(blood_request=pledged, donor=self.user)
        make_request(self.user, hospital='Mine Hospital')
        body = digest.build_digest(self.user).body
        self.assertNotIn('Pledged Hospital', body)
        self.assertNotIn('Mine Hospital', body)

    def test_streak_line(self):
        for i in range(3):
            DailyCompletion.objects.create(
                user=self.user, day=timezone.localdate() - datetime.timedelta(days=i),
                task='fact',
            )
        self.assertIn('3-day streak', digest.build_digest(self.user).body)
        DailyCompletion.objects.all().delete()
        self.assertIn('no pressure', digest.build_digest(self.user).body.lower())

    def test_squad_rank_line(self):
        owner = self.user
        squad = Squad.objects.create(name='Rankers', owner=owner)
        SquadMember.objects.create(squad=squad, user=owner, role='owner')
        buddy = person('Buddy')
        SquadMember.objects.create(squad=squad, user=buddy)
        give(owner, days_ago=0)
        give(buddy, days_ago=0)
        give(buddy, days_ago=0, request=make_request(person()))
        body = digest.build_digest(owner).body
        self.assertIn('Squad: you are #2 in Rankers', body)

    def test_no_squad_line_without_a_squad(self):
        self.assertNotIn('Squad:', digest.build_digest(self.user).body)

    def test_never_leaks_another_members_phone_or_email(self):
        requester = person('Requester', email='requester-secret@habits.test')
        buddy = person('Buddy', email='buddy-secret@habits.test')
        squad = Squad.objects.create(name='Leaky', owner=self.user)
        SquadMember.objects.create(squad=squad, user=self.user, role='owner')
        SquadMember.objects.create(squad=squad, user=buddy)
        give(self.user, days_ago=200)
        give(buddy)
        req = make_request(requester, hospital='Leak Hospital')
        req.refresh_from_db()
        body = digest.build_digest(self.user).body
        self.assertIn('Leak Hospital', body)
        for secret in (
            requester.phone, requester.email, buddy.phone, buddy.email,
            req.contact_phone, 'Requester', 'Buddy',
        ):
            self.assertNotIn(secret, body)
        self.assertNotIn(self.user.phone, body)

    def test_dry_run_output_has_no_foreign_contacts(self):
        requester = person('Requester', email='requester-secret@habits.test')
        make_request(requester)
        out, _ = self.run_cmd('--dry-run')
        self.assertNotIn(requester.phone, out)
        self.assertNotIn('requester-secret', out)
        self.assertNotIn('01912345678', out)


class UnsubscribeTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = person('Una')
        self.pref = DigestPref.objects.create(user=self.user, enabled=True)

    def test_token_round_trip(self):
        token = digest.make_token(self.user)
        self.assertEqual(digest.read_token(token), self.user.pk)

    def test_tampered_or_garbage_token(self):
        token = digest.make_token(self.user)
        self.assertIsNone(digest.read_token(token + 'x'))
        self.assertIsNone(digest.read_token('garbage'))
        self.assertIsNone(digest.read_token(''))

    def test_token_for_another_purpose_is_rejected(self):
        from django.core import signing
        wrong = signing.dumps({'u': self.user.pk}, salt='something.else')
        self.assertIsNone(digest.read_token(wrong))

    def test_get_without_login_unsubscribes(self):
        r = self.client.get(
            reverse('habits:unsubscribe', args=[digest.make_token(self.user)]),
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'unsubscribed')
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.enabled)

    def test_one_click_post_without_csrf_or_login(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        r = client.post(
            reverse('habits:unsubscribe', args=[digest.make_token(self.user)]),
            'List-Unsubscribe=One-Click', content_type='application/x-www-form-urlencoded',
        )
        self.assertEqual(r.status_code, 200)
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.enabled)

    def test_bad_token_is_friendly_and_changes_nothing(self):
        r = self.client.get(reverse('habits:unsubscribe', args=['nope']))
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, 'did not work', status_code=400)
        self.pref.refresh_from_db()
        self.assertTrue(self.pref.enabled)

    def test_token_for_deleted_user(self):
        token = digest.make_token(self.user)
        self.user.delete()
        r = self.client.get(reverse('habits:unsubscribe', args=[token]))
        self.assertEqual(r.status_code, 400)

    def test_email_link_unsubscribes_that_user_only(self):
        other = person('Other')
        other_pref = DigestPref.objects.create(user=other, enabled=True)
        self.client.get(digest.unsubscribe_path(self.user))
        other_pref.refresh_from_db()
        self.assertTrue(other_pref.enabled)


class SettingsPageTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = person('Sue')
        self.client.force_login(self.user)

    def test_defaults_to_off(self):
        r = self.client.get(reverse('habits:settings'))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(DigestPref.objects.get(user=self.user).enabled)
        self.assertContains(r, reverse('habits:today'))
        self.assertContains(r, reverse('log-donation'))

    def test_toggle_on_and_off(self):
        self.client.post(reverse('habits:settings'), {'enabled': 'on'})
        self.assertTrue(DigestPref.objects.get(user=self.user).enabled)
        self.client.post(reverse('habits:settings'), {})
        self.assertFalse(DigestPref.objects.get(user=self.user).enabled)

    def test_warns_when_no_email(self):
        self.user.email = None
        self.user.save()
        r = self.client.get(reverse('habits:settings'))
        self.assertContains(r, 'no email address')


# ------------------------------------------------------------- year review

class YearReviewTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = person('Yara')
        self.year = timezone.localdate().year
        self.client.force_login(self.user)

    def test_empty_state_is_friendly(self):
        r = self.client.get(reverse('habits:year-detail', args=[self.year]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'completely fine')
        self.assertNotContains(r, 'Est. people helped')

    def test_only_approved_donations_count(self):
        give(self.user, days_ago=0)
        give(self.user, days_ago=0, status=Donation.Status.PENDING)
        give(self.user, days_ago=0, status=Donation.Status.REJECTED)
        data = year_review.review(self.user, self.year)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['lives'], 3)

    def test_other_years_do_not_leak_in(self):
        give(self.user, on=d(2023, 5, 1))
        give(self.user, on=d(2024, 6, 1))
        data = year_review.review(self.user, 2023)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['months'], ['May'])

    def test_stats_and_labels(self):
        give(self.user, on=d(2025, 1, 10), location='City Blood Bank')
        give(self.user, on=d(2025, 5, 12), location='city  blood bank')
        give(self.user, on=d(2025, 9, 1), location='Other Place')
        r = self.client.get(reverse('habits:year-detail', args=[2025]))
        self.assertContains(r, 'Est. people helped')
        self.assertContains(r, 'City Blood Bank')
        data = year_review.review(self.user, 2025)
        self.assertEqual(data['months_active'], 3)
        self.assertEqual(data['place'], ('City Blood Bank', 2))
        self.assertEqual(data['lives'], 9)

    def test_longest_visit_streak_in_year(self):
        for day in (d(2025, 3, 1), d(2025, 3, 2), d(2025, 3, 3), d(2025, 8, 1)):
            DailyCompletion.objects.create(user=self.user, day=day, task='fact')
        give(self.user, on=d(2025, 3, 1))
        self.assertEqual(year_review.review(self.user, 2025)['longest_streak'], 3)

    def test_default_year_picks_latest_year_with_donations(self):
        give(self.user, on=d(2024, 6, 1))
        r = self.client.get(reverse('habits:year'))
        self.assertRedirects(
            r, reverse('habits:year-detail', args=[2024]),
            fetch_redirect_response=False,
        )

    def test_year_bounds_are_404(self):
        for y in (1999, self.year + 1):
            self.assertEqual(
                self.client.get(reverse('habits:year-detail', args=[y])).status_code, 404,
            )

    def test_percentile_uses_opted_in_donors_and_names_nobody(self):
        crowd = []
        for i in range(5):
            u = person(f'Crowd{i}')
            give(u, on=d(2025, 2, 1))
            crowd.append(u)
        hidden = person('Hidden', show_on_leaderboard=False)
        for i in range(5):
            give(hidden, on=d(2025, 2, 1 + i))
        give(self.user, on=d(2025, 3, 1))
        give(self.user, on=d(2025, 3, 20))
        data = year_review.review(self.user, 2025)
        # The hidden donor (5 donations) is not in the pool, so nobody is ahead.
        self.assertEqual(data['percentile']['pool'], 6)
        self.assertEqual(data['percentile']['top_percent'], 17)
        r = self.client.get(reverse('habits:year-detail', args=[2025]))
        for u in crowd + [hidden]:
            self.assertNotContains(r, u.first_name)

    def test_percentile_hidden_for_a_tiny_pool(self):
        give(self.user, on=d(2025, 3, 1))
        self.assertIsNone(year_review.review(self.user, 2025)['percentile'])

    def test_badges_earned_that_year(self):
        Badge.objects.all().delete()
        first = Badge.objects.create(
            name='First', badge_type=Badge.Type.TIER, min_donations=1,
        )
        third = Badge.objects.create(
            name='Third', badge_type=Badge.Type.TIER, min_donations=3,
        )
        hero = Badge.objects.create(
            name='Responder', badge_type=Badge.Type.HERO, min_request_donations=1,
        )
        req = make_request(person())
        give(self.user, on=d(2024, 12, 20))
        give(self.user, on=d(2025, 4, 1), request=req)
        give(self.user, on=d(2025, 9, 1))
        names_2025 = [e['badge'].name for e in year_review.badges_earned_in(self.user, 2025)]
        names_2024 = [e['badge'].name for e in year_review.badges_earned_in(self.user, 2024)]
        self.assertEqual(names_2024, ['First'])
        self.assertEqual(sorted(names_2025), ['Responder', 'Third'])
        self.assertNotIn(first.name, names_2025)
        self.assertNotIn(third.name, names_2024)
        self.assertNotIn(hero.name, names_2024)

    def test_page_never_shows_phone_or_email(self):
        give(self.user, on=d(2025, 3, 1))
        r = self.client.get(reverse('habits:year-detail', args=[2025]))
        self.assertNotContains(r, self.user.phone)
        self.assertNotContains(r, self.user.email)


class YearCardTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = person('Olive')
        give(self.owner, on=d(2025, 3, 1))
        self.stranger = person('Stranger')
        self.staff = person('Staffer')
        self.staff.staff = True
        self.staff.save()
        self.url = reverse('habits:year-card-user', args=[2025, self.owner.pk])

    def test_public_when_owner_allows(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Olive')
        self.assertContains(r, 'Est. people helped')
        self.assertNotContains(r, self.owner.phone)
        self.assertNotContains(r, self.owner.email)

    def test_private_card_is_owner_only(self):
        self.owner.profile.public_badge_card = False
        self.owner.profile.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_leaderboard_opt_out_also_hides_the_name(self):
        self.owner.profile.show_on_leaderboard = False
        self.owner.profile.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.owner)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'noindex')

    def test_staff_can_open_a_private_card(self):
        self.owner.profile.public_badge_card = False
        self.owner.profile.save()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_stranger_gets_404_for_a_year_without_donations(self):
        empty = reverse('habits:year-card-user', args=[2024, self.owner.pk])
        self.assertEqual(self.client.get(empty).status_code, 404)

    def test_owner_card_page_requires_login_and_shows_own(self):
        own = reverse('habits:year-card', args=[2025])
        r = self.client.get(own)
        self.assertEqual(r.status_code, 302)
        self.client.force_login(self.owner)
        r = self.client.get(own)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Olive')

    def test_owner_sees_friendly_card_for_empty_year(self):
        self.client.force_login(self.owner)
        r = self.client.get(reverse('habits:year-card', args=[2024]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'completely fine')

    def test_unknown_user_and_bad_year(self):
        self.assertEqual(self.client.get(
            reverse('habits:year-card-user', args=[2025, 99999])).status_code, 404)
        self.assertEqual(self.client.get(
            reverse('habits:year-card-user', args=[1990, self.owner.pk])).status_code, 404)

    def test_name_never_falls_back_to_phone(self):
        nameless = person('')
        nameless.first_name = ''
        nameless.save()
        give(nameless, on=d(2025, 3, 1))
        r = self.client.get(
            reverse('habits:year-card-user', args=[2025, nameless.pk]),
        )
        self.assertContains(r, f'Donor #{nameless.pk}')
        self.assertNotContains(r, nameless.phone)

    def test_share_links_use_the_user_url_and_site_name(self):
        r = self.client.get(self.url)
        self.assertContains(r, self.url)
        self.assertNotContains(r, 'Blood Lagbe')


# -------------------------------------------------------------------- panels

class PanelTests(ClearCacheMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = person('Pia')

    def test_home_teaser_only_for_signed_in(self):
        anon = self.client.get(reverse('home'))
        self.assertEqual(anon.status_code, 200)
        self.assertNotContains(anon, 'habits-home-h')
        self.client.force_login(self.user)
        signed = self.client.get(reverse('home'))
        self.assertContains(signed, 'habits-home-h')
        self.assertContains(signed, reverse('habits:today'))

    def test_hub_panel_for_signed_in(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse('log-donation'))
        self.assertContains(r, 'habits-hub-h')
        self.assertContains(r, 'done')

    def test_hub_panel_shows_streak(self):
        for i in range(3):
            DailyCompletion.objects.create(
                user=self.user, day=timezone.localdate() - datetime.timedelta(days=i),
                task='fact',
            )
        self.client.force_login(self.user)
        r = self.client.get(reverse('log-donation'))
        self.assertContains(r, '3-day')

    def test_request_panel_is_silent(self):
        from habits.templatetags.habits_tags import habits_request
        self.assertEqual(habits_request({}), {})


class QueryBudgetTests(ClearCacheMixin, TestCase):
    """The hub and home panels sit on busy pages: keep them cheap."""

    def test_titles_take_one_query(self):
        user = person()
        with self.assertNumQueries(1):
            titles.title_counts(user)

    def test_hub_and_home_panels_are_cheap(self):
        from django.template import Context
        from habits.templatetags.habits_tags import habits_home, habits_hub

        class FakeRequest:
            pass
        user = person()
        request = FakeRequest()
        request.user = user
        with self.assertNumQueries(2):
            habits_hub(Context({'request': request}))
        with self.assertNumQueries(1):
            habits_home(Context({'request': request}))

    def test_summary_counts_today_only(self):
        user = person()
        today = timezone.localdate()
        DailyCompletion.objects.create(user=user, day=today, task='fact')
        DailyCompletion.objects.create(
            user=user, day=today - datetime.timedelta(days=1), task='quiz',
        )
        info = services.summary(user)
        self.assertEqual((info['done_count'], info['total']), (1, 3))
        self.assertEqual(info['streak']['current'], 2)


class AdminTests(TestCase):
    def test_models_registered(self):
        from django.contrib import admin
        for model in (ShareAction, DailyCompletion, DigestPref, DigestLog):
            self.assertIn(model, admin.site._registry)
