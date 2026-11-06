import datetime
from io import StringIO

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from moments import services
from moments.models import (
    Celebration,
    ImpactUpdate,
    ReminderPref,
    StreakFreeze,
    ThankNote,
)
from moments.rare import is_rare, rare_label
from rewards.models import (
    Badge,
    BloodRequest,
    Donation,
    RequestResponse,
    get_current_streak,
    get_eligibility,
)
from rewards.request_notify import close_and_notify

User = get_user_model()
PASSWORD = 'testpass123'
HOSPITAL = 'Moments Test Hospital'
PATIENT = 'Patientia'
REQUESTER_PHONE = '01977700001'
DONOR_PHONE = '01977700002'
OTHER_PHONE = '01977700003'
STRANGER_PHONE = '01977700004'
CONTACT_PHONE = '01977700099'


def make_user(phone, first='', last='', email=None, **profile):
    user = User.objects.create_user(phone=phone, password=PASSWORD)
    user.first_name, user.last_name = first, last
    user.email = email
    user.save()
    if profile:
        for name, value in profile.items():
            setattr(user.profile, name, value)
        user.profile.save()
    return user


def days_ago(n):
    return timezone.localdate() - datetime.timedelta(days=n)


def donate(user, ago, status=Donation.Status.APPROVED, request=None,
           group='A+'):
    return Donation.objects.create(
        donor=user, blood_group=group, district='Dhaka', location=HOSPITAL,
        donation_date=days_ago(ago), status=status, blood_request=request,
    )


def make_request(requester, units=2, **extra):
    fields = dict(
        blood_group='B+', hospital=HOSPITAL, district='Dhaka',
        units_needed=units, contact_phone=CONTACT_PHONE,
        patient_name=PATIENT,
        needed_by=timezone.localdate() + datetime.timedelta(days=3),
    )
    fields.update(extra)
    return BloodRequest.objects.create(requester=requester, **fields)


class Base(TestCase):
    def setUp(self):
        self.requester = make_user(
            REQUESTER_PHONE, 'Rina', 'Requester', 'rina@moments.test',
        )
        self.donor = make_user(
            DONOR_PHONE, 'Dana', 'Donorson', 'dana@moments.test',
        )
        self.other = make_user(
            OTHER_PHONE, 'Omar', 'Otherson', 'omar@moments.test',
        )
        self.stranger = make_user(
            STRANGER_PHONE, 'Sam', 'Strangerton', 'sam@moments.test',
        )
        self.request_obj = make_request(self.requester)
        RequestResponse.objects.create(
            blood_request=self.request_obj, donor=self.donor,
        )

    def login(self, user):
        self.client.force_login(user)

    def close_fulfilled(self, blood_request=None):
        blood_request = blood_request or self.request_obj
        blood_request.close(BloodRequest.Outcome.FULFILLED_HERE)
        return blood_request

    def hub(self, user=None):
        self.login(user or self.donor)
        return self.client.get(reverse('log-donation'))


# ------------------------------------------------------------ 1. impact

class ImpactUpdateTests(Base):
    def test_close_as_fulfilled_creates_one_update_per_responder(self):
        RequestResponse.objects.create(
            blood_request=self.request_obj, donor=self.other,
        )
        self.close_fulfilled()
        self.assertEqual(ImpactUpdate.objects.count(), 2)
        update = ImpactUpdate.objects.get(user=self.donor)
        self.assertEqual(update.blood_group, 'B+')
        self.assertEqual(update.district, 'Dhaka')

    def test_idempotent_across_repeated_saves(self):
        blood_request = self.close_fulfilled()
        blood_request.save()
        blood_request.save()
        services.deliver_impact_updates(blood_request)
        self.assertEqual(ImpactUpdate.objects.count(), 1)

    def test_other_outcomes_make_no_update(self):
        for outcome in (
            BloodRequest.Outcome.FOUND_ELSEWHERE,
            BloodRequest.Outcome.NO_LONGER_NEEDED, '',
        ):
            blood_request = make_request(self.requester)
            RequestResponse.objects.create(
                blood_request=blood_request, donor=self.donor,
            )
            blood_request.close(outcome)
        self.assertEqual(ImpactUpdate.objects.count(), 0)

    def test_open_request_makes_no_update(self):
        self.request_obj.save()
        self.assertEqual(ImpactUpdate.objects.count(), 0)

    def test_non_responder_gets_nothing(self):
        self.close_fulfilled()
        self.assertFalse(ImpactUpdate.objects.filter(user=self.other).exists())

    def bulk_close(self):
        # A raw UPDATE (admin bulk edit, shell) sends no post_save signal.
        BloodRequest.objects.filter(pk=self.request_obj.pk).update(
            status=BloodRequest.Status.CLOSED,
            outcome=BloodRequest.Outcome.FULFILLED_HERE,
            closed_at=timezone.now(),
        )

    def test_close_view_path_creates_the_update_at_once(self):
        # close_and_notify updates rows in bulk, then announces the close.
        close_and_notify(
            self.request_obj, BloodRequest.Outcome.FULFILLED_HERE,
            'http://testserver/x/',
        )
        self.assertEqual(ImpactUpdate.objects.count(), 1)

    def test_bulk_close_path_is_caught_on_hub_visit(self):
        self.bulk_close()
        self.assertEqual(ImpactUpdate.objects.count(), 0)
        response = self.hub()
        self.assertEqual(ImpactUpdate.objects.count(), 1)
        self.assertContains(response, 'Thank you for stepping up.')

    def test_hub_card_says_blood_reached_someone_after_donation(self):
        donate(self.donor, 5, request=self.request_obj)
        self.close_fulfilled()
        response = self.hub()
        self.assertContains(response, 'Your blood reached someone.')
        self.assertContains(response, 'B+ request in Dhaka')

    def test_card_has_no_patient_or_contact_details(self):
        self.close_fulfilled()
        response = self.hub()
        body = response.content.decode()
        panel = body[body.index('mo-hub-title'):]
        for secret in (
            PATIENT, HOSPITAL, CONTACT_PHONE, REQUESTER_PHONE,
            'rina@moments.test', 'Requester',
        ):
            self.assertNotIn(secret, panel)

    def test_email_only_when_opted_in(self):
        self.close_fulfilled()
        self.assertEqual(len(mail.outbox), 0)

    def test_opted_in_email_is_private_and_has_one_click_off(self):
        ReminderPref.objects.create(user=self.donor, impact_email=True)
        self.close_fulfilled()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['dana@moments.test'])
        text = message.subject + message.body
        for secret in (
            PATIENT, HOSPITAL, CONTACT_PHONE, REQUESTER_PHONE,
            'rina@moments.test', DONOR_PHONE,
        ):
            self.assertNotIn(secret, text)
        self.assertIn('B+', message.body)
        self.assertIn('/moments/off/', message.body)
        self.assertIn('List-Unsubscribe', message.extra_headers)
        self.assertIsNotNone(ImpactUpdate.objects.get().emailed_at)
        # a second save does not email again
        self.request_obj.save()
        self.assertEqual(len(mail.outbox), 1)

    def test_no_email_without_address(self):
        no_email = make_user('01977700010')
        RequestResponse.objects.create(
            blood_request=self.request_obj, donor=no_email,
        )
        ReminderPref.objects.create(user=no_email, impact_email=True)
        self.close_fulfilled()
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(ImpactUpdate.objects.filter(user=no_email).exists())

    def test_dismiss_hides_card_and_is_owner_only(self):
        self.close_fulfilled()
        update = ImpactUpdate.objects.get()
        self.login(self.stranger)
        response = self.client.post(
            reverse('moments:update-dismiss', args=[update.pk]),
        )
        self.assertEqual(response.status_code, 404)
        self.login(self.donor)
        self.client.post(reverse('moments:update-dismiss', args=[update.pk]))
        update.refresh_from_db()
        self.assertTrue(update.dismissed)
        self.assertNotContains(
            self.client.get(reverse('log-donation')),
            'was fulfilled.',
        )

    def test_command_sweeps_and_dry_run_changes_nothing(self):
        self.bulk_close()
        out = StringIO()
        call_command('send_impact_updates', '--dry-run', stdout=out)
        self.assertIn('1 update(s) would be created', out.getvalue())
        self.assertEqual(ImpactUpdate.objects.count(), 0)
        call_command('send_impact_updates', stdout=out)
        call_command('send_impact_updates', stdout=out)
        self.assertEqual(ImpactUpdate.objects.count(), 1)


# --------------------------------------------------------------- 2. thanks

class ThankNoteTests(Base):
    def setUp(self):
        super().setUp()
        RequestResponse.objects.create(
            blood_request=self.request_obj, donor=self.other,
        )
        self.close_fulfilled()
        self.url = reverse('moments:thank-send', args=[self.request_obj.pk])

    def send(self, user, recipient, text):
        self.login(user)
        return self.client.post(
            self.url, {'recipient': recipient, 'text': text},
        )

    def test_requester_thanks_one_responder(self):
        response = self.send(self.requester, self.donor.pk, 'Thank you!')
        self.assertEqual(response.status_code, 302)
        note = ThankNote.objects.get()
        self.assertEqual(note.recipient, self.donor)
        self.assertEqual(note.sender, self.requester)
        self.assertFalse(note.to_all)

    def test_one_note_per_responder(self):
        self.send(self.requester, self.donor.pk, 'First')
        self.send(self.requester, self.donor.pk, 'Second')
        self.assertEqual(ThankNote.objects.count(), 1)
        self.assertEqual(ThankNote.objects.get().text, 'First')

    def test_one_note_to_all_skips_already_thanked(self):
        self.send(self.requester, self.donor.pk, 'Just you')
        self.send(self.requester, 'all', 'Thanks everyone')
        self.assertEqual(ThankNote.objects.count(), 2)
        other_note = ThankNote.objects.get(recipient=self.other)
        self.assertTrue(other_note.to_all)
        self.assertEqual(
            ThankNote.objects.get(recipient=self.donor).text, 'Just you',
        )
        self.send(self.requester, 'all', 'Again')
        self.assertEqual(ThankNote.objects.count(), 2)

    def test_cannot_thank_a_non_responder(self):
        self.send(self.requester, self.stranger.pk, 'Hello')
        self.assertEqual(ThankNote.objects.count(), 0)
        self.send(self.requester, 'abc', 'Hello')
        self.assertEqual(ThankNote.objects.count(), 0)

    def test_stranger_staff_and_responder_cannot_send(self):
        staff = make_user('01977700011', staff=False)
        staff.staff = True
        staff.save()
        for user in (self.stranger, self.donor, staff):
            response = self.send(user, 'all', 'Sneaky')
            self.assertEqual(response.status_code, 403)
        self.assertEqual(ThankNote.objects.count(), 0)

    def test_anonymous_redirects_to_login(self):
        self.client.logout()
        response = self.client.post(self.url, {'recipient': 'all', 'text': 'x'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])
        self.assertEqual(ThankNote.objects.count(), 0)

    def test_get_is_not_allowed(self):
        self.login(self.requester)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_missing_request_is_404(self):
        self.login(self.requester)
        response = self.client.post(
            reverse('moments:thank-send', args=[99999]),
            {'recipient': 'all', 'text': 'x'},
        )
        self.assertEqual(response.status_code, 404)

    def test_open_request_cannot_be_thanked(self):
        open_request = make_request(self.requester)
        RequestResponse.objects.create(
            blood_request=open_request, donor=self.donor,
        )
        self.login(self.requester)
        self.client.post(
            reverse('moments:thank-send', args=[open_request.pk]),
            {'recipient': 'all', 'text': 'Early thanks'},
        )
        self.assertEqual(ThankNote.objects.count(), 0)

    def test_text_rules(self):
        too_long = 'x' * 201
        for bad in (
            '', '   ', too_long, 'Call me 01712345678',
            'call 0171 234 5678', 'write to me@example.com',
            'see https://spam.example', 'visit www.spam.example',
            'visit spam.com',
        ):
            self.send(self.requester, self.donor.pk, bad)
            self.assertEqual(ThankNote.objects.count(), 0, bad)
        self.send(self.requester, self.donor.pk, 'x' * 200)
        self.assertEqual(ThankNote.objects.count(), 1)

    def test_text_is_collapsed_to_one_line(self):
        self.send(self.requester, self.donor.pk, 'Thanks\n\n  a lot')
        self.assertEqual(ThankNote.objects.get().text, 'Thanks a lot')

    def test_note_is_escaped_on_hub_and_wall(self):
        self.send(
            self.requester, self.donor.pk, '<script>alert(1)</script> thanks',
        )
        self.login(self.donor)
        for name in ('log-donation', 'moments:thanks'):
            body = self.client.get(reverse(name)).content.decode()
            self.assertNotIn('<script>alert(1)</script>', body)
            self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', body)

    def test_wall_shows_requester_name_via_display_rules(self):
        self.send(self.requester, self.donor.pk, 'Thanks a lot')
        self.login(self.donor)
        body = self.client.get(reverse('moments:thanks')).content.decode()
        self.assertIn('Rina Requester', body)
        self.assertNotIn(REQUESTER_PHONE, body)
        self.assertNotIn('rina@moments.test', body)

    def test_opted_out_requester_is_not_named(self):
        self.requester.profile.show_on_leaderboard = False
        self.requester.profile.save()
        self.send(self.requester, self.donor.pk, 'Thanks a lot')
        self.login(self.donor)
        for name in ('log-donation', 'moments:thanks'):
            body = self.client.get(reverse(name)).content.decode()
            self.assertIn('A requester', body)
            self.assertNotIn('Rina', body)
        note = ThankNote.objects.get()
        self.assertEqual(note.sender_label, 'A requester')

    def test_nameless_requester_never_falls_back_to_phone(self):
        nameless = make_user('01977700012')
        blood_request = make_request(nameless)
        RequestResponse.objects.create(
            blood_request=blood_request, donor=self.donor,
        )
        blood_request.close(BloodRequest.Outcome.FULFILLED_HERE)
        services.send_thanks(blood_request, nameless, 'all', 'Thank you')
        note = ThankNote.objects.get(blood_request=blood_request)
        self.assertNotIn('01977700012', note.sender_label)
        self.assertTrue(note.sender_label.startswith('Donor #'))

    def test_hub_shows_latest_five_and_page_shows_all(self):
        for i in range(7):
            blood_request = make_request(self.requester)
            RequestResponse.objects.create(
                blood_request=blood_request, donor=self.donor,
            )
            blood_request.close(BloodRequest.Outcome.FULFILLED_HERE)
            services.send_thanks(
                blood_request, self.requester, 'all', f'Note number {i}',
            )
        self.assertEqual(len(services.thanks_for(self.donor, 5)), 5)
        self.assertEqual(services.thanks_for(self.donor).count(), 7)
        body = self.hub().content.decode()
        self.assertIn('Note number 6', body)
        self.assertNotIn('Note number 0', body)
        self.assertIn('2 more', body)
        wall = self.client.get(reverse('moments:thanks')).content.decode()
        self.assertIn('Note number 0', wall)

    def test_recipient_can_hide_a_note(self):
        self.send(self.requester, self.donor.pk, 'Visible for now')
        note = ThankNote.objects.get()
        hide = reverse('moments:note-hide', args=[note.pk])
        self.login(self.stranger)
        self.assertEqual(self.client.post(hide).status_code, 404)
        self.login(self.requester)
        self.assertEqual(self.client.post(hide).status_code, 404)
        note.refresh_from_db()
        self.assertFalse(note.hidden)
        self.login(self.donor)
        self.assertEqual(self.client.post(hide).status_code, 302)
        note.refresh_from_db()
        self.assertTrue(note.hidden)
        self.assertEqual(services.thanks_for(self.donor).count(), 0)
        self.assertNotContains(
            self.client.get(reverse('moments:thanks')), 'Visible for now',
        )
        # hiding does not free the slot: still one note per responder
        self.send(self.requester, self.donor.pk, 'Another try')
        self.assertEqual(ThankNote.objects.count(), 1)

    def test_hide_needs_post(self):
        self.send(self.requester, self.donor.pk, 'Hello')
        self.login(self.donor)
        url = reverse('moments:note-hide', args=[ThankNote.objects.get().pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_hide_ignores_offsite_next(self):
        self.send(self.requester, self.donor.pk, 'Hello')
        self.login(self.donor)
        url = reverse('moments:note-hide', args=[ThankNote.objects.get().pk])
        response = self.client.post(url, {'next': 'https://evil.example/'})
        self.assertEqual(response['Location'], reverse('moments:thanks'))

    def test_thanks_wall_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('moments:thanks'))
        self.assertEqual(response.status_code, 302)

    def test_wall_only_shows_own_notes(self):
        self.send(self.requester, self.donor.pk, 'For Dana only')
        self.login(self.other)
        self.assertNotContains(
            self.client.get(reverse('moments:thanks')), 'For Dana only',
        )

    def test_rate_limit_on_submissions(self):
        self.login(self.requester)
        for _ in range(20):
            self.client.post(self.url, {'recipient': 'all', 'text': ''})
        self.client.post(self.url, {'recipient': 'all', 'text': 'Thanks'})
        self.assertEqual(ThankNote.objects.count(), 0)

    def test_daily_cap(self):
        original = services.MAX_NOTES_PER_DAY
        services.MAX_NOTES_PER_DAY = 1
        try:
            self.send(self.requester, 'all', 'Thanks everyone')
        finally:
            services.MAX_NOTES_PER_DAY = original
        self.assertEqual(ThankNote.objects.count(), 0)

    def test_panel_only_for_requester_of_closed_request(self):
        page = reverse('request-detail', args=[self.request_obj.pk])
        self.login(self.requester)
        response = self.client.get(page)
        self.assertContains(response, 'Say thank you')
        self.assertContains(response, 'Dana Donorson')
        for viewer in (self.stranger, self.donor):
            self.login(viewer)
            self.assertNotContains(self.client.get(page), 'Say thank you')
        self.client.logout()
        self.assertNotContains(self.client.get(page), 'Say thank you')
        staff = make_user('01977700013')
        staff.staff = True
        staff.save()
        self.login(staff)
        self.assertNotContains(self.client.get(page), 'Say thank you')

    def test_panel_hidden_while_request_is_open(self):
        open_request = make_request(self.requester)
        self.login(self.requester)
        response = self.client.get(
            reverse('request-detail', args=[open_request.pk]),
        )
        self.assertNotContains(response, 'Say thank you')


# ---------------------------------------------------------- 3. celebration

class CelebrationTests(Base):
    def test_first_approved_donation_celebrates_once(self):
        donate(self.donor, 10)
        first = self.hub().content.decode()
        self.assertIn('data-mo-celebrate', first)
        self.assertIn('Share my card', first)
        self.assertIn(
            reverse('badge-card', args=[self.donor.pk]), first,
        )
        second = self.client.get(reverse('log-donation')).content.decode()
        self.assertNotIn('data-mo-celebrate', second)
        self.assertTrue(
            Celebration.objects.filter(
                user=self.donor, key='first_donation',
            ).exists()
        )

    def test_pending_donation_does_not_celebrate(self):
        donate(self.other, 10, status=Donation.Status.PENDING)
        self.assertNotContains(self.hub(self.other), 'data-mo-celebrate')

    def test_new_tier_celebrates(self):
        badge = Badge.objects.filter(badge_type=Badge.Type.TIER).first()
        if badge is None:
            badge = Badge.objects.create(
                name='Test Tier', badge_type=Badge.Type.TIER,
                min_donations=1,
            )
        for i in range(badge.min_donations):
            donate(self.donor, 300 + i * 130)
        response = self.hub()
        self.assertContains(response, f'You reached {badge.name}')
        self.assertTrue(
            Celebration.objects.filter(
                user=self.donor, key=f'tier:{badge.pk}',
            ).exists()
        )
        self.assertNotContains(
            self.client.get(reverse('log-donation')), 'data-mo-celebrate',
        )

    def test_first_pledge_celebrates_once(self):
        response = self.hub()
        self.assertContains(response, 'You offered to help for the first time')
        self.assertNotContains(
            self.client.get(reverse('log-donation')), 'data-mo-celebrate',
        )

    def test_old_achievements_are_marked_seen_without_replay(self):
        donation = donate(self.donor, 400)
        Donation.objects.filter(pk=donation.pk).update(
            updated_at=timezone.now() - datetime.timedelta(days=200),
        )
        RequestResponse.objects.filter(donor=self.donor).update(
            created_at=timezone.now() - datetime.timedelta(days=200),
        )
        self.assertNotContains(self.hub(), 'data-mo-celebrate')
        self.assertTrue(
            Celebration.objects.filter(
                user=self.donor, key='first_donation',
            ).exists()
        )

    def test_nothing_for_other_viewers(self):
        donate(self.donor, 10)
        self.assertNotContains(self.hub(self.stranger), 'data-mo-celebrate')

    def test_script_honours_reduced_motion_focus_and_escape(self):
        from django.contrib.staticfiles import finders
        path = finders.find('moments/celebrate.js')
        source = open(path, encoding='utf-8').read()
        self.assertIn('prefers-reduced-motion', source)
        self.assertIn('Escape', source)
        self.assertIn("'Tab'", source)
        css = open(
            finders.find('moments/moments.css'), encoding='utf-8',
        ).read()
        self.assertIn('prefers-reduced-motion', css)


# -------------------------------------------------------------- 4. ready

class ReadyAgainTests(Base):
    def test_none_without_a_donation(self):
        self.assertIsNone(services.ready_in_days(self.donor))

    def test_counts_from_last_donation_with_site_rule(self):
        donate(self.donor, 20)
        expected = settings.DONATION_INTERVAL_DAYS - 20
        self.assertEqual(services.ready_in_days(self.donor), expected)
        self.assertEqual(
            services.ready_in_days(self.donor),
            get_eligibility(self.donor).days_left,
        )

    def test_pending_counts_and_rejected_does_not(self):
        donate(self.donor, 200, status=Donation.Status.APPROVED)
        self.assertEqual(services.ready_in_days(self.donor), 0)
        donate(self.donor, 5, status=Donation.Status.PENDING)
        self.assertEqual(
            services.ready_in_days(self.donor),
            settings.DONATION_INTERVAL_DAYS - 5,
        )
        Donation.objects.filter(status=Donation.Status.PENDING).update(
            status=Donation.Status.REJECTED,
        )
        self.assertEqual(services.ready_in_days(self.donor), 0)

    def test_hub_shows_countdown_and_safety_copy(self):
        donate(self.donor, 20)
        response = self.hub()
        days = settings.DONATION_INTERVAL_DAYS - 20
        self.assertContains(response, f'Ready again in {days} days')
        self.assertContains(response, 'not medical clearance')

    def test_hub_when_ready(self):
        donate(self.donor, settings.DONATION_INTERVAL_DAYS + 1)
        self.assertContains(self.hub(), 'Your recovery interval is over.')


class ReminderTests(Base):
    def run_command(self, *args):
        out = StringIO()
        call_command('send_ready_reminders', *args, stdout=out)
        return out.getvalue()

    def opt_in(self, user=None, **extra):
        return ReminderPref.objects.update_or_create(
            user=user or self.donor,
            defaults=dict(email_enabled=True, **extra),
        )[0]

    def test_default_is_off(self):
        self.assertFalse(services.get_pref(self.donor).email_enabled)
        self.assertFalse(services.get_pref(self.donor).impact_email)

    def test_not_sent_when_not_opted_in(self):
        donate(self.donor, settings.DONATION_INTERVAL_DAYS + 1)
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_sends_once_per_cycle_with_safe_copy(self):
        donate(self.donor, settings.DONATION_INTERVAL_DAYS + 1)
        self.opt_in()
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['dana@moments.test'])
        self.assertIn('not medical clearance', message.body)
        self.assertIn('/moments/off/', message.body)
        self.assertIn('List-Unsubscribe', message.extra_headers)
        self.assertNotIn(DONOR_PHONE, message.body)
        self.run_command()
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)

    def test_next_cycle_gets_its_own_reminder(self):
        first = donate(self.donor, settings.DONATION_INTERVAL_DAYS + 1)
        self.opt_in()
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        first.delete()
        donate(self.donor, settings.DONATION_INTERVAL_DAYS + 2)
        self.run_command()
        self.assertEqual(len(mail.outbox), 2)

    def test_dry_run_sends_nothing_and_claims_nothing(self):
        donate(self.donor, settings.DONATION_INTERVAL_DAYS + 1)
        self.opt_in()
        output = self.run_command('--dry-run')
        self.assertIn('1 reminder(s) would be sent', output)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(
            ReminderPref.objects.get(user=self.donor).last_reminded_cycle,
        )
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)

    def test_not_when_still_recovering(self):
        donate(self.donor, 30)
        self.opt_in()
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_not_when_pending_donation_is_recent(self):
        donate(self.donor, settings.DONATION_INTERVAL_DAYS + 30)
        donate(self.donor, 3, status=Donation.Status.PENDING)
        self.opt_in()
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_not_when_ready_long_ago(self):
        donate(self.donor, settings.DONATION_INTERVAL_DAYS + 90)
        self.opt_in()
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_not_without_donation_or_email(self):
        self.opt_in()
        no_email = make_user('01977700014')
        donate(no_email, settings.DONATION_INTERVAL_DAYS + 1)
        self.opt_in(no_email)
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_one_click_off_without_login(self):
        self.opt_in(impact_email=True)
        url = services.off_url(self.donor, 'ready')
        path = url[url.index('/moments/'):]
        self.client.logout()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'no more emails')
        pref = ReminderPref.objects.get(user=self.donor)
        self.assertFalse(pref.email_enabled)
        self.assertTrue(pref.impact_email)  # only the matching kind
        donate(self.donor, settings.DONATION_INTERVAL_DAYS + 1)
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_off_link_post_and_impact_kind(self):
        self.opt_in(impact_email=True)
        url = services.off_url(self.donor, 'impact')
        path = url[url.index('/moments/'):]
        self.assertEqual(self.client.post(path).status_code, 200)
        pref = ReminderPref.objects.get(user=self.donor)
        self.assertFalse(pref.impact_email)
        self.assertTrue(pref.email_enabled)

    def test_bad_tokens_are_404_and_change_nothing(self):
        pref = self.opt_in()
        token = services.make_off_token(self.donor, 'ready')
        for bad in (
            'garbage', token[:-3] + 'abc', token + 'x',
            services.signing.dumps({'u': self.donor.pk, 'k': 'ready'}),
            services.signing.dumps(
                {'u': self.donor.pk, 'k': 'other'}, salt=services.OFF_SALT,
            ),
        ):
            response = self.client.get(reverse('moments:reminder-off', args=[bad]))
            self.assertEqual(response.status_code, 404)
        pref.refresh_from_db()
        self.assertTrue(pref.email_enabled)

    def test_token_for_one_user_cannot_switch_off_another(self):
        self.opt_in()
        self.opt_in(self.other)
        self.client.get(
            reverse(
                'moments:reminder-off',
                args=[services.make_off_token(self.other, 'ready')],
            )
        )
        self.assertTrue(ReminderPref.objects.get(user=self.donor).email_enabled)
        self.assertFalse(ReminderPref.objects.get(user=self.other).email_enabled)

    def test_prefs_form(self):
        self.login(self.donor)
        self.client.post(
            reverse('moments:prefs'),
            {'email_enabled': 'on', 'impact_email': 'on'},
        )
        pref = ReminderPref.objects.get(user=self.donor)
        self.assertTrue(pref.email_enabled and pref.impact_email)
        self.client.post(reverse('moments:prefs'), {})
        pref.refresh_from_db()
        self.assertFalse(pref.email_enabled or pref.impact_email)

    def test_prefs_need_an_email_address(self):
        nobody = make_user('01977700015')
        self.login(nobody)
        self.client.post(reverse('moments:prefs'), {'email_enabled': 'on'})
        self.assertFalse(ReminderPref.objects.get(user=nobody).email_enabled)

    def test_prefs_require_login_and_post(self):
        self.assertEqual(
            self.client.post(reverse('moments:prefs'), {}).status_code, 302,
        )
        self.login(self.donor)
        self.assertEqual(self.client.get(reverse('moments:prefs')).status_code, 405)


# ---------------------------------------------------------- 5. streak freeze

class StreakFreezeTests(Base):
    def broken_streak(self, user=None):
        """Streak of 2 (before), a long gap, then 2 more. Without a freeze
        the current streak is 2; with one it is 4."""
        user = user or self.donor
        for ago in (500, 380, 180, 50):
            donate(user, ago)
        return user

    def test_no_freeze_leaves_streak_unchanged(self):
        self.broken_streak()
        self.assertEqual(get_current_streak(self.donor), 2)

    def test_freeze_saves_the_streak(self):
        self.broken_streak()
        freeze, error = services.activate_freeze(self.donor)
        self.assertEqual(error, '')
        self.assertEqual(freeze.gap_start, days_ago(380))
        self.assertEqual(freeze.gap_end, days_ago(180))
        self.assertEqual(get_current_streak(self.donor), 4)

    def test_offer_describes_the_save(self):
        self.broken_streak()
        offer = services.freeze_offer(self.donor)
        self.assertEqual((offer['current'], offer['restored']), (2, 4))

    def test_one_per_year(self):
        # two broken gaps; only the latest can be frozen, and only once
        for ago in (900, 780, 600, 480, 300, 180, 50):
            donate(self.donor, ago)
        _, error = services.activate_freeze(self.donor)
        self.assertEqual(error, '')
        self.assertIsNone(services.freeze_offer(self.donor))
        _, error = services.activate_freeze(self.donor)
        self.assertIn('already used', error)
        self.assertEqual(StreakFreeze.objects.count(), 1)

    def test_a_new_year_allows_another_freeze(self):
        self.broken_streak()
        StreakFreeze.objects.create(
            user=self.donor, year=timezone.localdate().year - 1,
            gap_start=days_ago(900), gap_end=days_ago(850),
        )
        self.assertIsNotNone(services.freeze_offer(self.donor))

    def test_cannot_freeze_when_not_needed(self):
        for ago in (260, 140, 20):  # every gap within cycle + grace
            donate(self.donor, ago)
        self.assertIsNone(services.freeze_offer(self.donor))
        freeze, error = services.activate_freeze(self.donor)
        self.assertIsNone(freeze)
        self.assertIn('would not save', error)
        self.assertEqual(StreakFreeze.objects.count(), 0)

    def test_cannot_freeze_without_donations(self):
        self.assertIsNone(services.freeze_offer(self.donor))

    def test_not_offered_when_it_would_not_restore_a_streak(self):
        for ago in (400, 100):  # one donation, a long gap, one donation
            donate(self.donor, ago)
        self.assertIsNone(services.freeze_offer(self.donor))

    def test_not_offered_when_the_streak_is_already_lapsed(self):
        for ago in (700, 580, 200):  # last donation is too old: streak 0
            donate(self.donor, ago)
        self.assertEqual(get_current_streak(self.donor), 0)
        self.assertIsNone(services.freeze_offer(self.donor))

    def test_pending_and_rejected_donations_are_ignored(self):
        for ago in (500, 380):
            donate(self.donor, ago)
        donate(self.donor, 180, status=Donation.Status.PENDING)
        donate(self.donor, 50, status=Donation.Status.REJECTED)
        self.assertIsNone(services.freeze_offer(self.donor))

    def test_cannot_stack_by_double_post(self):
        self.broken_streak()
        self.login(self.donor)
        url = reverse('moments:freeze')
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(StreakFreeze.objects.count(), 1)
        self.assertEqual(get_current_streak(self.donor), 4)

    def test_freeze_never_changes_when_you_can_donate(self):
        self.broken_streak()
        donate(self.donor, 5, status=Donation.Status.PENDING)
        before = get_eligibility(self.donor)
        services.activate_freeze(self.donor)
        after = get_eligibility(self.donor)
        self.assertEqual(before, after)
        self.assertFalse(after.eligible)
        self.assertEqual(
            services.ready_in_days(self.donor),
            settings.DONATION_INTERVAL_DAYS - 5,
        )

    def test_freeze_is_per_user(self):
        self.broken_streak()
        self.broken_streak(self.other)
        services.activate_freeze(self.donor)
        self.assertEqual(get_current_streak(self.other), 2)
        self.assertIsNotNone(services.freeze_offer(self.other))

    def test_hub_button_only_when_offered(self):
        self.login(self.donor)
        self.assertNotContains(
            self.client.get(reverse('log-donation')), 'Use my streak freeze',
        )
        self.broken_streak()
        response = self.client.get(reverse('log-donation'))
        self.assertContains(response, 'Use my streak freeze')
        self.assertContains(response, 'never changes when you can donate')
        self.client.post(reverse('moments:freeze'))
        self.assertNotContains(
            self.client.get(reverse('log-donation')), 'Use my streak freeze',
        )

    def test_freeze_endpoint_rules(self):
        url = reverse('moments:freeze')
        self.assertEqual(self.client.post(url).status_code, 302)  # login
        self.login(self.donor)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)  # nothing to freeze: friendly, no crash
        self.assertEqual(StreakFreeze.objects.count(), 0)

    def test_streak_query_cost_is_unchanged_without_a_gap(self):
        for ago in (260, 140, 20):
            donate(self.donor, ago)
        with self.assertNumQueries(1):
            get_current_streak(self.donor)


# ------------------------------------------------------------- 6. pulse

class PulseTests(Base):
    def pulse_url(self, pk=None):
        return reverse('moments:pulse', args=[pk or self.request_obj.pk])

    def test_counts_only(self):
        response = self.client.get(self.pulse_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'people': 1, 'bags_pledged': 1, 'still_needed': 1, 'open': True},
        )
        text = response.content.decode()
        for secret in ('Dana', 'Donorson', DONOR_PHONE, 'dana@moments.test'):
            self.assertNotIn(secret, text)

    def test_pledges_never_exceed_bags_needed(self):
        for user in (self.other, self.stranger):
            RequestResponse.objects.create(
                blood_request=self.request_obj, donor=user,
            )
        data = self.client.get(self.pulse_url()).json()
        self.assertEqual(data['people'], 3)
        self.assertEqual(data['bags_pledged'], 2)
        self.assertEqual(data['still_needed'], 0)

    def test_reports_closed(self):
        self.request_obj.close(BloodRequest.Outcome.FULFILLED_HERE)
        self.assertFalse(self.client.get(self.pulse_url()).json()['open'])

    def test_missing_request_is_404(self):
        self.assertEqual(self.client.get(self.pulse_url(99999)).status_code, 404)

    def test_is_cached_briefly(self):
        self.client.get(self.pulse_url())
        RequestResponse.objects.create(
            blood_request=self.request_obj, donor=self.other,
        )
        self.assertEqual(self.client.get(self.pulse_url()).json()['people'], 1)
        cache.clear()
        self.assertEqual(self.client.get(self.pulse_url()).json()['people'], 2)

    def test_is_rate_limited(self):
        for _ in range(30):
            self.assertEqual(self.client.get(self.pulse_url()).status_code, 200)
        response = self.client.get(self.pulse_url())
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)

    def test_post_not_allowed(self):
        # read-only endpoint: nothing to change, but a POST must not 500
        response = self.client.post(self.pulse_url())
        self.assertLess(response.status_code, 500)

    def test_request_page_has_aria_live_line_for_open_requests(self):
        page = reverse('request-detail', args=[self.request_obj.pk])
        response = self.client.get(page)
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'data-mo-pulse')
        self.assertContains(response, '1 person responding')
        self.assertContains(response, 'moments/pulse.js')

    def test_closed_request_has_no_live_line(self):
        self.request_obj.close(BloodRequest.Outcome.FULFILLED_HERE)
        response = self.client.get(
            reverse('request-detail', args=[self.request_obj.pk]),
        )
        self.assertNotContains(response, 'data-mo-pulse')

    def test_script_polls_every_30s_and_pauses_when_hidden(self):
        from django.contrib.staticfiles import finders
        source = open(
            finders.find('moments/pulse.js'), encoding='utf-8',
        ).read()
        self.assertIn('30000', source)
        self.assertIn('document.hidden', source)
        self.assertIn('visibilitychange', source)


# --------------------------------------------------------------- 7. rare

class RareTests(Base):
    def test_is_rare(self):
        for group in ('O-', 'AB-', 'o-', ' AB- '):
            self.assertTrue(is_rare(group), group)
        for group in ('O+', 'AB+', 'A-', 'B-', 'A+', 'B+', '', None):
            self.assertFalse(is_rare(group), group)

    def test_label_needs_rare_group_and_approved_donation(self):
        self.donor.profile.blood_group = 'O-'
        self.donor.profile.save()
        self.assertEqual(rare_label(self.donor), '')
        donate(self.donor, 10, status=Donation.Status.PENDING)
        self.assertEqual(rare_label(self.donor), '')
        donate(self.donor, 200, group='O-')
        self.assertEqual(rare_label(self.donor), 'Rare hero')

    def test_common_group_gets_no_label(self):
        self.donor.profile.blood_group = 'A+'
        self.donor.profile.save()
        donate(self.donor, 10)
        self.assertEqual(rare_label(self.donor), '')

    def test_hub_shows_label_and_creates_no_badge(self):
        self.donor.profile.blood_group = 'AB-'
        self.donor.profile.save()
        donate(self.donor, 10)
        badges = Badge.objects.count()
        self.assertContains(self.hub(), 'Rare hero')
        self.assertEqual(Badge.objects.count(), badges)


# ------------------------------------------------- hub, privacy and admin

class HubPanelTests(Base):
    def test_empty_for_a_member_with_nothing_yet(self):
        body = self.hub(self.stranger).content.decode()
        self.assertNotIn('mo-hub-title', body)

    def test_anonymous_hub_redirects_and_panel_renders_nothing(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse('log-donation')).status_code, 302,
        )

    def test_reminder_options_need_a_logged_donation(self):
        donate(self.donor, 20)
        self.assertContains(self.hub(), 'Remind me when I am ready again')

    def test_site_name_not_hardcoded_in_emails(self):
        ReminderPref.objects.create(user=self.donor, impact_email=True)
        with self.settings(SITE_NAME='Zed Network'):
            self.close_fulfilled()
        message = mail.outbox[0]
        self.assertIn('Zed Network', message.subject + message.body)
        self.assertNotIn('BloodBond', message.subject + message.body)

    def test_no_phone_numbers_on_request_page_panel(self):
        self.request_obj.close(BloodRequest.Outcome.FULFILLED_HERE)
        self.login(self.requester)
        body = self.client.get(
            reverse('request-detail', args=[self.request_obj.pk]),
        ).content.decode()
        start = body.index('mo-thank-title')
        panel = body[start:start + 2500]
        self.assertNotIn(DONOR_PHONE, panel)


class AdminTests(TestCase):
    def test_all_models_registered_with_list_display(self):
        for model in (
            ImpactUpdate, ThankNote, ReminderPref, Celebration, StreakFreeze,
        ):
            self.assertIn(model, admin.site._registry)
            self.assertTrue(admin.site._registry[model].list_display)

    def test_admin_changelists_render(self):
        staff = make_user('01977700020')
        staff.staff = True
        staff.admin = True
        staff.save()
        self.client.force_login(staff)
        for name in (
            'impactupdate', 'thanknote', 'reminderpref', 'celebration',
            'streakfreeze',
        ):
            response = self.client.get(f'/admin/moments/{name}/')
            self.assertEqual(response.status_code, 200, name)
