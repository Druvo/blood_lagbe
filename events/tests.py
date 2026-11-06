"""Events: drives, RSVPs, attendance, squad entries, calendar files,
campaigns and badges, squad challenges, panels and query counts."""
import datetime
import io
import re
from unittest import mock

from django.contrib.admin.sites import site as admin_site
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from events import awards, challenges as challenge_rules
from events.admin import (
    approve_events,
    cancel_events,
    verify_attendance,
)
from events.ics import build_calendar, escape_text, fold
from events.models import (
    CAMPAIGN_ICONS,
    CAMPAIGN_THEMES,
    Campaign,
    CampaignBadgeAward,
    Challenge,
    Event,
    EventRSVP,
    EventTeam,
    VerifiedOrganizer,
)
from events.services import EventError, rsvp_going
from rewards.models import Donation
from rewards.tests import make_user
from squads.services import create_squad, join_squad

_n = iter(range(1, 100000))


def person(first='Pat', last='', staff=False, **profile):
    user = make_user(phone=f'0176{next(_n):07d}', first_name=first,
                     last_name=last)
    if staff:
        user.staff = True
        user.save()
    for key, value in profile.items():
        setattr(user.profile, key, value)
    if profile:
        user.profile.save()
    return user


def soon(days=2, hours=0):
    return timezone.now() + datetime.timedelta(days=days, hours=hours)


def mk_event(organizer, **kw):
    kw.setdefault('title', f'Drive {next(_n)}')
    kw.setdefault('starts_at', soon())
    kw.setdefault('district', 'Dhaka')
    kw.setdefault('venue', 'Town hall')
    kw.setdefault('status', Event.Status.APPROVED)
    if kw['status'] == Event.Status.APPROVED:
        kw.setdefault('approved_at', timezone.now())
    return Event.objects.create(organizer=organizer, **kw)


def give(user, days_ago=0, status=Donation.Status.APPROVED):
    return Donation.objects.create(
        donor=user, blood_group='A+', location='Bank', status=status,
        donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
    )


def client_for(user=None):
    c = Client()
    if user:
        c.force_login(user)
    return c


def form_data(**over):
    start = timezone.localtime(soon(3)).strftime('%Y-%m-%dT%H:%M')
    data = {
        'title': 'Campus drive', 'organizer_name': '', 'starts_at': start,
        'ends_at': '', 'district': 'Dhaka', 'place': '', 'venue': 'Main gate',
        'description': 'Come and give.', 'capacity': '',
    }
    data.update(over)
    return data


# -- models and querysets -------------------------------------------------

class ModelTests(TestCase):
    def setUp(self):
        self.org = person('Olive')

    def test_upcoming_and_completed_split(self):
        up = mk_event(self.org)
        past = mk_event(self.org, starts_at=soon(-3))
        pending = mk_event(self.org, status=Event.Status.PENDING)
        cancelled = mk_event(self.org, status=Event.Status.CANCELLED)
        done = mk_event(self.org, starts_at=soon(-5),
                        status=Event.Status.COMPLETED)
        self.assertEqual(list(Event.objects.upcoming()), [up])
        self.assertEqual(
            set(Event.objects.completed()), {past, done},
        )
        self.assertNotIn(pending, Event.objects.completed())
        self.assertNotIn(cancelled, Event.objects.upcoming())

    def test_event_without_end_lasts_a_few_hours(self):
        just_started = mk_event(self.org, starts_at=soon(0, -1))
        self.assertIn(just_started, Event.objects.upcoming())
        self.assertFalse(just_started.is_past)
        long_ago = mk_event(self.org, starts_at=soon(0, -5))
        self.assertNotIn(long_ago, Event.objects.upcoming())
        self.assertTrue(long_ago.is_past)

    def test_end_must_follow_start(self):
        event = Event(organizer=self.org, title='x', district='Dhaka',
                      venue='v', starts_at=soon(3), ends_at=soon(2))
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn('ends_at', ctx.exception.message_dict)

    def test_past_event_cannot_be_created(self):
        event = Event(organizer=self.org, title='x', district='Dhaka',
                      venue='v', starts_at=soon(-1))
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn('starts_at', ctx.exception.message_dict)

    def test_one_rsvp_per_user_constraint(self):
        event = mk_event(self.org)
        EventRSVP.objects.create(event=event, user=self.org)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventRSVP.objects.create(event=event, user=self.org)

    def test_public_organizer_never_shows_phone_or_opted_out_name(self):
        hidden = person('Secret', 'Person', show_on_leaderboard=False)
        event = mk_event(hidden)
        self.assertEqual(event.public_organizer, 'A community member')
        named = mk_event(hidden, organizer_name='Red Club')
        self.assertEqual(named.public_organizer, 'Red Club')
        shown = person('Sana', 'Open')
        self.assertEqual(mk_event(shown).public_organizer, 'Sana Open')

    def test_verified_organizer_flag(self):
        event = mk_event(self.org)
        self.assertFalse(event.is_verified_organizer)
        VerifiedOrganizer.objects.create(user=self.org)
        self.assertTrue(event.is_verified_organizer)
        annotated = Event.objects.with_stats().get(pk=event.pk)
        self.assertTrue(annotated.organizer_verified)


# -- creating and editing -------------------------------------------------

class CreateTests(TestCase):
    def setUp(self):
        self.org = person('Olive', 'Organa')
        self.c = client_for(self.org)

    def test_anonymous_is_sent_to_login(self):
        resp = Client().get(reverse('events:create'))
        self.assertEqual(resp.status_code, 302)
        resp = Client().post(reverse('events:create'), form_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Event.objects.count(), 0)

    def test_create_starts_pending_and_stays_unlisted(self):
        resp = self.c.post(reverse('events:create'), form_data())
        event = Event.objects.get()
        self.assertRedirects(resp, reverse('events:detail', args=[event.pk]))
        self.assertEqual(event.status, Event.Status.PENDING)
        self.assertEqual(event.organizer, self.org)
        self.assertIsNone(event.approved_at)
        listing = Client().get(reverse('events:list'))
        self.assertNotContains(listing, 'Campus drive')
        self.assertEqual(
            Client().get(reverse('events:detail', args=[event.pk])).status_code,
            404,
        )
        # The organizer still sees it, with a notice.
        mine = self.c.get(reverse('events:detail', args=[event.pk]))
        self.assertContains(mine, 'Waiting for approval')
        self.assertContains(self.c.get(reverse('events:mine')), 'Campus drive')

    def test_cannot_post_status_approved_through_the_form(self):
        self.c.post(reverse('events:create'), form_data(status='approved'))
        self.assertEqual(Event.objects.get().status, Event.Status.PENDING)

    def test_past_start_is_rejected(self):
        past = timezone.localtime(soon(-1)).strftime('%Y-%m-%dT%H:%M')
        resp = self.c.post(reverse('events:create'), form_data(starts_at=past))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'cannot start in the past')
        self.assertEqual(Event.objects.count(), 0)

    def test_end_before_start_is_rejected(self):
        end = timezone.localtime(soon(2)).strftime('%Y-%m-%dT%H:%M')
        resp = self.c.post(reverse('events:create'), form_data(ends_at=end))
        self.assertContains(resp, 'end must be after the start')
        self.assertEqual(Event.objects.count(), 0)

    def test_needs_a_venue_or_place(self):
        resp = self.c.post(reverse('events:create'), form_data(venue=''))
        self.assertContains(resp, 'Say where it is')

    def test_place_in_other_district_is_rejected(self):
        from rewards.models import Place
        place = Place.objects.create(name='Sylhet MC', district='Sylhet')
        resp = self.c.post(reverse('events:create'),
                           form_data(place=place.pk, venue=''))
        self.assertContains(resp, 'different district')

    def test_daily_cap(self):
        for i in range(3):
            self.c.post(reverse('events:create'), form_data(title=f'E{i}'))
        self.assertEqual(Event.objects.count(), 3)
        resp = self.c.post(reverse('events:create'), form_data(title='Extra'))
        self.assertEqual(Event.objects.count(), 3)
        self.assertContains(resp, 'up to 3 drives a day')

    def test_cap_resets_after_a_day(self):
        for i in range(3):
            e = mk_event(self.org, status=Event.Status.COMPLETED)
            Event.objects.filter(pk=e.pk).update(
                created_at=timezone.now() - datetime.timedelta(hours=25))
        self.c.post(reverse('events:create'), form_data())
        self.assertEqual(Event.objects.count(), 4)

    def test_pending_cap(self):
        for i in range(5):
            e = mk_event(self.org, status=Event.Status.PENDING)
            Event.objects.filter(pk=e.pk).update(
                created_at=timezone.now() - datetime.timedelta(hours=30))
        self.c.post(reverse('events:create'), form_data())
        self.assertEqual(Event.objects.count(), 5)

    def test_description_is_escaped(self):
        self.c.post(reverse('events:create'),
                    form_data(description='<script>alert(1)</script>'))
        event = Event.objects.get()
        Event.objects.filter(pk=event.pk).update(status='approved',
                                                 approved_at=timezone.now())
        page = Client().get(reverse('events:detail', args=[event.pk]))
        self.assertNotContains(page, '<script>alert(1)')


class EditCancelTests(TestCase):
    def setUp(self):
        self.org = person('Olive')
        self.other = person('Other')
        self.event = mk_event(self.org, title='Original')
        self.url = reverse('events:edit', args=[self.event.pk])

    def test_stranger_cannot_edit_public_event(self):
        c = client_for(self.other)
        self.assertEqual(c.get(self.url).status_code, 403)
        self.assertEqual(c.post(self.url, form_data(title='Hacked')).status_code, 403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Original')

    def test_stranger_gets_404_for_pending_event(self):
        self.event.status = Event.Status.PENDING
        self.event.save()
        self.assertEqual(client_for(self.other).get(self.url).status_code, 404)

    def test_anonymous_redirected(self):
        self.assertEqual(Client().get(self.url).status_code, 302)

    def test_text_change_sends_approved_event_back_for_review(self):
        client_for(self.org).post(self.url, form_data(title='Changed'))
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Changed')
        self.assertEqual(self.event.status, Event.Status.PENDING)

    def test_time_only_change_keeps_it_approved(self):
        later = timezone.localtime(soon(5)).strftime('%Y-%m-%dT%H:%M')
        data = form_data(title='Original', description='', venue='Town hall',
                         starts_at=later)
        client_for(self.org).post(self.url, data)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.APPROVED)
        self.assertEqual(
            timezone.localtime(self.event.starts_at).strftime('%Y-%m-%dT%H:%M'),
            later)

    def test_capacity_cannot_drop_below_going(self):
        for _ in range(3):
            EventRSVP.objects.create(event=self.event, user=person())
        data = form_data(title='Original', description='', venue='Town hall',
                         capacity='2')
        resp = client_for(self.org).post(self.url, data)
        self.assertContains(resp, 'already going')

    def test_finished_event_cannot_be_edited(self):
        past = mk_event(self.org, starts_at=soon(-4))
        resp = client_for(self.org).get(reverse('events:edit', args=[past.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_cancel_is_post_only_owner_only_and_idempotent(self):
        url = reverse('events:cancel', args=[self.event.pk])
        self.assertEqual(client_for(self.org).get(url).status_code, 405)
        self.assertEqual(client_for(self.other).post(url).status_code, 403)
        self.assertEqual(Client().post(url).status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.APPROVED)
        c = client_for(self.org)
        c.post(url)
        c.post(url)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.CANCELLED)
        page = Client().get(reverse('events:detail', args=[self.event.pk]))
        self.assertContains(page, 'was cancelled')
        self.assertNotContains(Client().get(reverse('events:list')), 'Original')

    def test_cancelled_never_approved_event_stays_hidden(self):
        hidden = mk_event(self.org, status=Event.Status.CANCELLED,
                          approved_at=None)
        self.assertEqual(
            Client().get(reverse('events:detail', args=[hidden.pk])).status_code,
            404)

    def test_finished_event_cannot_be_cancelled(self):
        past = mk_event(self.org, starts_at=soon(-4))
        client_for(self.org).post(reverse('events:cancel', args=[past.pk]))
        past.refresh_from_db()
        self.assertEqual(past.status, Event.Status.APPROVED)


# -- lists and visibility ---------------------------------------------------

class ListTests(TestCase):
    def setUp(self):
        self.org = person('Olive')

    def test_upcoming_list_filters_by_district_and_hides_pending(self):
        mk_event(self.org, title='Dhaka drive')
        mk_event(self.org, title='Sylhet drive', district='Sylhet')
        mk_event(self.org, title='Pending drive', status=Event.Status.PENDING)
        resp = Client().get(reverse('events:list'))
        self.assertContains(resp, 'Dhaka drive')
        self.assertContains(resp, 'Sylhet drive')
        self.assertNotContains(resp, 'Pending drive')
        resp = Client().get(reverse('events:list') + '?district=Sylhet')
        self.assertNotContains(resp, 'Dhaka drive')
        self.assertContains(resp, 'Sylhet drive')
        resp = Client().get(reverse('events:list') + '?district=<b>x</b>')
        self.assertContains(resp, 'Dhaka drive')

    def test_past_events_move_to_completed_view(self):
        mk_event(self.org, title='Old drive', starts_at=soon(-6))
        mk_event(self.org, title='New drive')
        self.assertNotContains(Client().get(reverse('events:list')), 'Old drive')
        past = Client().get(reverse('events:past'))
        self.assertContains(past, 'Old drive')
        self.assertNotContains(past, 'New drive')

    def test_verified_badge_shows(self):
        VerifiedOrganizer.objects.create(user=self.org)
        mk_event(self.org)
        self.assertContains(Client().get(reverse('events:list')),
                            'Verified organizer')

    def test_list_does_not_leak_contact_details(self):
        org = person('Hidden', 'Hermit', show_on_leaderboard=False)
        org.email = 'hermit@example.test'
        org.save()
        mk_event(org)
        for url in (reverse('events:list'), reverse('events:past')):
            body = Client().get(url).content.decode()
            self.assertNotIn(org.phone, body)
            self.assertNotIn('hermit@example.test', body)
            self.assertNotIn('Hermit', body)

    def test_mine_requires_login(self):
        self.assertEqual(Client().get(reverse('events:mine')).status_code, 302)

    def test_list_query_count_does_not_grow_with_events(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                resp = Client().get(reverse('events:list'))
            self.assertEqual(resp.status_code, 200)
            return len(ctx)

        for _ in range(2):
            o = person()
            VerifiedOrganizer.objects.create(user=o)
            e = mk_event(o)
            EventRSVP.objects.create(event=e, user=person())
        few = count()
        for _ in range(9):
            o = person()
            VerifiedOrganizer.objects.create(user=o)
            e = mk_event(o)
            EventRSVP.objects.create(event=e, user=person())
            EventTeam.objects.create(
                event=e, squad=create_squad(o, name=f'S{next(_n)}'),
                entered_by=o)
        self.assertEqual(count(), few)
        self.assertLessEqual(few, 6)

    def test_mine_and_past_query_counts_are_flat(self):
        user = person()
        c = client_for(user)

        def counts():
            out = []
            for name in ('events:mine', 'events:past'):
                with CaptureQueriesContext(connection) as ctx:
                    c.get(reverse(name))
                out.append(len(ctx))
            return out

        for _ in range(2):
            e = mk_event(user, starts_at=soon(-5))
            EventRSVP.objects.create(event=e, user=user)
        few = counts()
        for _ in range(8):
            e = mk_event(person(), starts_at=soon(-5))
            EventRSVP.objects.create(event=e, user=user)
        self.assertEqual(counts(), few)


# -- RSVP ---------------------------------------------------------------------

class RSVPTests(TestCase):
    def setUp(self):
        self.org = person('Olive')
        self.me = person('Mina', 'Member')
        self.event = mk_event(self.org)
        self.url = reverse('events:rsvp', args=[self.event.pk])

    def test_anonymous_cannot_rsvp(self):
        self.assertEqual(Client().post(self.url).status_code, 302)
        self.assertEqual(EventRSVP.objects.count(), 0)

    def test_get_is_not_allowed(self):
        self.assertEqual(client_for(self.me).get(self.url).status_code, 405)

    def test_going_then_double_click_is_idempotent(self):
        c = client_for(self.me)
        c.post(self.url, {'share_name': 'on'})
        c.post(self.url, {'share_name': 'on'})
        self.assertEqual(EventRSVP.objects.filter(event=self.event).count(), 1)
        self.assertTrue(EventRSVP.objects.get().share_name)

    def test_capacity_is_respected(self):
        self.event.capacity = 2
        self.event.save()
        for _ in range(2):
            client_for(person()).post(self.url)
        late = client_for(self.me)
        resp = late.post(self.url, follow=True)
        self.assertContains(resp, 'this drive is full')
        self.assertEqual(EventRSVP.objects.count(), 2)
        self.assertFalse(EventRSVP.objects.filter(user=self.me).exists())

    def test_existing_rsvp_survives_when_full(self):
        self.event.capacity = 1
        self.event.save()
        client_for(self.me).post(self.url)
        client_for(self.me).post(self.url)
        self.assertEqual(EventRSVP.objects.count(), 1)

    def test_service_raises_when_full(self):
        self.event.capacity = 1
        self.event.save()
        rsvp_going(self.event.pk, person())
        with self.assertRaises(EventError):
            rsvp_going(self.event.pk, self.me)

    def test_pending_cancelled_and_past_events_take_no_rsvps(self):
        pending = mk_event(self.org, status=Event.Status.PENDING)
        # A stranger cannot even see it.
        resp = client_for(self.me).post(reverse('events:rsvp', args=[pending.pk]))
        self.assertEqual(resp.status_code, 404)
        past = mk_event(self.org, starts_at=soon(-3))
        client_for(self.me).post(reverse('events:rsvp', args=[past.pk]))
        gone = mk_event(self.org, status=Event.Status.CANCELLED)
        client_for(self.me).post(reverse('events:rsvp', args=[gone.pk]))
        self.assertEqual(EventRSVP.objects.count(), 0)

    def test_cancel_rsvp(self):
        c = client_for(self.me)
        c.post(self.url)
        c.post(reverse('events:rsvp-cancel', args=[self.event.pk]))
        self.assertEqual(EventRSVP.objects.count(), 0)
        # Cancelling twice is harmless.
        c.post(reverse('events:rsvp-cancel', args=[self.event.pk]))
        self.assertEqual(client_for(self.me).get(
            reverse('events:rsvp-cancel', args=[self.event.pk])).status_code, 405)

    def test_rsvp_is_kept_once_the_drive_started(self):
        started = mk_event(self.org, starts_at=soon(0, -1))
        EventRSVP.objects.create(event=started, user=self.me, checked_in=True)
        client_for(self.me).post(reverse('events:rsvp-cancel', args=[started.pk]))
        self.assertEqual(EventRSVP.objects.count(), 1)

    def test_rate_limit(self):
        with mock.patch('events.views.RSVP_LIMIT', 2):
            c = client_for(self.me)
            c.post(self.url)
            c.post(self.url)
            resp = c.post(self.url, follow=True)
        self.assertContains(resp, 'Too many tries')

    def test_public_sees_count_but_never_names(self):
        EventRSVP.objects.create(event=self.event, user=self.me,
                                 share_name=True)
        for viewer in (Client(), client_for(person('Stranger'))):
            page = viewer.get(reverse('events:detail', args=[self.event.pk]))
            self.assertContains(page, '1 going')
            self.assertNotContains(page, 'Mina')
            self.assertNotContains(page, self.me.phone)
            self.assertNotContains(page, 'Who is going')

    def test_organizer_sees_names_only_when_shared(self):
        shared = person('Shara', 'Shared')
        hidden = person('Hida', 'Hidden')
        EventRSVP.objects.create(event=self.event, user=shared, share_name=True)
        EventRSVP.objects.create(event=self.event, user=hidden, share_name=False)
        page = client_for(self.org).get(reverse('events:detail', args=[self.event.pk]))
        self.assertContains(page, 'Shara Shared')
        self.assertNotContains(page, 'Hida')
        self.assertContains(page, f'Donor #{hidden.pk}')
        for user in (shared, hidden):
            self.assertNotContains(page, user.phone)

    def test_name_sharing_defaults_off_for_opted_out_members(self):
        quiet = person('Quiet', show_on_leaderboard=False)
        page = client_for(quiet).get(reverse('events:detail', args=[self.event.pk]))
        self.assertNotContains(page, 'name="share_name" checked')
        self.assertNotContains(page, 'id="ev-share" name="share_name" checked')
        loud = person('Loud')
        page = client_for(loud).get(reverse('events:detail', args=[self.event.pk]))
        self.assertContains(page, 'name="share_name" checked')


class AttendanceTests(TestCase):
    def setUp(self):
        self.org = person('Olive')
        self.guest = person('Gus', 'Guest')
        self.event = mk_event(self.org, starts_at=soon(0, -1))
        self.rsvp = EventRSVP.objects.create(
            event=self.event, user=self.guest, share_name=True)
        self.url = reverse('events:checkin', args=[self.event.pk, self.rsvp.pk])

    def test_organizer_checks_in_and_undoes(self):
        c = client_for(self.org)
        c.post(self.url, {'action': 'in'})
        self.rsvp.refresh_from_db()
        self.assertTrue(self.rsvp.checked_in)
        self.assertIsNotNone(self.rsvp.checked_in_at)
        EventRSVP.objects.filter(pk=self.rsvp.pk).update(verified=True)
        c.post(self.url, {'action': 'out'})
        self.rsvp.refresh_from_db()
        self.assertFalse(self.rsvp.checked_in)
        self.assertFalse(self.rsvp.verified)

    def test_only_the_organizer_can_check_in(self):
        for viewer in (Client(), client_for(self.guest),
                       client_for(person(staff=True))):
            viewer.post(self.url, {'action': 'in'})
        self.rsvp.refresh_from_db()
        self.assertFalse(self.rsvp.checked_in)

    def test_not_before_the_start(self):
        future = mk_event(self.org)
        rsvp = EventRSVP.objects.create(event=future, user=self.guest)
        client_for(self.org).post(
            reverse('events:checkin', args=[future.pk, rsvp.pk]))
        rsvp.refresh_from_db()
        self.assertFalse(rsvp.checked_in)

    def test_rsvp_of_another_event_is_404(self):
        other = mk_event(self.org, starts_at=soon(0, -1))
        resp = client_for(self.org).post(
            reverse('events:checkin', args=[other.pk, self.rsvp.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_staff_verification_only_for_checked_in(self):
        staff = person(staff=True)
        req = RequestFactory().post('/')
        req.user = staff
        admin = admin_site._registry[EventRSVP]
        admin.message_user = mock.Mock()
        verify_attendance(admin, req, EventRSVP.objects.all())
        self.rsvp.refresh_from_db()
        self.assertFalse(self.rsvp.verified)
        EventRSVP.objects.filter(pk=self.rsvp.pk).update(checked_in=True)
        verify_attendance(admin, req, EventRSVP.objects.all())
        self.rsvp.refresh_from_db()
        self.assertTrue(self.rsvp.verified)
        self.assertEqual(self.rsvp.verified_by, staff)

    def test_hub_panel_shows_attendance(self):
        EventRSVP.objects.filter(pk=self.rsvp.pk).update(
            checked_in=True, verified=True)
        page = client_for(self.guest).get(reverse('log-donation'))
        self.assertContains(page, '1 drive attended (1 verified)')


# -- admin ------------------------------------------------------------------------

class AdminActionTests(TestCase):
    def test_approve_and_cancel_actions(self):
        org = person('Olive')
        pending = mk_event(org, status=Event.Status.PENDING)
        staff = person(staff=True)
        req = RequestFactory().post('/')
        req.user = staff
        admin = admin_site._registry[Event]
        admin.message_user = mock.Mock()
        approve_events(admin, req, Event.objects.all())
        pending.refresh_from_db()
        self.assertEqual(pending.status, Event.Status.APPROVED)
        self.assertIsNotNone(pending.approved_at)
        self.assertIn(pending, Event.objects.upcoming())
        cancel_events(admin, req, Event.objects.all())
        pending.refresh_from_db()
        self.assertEqual(pending.status, Event.Status.CANCELLED)

    def test_admin_pages_load_for_staff(self):
        staff = person(staff=True)
        c = client_for(staff)
        for model in ('event', 'eventrsvp', 'verifiedorganizer', 'campaign',
                      'campaignbadgeaward', 'challenge'):
            resp = c.get(f'/admin/events/{model}/')
            self.assertEqual(resp.status_code, 200, model)
        resp = c.get('/admin/events/event/add/')
        self.assertEqual(resp.status_code, 200)

    def test_stranger_cannot_open_admin(self):
        resp = client_for(person()).get('/admin/events/event/')
        self.assertNotEqual(resp.status_code, 200)

    def test_challenge_created_by_is_set(self):
        staff = person(staff=True)
        today = timezone.localdate()
        resp = client_for(staff).post('/admin/events/challenge/add/', {
            'title': 'Spring push', 'description': '',
            'starts_on': today.isoformat(),
            'ends_on': (today + datetime.timedelta(days=5)).isoformat(),
            'target_donations': 3, 'scope': 'goal', 'is_published': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Challenge.objects.get().created_by, staff)


# -- squads ----------------------------------------------------------------------------

class SquadEntryTests(TestCase):
    def setUp(self):
        self.org = person('Olive')
        self.event = mk_event(self.org)
        self.owner = person('Sqo', 'Owner')
        self.pub = create_squad(self.owner, name='Open Heroes', is_public=True)
        self.priv = create_squad(self.owner, name='Secret Society Zed')
        self.url = reverse('events:enter-squad', args=[self.event.pk])

    def test_owner_enters_squad_once(self):
        c = client_for(self.owner)
        c.post(self.url, {'squad': self.pub.pk})
        c.post(self.url, {'squad': self.pub.pk})
        self.assertEqual(EventTeam.objects.count(), 1)

    def test_only_owner_can_enter(self):
        member = person('Mem')
        join_squad(member, self.pub)
        client_for(member).post(self.url, {'squad': self.pub.pk})
        client_for(person('Rando')).post(self.url, {'squad': self.pub.pk})
        Client().post(self.url, {'squad': self.pub.pk})
        self.assertEqual(EventTeam.objects.count(), 0)

    def test_bad_squad_values_do_not_crash(self):
        c = client_for(self.owner)
        for value in ('', 'abc', '99999'):
            self.assertEqual(c.post(self.url, {'squad': value}).status_code, 302)
        self.assertEqual(EventTeam.objects.count(), 0)

    def test_no_entries_for_finished_or_pending_event(self):
        past = mk_event(self.org, starts_at=soon(-3))
        client_for(self.owner).post(
            reverse('events:enter-squad', args=[past.pk]), {'squad': self.pub.pk})
        self.assertEqual(EventTeam.objects.count(), 0)

    def test_public_squad_named_private_squad_hidden(self):
        c = client_for(self.owner)
        c.post(self.url, {'squad': self.pub.pk})
        c.post(self.url, {'squad': self.priv.pk})
        page = Client().get(reverse('events:detail', args=[self.event.pk]))
        self.assertContains(page, 'Open Heroes')
        self.assertContains(page, 'A private squad')
        self.assertNotContains(page, 'Secret Society Zed')
        self.assertNotContains(page, self.priv.join_code)

    def test_member_rsvp_counts_per_team(self):
        m1, m2, outsider = person('M1'), person('M2'), person('Out')
        join_squad(m1, self.pub)
        join_squad(m2, self.pub)
        client_for(self.owner).post(self.url, {'squad': self.pub.pk})
        for u in (m1, m2, outsider):
            EventRSVP.objects.create(event=self.event, user=u)
        page = Client().get(reverse('events:detail', args=[self.event.pk]))
        self.assertContains(page, '2 members going')

    def test_owner_can_withdraw_others_cannot(self):
        EventTeam.objects.create(event=self.event, squad=self.pub,
                                 entered_by=self.owner)
        url = reverse('events:withdraw-squad', args=[self.event.pk, self.pub.pk])
        self.assertEqual(client_for(person('Rando')).post(url).status_code, 404)
        self.assertEqual(EventTeam.objects.count(), 1)
        client_for(self.owner).post(url)
        self.assertEqual(EventTeam.objects.count(), 0)

    def test_enter_form_lists_only_my_unentered_squads(self):
        page = client_for(self.owner).get(
            reverse('events:detail', args=[self.event.pk]))
        self.assertContains(page, 'Enter a squad you own')
        other = client_for(person('Rando')).get(
            reverse('events:detail', args=[self.event.pk]))
        self.assertNotContains(other, 'Enter a squad you own')


# -- calendar --------------------------------------------------------------------------------

class CalendarTests(TestCase):
    def setUp(self):
        self.org = person('Olive')

    def unfold(self, text):
        return text.replace('\r\n ', '')

    def test_valid_ics(self):
        event = mk_event(self.org, title='Give, share; care',
                         description='Line one\nLine two, with; punctuation',
                         ends_at=soon(2, 3))
        resp = Client().get(reverse('events:calendar', args=[event.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp['Content-Type'].startswith('text/calendar'))
        self.assertIn('attachment', resp['Content-Disposition'])
        body = resp.content.decode()
        self.assertTrue(body.startswith('BEGIN:VCALENDAR\r\n'))
        self.assertTrue(body.endswith('END:VCALENDAR\r\n'))
        self.assertNotIn('\n\n', body.replace('\r\n', '\n'))
        for line in body.split('\r\n')[:-1]:
            self.assertLessEqual(len(line.encode()), 75)
            self.assertNotIn('\n', line)
        text = self.unfold(body)
        self.assertIn('SUMMARY:Give\\, share\\; care', text)
        self.assertIn('Line one\\nLine two\\, with\\; punctuation', text)
        self.assertRegex(text, r'DTSTART:\d{8}T\d{6}Z')
        self.assertRegex(text, r'DTEND:\d{8}T\d{6}Z')
        self.assertIn(f'UID:event-{event.pk}@', text)
        self.assertIn('STATUS:CONFIRMED', text)
        # UTC time matches the stored instant.
        stamp = re.search(r'DTSTART:(\d{8}T\d{6})Z', text).group(1)
        parsed = datetime.datetime.strptime(stamp, '%Y%m%dT%H%M%S').replace(
            tzinfo=datetime.timezone.utc)
        self.assertEqual(parsed, event.starts_at.replace(microsecond=0))

    def test_end_defaults_to_start_plus_duration(self):
        event = mk_event(self.org)
        text = Client().get(reverse('events:calendar', args=[event.pk])).content.decode()
        start = re.search(r'DTSTART:(\S+)', text).group(1)
        end = re.search(r'DTEND:(\S+)', text).group(1)
        fmt = '%Y%m%dT%H%M%SZ'
        delta = datetime.datetime.strptime(end, fmt) - datetime.datetime.strptime(start, fmt)
        self.assertEqual(delta, datetime.timedelta(hours=3))

    def test_cancelled_marks_status(self):
        event = mk_event(self.org, status=Event.Status.CANCELLED,
                         approved_at=timezone.now())
        text = Client().get(reverse('events:calendar', args=[event.pk])).content.decode()
        self.assertIn('STATUS:CANCELLED', text)

    def test_pending_event_calendar_hidden_from_strangers(self):
        event = mk_event(self.org, status=Event.Status.PENDING)
        url = reverse('events:calendar', args=[event.pk])
        self.assertEqual(Client().get(url).status_code, 404)
        self.assertEqual(client_for(person()).get(url).status_code, 404)
        self.assertEqual(client_for(self.org).get(url).status_code, 200)

    def test_no_phone_or_email_in_file(self):
        event = mk_event(self.org)
        text = Client().get(reverse('events:calendar', args=[event.pk])).content.decode()
        self.assertNotIn(self.org.phone, text)

    def test_escape_and_fold_helpers(self):
        self.assertEqual(escape_text('a\\b;c,d\ne'), 'a\\\\b\\;c\\,d\\ne')
        long = 'X' * 200
        folded = fold('SUMMARY:' + long)
        for part in folded.split('\r\n'):
            self.assertLessEqual(len(part.encode()), 75)
        self.assertEqual(folded.replace('\r\n ', ''), 'SUMMARY:' + long)
        bangla = 'SUMMARY:' + 'রক্ত' * 40
        folded = fold(bangla)
        self.assertEqual(folded.replace('\r\n ', ''), bangla)
        for part in folded.split('\r\n'):
            part.encode('utf-8').decode('utf-8')
            self.assertLessEqual(len(part.encode()), 75)

    def test_build_calendar_is_pure(self):
        event = mk_event(self.org)
        out = build_calendar(event, 'http://x/e/1', 'x', 'Site', timezone.now())
        self.assertIn('PRODID:-//Site//Events//EN', out)


# -- campaigns -----------------------------------------------------------------------------------

class CampaignWindowTests(TestCase):
    def camp(self, **kw):
        kw.setdefault('name', 'C')
        kw.setdefault('slug', f'c{next(_n)}')
        kw.setdefault('headline', 'H')
        kw.setdefault('badge_name', 'B')
        return Campaign.objects.create(**kw)

    def test_seeded_campaigns_are_inactive(self):
        slugs = set(Campaign.objects.values_list('slug', flat=True))
        self.assertTrue({'world-blood-donor-day', 'thalassemia-awareness',
                         'victory-day', 'ramadan'} <= slugs)
        self.assertFalse(Campaign.objects.filter(is_active=True).exists())
        wbdd = Campaign.objects.get(slug='world-blood-donor-day')
        self.assertEqual((wbdd.recurring_month, wbdd.recurring_day), (6, 14))
        ramadan = Campaign.objects.get(slug='ramadan')
        self.assertIsNone(ramadan.starts_on)
        self.assertEqual(Campaign.active_on(datetime.date(2030, 6, 14)), [])

    def test_recurring_window_is_year_agnostic(self):
        c = self.camp(recurring_month=6, recurring_day=14, duration_days=3,
                      is_active=True)
        for year in (2025, 2031):
            self.assertTrue(c.contains(datetime.date(year, 6, 14)))
            self.assertTrue(c.contains(datetime.date(year, 6, 16)))
            self.assertFalse(c.contains(datetime.date(year, 6, 13)))
            self.assertFalse(c.contains(datetime.date(year, 6, 17)))

    def test_window_crossing_new_year(self):
        c = self.camp(recurring_month=12, recurring_day=30, duration_days=5)
        self.assertTrue(c.contains(datetime.date(2027, 1, 3)))
        self.assertFalse(c.contains(datetime.date(2027, 1, 5)))

    def test_leap_day_in_common_year(self):
        c = self.camp(recurring_month=2, recurring_day=29, duration_days=1)
        self.assertEqual(c.yearly_window(2027)[0], datetime.date(2027, 2, 28))
        self.assertEqual(c.yearly_window(2028)[0], datetime.date(2028, 2, 29))

    def test_fixed_dates_win_and_no_dates_never_match(self):
        c = self.camp(starts_on=datetime.date(2030, 3, 1),
                      ends_on=datetime.date(2030, 3, 30),
                      recurring_month=6, recurring_day=14)
        self.assertTrue(c.contains(datetime.date(2030, 3, 15)))
        self.assertFalse(c.contains(datetime.date(2030, 6, 14)))
        empty = self.camp()
        self.assertFalse(empty.contains(datetime.date(2030, 6, 14)))

    def test_validation(self):
        with self.assertRaises(ValidationError):
            self.camp(starts_on=datetime.date(2030, 3, 1)).full_clean()
        bad = Campaign(name='x', slug='x1', headline='h', badge_name='b',
                       starts_on=datetime.date(2030, 3, 5),
                       ends_on=datetime.date(2030, 3, 1))
        with self.assertRaises(ValidationError):
            bad.full_clean()
        bad = Campaign(name='x', slug='x2', headline='h', badge_name='b',
                       recurring_month=2, recurring_day=31)
        with self.assertRaises(ValidationError):
            bad.full_clean()
        half = Campaign(name='x', slug='x3', headline='h', badge_name='b',
                        recurring_month=2)
        with self.assertRaises(ValidationError):
            half.full_clean()

    def test_theme_and_icon_are_restricted(self):
        c = Campaign(name='x', slug='x4', headline='h', badge_name='b',
                     theme='javascript:alert(1)', icon='../../x')
        with self.assertRaises(ValidationError) as ctx:
            c.full_clean()
        self.assertIn('theme', ctx.exception.message_dict)
        self.assertIn('icon', ctx.exception.message_dict)

    def test_every_icon_exists_in_the_feather_css(self):
        from django.conf import settings
        css = (settings.BASE_DIR / 'staticfiles/css/feather.css').read_text()
        for name, _label in CAMPAIGN_ICONS:
            self.assertIn(f'.feather-{name}:before', css, name)
        self.assertEqual(
            {t for t, _ in CAMPAIGN_THEMES},
            {'red', 'green', 'blue', 'amber', 'violet'})


class CampaignBannerTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.org = person('Olive')

    def active(self, **kw):
        return Campaign.objects.create(
            name='Live one', slug=f'live{next(_n)}', headline='Give this week!',
            badge_name='Live Badge', is_active=True, theme='green',
            recurring_month=self.today.month, recurring_day=self.today.day,
            **kw)

    def test_home_without_campaign_or_events_has_no_events_panel(self):
        page = Client().get(reverse('home'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'ev-banner')
        self.assertNotContains(page, 'Upcoming blood drives')

    def test_banner_when_active_and_in_window(self):
        self.active()
        page = Client().get(reverse('home'))
        self.assertContains(page, 'Give this week!')
        self.assertContains(page, 'ev-theme-green')
        self.assertContains(page, 'Live Badge')

    def test_inactive_or_out_of_window_campaign_has_no_banner(self):
        c = self.active()
        Campaign.objects.filter(pk=c.pk).update(is_active=False)
        self.assertNotContains(Client().get(reverse('home')), 'Give this week!')
        other = self.today + datetime.timedelta(days=40)
        Campaign.objects.filter(pk=c.pk).update(
            is_active=True, recurring_month=other.month,
            recurring_day=other.day)
        self.assertNotContains(Client().get(reverse('home')), 'Give this week!')

    def test_home_lists_next_three_approved_upcoming_events(self):
        for i in range(5):
            mk_event(self.org, title=f'Up {i}', starts_at=soon(i + 1))
        mk_event(self.org, title='Hidden pending', status=Event.Status.PENDING,
                 starts_at=soon(1, 1))
        mk_event(self.org, title='Old one', starts_at=soon(-2))
        page = Client().get(reverse('home'))
        for i in range(3):
            self.assertContains(page, f'Up {i}')
        self.assertNotContains(page, 'Up 3')
        self.assertNotContains(page, 'Hidden pending')
        self.assertNotContains(page, 'Old one')

    def test_campaign_text_is_escaped(self):
        c = self.active()
        Campaign.objects.filter(pk=c.pk).update(headline='<b>x</b>')
        self.assertNotContains(Client().get(reverse('home')), '<b>x</b>')

    def test_campaigns_page_public(self):
        self.active()
        page = Client().get(reverse('events:campaigns'))
        self.assertContains(page, 'Live one')
        self.assertNotContains(page, 'My campaign badges')


class BadgeAwardTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.donor = person('Dana')
        self.campaign = Campaign.objects.create(
            name='Window', slug='window', headline='h', badge_name='Window Badge',
            is_active=True, starts_on=self.today - datetime.timedelta(days=3),
            ends_on=self.today)

    def awards(self):
        return CampaignBadgeAward.objects.filter(user=self.donor)

    def test_approved_donation_in_window_awards_once(self):
        d = give(self.donor, days_ago=1)
        self.assertEqual(self.awards().count(), 1)
        self.assertEqual(self.awards().get().donation, d)
        d.save()  # re-saving (or another donation) must not duplicate
        give(self.donor, days_ago=0)
        self.assertEqual(self.awards().count(), 1)

    def test_pending_and_rejected_donations_do_not_award(self):
        give(self.donor, 1, status=Donation.Status.PENDING)
        give(self.donor, 1, status=Donation.Status.REJECTED)
        self.assertEqual(self.awards().count(), 0)

    def test_awarded_when_status_becomes_approved(self):
        d = give(self.donor, 1, status=Donation.Status.PENDING)
        d.status = Donation.Status.APPROVED
        d.save()
        self.assertEqual(self.awards().count(), 1)

    def test_outside_window_does_not_award(self):
        give(self.donor, days_ago=10)
        self.assertEqual(self.awards().count(), 0)

    def test_inactive_campaign_does_not_award(self):
        Campaign.objects.update(is_active=False)
        give(self.donor, 1)
        self.assertEqual(self.awards().count(), 0)

    def test_bulk_approve_is_caught_by_sync(self):
        d = give(self.donor, 1, status=Donation.Status.PENDING)
        Donation.objects.filter(pk=d.pk).update(status=Donation.Status.APPROVED)
        self.assertEqual(self.awards().count(), 0)
        self.assertEqual(len(awards.sync_user(self.donor)), 1)
        self.assertEqual(len(awards.sync_user(self.donor)), 0)
        self.assertEqual(self.awards().count(), 1)

    def test_hub_visit_catches_up_and_shows_badge(self):
        d = give(self.donor, 1, status=Donation.Status.PENDING)
        Donation.objects.filter(pk=d.pk).update(status=Donation.Status.APPROVED)
        page = client_for(self.donor).get(reverse('log-donation'))
        self.assertContains(page, 'Window Badge')

    def test_command_backfills_and_dry_run_changes_nothing(self):
        d = give(self.donor, 1, status=Donation.Status.PENDING)
        Donation.objects.filter(pk=d.pk).update(status=Donation.Status.APPROVED)
        out = io.StringIO()
        call_command('award_campaign_badges', '--dry-run', stdout=out)
        self.assertIn('Would award 1', out.getvalue())
        self.assertEqual(self.awards().count(), 0)
        out = io.StringIO()
        call_command('award_campaign_badges', stdout=out)
        self.assertIn('Awarded 1', out.getvalue())
        out = io.StringIO()
        call_command('award_campaign_badges', stdout=out)
        self.assertIn('Awarded 0', out.getvalue())
        self.assertEqual(self.awards().count(), 1)

    def test_recurring_campaign_in_a_past_year(self):
        Campaign.objects.create(
            name='Yearly', slug='yearly', headline='h', badge_name='Y',
            is_active=True, recurring_month=6, recurring_day=14, duration_days=1)
        Donation.objects.create(
            donor=self.donor, blood_group='A+', location='x',
            status=Donation.Status.PENDING,
            donation_date=datetime.date(2024, 6, 14))
        Donation.objects.filter(donor=self.donor).update(status='approved')
        awards.sync_all()
        self.assertTrue(self.awards().filter(campaign__slug='yearly').exists())

    def test_award_is_unique_per_user_and_campaign(self):
        give(self.donor, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CampaignBadgeAward.objects.create(user=self.donor,
                                              campaign=self.campaign)

    def test_campaigns_page_shows_my_badge_not_others(self):
        give(self.donor, 1)
        other = person('Other')
        mine = client_for(self.donor).get(reverse('events:campaigns'))
        self.assertContains(mine, 'My campaign badges')
        self.assertContains(mine, 'ev-earned')
        theirs = client_for(other).get(reverse('events:campaigns'))
        self.assertNotContains(theirs, 'ev-earned')

    def test_signal_is_connected_once(self):
        from django.db.models.signals import post_save
        from rewards.models import Donation as D
        receivers = [r for r in post_save._live_receivers(D)
                     if getattr(r, '__name__', '') == 'award_on_donation_saved']
        self.assertEqual(len(receivers), 1)


class HubPanelTests(TestCase):
    def test_anonymous_gets_nothing_and_member_gets_links(self):
        # The hub page itself needs login; the tag must also be safe alone.
        from django.template import Context, Template
        req = RequestFactory().get('/')
        from django.contrib.auth.models import AnonymousUser
        req.user = AnonymousUser()
        out = Template('{% load events_tags %}{% events_hub %}').render(
            Context({'request': req}))
        self.assertEqual(out.strip(), '')

    def test_member_sees_next_rsvp_and_create_link(self):
        org = person('Olive')
        me = person('Me')
        c = client_for(me)
        page = c.get(reverse('log-donation'))
        self.assertContains(page, reverse('events:create'))
        self.assertContains(page, 'No RSVP yet')
        near = mk_event(org, title='Nearest drive', starts_at=soon(1))
        far = mk_event(org, title='Farther drive', starts_at=soon(9))
        EventRSVP.objects.create(event=far, user=me)
        EventRSVP.objects.create(event=near, user=me)
        done = mk_event(org, title='Finished drive', starts_at=soon(-5))
        EventRSVP.objects.create(event=done, user=me)
        page = c.get(reverse('log-donation'))
        self.assertContains(page, 'Nearest drive')
        self.assertNotContains(page, 'Farther drive')
        self.assertNotContains(page, 'Finished drive')

    def test_hub_panel_stays_within_a_small_query_budget(self):
        from django.contrib.auth.models import AnonymousUser  # noqa: F401
        from django.template import Context, Template
        me = person('Me')
        req = RequestFactory().get('/')
        req.user = me
        tpl = Template('{% load events_tags %}{% events_hub %}')
        with CaptureQueriesContext(connection) as ctx:
            tpl.render(Context({'request': req}))
        self.assertLessEqual(len(ctx), 5)

    def test_request_panel_is_empty(self):
        from django.template import Context, Template
        out = Template('{% load events_tags %}{% events_request %}').render(
            Context({}))
        self.assertEqual(out.strip(), '')


# -- challenges ---------------------------------------------------------------------------------------

class ChallengeTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.challenge = Challenge.objects.create(
            title='Squad sprint', starts_on=self.today - datetime.timedelta(days=20),
            ends_on=self.today + datetime.timedelta(days=5),
            target_donations=4, scope=Challenge.Scope.VERSUS)
        self.a1, self.a2 = person('Alpha1'), person('Alpha2')
        self.b1 = person('Beta1')
        self.squad_a = create_squad(self.a1, name='Alpha Squad', is_public=True)
        join_squad(self.a2, self.squad_a)
        self.squad_b = create_squad(self.b1, name='Beta Squad', is_public=True)

    def rows(self, **kw):
        return {r['squad'].name: r for r in challenge_rules.standings(
            self.challenge, **kw)}

    def test_counts_only_approved_donations_inside_the_window(self):
        give(self.a1, 2)
        give(self.a2, 3)
        give(self.a2, 30, status=Donation.Status.APPROVED)  # before window
        give(self.b1, 1, status=Donation.Status.PENDING)
        give(self.b1, 1, status=Donation.Status.REJECTED)
        rows = self.rows()
        self.assertEqual(rows['Alpha Squad']['donations'], 2)
        self.assertNotIn('Beta Squad', rows)

    def test_one_donation_per_member_counts_inside_recovery_interval(self):
        # Window is 26 days, interval 120: one donation per member counts.
        give(self.a1, 1)
        give(self.a1, 2)
        give(self.a1, 3)
        self.assertEqual(self.rows()['Alpha Squad']['donations'], 1)
        self.assertEqual(challenge_rules.per_member_cap(self.challenge), 1)

    def test_cap_grows_with_long_windows(self):
        long = Challenge(title='y', starts_on=self.today - datetime.timedelta(days=300),
                         ends_on=self.today, target_donations=5)
        self.assertEqual(challenge_rules.per_member_cap(long), 3)
        with self.settings(DONATION_INTERVAL_DAYS=10):
            self.assertEqual(challenge_rules.per_member_cap(self.challenge), 3)

    def test_private_squads_never_ranked_but_visible_to_own_members(self):
        solo = person('Solo')
        private = create_squad(solo, name='Hush Hush')
        give(solo, 1)
        self.assertNotIn('Hush Hush', self.rows())
        self.assertIn('Hush Hush', self.rows(squad_ids=[private.pk]))
        page = Client().get(reverse('events:challenge-detail', args=[self.challenge.pk]))
        self.assertNotContains(page, 'Hush Hush')
        mine = client_for(solo).get(
            reverse('events:challenge-detail', args=[self.challenge.pk]))
        self.assertContains(mine, 'Hush Hush')
        stranger = client_for(person('Nosy')).get(
            reverse('events:challenge-detail', args=[self.challenge.pk]))
        self.assertNotContains(stranger, 'Hush Hush')

    def test_member_of_public_and_private_squad_counts_in_both(self):
        both = person('Both')
        join_squad(both, self.squad_a)
        private = create_squad(both, name='Hidden Twin')
        give(both, 1)
        self.assertEqual(self.rows()['Alpha Squad']['donations'], 1)
        self.assertEqual(
            self.rows(squad_ids=[private.pk])['Hidden Twin']['donations'], 1)

    def test_ranking_progress_and_ties(self):
        give(self.a1, 1)
        give(self.a2, 1)
        give(self.b1, 1)
        rows = challenge_rules.standings(self.challenge)
        self.assertEqual([r['squad'].name for r in rows], ['Alpha Squad', 'Beta Squad'])
        self.assertEqual([r['rank'] for r in rows], [1, 2])
        self.assertEqual(rows[0]['percent'], 50)
        a3, a4 = person('A3'), person('A4')
        join_squad(a3, self.squad_a)
        join_squad(a4, self.squad_a)
        give(a3, 1)
        give(a4, 1)
        top = challenge_rules.standings(self.challenge)[0]
        self.assertEqual(top['donations'], 4)
        self.assertTrue(top['reached'])
        self.assertEqual(top['percent'], 100)

    def test_opted_out_donors_count_but_are_never_named(self):
        shy = person('Shy', 'Violet', show_on_leaderboard=False)
        join_squad(shy, self.squad_a)
        loud = person('Loud', 'Larry')
        join_squad(loud, self.squad_a)
        give(shy, 1)
        give(loud, 1)
        self.assertEqual(self.rows()['Alpha Squad']['donations'], 2)
        page = Client().get(reverse('events:challenge-detail', args=[self.challenge.pk]))
        self.assertContains(page, 'Loud Larry')
        self.assertNotContains(page, 'Shy')
        self.assertNotContains(page, 'Violet')
        self.assertNotContains(page, shy.phone)
        self.assertNotContains(page, loud.phone)

    def test_public_pages(self):
        give(self.a1, 1)
        listing = Client().get(reverse('events:challenges'))
        self.assertContains(listing, 'Squad sprint')
        page = Client().get(reverse('events:challenge-detail', args=[self.challenge.pk]))
        self.assertContains(page, 'Alpha Squad')
        self.assertContains(page, 'role="progressbar"')

    def test_unpublished_challenge_is_404(self):
        Challenge.objects.update(is_published=False)
        self.assertEqual(Client().get(reverse(
            'events:challenge-detail', args=[self.challenge.pk])).status_code, 404)
        self.assertNotContains(Client().get(reverse('events:challenges')),
                               'Squad sprint')

    def test_finished_and_upcoming_sections(self):
        Challenge.objects.create(
            title='Old race', starts_on=self.today - datetime.timedelta(days=60),
            ends_on=self.today - datetime.timedelta(days=30), target_donations=2)
        page = Client().get(reverse('events:challenges'))
        self.assertContains(page, 'Finished')
        self.assertContains(page, 'Old race')

    def test_validation_and_no_public_create(self):
        bad = Challenge(title='x', starts_on=self.today,
                        ends_on=self.today - datetime.timedelta(days=1),
                        target_donations=1)
        with self.assertRaises(ValidationError):
            bad.full_clean()
        resp = client_for(person()).post('/events/challenges/', {'title': 'x'})
        self.assertEqual(resp.status_code, 200)  # a list page, nothing created
        self.assertEqual(Challenge.objects.count(), 1)

    def test_detail_query_count_is_flat(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                Client().get(reverse('events:challenge-detail', args=[self.challenge.pk]))
            return len(ctx)

        give(self.a1, 1)
        few = count()
        for i in range(6):
            owner = person(f'Own{i}')
            sq = create_squad(owner, name=f'Extra {i}', is_public=True)
            give(owner, 1)
            join_squad(person(), sq)
        self.assertEqual(count(), few)


class MiscPageTests(TestCase):
    def test_pages_render_for_anonymous_and_member(self):
        org = person('Olive')
        event = mk_event(org)
        urls = [
            reverse('events:list'), reverse('events:past'),
            reverse('events:detail', args=[event.pk]),
            reverse('events:campaigns'), reverse('events:challenges'),
        ]
        for url in urls:
            self.assertEqual(Client().get(url).status_code, 200, url)
        member = client_for(person())
        for url in urls + [reverse('events:mine'), reverse('events:create')]:
            self.assertEqual(member.get(url).status_code, 200, url)

    def test_unknown_event_is_404(self):
        self.assertEqual(Client().get(reverse('events:detail', args=[9999])).status_code, 404)

    def test_no_hardcoded_old_product_name(self):
        org = person('Olive')
        event = mk_event(org)
        page = Client().get(reverse('events:detail', args=[event.pk])).content.decode()
        self.assertNotIn('Blood Lagbe', page)
