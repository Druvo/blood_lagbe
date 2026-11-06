import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.management.commands import run_scheduled
from rewards.models import (
    BloodRequest,
    Donation,
    RequestReport,
    RequestResponse,
    get_eligibility,
)
from spotlight import heroes, pulse, styles
from spotlight.models import MonthlyAward
from squads.models import Squad, SquadMember
from stories.models import Story

User = get_user_model()
PASSWORD = 'testpass123'
HOSPITAL = 'Spotlight Test Hospital'
NOTE = 'Ward nine, bed four, secret note'
PATIENT = 'Patientus'


def make_user(n, first='', last='', **profile):
    user = User.objects.create_user(phone=f'0199900{n:04d}', password=PASSWORD)
    user.first_name, user.last_name = first, last
    user.email = f'spot{n}@spotlight.test'
    user.save()
    for name, value in profile.items():
        setattr(user.profile, name, value)
    user.profile.save()
    return user


def donate(user, day, status=Donation.Status.APPROVED, **extra):
    return Donation.objects.create(
        donor=user, blood_group='A+', district='Dhaka', location=HOSPITAL,
        donation_date=day, status=status, **extra,
    )


def this_month_day(n=1):
    today = timezone.localdate()
    return today.replace(day=min(n, today.day))


def last_month_day(n=10):
    first = timezone.localdate().replace(day=1)
    return (first - datetime.timedelta(days=1)).replace(day=n)


def make_request(requester, **extra):
    fields = dict(
        blood_group='O+', hospital=HOSPITAL, district='Sylhet', units_needed=1,
        needed_by=timezone.localdate() + datetime.timedelta(days=3),
        contact_phone='01999990000', note=NOTE, patient_name=PATIENT,
    )
    fields.update(extra)
    return BloodRequest.objects.create(requester=requester, **fields)


def fulfil(req, when=None):
    req.status = BloodRequest.Status.CLOSED
    req.outcome = BloodRequest.Outcome.FULFILLED_HERE
    req.closed_at = when or timezone.now()
    req.save()


def age(obj, minutes=60, field='created_at'):
    """Move an object's timestamp into the past, bypassing auto_now."""
    stamp = timezone.now() - datetime.timedelta(minutes=minutes)
    type(obj).objects.filter(pk=obj.pk).update(**{field: stamp})


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.a = make_user(1, 'Alma', 'Aster')
        self.b = make_user(2, 'Bela', 'Bloom')
        self.c = make_user(3, 'Cora', 'Hidden', show_on_leaderboard=False)
        self.staff = make_user(4, 'Stan', 'Staffer')
        self.staff.staff = True
        self.staff.save()
        self.stranger = make_user(5, 'Sven', 'Stranger')

    def login(self, user):
        self.client.force_login(user)


# ---------------------------------------------------------- hero of the month

class DonorOfTheMonthTests(Base):
    def winner(self):
        return heroes.compute_winner(
            heroes.DONOR, *self.ym())

    def ym(self):
        today = timezone.localdate()
        return today.year, today.month

    def test_most_approved_donations_wins(self):
        for _ in range(3):
            donate(self.a, this_month_day())
        for _ in range(2):
            donate(self.b, this_month_day())
        entry = self.winner()
        self.assertEqual((entry['id'], entry['count']), (self.a.pk, 3))
        self.assertEqual(entry['name'], 'Alma Aster')

    def test_pending_and_rejected_do_not_count(self):
        donate(self.a, this_month_day())
        for status in (Donation.Status.PENDING, Donation.Status.REJECTED):
            for _ in range(3):
                donate(self.b, this_month_day(), status=status)
        self.assertEqual(self.winner()['id'], self.a.pk)

    def test_donations_outside_the_month_do_not_count(self):
        donate(self.a, this_month_day())
        for _ in range(4):
            donate(self.b, last_month_day())
        self.assertEqual(self.winner()['id'], self.a.pk)

    def test_opted_out_member_is_skipped_entirely(self):
        for _ in range(5):
            donate(self.c, this_month_day())
        donate(self.a, this_month_day())
        entry = self.winner()
        self.assertEqual(entry['id'], self.a.pk)
        self.assertNotIn('Hidden', str(heroes.heroes_for_month(*self.ym())))

    def test_only_opted_out_donors_means_no_hero(self):
        donate(self.c, this_month_day())
        self.assertIsNone(self.winner())

    def test_tie_goes_to_earlier_first_donation(self):
        today = timezone.localdate()
        if today.day < 2:
            self.skipTest('needs two days in the month')
        donate(self.b, today)
        donate(self.a, today.replace(day=1))
        self.assertEqual(self.winner()['id'], self.a.pk)

    def test_tie_then_lowest_id(self):
        # Same count, same day: the lower id wins whatever the order made.
        d1 = donate(self.b, this_month_day())
        d2 = donate(self.a, this_month_day())
        stamp = timezone.now() - datetime.timedelta(hours=2)
        Donation.objects.filter(pk__in=[d1.pk, d2.pk]).update(created_at=stamp)
        self.assertEqual(self.winner()['id'], min(self.a.pk, self.b.pk))

    def test_district_only_when_in_directory(self):
        donate(self.a, this_month_day())
        self.a.profile.district = 'Khulna'
        self.a.profile.in_directory = False
        self.a.profile.save()
        self.assertEqual(self.winner()['district'], '')
        self.a.profile.in_directory = True
        self.a.profile.save()
        self.assertEqual(self.winner()['district'], 'Khulna')

    def test_name_is_never_the_phone(self):
        nameless = make_user(9)
        donate(nameless, this_month_day())
        entry = self.winner()
        self.assertEqual(entry['name'], f'Donor #{nameless.pk}')
        self.assertNotIn(nameless.phone, str(entry))


class SquadOfTheMonthTests(Base):
    def make_squad(self, owner, public=True, members=(), name='Red Team'):
        squad = Squad.objects.create(name=name, owner=owner, is_public=public)
        for member in (owner, *members):
            SquadMember.objects.get_or_create(squad=squad, user=member)
        return squad

    def top(self):
        today = timezone.localdate()
        return heroes.compute_winner(heroes.SQUAD, today.year, today.month)

    def test_public_squad_with_most_member_donations_wins(self):
        big = self.make_squad(self.a, members=[self.b], name='Big Squad')
        small = self.make_squad(self.stranger, name='Small Squad')
        donate(self.a, this_month_day())
        donate(self.b, this_month_day())
        donate(self.stranger, this_month_day())
        entry = self.top()
        self.assertEqual((entry['id'], entry['count']), (big.pk, 2))
        self.assertNotEqual(entry['id'], small.pk)

    def test_private_squad_never_wins(self):
        self.make_squad(self.a, public=False)
        donate(self.a, this_month_day())
        self.assertIsNone(self.top())

    def test_opted_out_members_donations_do_not_count(self):
        squad = self.make_squad(self.a, members=[self.c])
        for _ in range(4):
            donate(self.c, this_month_day())
        donate(self.a, this_month_day())
        self.assertEqual(self.top()['count'], 1)
        self.assertEqual(self.top()['id'], squad.pk)

    def test_squad_tie_goes_to_lowest_id(self):
        first = self.make_squad(self.a, name='First')
        second = self.make_squad(self.b, name='Second')
        d1 = donate(self.a, this_month_day())
        d2 = donate(self.b, this_month_day())
        stamp = timezone.now() - datetime.timedelta(hours=2)
        Donation.objects.filter(pk__in=[d1.pk, d2.pk]).update(created_at=stamp)
        self.assertEqual(self.top()['id'], min(first.pk, second.pk))


class HelperOfTheMonthTests(Base):
    def top(self):
        today = timezone.localdate()
        return heroes.compute_winner(heroes.HELPER, today.year, today.month)

    def pledge(self, user, req):
        return RequestResponse.objects.create(blood_request=req, donor=user)

    def test_pledges_on_fulfilled_requests_count(self):
        req1 = make_request(self.stranger)
        req2 = make_request(self.stranger)
        for req in (req1, req2):
            self.pledge(self.a, req)
            fulfil(req)
        self.pledge(self.b, req1)
        entry = self.top()
        self.assertEqual((entry['id'], entry['count']), (self.a.pk, 2))

    def test_open_or_other_outcomes_do_not_count(self):
        open_req = make_request(self.stranger)
        self.pledge(self.a, open_req)
        elsewhere = make_request(self.stranger)
        self.pledge(self.a, elsewhere)
        elsewhere.close(BloodRequest.Outcome.FOUND_ELSEWHERE)
        self.assertIsNone(self.top())

    def test_closed_in_another_month_does_not_count(self):
        req = make_request(self.stranger)
        self.pledge(self.a, req)
        fulfil(req, when=timezone.now() - datetime.timedelta(days=45))
        self.assertIsNone(self.top())

    def test_opted_out_helper_is_skipped(self):
        req = make_request(self.stranger)
        self.pledge(self.c, req)
        fulfil(req)
        self.assertIsNone(self.top())

    def test_requester_pledging_on_own_request_does_not_count(self):
        req = make_request(self.a)
        self.pledge(self.a, req)
        fulfil(req)
        self.assertIsNone(self.top())


class HeroPagesTests(Base):
    def test_home_empty_state(self):
        response = self.client.get(reverse('spotlight:home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No heroes yet')
        self.assertContains(response, 'So far, not final')
        self.assertContains(response, 'Ties go to')

    def test_home_shows_this_month_hero_without_phone_or_email(self):
        donate(self.a, this_month_day())
        response = self.client.get(reverse('spotlight:home'))
        self.assertContains(response, 'Alma Aster')
        self.assertContains(response, '<svg')
        self.assertNotContains(response, self.a.phone)
        self.assertNotContains(response, self.a.email)

    def test_archive_current_month_is_labelled_so_far(self):
        today = timezone.localdate()
        response = self.client.get(
            reverse('spotlight:month', args=[today.year, today.month]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'So far, not final')

    def test_archive_past_month_is_final(self):
        donate(self.a, last_month_day())
        last = last_month_day()
        response = self.client.get(
            reverse('spotlight:month', args=[last.year, last.month]))
        self.assertContains(response, 'The final heroes')
        self.assertNotContains(response, 'So far, not final')
        self.assertContains(response, 'Alma Aster')

    def test_invalid_and_future_months_404(self):
        today = timezone.localdate()
        for year, month in ((today.year, 13), (today.year, 0), (1999, 5),
                            (today.year + 1, 1), (99999, 1)):
            response = self.client.get(f'/spotlight/{year}/{month}/')
            self.assertEqual(response.status_code, 404, (year, month))
        if today.month < 12:
            self.assertEqual(self.client.get(
                f'/spotlight/{today.year}/{today.month + 1}/').status_code, 404)

    def test_hall_of_fame_lists_saved_awards_only(self):
        donate(self.a, last_month_day())
        call_command('award_monthly_heroes', '--month', self.last_ym(),
                     stdout=StringIO())
        response = self.client.get(reverse('spotlight:home'))
        self.assertContains(response, 'Hall of fame')
        self.assertContains(response, 'Alma Aster')

    def last_ym(self):
        last = last_month_day()
        return f'{last.year}-{last.month:02d}'

    def test_saved_winner_who_opts_out_later_disappears(self):
        donate(self.a, last_month_day())
        call_command('award_monthly_heroes', '--month', self.last_ym(),
                     stdout=StringIO())
        self.a.profile.show_on_leaderboard = False
        self.a.profile.save()
        last = last_month_day()
        for url in (reverse('spotlight:home'),
                    reverse('spotlight:month', args=[last.year, last.month])):
            self.assertNotContains(self.client.get(url), 'Alma')

    def test_squad_winner_links_to_public_squad(self):
        squad = Squad.objects.create(name='Link Squad', owner=self.a, is_public=True)
        SquadMember.objects.create(squad=squad, user=self.a)
        donate(self.a, this_month_day())
        response = self.client.get(reverse('spotlight:home'))
        self.assertContains(response, 'Link Squad')
        self.assertContains(response, reverse('squads:detail', args=[squad.pk]))


class AwardCommandTests(Base):
    def run_cmd(self, *args):
        out = StringIO()
        call_command('award_monthly_heroes', *args, stdout=out)
        return out.getvalue()

    def ym(self):
        last = last_month_day()
        return last.year, last.month, f'{last.year}-{last.month:02d}'

    def test_saves_winners_and_is_idempotent(self):
        year, month, label = self.ym()
        donate(self.a, last_month_day())
        out = self.run_cmd('--month', label)
        self.assertIn('awarded donor', out)
        self.assertEqual(MonthlyAward.objects.filter(year=year, month=month).count(), 1)
        out = self.run_cmd('--month', label)
        self.assertEqual(MonthlyAward.objects.filter(year=year, month=month).count(), 1)
        self.assertIn('nothing to award', out)

    def test_existing_award_is_not_overwritten(self):
        year, month, label = self.ym()
        donate(self.a, last_month_day())
        self.run_cmd('--month', label)
        for _ in range(3):
            donate(self.b, last_month_day())
        self.run_cmd('--month', label)
        self.assertEqual(
            MonthlyAward.objects.get(kind='donor', year=year, month=month).user_id,
            self.a.pk)

    def test_dry_run_saves_nothing(self):
        _, _, label = self.ym()
        donate(self.a, last_month_day())
        out = self.run_cmd('--month', label, '--dry-run')
        self.assertIn('would award donor', out)
        self.assertEqual(MonthlyAward.objects.count(), 0)

    def test_current_future_and_malformed_months_are_refused(self):
        today = timezone.localdate()
        for bad in (f'{today.year}-{today.month:02d}', f'{today.year + 1}-01',
                    'nonsense', '2026-13'):
            with self.assertRaises(CommandError, msg=bad):
                self.run_cmd('--month', bad)

    def test_opted_out_donor_is_never_awarded(self):
        _, _, label = self.ym()
        for _ in range(3):
            donate(self.c, last_month_day())
        self.run_cmd('--month', label)
        self.assertEqual(MonthlyAward.objects.count(), 0)

    def test_database_allows_one_winner_per_award_per_month(self):
        from django.db import IntegrityError, transaction
        MonthlyAward.objects.create(kind='donor', year=2026, month=1, user=self.a)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MonthlyAward.objects.create(kind='donor', year=2026, month=1, user=self.b)

    def test_default_month_waits_for_grace_days(self):
        self.assertEqual(heroes.target_month(datetime.date(2026, 11, 2)), (2026, 9))
        self.assertEqual(heroes.target_month(datetime.date(2026, 11, 4)), (2026, 10))
        self.assertEqual(heroes.target_month(datetime.date(2026, 1, 10)), (2025, 12))

    def test_run_without_arguments_works(self):
        self.run_cmd()
        self.run_cmd('--dry-run')

    def test_listed_in_scheduled_jobs_and_runs_in_dry_run(self):
        self.assertIn(('award_monthly_heroes', True), run_scheduled.JOBS)
        out = StringIO()
        call_command('run_scheduled', '--dry-run', '--only',
                     'award_monthly_heroes', stdout=out)
        self.assertIn('award_monthly_heroes: ok', out.getvalue())


# --------------------------------------------------------------------- pulse

class PulseTests(Base):
    def pledge(self, user=None, req=None, minutes=60):
        req = req or make_request(self.stranger)
        row = RequestResponse.objects.create(blood_request=req, donor=user or self.a)
        age(row, minutes)
        return row

    def events(self):
        cache.clear()
        return pulse.pulse()

    def test_pledge_event_is_anonymous(self):
        self.pledge()
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['text'], 'A donor in Sylhet offered to give O+')

    def test_fulfilled_event_text_and_article(self):
        req = make_request(self.stranger, blood_group='A+', district='Dhaka')
        fulfil(req, timezone.now() - datetime.timedelta(hours=2))
        req2 = make_request(self.stranger, blood_group='B+', district='Dhaka')
        fulfil(req2, timezone.now() - datetime.timedelta(hours=3))
        texts = [e['text'] for e in self.events()]
        self.assertIn('An A+ request in Dhaka was fulfilled', texts)
        self.assertIn('A B+ request in Dhaka was fulfilled', texts)

    def test_verified_donation_event(self):
        donation = donate(self.a, this_month_day())
        age(donation, 90, 'updated_at')
        texts = [e['text'] for e in self.events()]
        self.assertEqual(texts, ['A donor in Dhaka completed a verified donation'])

    def test_pending_donation_is_not_an_event(self):
        donation = donate(self.a, this_month_day(), status=Donation.Status.PENDING)
        age(donation, 90, 'updated_at')
        self.assertEqual(self.events(), [])

    def test_events_are_held_back_ten_minutes(self):
        self.pledge(minutes=5)
        self.assertEqual(self.events(), [])
        self.pledge(user=self.b, minutes=11)
        self.assertEqual(len(self.events()), 1)

    def test_opted_out_members_events_are_ignored(self):
        self.pledge(user=self.c)
        req = make_request(self.c)
        fulfil(req, timezone.now() - datetime.timedelta(hours=2))
        donation = donate(self.c, this_month_day())
        age(donation, 120, 'updated_at')
        self.assertEqual(self.events(), [])

    def test_opting_out_removes_events_even_from_the_cache(self):
        self.pledge()
        self.assertEqual(len(pulse.pulse()), 1)
        self.a.profile.show_on_leaderboard = False
        self.a.profile.save()
        self.assertEqual(pulse.pulse(), [])

    def test_flagged_requests_are_skipped(self):
        req = make_request(self.stranger)
        self.pledge(req=req)
        reporters = [make_user(20 + i) for i in range(3)]
        for reporter in reporters:
            RequestReport.objects.create(
                blood_request=req, reporter=reporter,
                reason=RequestReport.Reason.SPAM)
        self.assertEqual(self.events(), [])

    def test_at_most_eight_newest_first(self):
        for i in range(10):
            self.pledge(user=make_user(30 + i), minutes=20 + i)
        events = self.events()
        self.assertEqual(len(events), 8)
        whens = [e['when'] for e in events]
        self.assertEqual(whens[0], 'about 20 minutes ago')

    def test_old_events_drop_out(self):
        self.pledge(minutes=60 * 24 * 30)
        self.assertEqual(self.events(), [])

    def test_relative_times_are_rounded(self):
        now = timezone.now()
        td = datetime.timedelta
        self.assertEqual(pulse.relative_time(now - td(minutes=12), now), 'about 10 minutes ago')
        self.assertEqual(pulse.relative_time(now - td(minutes=50), now), 'about 50 minutes ago')
        self.assertEqual(pulse.relative_time(now - td(minutes=58), now), 'about 1 hour ago')
        self.assertEqual(pulse.relative_time(now - td(minutes=130), now), 'about 2 hours ago')
        self.assertEqual(pulse.relative_time(now - td(hours=50), now), 'about 2 days ago')

    def test_json_endpoint_has_only_safe_fields(self):
        for i in range(3):
            self.pledge(user=make_user(40 + i))
        response = self.client.get(reverse('spotlight:pulse'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('max-age=60', response['Cache-Control'])
        data = response.json()
        self.assertTrue(data['show'])
        self.assertEqual(data['count'], 3)
        for event in data['events']:
            self.assertEqual(set(event), {'kind', 'icon', 'text', 'when'})
        body = response.content.decode()
        for secret in (HOSPITAL, NOTE, PATIENT, 'Alma', self.a.phone,
                       self.a.email, '01999990000'):
            self.assertNotIn(secret, body)

    def test_json_show_false_when_sparse(self):
        self.pledge()
        data = self.client.get(reverse('spotlight:pulse')).json()
        self.assertFalse(data['show'])
        self.assertEqual(data['count'], 1)

    def test_raw_list_is_cached_for_a_minute(self):
        self.pledge()
        self.assertEqual(len(pulse.pulse()), 1)
        self.pledge(user=self.b)
        self.assertEqual(len(pulse.pulse()), 1)  # still the cached list
        cache.clear()
        self.assertEqual(len(pulse.pulse()), 2)

    def test_home_panel_hidden_below_three_events(self):
        self.pledge()
        self.assertNotContains(self.client.get('/'), 'Happening now')

    def test_home_panel_shown_with_three_events(self):
        for i in range(3):
            self.pledge(user=make_user(50 + i))
        response = self.client.get('/')
        self.assertContains(response, 'Happening now')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'A donor in Sylhet offered to give O+')
        self.assertContains(response, 'spotlight/pulse.js')
        panel = response.content.decode().split('Happening now')[1].split('</section>')[0]
        self.assertNotIn(HOSPITAL, panel)
        self.assertNotIn(self.stranger.phone, panel)

    def test_pulse_js_pauses_when_hidden_and_respects_motion(self):
        from django.contrib.staticfiles import finders
        source = open(finders.find('spotlight/pulse.js'), encoding='utf-8').read()
        self.assertIn('document.hidden', source)
        self.assertIn('prefers-reduced-motion', source)
        self.assertNotIn('innerHTML', source)


# --------------------------------------------------------------------- style

class StyleTests(Base):
    def test_no_activity_means_no_style(self):
        self.assertIsNone(styles.style_for(self.a))

    def test_regular_needs_three_verified_donations(self):
        today = timezone.localdate()
        for n in range(2):
            donate(self.a, today - datetime.timedelta(days=130 * (n + 1)))
        donate(self.a, today - datetime.timedelta(days=500),
               status=Donation.Status.PENDING)
        self.assertNotEqual(getattr(styles.style_for(self.a), 'key', None), 'regular')
        donate(self.a, today - datetime.timedelta(days=900))
        style = styles.style_for(self.a)
        self.assertEqual(style.key, 'regular')
        self.assertEqual(style.progress['target'], 5)
        self.assertEqual(style.progress['current'], 3)

    def test_first_responder_from_pledges(self):
        RequestResponse.objects.create(
            blood_request=make_request(self.stranger), donor=self.a)
        style = styles.style_for(self.a)
        self.assertEqual(style.key, 'first_responder')
        self.assertEqual(style.progress['current'], 1)

    def test_storyteller_needs_an_approved_story(self):
        story = Story.objects.create(
            author=self.a, title='A real story', body='x' * 120,
            status=Story.Status.PENDING)
        self.assertNotEqual(styles.style_for(self.a).key, 'storyteller')
        story.status = Story.Status.APPROVED
        story.save()
        self.assertEqual(styles.style_for(self.a).key, 'storyteller')

    def test_connector_needs_a_rewarded_invite(self):
        from growth.models import Referral
        ref = Referral.objects.create(inviter=self.a, invitee=self.b)
        self.assertIsNone(styles.style_for(self.a))
        ref.rewarded_at = timezone.now()
        ref.save()
        self.assertEqual(styles.style_for(self.a).key, 'connector')

    def test_organizer_needs_an_approved_drive(self):
        from events.models import Event
        event = Event.objects.create(
            title='Drive', organizer=self.a, district='Dhaka',
            starts_at=timezone.now() + datetime.timedelta(days=4),
            status=Event.Status.PENDING)
        self.assertIsNone(styles.style_for(self.a))
        event.status = Event.Status.APPROVED
        event.save()
        self.assertEqual(styles.style_for(self.a).key, 'organizer')

    def test_rising_star_only_for_new_members_with_an_early_action(self):
        donate(self.a, this_month_day(), status=Donation.Status.PENDING)
        self.assertEqual(styles.style_for(self.a).key, 'rising_star')
        old = timezone.now() - datetime.timedelta(days=400)
        type(self.a.profile).objects.filter(pk=self.a.pk).update(created_at=old)
        self.a.refresh_from_db()
        self.assertIsNone(styles.style_for(self.a))

    def test_top_style_follows_priority_and_lists_the_rest(self):
        today = timezone.localdate()
        for n in range(3):
            donate(self.a, today - datetime.timedelta(days=130 * (n + 1)))
        RequestResponse.objects.create(
            blood_request=make_request(self.stranger), donor=self.a)
        style = styles.style_for(self.a)
        self.assertEqual(style.key, 'regular')
        self.assertIn('first_responder', style.earned)

    def test_copy_never_pushes_donating(self):
        for info in styles.ARCHETYPES.values():
            text = f'{info["line"]} {info["next"]}'.lower()
            self.assertNotIn('donate now', text)
            self.assertNotIn('hurry', text)
            self.assertNotIn('don\'t miss', text)

    def test_style_page_requires_login(self):
        response = self.client.get(reverse('spotlight:style'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_style_page_empty_and_filled(self):
        self.login(self.a)
        self.assertContains(self.client.get(reverse('spotlight:style')),
                            'appears as you take part')
        RequestResponse.objects.create(
            blood_request=make_request(self.stranger), donor=self.a)
        response = self.client.get(reverse('spotlight:style'))
        self.assertContains(response, 'The First Responder')
        self.assertContains(response, 'sp-emblem--responder')

    def test_every_archetype_has_a_distinct_emblem(self):
        from django.template.loader import render_to_string
        shapes = {key: render_to_string('spotlight/_emblem.html', {'key': key})
                  for key in styles.ORDER}
        self.assertEqual(len(set(shapes.values())), len(styles.ORDER))
        for html in shapes.values():
            self.assertNotRegex(html, r'#[0-9a-fA-F]{3,6}')


class StyleCardTests(Base):
    def setUp(self):
        super().setUp()
        for user in (self.a, self.c):
            RequestResponse.objects.create(
                blood_request=make_request(self.stranger), donor=user)

    def url(self, user):
        return reverse('spotlight:style-card', args=[user.pk])

    def test_public_when_card_and_leaderboard_both_allowed(self):
        response = self.client.get(self.url(self.a))
        self.assertContains(response, 'Alma Aster')
        self.assertContains(response, 'The First Responder')
        self.assertNotContains(response, self.a.phone)
        self.assertNotContains(response, self.a.email)

    def test_private_when_card_sharing_is_off(self):
        self.a.profile.public_badge_card = False
        self.a.profile.save()
        self.assertEqual(self.client.get(self.url(self.a)).status_code, 404)
        self.login(self.stranger)
        self.assertEqual(self.client.get(self.url(self.a)).status_code, 404)
        self.login(self.a)
        self.assertEqual(self.client.get(self.url(self.a)).status_code, 200)
        self.login(self.staff)
        self.assertEqual(self.client.get(self.url(self.a)).status_code, 200)

    def test_opted_out_member_card_is_not_public(self):
        self.assertEqual(self.client.get(self.url(self.c)).status_code, 404)
        self.login(self.stranger)
        response = self.client.get(self.url(self.c))
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'Hidden', status_code=404)
        self.login(self.c)
        response = self.client.get(self.url(self.c))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'noindex')

    def test_unknown_user_and_no_style_404(self):
        self.assertEqual(self.client.get('/spotlight/style/card/99999/').status_code, 404)
        self.assertEqual(self.client.get(self.url(self.b)).status_code, 404)


# --------------------------------------------------------------- certificate

class CertificateTests(Base):
    def setUp(self):
        super().setUp()
        self.donation = donate(self.a, this_month_day())
        self.pending = donate(self.a, this_month_day(),
                              status=Donation.Status.PENDING)
        self.rejected = donate(self.a, this_month_day(),
                               status=Donation.Status.REJECTED)

    def url(self, donation=None):
        return reverse('spotlight:certificate', args=[(donation or self.donation).pk])

    def test_owner_sees_certificate(self):
        self.login(self.a)
        response = self.client.get(self.url())
        self.assertContains(response, 'Alma Aster')
        self.assertContains(response, HOSPITAL)
        self.assertContains(response, f'BB-{self.donation.pk:06d}')
        self.assertContains(response, 'Recognition of a volunteer-verified donation. This is not a medical record.')
        self.assertContains(response, 'window.print()')
        self.assertContains(response, 'sp-noprint')
        self.assertNotContains(response, self.a.phone)
        self.assertNotContains(response, self.a.email)

    def test_name_falls_back_to_donor(self):
        self.a.first_name = self.a.last_name = ''
        self.a.save()
        self.login(self.a)
        response = self.client.get(self.url())
        self.assertContains(response, 'sp-cert__name">Donor<')
        self.assertNotContains(response, self.a.phone)

    def test_staff_can_open_any_certificate(self):
        self.login(self.staff)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_stranger_gets_404(self):
        self.login(self.stranger)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'Alma', status_code=404)

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_only_verified_donations_get_a_certificate(self):
        for donation in (self.pending, self.rejected):
            for user in (self.a, self.staff):
                self.login(user)
                self.assertEqual(self.client.get(self.url(donation)).status_code, 404)
        self.login(self.a)
        self.assertEqual(self.client.get('/spotlight/certificate/99999/').status_code, 404)

    def test_print_layout_is_a4_landscape(self):
        from django.contrib.staticfiles import finders
        css = open(finders.find('spotlight/spotlight.css'), encoding='utf-8').read()
        self.assertIn('size: A4 landscape', css)
        self.assertIn('@media print', css)

    def test_hub_panel_lists_at_most_three_verified_certificates(self):
        for n in range(4):
            donate(self.a, this_month_day() - datetime.timedelta(days=130 * (n + 1)))
        self.login(self.a)
        html = self.client.get(reverse('log-donation')).content.decode()
        self.assertEqual(html.count('/spotlight/certificate/'), 3)
        self.assertNotIn(f'/spotlight/certificate/{self.pending.pk}/', html)
        self.assertNotIn(f'/spotlight/certificate/{self.rejected.pk}/', html)


# ------------------------------------------------------------------ calendar

class ReadyCalendarTests(Base):
    def url(self):
        return reverse('spotlight:ready-ics')

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_no_donation_is_a_friendly_404(self):
        self.login(self.a)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Nothing to remind you about yet', status_code=404)

    def test_valid_calendar_on_next_eligible_date(self):
        last = timezone.localdate() - datetime.timedelta(days=10)
        donate(self.a, last)
        eligibility = get_eligibility(self.a)
        self.login(self.a)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/calendar'))
        self.assertIn('attachment', response['Content-Disposition'])
        body = response.content.decode()
        self.assertTrue(body.startswith('BEGIN:VCALENDAR\r\n'))
        self.assertTrue(body.endswith('END:VCALENDAR\r\n'))
        self.assertNotIn('\n', body.replace('\r\n', ''))
        day = eligibility.next_date
        self.assertIn(f'DTSTART;VALUE=DATE:{day:%Y%m%d}\r\n', body)
        nxt = day + datetime.timedelta(days=1)
        self.assertIn(f'DTEND;VALUE=DATE:{nxt:%Y%m%d}\r\n', body)
        self.assertIn('UID:ready-', body)
        self.assertIn('DTSTAMP:', body)
        self.assertIn(
            'SUMMARY:Ready to donate again (a reminder\\, not medical clearance)',
            body.replace('\r\n ', ''))
        self.assertNotIn(self.a.phone, body)
        self.assertNotIn(self.a.email, body)

    def test_already_past_the_interval_lands_on_today(self):
        donate(self.a, timezone.localdate() - datetime.timedelta(days=400))
        self.login(self.a)
        body = self.client.get(self.url()).content.decode()
        self.assertIn(f'DTSTART;VALUE=DATE:{timezone.localdate():%Y%m%d}', body)

    def test_pending_donation_counts_like_the_rest_of_the_site(self):
        donate(self.a, timezone.localdate() - datetime.timedelta(days=5),
               status=Donation.Status.PENDING)
        self.login(self.a)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_ics_helpers_escape_and_fold(self):
        from spotlight import ics
        self.assertEqual(ics.escape_text('a,b;c\\d\ne'), 'a\\,b\\;c\\\\d\\ne')
        long = ics.fold('X:' + 'é' * 100)
        for part in long.split('\r\n'):
            self.assertLessEqual(len(part.encode('utf-8')), 75)
            part.encode('utf-8').decode('utf-8')

    def test_hub_button_only_with_a_donation(self):
        self.login(self.a)
        self.assertNotContains(self.client.get(reverse('log-donation')), 'ready.ics')
        donate(self.a, timezone.localdate() - datetime.timedelta(days=5))
        self.assertContains(self.client.get(reverse('log-donation')), 'ready.ics')


# ------------------------------------------------------- panels and admin

class PanelTests(Base):
    def test_hub_panel_shows_style_and_last_award(self):
        MonthlyAward.objects.create(kind='donor', year=2026, month=1, user=self.a, count=2)
        RequestResponse.objects.create(
            blood_request=make_request(self.stranger), donor=self.a)
        self.login(self.a)
        response = self.client.get(reverse('log-donation'))
        self.assertContains(response, 'Your spotlight')
        self.assertContains(response, 'The First Responder')
        self.assertContains(response, 'Donor of the Month')
        self.assertContains(response, '/spotlight/2026/1/')

    def test_home_strip_only_names_visible_heroes(self):
        donate(self.a, this_month_day())
        for _ in range(3):
            donate(self.c, this_month_day())
        html = self.client.get('/').content.decode()
        self.assertIn('This month', html)
        self.assertIn('Alma Aster', html)
        self.assertNotIn('Cora', html)

    def test_home_strip_drops_a_hero_who_just_opted_out(self):
        donate(self.a, this_month_day())
        self.assertContains(self.client.get('/'), 'Alma Aster')  # now cached
        self.a.profile.show_on_leaderboard = False
        self.a.profile.save()
        self.assertNotContains(self.client.get('/'), 'Alma Aster')

    def test_request_panel_is_empty(self):
        req = make_request(self.stranger)
        self.assertNotContains(self.client.get(reverse('request-detail', args=[req.pk])),
                               'spotlight/')

    def test_admin_registered_without_phone(self):
        from django.contrib import admin
        self.assertIn(MonthlyAward, admin.site._registry)
        award = MonthlyAward.objects.create(kind='donor', year=2026, month=2, user=self.a)
        self.assertNotIn(self.a.phone, str(award))
        model_admin = admin.site._registry[MonthlyAward]
        self.assertEqual(model_admin.winner(award), 'Alma Aster')

    def test_sitemap_lists_the_hero_page(self):
        self.assertContains(self.client.get(reverse('sitemap')), '/spotlight/')


# ---------------------------------------------------------- privacy crawl

class PrivacyCrawlTests(Base):
    """Every page built here, as every kind of viewer: no phone or email of
    another member and no name of an opted-out member, ever."""

    def setUp(self):
        super().setUp()
        self.owner = make_user(6, 'Olga', 'Owner')
        today = timezone.localdate()
        self.squad = Squad.objects.create(name='Crawl Squad', owner=self.a, is_public=True)
        SquadMember.objects.create(squad=self.squad, user=self.a)
        SquadMember.objects.create(squad=self.squad, user=self.c)
        for n in range(3):
            donate(self.c, this_month_day())
            donate(self.owner, today - datetime.timedelta(days=130 * (n + 1)))
        donate(self.a, this_month_day())
        self.cert = donate(self.owner, today - datetime.timedelta(days=1))
        self.cert.status = Donation.Status.APPROVED
        self.cert.save()
        req = make_request(self.c)
        RequestResponse.objects.create(blood_request=req, donor=self.c)
        fulfil(req)
        MonthlyAward.objects.create(kind='donor', year=2026, month=3, user=self.c, count=9)
        MonthlyAward.objects.create(kind='helper', year=2026, month=3, user=self.a, count=4)
        for i in range(4):
            row = RequestResponse.objects.create(
                blood_request=make_request(self.stranger), donor=make_user(60 + i))
            age(row, 90)
        cache.clear()

    def paths(self):
        from types import SimpleNamespace
        today = timezone.localdate()
        last = heroes.previous_month(today.year, today.month)
        return [
            '/spotlight/', f'/spotlight/{today.year}/{today.month}/',
            f'/spotlight/{last[0]}/{last[1]}/', '/spotlight/2026/3/',
            '/spotlight/pulse.json', '/spotlight/style/', '/spotlight/ready.ics',
            f'/spotlight/style/card/{self.a.pk}/',
            f'/spotlight/style/card/{self.c.pk}/',
            f'/spotlight/style/card/{self.owner.pk}/',
            f'/spotlight/certificate/{self.cert.pk}/',
            '/', reverse('log-donation'),
        ]

    def check(self, label):
        others = [self.a, self.b, self.c, self.stranger, self.owner, self.staff]
        for path in self.paths():
            response = self.client.get(path)
            self.assertLess(response.status_code, 500, (label, path))
            if response.status_code in (301, 302):
                continue
            body = response.content.decode()
            for member in others:
                if member.pk == self._viewer_pk:
                    continue
                self.assertNotIn(member.phone, body, (label, path))
                self.assertNotIn(member.email, body, (label, path))
            if self._viewer_pk != self.c.pk and not self._staff:
                self.assertNotIn('Cora', body, (label, path))
                self.assertNotIn('Hidden', body, (label, path))

    def test_anonymous(self):
        self._viewer_pk, self._staff = None, False
        self.check('anonymous')

    def test_stranger(self):
        self.login(self.stranger)
        self._viewer_pk, self._staff = self.stranger.pk, False
        self.check('stranger')

    def test_owner(self):
        self.login(self.owner)
        self._viewer_pk, self._staff = self.owner.pk, False
        self.check('owner')

    def test_staff(self):
        self.login(self.staff)
        self._viewer_pk, self._staff = self.staff.pk, True
        # Staff may open an opted-out member's own card, but still never see
        # other members' contact details on these pages.
        self.check('staff')

    def test_opted_out_member_sees_only_their_own_card(self):
        self.login(self.c)
        self._viewer_pk, self._staff = self.c.pk, False
        self.check('opted-out')
        self.assertEqual(
            self.client.get(f'/spotlight/style/card/{self.a.pk}/').status_code, 200)

    def test_shared_audit_paths_all_render(self):
        from rewards.audit_fixtures import build_cast
        from spotlight import audit
        cast = build_cast()
        for name, path in audit.paths(cast):
            self.client.logout()
            for user in (None, cast.users['stranger'], cast.users['owner'],
                         cast.users['staff']):
                if user is not None:
                    self.client.force_login(user)
                response = self.client.get(path)
                self.assertLess(response.status_code, 500, (name, path))
                body = response.content.decode()
                for key, person in cast.users.items():
                    if user is not None and person.pk == user.pk:
                        continue
                    self.assertNotIn(person.phone, body, (name, path))
                    self.assertNotIn(person.email, body, (name, path))
                if user is None or not user.is_staff:
                    self.assertNotIn('Hiddenson', body, (name, path))
