"""Regression tests from an end-to-end QA pass over the logged-in flows:
double submits, sloppy dates, consent edge cases, invite links that must
survive a login, and the admin site."""
import datetime
from unittest import mock

from django.contrib import admin as django_admin
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile
from rewards.models import (
    BloodRequest,
    ContactRequest,
    Donation,
    RequestReport,
    RequestResponse,
)
from rewards.request_notify import close_and_notify
from rewards.test_game_loop import donate, make_request
from rewards.tests import make_user
from squads.models import Squad
from squads.services import create_squad
from stories.models import Story

_n = iter(range(1, 100000))


def member(name='Pat', **profile):
    user = make_user(phone=f'0178{next(_n):07d}', first_name=name)
    for key, value in profile.items():
        setattr(user.profile, key, value)
    if profile:
        user.profile.save()
    return user


def listed(name, group='O+', district='Dhaka', **extra):
    return member(
        name, in_directory=True, blood_group=group, district=district,
        **extra,
    )


class DonationDoubleSubmitTests(TestCase):
    def setUp(self):
        self.user = member('Dina')
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def post(self, **overrides):
        data = {
            'blood_group': 'A+', 'donation_date': self.today.isoformat(),
            'district': 'Dhaka', 'place': '', 'location': 'Somewhere',
        }
        data.update(overrides)
        return self.client.post(reverse('log-donation'), data)

    def test_same_date_twice_creates_one_donation(self):
        self.assertEqual(self.post().status_code, 302)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already logged a donation on this date')
        self.assertEqual(Donation.objects.filter(donor=self.user).count(), 1)

    def test_error_keeps_what_was_typed(self):
        self.post()
        response = self.post(location='Typed place')
        self.assertContains(response, 'Typed place')

    def test_a_rejected_donation_does_not_block_the_date(self):
        Donation.objects.create(
            donor=self.user, blood_group='A+', donation_date=self.today,
            location='X', status=Donation.Status.REJECTED,
        )
        self.assertEqual(self.post().status_code, 302)

    def test_other_donors_same_date_is_fine(self):
        donate(member('Eli'), days_ago=0, status='pending')
        self.assertEqual(self.post().status_code, 302)

    def test_absurdly_old_date_is_rejected(self):
        response = self.post(donation_date='1900-01-01')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too long ago')
        self.assertFalse(Donation.objects.filter(donor=self.user).exists())


class RequestFormSanityTests(TestCase):
    def setUp(self):
        self.user = member('Rina')
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def data(self, **overrides):
        data = {
            'blood_group': 'A+', 'district': 'Dhaka', 'place': '',
            'hospital': 'Square', 'units_needed': '1',
            'needed_by': self.today.isoformat(),
            'contact_phone': '01712345678', 'patient_name': '', 'note': '',
        }
        data.update(overrides)
        return data

    def test_far_future_date_is_rejected(self):
        response = self.client.post(
            reverse('request-create'), self.data(needed_by='9999-12-31'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'within the next')
        self.assertEqual(BloodRequest.objects.count(), 0)

    def test_two_months_ahead_is_still_fine(self):
        later = (self.today + datetime.timedelta(days=60)).isoformat()
        response = self.client.post(
            reverse('request-create'), self.data(needed_by=later),
        )
        self.assertEqual(response.status_code, 302)

    def test_date_picker_gets_a_max(self):
        response = self.client.get(reverse('request-create'))
        limit = (self.today + datetime.timedelta(days=60)).isoformat()
        self.assertContains(response, f'max="{limit}"')

    def test_double_submit_creates_one_request(self):
        url = reverse('request-create')
        self.assertEqual(self.client.post(url, self.data()).status_code, 302)
        response = self.client.post(url, self.data(hospital='  SQUARE '))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already posted this request')
        self.assertEqual(BloodRequest.objects.count(), 1)

    def test_a_different_request_is_not_a_duplicate(self):
        url = reverse('request-create')
        self.client.post(url, self.data())
        self.assertEqual(
            self.client.post(url, self.data(blood_group='B+')).status_code,
            302,
        )
        self.assertEqual(BloodRequest.objects.count(), 2)

    def test_closed_request_does_not_block_posting_it_again(self):
        url = reverse('request-create')
        self.client.post(url, self.data())
        BloodRequest.objects.get().close('no_longer_needed')
        self.assertEqual(self.client.post(url, self.data()).status_code, 302)


class FlaggedRequestOwnerTests(TestCase):
    def setUp(self):
        self.owner = member('Owen')
        self.req = make_request(self.owner)
        for i in range(3):
            RequestReport.objects.create(
                blood_request=self.req, reporter=member(f'R{i}'),
                reason='spam',
            )
        self.client.force_login(self.owner)

    def test_owner_is_told_it_is_hidden_from_the_board(self):
        response = self.client.get(
            reverse('request-detail', args=[self.req.pk]),
        )
        self.assertContains(response, 'hidden from the public board')

    def test_no_such_notice_once_closed(self):
        self.req.close('found_elsewhere')
        response = self.client.get(
            reverse('request-detail', args=[self.req.pk]),
        )
        self.assertNotContains(response, 'hidden from the public board')

    def test_hub_and_welcome_do_not_count_hidden_requests(self):
        donor = member('Dora', blood_group='A+', district='Dhaka')
        self.client.force_login(donor)
        hub = self.client.get(reverse('log-donation'))
        self.assertEqual(hub.context['urgent_matches'], 0)
        welcome = self.client.get(reverse('welcome'))
        step = next(s for s in welcome.context['steps'] if s['key'] == 'requests')
        self.assertIn('No open requests', step['hint'])


class CloseOnceTests(TestCase):
    def test_two_stale_copies_email_responders_once(self):
        owner = member('Olga')
        req = make_request(owner)
        donor = member('Dan')
        donor.email = 'dan@example.com'
        donor.save()
        RequestResponse.objects.create(blood_request=req, donor=donor)
        first = BloodRequest.objects.get(pk=req.pk)
        second = BloodRequest.objects.get(pk=req.pk)
        mail.outbox.clear()
        self.assertTrue(close_and_notify(first, 'found_elsewhere', 'http://x/'))
        # `second` still believes the request is open (a double click, or an
        # admin acting at the same moment).
        self.assertFalse(close_and_notify(second, 'no_longer_needed', 'http://x/'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            BloodRequest.objects.get(pk=req.pk).outcome, 'found_elsewhere',
        )


class ContactEdgeTests(TestCase):
    def setUp(self):
        self.asker = member('Asha')
        self.donor = listed('Dev')
        self.client.force_login(self.asker)

    def ask(self, **extra):
        return self.client.post(
            reverse('contact-donor', args=[self.donor.pk]),
            {'message': 'please', **extra},
        )

    def test_cannot_ask_again_after_accept(self):
        ContactRequest.objects.create(
            sender=self.asker, donor=self.donor, status='accepted',
        )
        self.ask()
        self.assertEqual(ContactRequest.objects.count(), 1)

    def test_directory_keeps_showing_the_number_after_a_second_try(self):
        ContactRequest.objects.create(
            sender=self.asker, donor=self.donor, status='accepted',
            answered_at=timezone.now(),
        )
        self.ask()
        response = self.client.get(reverse('donor-directory'))
        self.assertContains(response, self.donor.phone)

    def test_race_on_the_pending_constraint_is_not_a_500(self):
        ContactRequest.objects.create(sender=self.asker, donor=self.donor)
        # The pre-check passed for both clicks; the database stops the second.
        with mock.patch(
            'rewards.directory_views.contact_block_reason', return_value=None,
        ):
            response = self.ask()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactRequest.objects.count(), 1)

    def test_phone_stays_hidden_until_accepted(self):
        ContactRequest.objects.create(sender=self.asker, donor=self.donor)
        for name in ('donor-directory', 'contact-inbox'):
            response = self.client.get(reverse(name))
            self.assertNotContains(response, self.donor.phone)


class InviteLoginRoundTripTests(TestCase):
    def setUp(self):
        self.owner = member('Ola')
        self.squad = create_squad(self.owner, name='Round Trip', is_public=True)
        self.invite = reverse('squads:join-code', args=[self.squad.join_code])

    def test_anonymous_invite_links_carry_next(self):
        response = self.client.get(self.invite)
        self.assertContains(response, f'/accounts/login/?next={self.invite}')
        self.assertContains(response, f'/accounts/signup/?next={self.invite}')

    def test_anonymous_public_squad_page_login_link_carries_next(self):
        url = reverse('squads:detail', args=[self.squad.pk])
        response = self.client.get(url)
        self.assertContains(response, f'/accounts/login/?next={url}')

    def test_login_with_next_comes_back_to_the_invite(self):
        user = member('Lou')
        user.set_password('pw-12345-pw')
        user.save()
        response = self.client.post(
            reverse('login') + f'?next={self.invite}',
            {'phone': user.phone, 'password': 'pw-12345-pw',
             'next': self.invite},
        )
        self.assertRedirects(
            response, self.invite, fetch_redirect_response=False,
        )

    def test_login_page_signup_link_keeps_next(self):
        response = self.client.get(reverse('login') + f'?next={self.invite}')
        self.assertContains(response, f'/accounts/signup/?next={self.invite}')

    def test_signup_page_keeps_next_in_form_and_login_link(self):
        response = self.client.get(reverse('signup') + f'?next={self.invite}')
        self.assertContains(
            response, f'<input type="hidden" name="next" value="{self.invite}">',
        )
        self.assertContains(response, f'/accounts/login/?next={self.invite}')

    def test_signup_with_next_lands_on_the_invite(self):
        response = self.client.post(reverse('signup'), {
            'first_name': 'New', 'phone': '01788880001', 'email': '',
            'password1': 'Zx9-qa-pass-4471', 'password2': 'Zx9-qa-pass-4471',
            'blood_group': '', 'district': '', 'next': self.invite,
        })
        self.assertRedirects(
            response, self.invite, fetch_redirect_response=False,
        )

    def test_signup_without_next_still_gets_the_checklist(self):
        response = self.client.post(reverse('signup'), {
            'first_name': 'New', 'phone': '01788880002', 'email': '',
            'password1': 'Zx9-qa-pass-4471', 'password2': 'Zx9-qa-pass-4471',
            'blood_group': '', 'district': '',
        })
        self.assertRedirects(
            response, reverse('welcome'), fetch_redirect_response=False,
        )

    def test_signup_ignores_an_offsite_next(self):
        response = self.client.post(reverse('signup'), {
            'first_name': 'New', 'phone': '01788880003', 'email': '',
            'password1': 'Zx9-qa-pass-4471', 'password2': 'Zx9-qa-pass-4471',
            'next': 'https://evil.example/steal',
        })
        self.assertRedirects(
            response, reverse('welcome'), fetch_redirect_response=False,
        )

    def test_logged_in_visitor_of_signup_goes_to_next(self):
        self.client.force_login(member('Already'))
        response = self.client.get(reverse('signup') + f'?next={self.invite}')
        self.assertRedirects(
            response, self.invite, fetch_redirect_response=False,
        )

    def test_anonymous_join_post_keeps_a_tricky_code_intact_in_next(self):
        # %23 is "#": left unquoted it would cut the next target short.
        response = self.client.post('/squads/join/a%23b/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=/squads/join/a%23b/', response['Location'])


class DoubleCreateTests(TestCase):
    def setUp(self):
        self.user = member('Cleo')
        self.client.force_login(self.user)

    def test_squad_double_submit_makes_one_squad(self):
        data = {'name': 'Twin Squad', 'description': '', 'district': ''}
        url = reverse('squads:create')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        response = self.client.post(url, {**data, 'name': ' twin  SQUAD '})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already have a squad with this name')
        self.assertEqual(Squad.objects.count(), 1)

    def test_another_owner_may_use_the_same_private_name(self):
        Squad.objects.create(owner=member('Other'), name='Shared Name')
        response = self.client.post(
            reverse('squads:create'),
            {'name': 'Shared Name', 'description': '', 'district': ''},
        )
        self.assertEqual(response.status_code, 302)

    def test_story_double_submit_makes_one_story(self):
        data = {
            'kind': 'donor', 'title': 'My first time',
            'body': 'I gave blood for the first time and it went well. ' * 2,
        }
        url = reverse('story-create')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already shared this story')
        self.assertEqual(Story.objects.count(), 1)

    def test_a_different_story_is_fine(self):
        data = {
            'kind': 'donor', 'title': 'My first time',
            'body': 'I gave blood for the first time and it went well. ' * 2,
        }
        url = reverse('story-create')
        self.client.post(url, data)
        self.assertEqual(
            self.client.post(url, {**data, 'title': 'My second time'}).status_code,
            302,
        )


class ProfileStrTests(TestCase):
    def test_profile_without_email_has_a_text_label(self):
        user = member('Nomail')
        user.first_name, user.last_name, user.email = 'No', 'Mail', None
        user.save()
        self.assertEqual(str(Profile.objects.get(user=user)), 'No Mail')

    def test_profile_label_never_falls_back_to_the_phone(self):
        user = member()
        user.first_name = user.last_name = user.email = None
        user.save()
        label = str(Profile.objects.get(user=user))
        self.assertTrue(label)
        self.assertNotIn(user.phone, label)

    def test_user_without_phone_still_has_a_label(self):
        user = member()
        user.phone = None
        user.save()
        self.assertEqual(str(user), f'User #{user.pk}')


class AdminSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from accounts.models import User
        cls.boss = User.objects.create_superuser('01788889999', 'pw-12345-pw')
        cls.boss.first_name = 'Boss'
        cls.boss.save()
        cls.donor = member('Dolly')
        cls.donor.email = 'dolly@example.com'
        cls.donor.save()
        cls.donor2 = member('Dan')
        cls.pending = Donation.objects.create(
            donor=cls.donor, blood_group='A+', location='Bank',
            donation_date=timezone.localdate(), status='pending',
        )
        cls.pending2 = Donation.objects.create(
            donor=cls.donor2, blood_group='A+', location='Bank',
            donation_date=timezone.localdate(), status='pending',
        )
        cls.req = make_request(cls.donor)
        cls.contact = ContactRequest.objects.create(
            sender=cls.donor, donor=cls.donor2,
        )

    def setUp(self):
        self.client.force_login(self.boss)

    def test_site_title_uses_the_site_name(self):
        from django.conf import settings
        response = self.client.get('/admin/')
        self.assertContains(response, f'{settings.SITE_NAME} Administration')
        self.assertContains(response, f'{settings.SITE_NAME} Admin Panel')
        self.assertNotContains(response, 'Blood Lagbe')

    def test_contact_requests_are_registered_and_listed(self):
        self.assertIn(ContactRequest, django_admin.site._registry)
        response = self.client.get('/admin/rewards/contactrequest/')
        self.assertContains(response, 'column-status')
        self.assertContains(response, 'column-sender')
        response = self.client.get(
            '/admin/rewards/contactrequest/?status__exact=pending',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get(
                f'/admin/rewards/contactrequest/{self.contact.pk}/change/',
            ).status_code, 200,
        )

    def test_changelists_and_forms_render(self):
        urls = [
            'accounts/profile', 'accounts/user', 'rewards/badge',
            'rewards/bloodrequest', 'rewards/donation', 'rewards/place',
            'rewards/requestreport', 'rewards/requestresponse',
            'rewards/contactrequest', 'squads/squad', 'stories/story',
            'stories/storyreaction', 'stories/storyreport',
        ]
        for path in urls:
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.get(f'/admin/{path}/').status_code, 200,
                )
        for path in (
            f'rewards/donation/{self.pending.pk}/change',
            f'rewards/bloodrequest/{self.req.pk}/change',
            'rewards/donation/add', 'squads/squad/add',
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.get(f'/admin/{path}/').status_code, 200,
                )

    def test_profile_list_renders_for_users_without_email(self):
        # str(profile) used to return None for them: a 500 in the admin.
        response = self.client.get('/admin/accounts/profile/')
        self.assertEqual(response.status_code, 200)

    def test_donation_filters_and_columns(self):
        response = self.client.get('/admin/rewards/donation/')
        for column in ('donor', 'status', 'donation_date', 'location'):
            self.assertContains(response, f'column-{column}')
        self.assertContains(response, 'By status')

    def _act(self, action, *objs):
        return self.client.post('/admin/rewards/donation/', {
            'action': action,
            '_selected_action': [o.pk for o in objs],
        }, follow=True)

    def test_approve_action_confirms_how_many_and_emails_the_badge(self):
        mail.outbox.clear()
        response = self._act('approve_donations', self.pending, self.pending2)
        self.assertContains(response, 'Approved 2 donation(s).')
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'approved')
        self.assertEqual(self.pending.verified_by, self.boss)
        # Only the donor with an email on file is mailed.
        self.assertEqual([m.to for m in mail.outbox], [['dolly@example.com']])
        self.assertIn('badge', mail.outbox[0].subject)

    def test_reject_action_confirms_how_many(self):
        response = self._act('reject_donations', self.pending)
        self.assertContains(response, 'Rejected 1 donation(s).')
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'rejected')

    def test_story_approval_message_reads_naturally(self):
        story = Story.objects.create(
            author=self.donor, title='Hello there', body='x' * 50,
        )
        response = self.client.post('/admin/stories/story/', {
            'action': 'approve_stories', '_selected_action': [story.pk],
        }, follow=True)
        self.assertContains(response, '1 story approved.')
        self.assertNotContains(response, '1 stories')
        story.refresh_from_db()
        self.assertEqual(story.status, 'approved')

    def test_squad_added_in_admin_makes_the_owner_a_member(self):
        squad = Squad.objects.create(owner=self.donor, name='Admin Made')
        from squads.admin import SquadAdmin
        SquadAdmin(Squad, django_admin.site).save_model(
            None, squad, None, True,
        )
        self.assertTrue(squad.memberships.filter(user=self.donor).exists())
