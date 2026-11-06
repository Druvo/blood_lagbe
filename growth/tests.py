"""Growth: invites and referrals, public profile, share links, verified
hospitals and the install prompt."""
import datetime
import sys
import types
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.forms import PrivacyForm
from growth import services, share, trust
from growth.models import (
    Invite,
    Referral,
    ReferralReward,
    VerifiedHospital,
)
from rewards.models import (
    BloodRequest,
    Donation,
    Place,
    RequestReport,
)
from squads.models import Squad, SquadMember
from stories.models import Story

User = get_user_model()
PASSWORD = 'testpass123'
_phones = iter(range(1, 100000))


def member(first='Mina', last='Rahman', **profile):
    n = next(_phones)
    user = User.objects.create_user(phone=f'0181{n:07d}', password=PASSWORD)
    user.first_name, user.last_name = first, last
    user.email = f'member{n}@example.com'
    user.save()
    p = user.profile
    for key, value in profile.items():
        setattr(p, key, value)
    p.save()
    return user


def old_account(user):
    """Make the account look a day old, so an invite can no longer attach."""
    user.profile.__class__.objects.filter(pk=user.pk).update(
        created_at=timezone.now() - datetime.timedelta(days=1),
    )
    user.refresh_from_db()
    return user


def donation(user, status=Donation.Status.APPROVED, days_ago=200, **kwargs):
    return Donation.objects.create(
        donor=user, blood_group='O+', location='Dhaka Medical',
        donation_date=timezone.localdate() - datetime.timedelta(days=days_ago),
        status=status, **kwargs,
    )


def blood_request(requester, **kwargs):
    defaults = dict(
        blood_group='B+', hospital='Dhaka Medical College', district='Dhaka',
        units_needed=2, contact_phone='01711999888',
        needed_by=timezone.localdate() + datetime.timedelta(days=3),
    )
    defaults.update(kwargs)
    return BloodRequest.objects.create(requester=requester, **defaults)


def fake_request(user=None, session=None):
    request = RequestFactory().get('/', HTTP_HOST='testserver')
    request.session = session if session is not None else {}
    if user is not None:
        request.user = user
    return request


# --------------------------------------------------------------------- invite

class InviteCodeTests(TestCase):
    def test_code_is_stable_and_created_on_demand(self):
        user = member()
        self.assertFalse(Invite.objects.filter(user=user).exists())
        first = services.get_or_create_invite(user)
        again = services.get_or_create_invite(user)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(first.code, again.code)
        self.assertEqual(Invite.objects.filter(user=user).count(), 1)

    def test_codes_are_unique_and_unambiguous(self):
        codes = {services.get_or_create_invite(member()).code for _ in range(5)}
        self.assertEqual(len(codes), 5)
        for code in codes:
            self.assertEqual(len(code), 8)
            self.assertFalse(set(code) & set('ILO01'))


class InviteLandingTests(TestCase):
    def setUp(self):
        self.inviter = member('Ayesha', 'Khan')
        self.invite = services.get_or_create_invite(self.inviter)
        self.url = reverse('growth:invite', args=[self.invite.code])

    def test_public_page_stores_code_and_links_to_signup(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.session[services.SESSION_KEY], self.invite.code,
        )
        self.assertContains(response, reverse('signup'))
        self.assertContains(response, '<meta name="robots" content="noindex">')

    def test_code_is_case_and_space_insensitive(self):
        url = reverse('growth:invite', args=[f' {self.invite.code.lower()} '])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_unknown_code_is_404_and_stores_nothing(self):
        response = self.client.get(reverse('growth:invite', args=['NOSUCHCD']))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(services.SESSION_KEY, self.client.session)

    def test_inviter_named_when_listed_publicly(self):
        self.assertContains(self.client.get(self.url), 'Ayesha Khan')

    def test_inviter_is_a_friend_when_opted_out_of_the_leaderboard(self):
        self.inviter.profile.show_on_leaderboard = False
        self.inviter.profile.save()
        body = self.client.get(self.url).content.decode()
        self.assertNotIn('Ayesha', body)
        self.assertNotIn('Khan', body)
        self.assertIn('A friend invited you', body)

    def test_nobody_without_a_name_is_shown_as_their_phone(self):
        self.inviter.first_name = self.inviter.last_name = ''
        self.inviter.save()
        body = self.client.get(self.url).content.decode()
        self.assertIn(f'Donor #{self.inviter.pk}', body)
        self.assertNotIn(self.inviter.phone, body)

    def test_no_phone_or_email_of_the_inviter(self):
        body = self.client.get(self.url).content.decode()
        self.assertNotIn(self.inviter.phone, body)
        self.assertNotIn(self.inviter.email, body)

    def test_inactive_inviter_link_is_dead(self):
        self.inviter.is_active = False
        self.inviter.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_signed_in_visitor_does_not_store_the_code(self):
        other = member()
        self.client.force_login(other)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(services.SESSION_KEY, self.client.session)

    def test_owner_sees_share_prompt(self):
        self.client.force_login(self.inviter)
        self.assertContains(self.client.get(self.url), 'your own invite link')


class SignupLinkingTests(TestCase):
    """The user_logged_in hook, through the real sign-up view."""

    def setUp(self):
        self.inviter = member('Ayesha', 'Khan')
        self.invite = services.get_or_create_invite(self.inviter)

    def signup(self, phone='01712340001', **extra):
        data = {
            'first_name': 'Newbie', 'last_name': 'Member', 'email': '',
            'phone': phone, 'password1': PASSWORD, 'password2': PASSWORD,
            'blood_group': '', 'district': '',
        }
        data.update(extra)
        return self.client.post(reverse('signup'), data)

    def visit_invite(self):
        self.client.get(reverse('growth:invite', args=[self.invite.code]))

    def test_signup_after_invite_links_the_member(self):
        self.visit_invite()
        response = self.signup()
        self.assertEqual(response.status_code, 302)
        invitee = User.objects.get(phone='01712340001')
        referral = Referral.objects.get(invitee=invitee)
        self.assertEqual(referral.inviter, self.inviter)
        # the session code is used up
        self.assertNotIn(services.SESSION_KEY, self.client.session)

    def test_no_reward_at_signup(self):
        self.visit_invite()
        self.signup()
        referral = Referral.objects.get()
        self.assertIsNone(referral.rewarded_at)
        self.assertFalse(ReferralReward.objects.exists())

    def test_signup_without_invite_creates_no_referral(self):
        self.signup()
        self.assertFalse(Referral.objects.exists())

    def test_failed_signup_does_not_consume_or_link(self):
        self.visit_invite()
        self.signup(password2='different')
        self.assertFalse(Referral.objects.exists())
        self.assertIn(services.SESSION_KEY, self.client.session)

    def test_login_of_an_existing_account_never_links(self):
        """Mutation check: drop the new-account rule and this fails."""
        old = old_account(member())
        self.visit_invite()
        response = self.client.post(reverse('login'), {
            'phone': old.phone, 'password': PASSWORD,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Referral.objects.exists())

    def test_ordinary_login_without_session_code_is_untouched(self):
        old = member()
        self.client.post(reverse('login'), {
            'phone': old.phone, 'password': PASSWORD,
        })
        self.assertFalse(Referral.objects.exists())


class AttachReferralRulesTests(TestCase):
    def setUp(self):
        self.inviter = member()
        self.invite = services.get_or_create_invite(self.inviter)

    def attach(self, user, code=None):
        request = fake_request(
            session={services.SESSION_KEY: code or self.invite.code},
        )
        return services.attach_referral(request, user), request

    def test_attaches_a_new_member(self):
        invitee = member()
        referral, request = self.attach(invitee)
        self.assertEqual((referral.inviter, referral.invitee),
                         (self.inviter, invitee))
        self.assertNotIn(services.SESSION_KEY, request.session)

    def test_self_referral_blocked(self):
        referral, _ = self.attach(self.inviter)
        self.assertIsNone(referral)
        self.assertFalse(Referral.objects.exists())

    def test_same_phone_written_another_way_blocked(self):
        # the phone column is unique, so a duplicate can only be a different
        # spelling of the same number (+880 form)
        twin = types.SimpleNamespace(
            pk=0, phone='+88' + self.inviter.phone, email=None,
        )
        self.assertTrue(services._same_person(self.inviter, twin))
        other = types.SimpleNamespace(pk=0, phone='01999999999', email=None)
        self.assertFalse(services._same_person(self.inviter, other))

    def test_same_email_blocked(self):
        twin = types.SimpleNamespace(
            pk=0, phone='01999999999',
            email=' ' + self.inviter.email.upper() + ' ',
        )
        self.assertTrue(services._same_person(self.inviter, twin))

    def test_old_account_blocked(self):
        invitee = old_account(member())
        referral, _ = self.attach(invitee)
        self.assertIsNone(referral)

    def test_one_referral_per_invitee(self):
        invitee = member()
        other_inviter = member()
        other_code = services.get_or_create_invite(other_inviter).code
        first, _ = self.attach(invitee)
        second, _ = self.attach(invitee, code=other_code)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(Referral.objects.get(invitee=invitee).inviter,
                         self.inviter)

    def test_bad_or_missing_code_is_harmless(self):
        invitee = member()
        self.assertIsNone(self.attach(invitee, code='ZZZZZZZZ')[0])
        self.assertIsNone(
            services.attach_referral(fake_request(), invitee),
        )
        self.assertIsNone(
            services.attach_referral(types.SimpleNamespace(), invitee),
        )

    def test_daily_cap_per_inviter(self):
        for _ in range(services.MAX_NEW_REFERRALS_PER_DAY):
            self.assertIsNotNone(self.attach(member())[0])
        self.assertIsNone(self.attach(member())[0])
        self.assertEqual(Referral.objects.count(),
                         services.MAX_NEW_REFERRALS_PER_DAY)


# --------------------------------------------------------------------- reward

class ReferralRewardTests(TestCase):
    def setUp(self):
        self.inviter = member()
        self.invitee = member()
        self.referral = Referral.objects.create(
            inviter=self.inviter, invitee=self.invitee,
        )

    def referral_state(self):
        self.referral.refresh_from_db()
        return self.referral.rewarded_at

    def test_pending_donation_pays_nothing(self):
        donation(self.invitee, status=Donation.Status.PENDING)
        self.assertIsNone(self.referral_state())
        self.assertFalse(ReferralReward.objects.exists())

    def test_rejected_donation_pays_nothing(self):
        donation(self.invitee, status=Donation.Status.REJECTED)
        self.assertIsNone(self.referral_state())

    def test_first_approval_rewards_the_inviter(self):
        d = donation(self.invitee, status=Donation.Status.PENDING)
        d.status = Donation.Status.APPROVED
        d.save()
        self.assertIsNotNone(self.referral_state())
        reward = ReferralReward.objects.get(user=self.inviter)
        self.assertEqual((reward.level, reward.name), (1, 'Friend Maker'))
        # nothing goes to the invitee
        self.assertFalse(ReferralReward.objects.filter(user=self.invitee))

    def test_created_as_approved_also_counts(self):
        donation(self.invitee)
        self.assertIsNotNone(self.referral_state())

    def test_idempotent_on_resave_and_replay(self):
        d = donation(self.invitee)
        stamp = self.referral_state()
        d.save()
        d.save()
        services.reward_referral(d)
        self.assertEqual(self.referral_state(), stamp)
        self.assertEqual(ReferralReward.objects.filter(
            user=self.inviter).count(), 1)

    def test_second_donation_does_not_pay_again(self):
        donation(self.invitee, days_ago=400)
        stamp = self.referral_state()
        donation(self.invitee, days_ago=100)
        self.assertEqual(self.referral_state(), stamp)

    def test_only_the_first_approved_donation_counts(self):
        """A second approved donation never triggers a late reward when the
        referral somehow is unpaid: it must be the donor's first."""
        donation(self.invitee, days_ago=400)
        Referral.objects.filter(pk=self.referral.pk).update(rewarded_at=None)
        donation(self.invitee, days_ago=100)
        self.assertIsNone(self.referral_state())

    def test_donation_by_someone_else_does_not_pay(self):
        donation(member())
        self.assertIsNone(self.referral_state())

    def test_inactive_inviter_gets_nothing(self):
        self.inviter.is_active = False
        self.inviter.save()
        donation(self.invitee)
        self.assertIsNone(self.referral_state())

    def test_levels_at_one_three_and_ten(self):
        donation(self.invitee)
        self.assertEqual(
            list(ReferralReward.objects.filter(user=self.inviter)
                 .values_list('level', flat=True)), [1],
        )
        for _ in range(2):
            friend = member()
            Referral.objects.create(inviter=self.inviter, invitee=friend)
            donation(friend)
        self.assertEqual(
            sorted(ReferralReward.objects.filter(user=self.inviter)
                   .values_list('level', flat=True)), [1, 2],
        )
        for _ in range(7):
            friend = member()
            Referral.objects.create(inviter=self.inviter, invitee=friend)
            donation(friend)
        self.assertEqual(
            sorted(ReferralReward.objects.filter(user=self.inviter)
                   .values_list('level', flat=True)), [1, 2, 3],
        )
        stats = services.referral_stats(self.inviter)
        self.assertEqual((stats['joined'], stats['rewarded']), (10, 10))
        self.assertEqual(stats['current'].name, 'Friend Maker III')
        self.assertIsNone(stats['next'])

    def test_award_levels_is_idempotent(self):
        donation(self.invitee)
        services.award_levels(self.inviter)
        services.award_levels(self.inviter)
        self.assertEqual(ReferralReward.objects.count(), 1)

    def test_self_referral_row_is_refused_by_the_database(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Referral.objects.create(inviter=self.inviter, invitee=self.inviter)


# ------------------------------------------------------------ hub/home panels

class PanelTests(TestCase):
    def setUp(self):
        self.user = member('Hubby', 'Person')

    def test_hub_panel_for_signed_in_member(self):
        invite = services.get_or_create_invite(self.user)
        self.client.force_login(self.user)
        response = self.client.get(reverse('log-donation'))
        self.assertEqual(response.status_code, 200)
        link = f'http://testserver/invite/{invite.code}/'
        self.assertContains(response, link)
        self.assertContains(response, 'data-copy="#gr-hub-link"')
        self.assertContains(response, 'wa.me/?text=')
        self.assertContains(response, 'fb-messenger://share/')
        self.assertContains(response, 'facebook.com/sharer/sharer.php?u=')
        self.assertContains(response, 'growth/install.js')

    def test_hub_panel_counts_friends_without_naming_them(self):
        friend = member('Secretly', 'Named')
        Referral.objects.create(inviter=self.user, invitee=friend)
        self.client.force_login(self.user)
        response = self.client.get(reverse('log-donation'))
        body = response.content.decode()
        self.assertIn('friend joined', body)
        self.assertNotIn('Secretly', body)
        self.assertNotIn(friend.phone, body)

    def test_hub_panel_shows_friend_maker_level(self):
        friend = member()
        Referral.objects.create(inviter=self.user, invitee=friend)
        donation(friend)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('log-donation')),
                            'Friend Maker')

    def test_home_panel_hidden_for_anonymous_but_loads_install_script(self):
        response = self.client.get(reverse('home'))
        self.assertNotContains(response, 'gr-home-link')
        self.assertContains(response, 'growth/install.js')
        self.assertFalse(Invite.objects.exists())

    def test_home_panel_for_member(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'gr-home-link')

    def test_invite_page_requires_login(self):
        response = self.client.get(reverse('growth:invite-home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_invite_page_for_member(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('growth:invite-home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Friend Maker')
        self.assertNotContains(response, self.user.phone)


# -------------------------------------------------------------------- profile

class PublicProfileTests(TestCase):
    def setUp(self):
        self.owner = member(
            'Noor', 'Jahan', public_profile=True, district='Sylhet',
            in_directory=False, blood_group='A+',
        )
        self.url = reverse('growth:profile', args=[self.owner.pk])
        self.stranger = member()
        self.staff = member()
        self.staff.staff = True
        self.staff.save()

    def optout(self, **flags):
        for key, value in flags.items():
            setattr(self.owner.profile, key, value)
        self.owner.profile.save()

    def test_public_profile_visible_to_anyone(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Noor Jahan')
        self.assertContains(response, 'og:title')
        self.assertNotContains(response, 'content="noindex"')

    def test_opted_out_is_404_for_anonymous_and_strangers(self):
        self.optout(public_profile=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_404_is_identical_to_a_missing_member(self):
        self.optout(public_profile=False)
        missing = reverse('growth:profile', args=[999999])
        self.assertEqual(
            self.client.get(self.url).status_code,
            self.client.get(missing).status_code,
        )

    def test_owner_and_staff_can_see_a_private_profile_noindexed(self):
        self.optout(public_profile=False)
        for who in (self.owner, self.staff):
            client = self.client_class()
            client.force_login(who)
            response = client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<meta name="robots" content="noindex">')
            self.assertContains(response, 'Noor Jahan')

    def test_hidden_from_leaderboard_means_no_public_profile(self):
        self.optout(show_on_leaderboard=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_inactive_member_is_404_even_to_staff(self):
        self.owner.is_active = False
        self.owner.save()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_never_shows_phone_or_email(self):
        body = self.client.get(self.url).content.decode()
        self.assertNotIn(self.owner.phone, body)
        self.assertNotIn(self.owner.email, body)

    def test_district_only_when_in_directory(self):
        self.assertNotContains(self.client.get(self.url), 'Sylhet')
        self.optout(in_directory=True)
        self.assertContains(self.client.get(self.url), 'Sylhet')

    def test_blood_group_not_published(self):
        self.assertNotContains(self.client.get(self.url), 'A+')

    def test_counts_only_approved_donations(self):
        donation(self.owner, days_ago=400)
        donation(self.owner, days_ago=250, status=Donation.Status.PENDING)
        donation(self.owner, days_ago=100, status=Donation.Status.REJECTED)
        response = self.client.get(self.url)
        self.assertEqual(response.context['donations'], 1)
        self.assertContains(response, '1</strong><span>verified donation<')

    def test_only_public_squads_are_listed(self):
        public = Squad.objects.create(name='Open Squad', owner=self.owner,
                                      is_public=True)
        private = Squad.objects.create(name='Secret Squad', owner=self.owner,
                                       is_public=False)
        for squad in (public, private):
            SquadMember.objects.create(squad=squad, user=self.owner)
        body = self.client.get(self.url).content.decode()
        self.assertIn('Open Squad', body)
        self.assertNotIn('Secret Squad', body)

    def test_only_published_non_anonymous_stories(self):
        def story(title, **kwargs):
            return Story.objects.create(
                author=self.owner, title=title,
                body='x' * 60, **kwargs,
            )
        story('Published tale here', status=Story.Status.APPROVED)
        story('Pending tale here')
        story('Anon tale here', status=Story.Status.APPROVED, anonymous=True)
        body = self.client.get(self.url).content.decode()
        self.assertIn('Published tale here', body)
        self.assertNotIn('Pending tale here', body)
        self.assertNotIn('Anon tale here', body)

    def test_titles_are_optional(self):
        with mock.patch.dict(sys.modules, {'habits.titles': None}):
            self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_titles_shown_when_the_module_exists(self):
        fake = types.ModuleType('habits.titles')
        fake.titles_for = lambda user: ['Night Owl', {'name': 'Early Bird'}]
        with mock.patch.dict(sys.modules, {'habits.titles': fake}):
            body = self.client.get(self.url).content.decode()
        self.assertIn('Night Owl', body)
        self.assertIn('Early Bird', body)

    def test_a_broken_titles_module_does_not_break_the_page(self):
        fake = types.ModuleType('habits.titles')

        def boom(user):
            raise RuntimeError('nope')
        fake.titles_for = boom
        patched = mock.patch.dict(sys.modules, {'habits.titles': fake})
        with patched, self.assertLogs('growth.views', level='ERROR'):
            self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_anonymous_fallback_name_never_a_phone(self):
        self.owner.first_name = self.owner.last_name = ''
        self.owner.save()
        body = self.client.get(self.url).content.decode()
        self.assertIn(f'Donor #{self.owner.pk}', body)
        self.assertNotIn(self.owner.phone, body)

    def test_not_in_the_sitemap(self):
        body = self.client.get(reverse('sitemap')).content.decode()
        self.assertNotIn('/u/', body)


class PrivacyFormTests(TestCase):
    def post(self, user, **fields):
        self.client.force_login(user)
        return self.client.post(reverse('privacy'), fields)

    def test_default_is_private(self):
        self.assertFalse(member().profile.public_profile)

    def test_checkbox_saves(self):
        user = member()
        response = self.post(user, show_on_leaderboard='on',
                             public_profile='on')
        self.assertEqual(response.status_code, 302)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.public_profile)

    def test_unchecking_turns_it_off(self):
        user = member(public_profile=True)
        self.post(user, show_on_leaderboard='on')
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.public_profile)

    def test_needs_the_leaderboard_switch(self):
        user = member()
        response = self.post(user, public_profile='on')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Turn on')
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.public_profile)

    def test_form_validates_directly(self):
        user = member()
        form = PrivacyForm({'public_profile': 'on'}, instance=user.profile)
        self.assertFalse(form.is_valid())
        self.assertIn('public_profile', form.errors)

    def test_privacy_page_shows_the_toggle(self):
        user = member()
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse('privacy')),
                            'name="public_profile"')


# ---------------------------------------------------------------------- share

class ShareTests(TestCase):
    def setUp(self):
        self.owner = member()
        self.req = blood_request(self.owner)
        self.http = fake_request(self.owner)

    def text_of(self, url):
        return parse_qs(urlsplit(url).query)['text'][0]

    def test_whatsapp_text_has_the_essentials_and_no_phone(self):
        url = share.whatsapp_url(self.http, self.req)
        text = self.text_of(url)
        page = f'http://testserver/requests/{self.req.pk}/'
        self.assertIn('B+', text)
        self.assertIn('Dhaka Medical College', text)
        self.assertIn('Dhaka', text)
        needed = self.req.needed_by
        self.assertIn(f'{needed.day} {needed:%b} {needed.year}', text)
        self.assertIn(page, text)
        self.assertIn('জরুরি রক্ত', text)  # Bangla and English together
        self.assertIn('Urgent blood needed', text)
        self.assertNotIn(self.req.contact_phone, text)
        self.assertNotIn(self.owner.phone, text)
        self.assertNotIn('@', text)

    def test_url_is_percent_encoded(self):
        url = share.whatsapp_url(self.http, self.req)
        self.assertTrue(url.startswith('https://wa.me/?text='))
        raw = url.split('?text=')[1]
        self.assertNotIn(' ', raw)
        self.assertNotIn('\n', raw)
        self.assertNotIn('://', raw)  # the page URL is encoded inside
        self.assertIn('%0A', raw)
        self.assertEqual(unquote(raw).splitlines()[0],
                         'জরুরি রক্ত দরকার | Urgent blood needed')

    def test_hospital_text_that_looks_like_a_phone_is_dropped(self):
        req = blood_request(self.owner, hospital='City Care 01711-222333 ward')
        text = self.text_of(share.whatsapp_url(self.http, req))
        self.assertNotIn('01711', text)
        self.assertNotIn('222333', text)
        self.assertIn('City Care', text)

    def test_facebook_and_messenger_links(self):
        page = f'http://testserver/requests/{self.req.pk}/'
        fb = share.facebook_url(self.http, self.req)
        self.assertEqual(
            parse_qs(urlsplit(fb).query)['u'][0], page,
        )
        ms = share.messenger_url(self.http, self.req)
        self.assertTrue(ms.startswith('fb-messenger://share/?link='))
        self.assertEqual(
            unquote(ms.split('link=')[1]), page,
        )

    def test_messenger_web_dialog_when_an_app_id_is_configured(self):
        with self.settings(FACEBOOK_APP_ID='12345'):
            url = share.messenger_url(self.http, self.req)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(urlsplit(url).netloc, 'www.facebook.com')
        self.assertEqual(query['app_id'], ['12345'])

    def render_panel(self, blood_req, viewer=None):
        request = fake_request(viewer or self.owner)
        tpl = Template('{% load growth_tags %}{% growth_request %}')
        return tpl.render(Context({
            'request': request, 'blood_request': blood_req,
        }))

    def test_panel_for_an_open_request(self):
        html = self.render_panel(self.req)
        self.assertIn('wa.me/?text=', html)
        self.assertIn('fb-messenger://share/', html)
        self.assertIn('data-copy=', html)
        self.assertNotIn(self.req.contact_phone, html)

    def test_panel_hidden_for_closed_expired_and_fulfilled(self):
        closed = blood_request(self.owner)
        closed.close()
        self.assertNotIn('wa.me', self.render_panel(closed))
        expired = blood_request(self.owner)
        BloodRequest.objects.filter(pk=expired.pk).update(
            needed_by=timezone.localdate() - datetime.timedelta(days=1),
        )
        expired.refresh_from_db()
        self.assertNotIn('wa.me', self.render_panel(expired))

    def test_panel_hidden_for_flagged_requests(self):
        for i in range(3):
            RequestReport.objects.create(
                blood_request=self.req, reporter=member(),
                reason=RequestReport.Reason.SPAM,
            )
        req = BloodRequest.objects.with_report_count().get(pk=self.req.pk)
        self.assertEqual(self.render_panel(req).strip(), '')

    def test_request_page_renders_the_panel_and_leaks_no_phone_in_links(self):
        viewer = member()
        self.client.force_login(viewer)
        response = self.client.get(reverse('request-detail', args=[self.req.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Send it to your people')
        panel = response.content.decode().split('Send it to your people')[1]
        panel = panel.split('</section>')[0]
        self.assertNotIn(self.req.contact_phone, panel)


# ---------------------------------------------------------- verified hospital

class VerifiedHospitalTests(TestCase):
    def setUp(self):
        self.owner = member()
        VerifiedHospital.objects.create(
            name='Dhaka Medical College', district='Dhaka',
        )

    def test_case_and_spacing_insensitive_match(self):
        req = blood_request(self.owner, hospital='  dhaka   MEDICAL college ')
        self.assertTrue(trust.is_verified_hospital(req))

    def test_district_must_match(self):
        req = blood_request(self.owner, hospital='Dhaka Medical College',
                            district='Sylhet')
        self.assertFalse(trust.is_verified_hospital(req))

    def test_different_name_does_not_match(self):
        req = blood_request(self.owner, hospital='Dhaka Medical')
        self.assertFalse(trust.is_verified_hospital(req))

    def test_listed_place_match(self):
        place = Place.objects.create(name='DMCH', district='Dhaka')
        VerifiedHospital.objects.create(
            name='Another Name', district='Dhaka', place=place,
        )
        req = blood_request(self.owner, hospital='Typed differently',
                            place=place)
        self.assertEqual(trust.verified_hospital_for(req).name, 'Another Name')

    def test_template_filter(self):
        req = blood_request(self.owner)
        out = Template(
            '{% load growth_tags %}{{ r|is_verified_hospital }}'
        ).render(Context({'r': req}))
        self.assertEqual(out, 'True')

    def test_chip_with_tooltip_on_the_request_page(self):
        req = blood_request(self.owner)
        self.client.force_login(member())
        response = self.client.get(reverse('request-detail', args=[req.pk]))
        self.assertContains(response, 'Verified hospital')
        self.assertContains(response, 'role="tooltip"')

    def test_no_chip_for_an_unverified_hospital(self):
        req = blood_request(self.owner, hospital='Nowhere Clinic')
        self.client.force_login(member())
        response = self.client.get(reverse('request-detail', args=[req.pk]))
        self.assertNotContains(response, 'Verified hospital')

    def test_no_chip_on_a_flagged_request(self):
        req = blood_request(self.owner)
        for i in range(3):
            RequestReport.objects.create(
                blood_request=req, reporter=member(),
                reason=RequestReport.Reason.SPAM,
            )
        self.client.force_login(self.owner)
        response = self.client.get(reverse('request-detail', args=[req.pk]))
        self.assertNotContains(response, 'Verified hospital')


# ------------------------------------------------------------- install prompt

class InstallPromptTests(TestCase):
    def js(self):
        path = Path(settings.BASE_DIR) / 'growth/static/growth/install.js'
        return path.read_text(encoding='utf-8')

    def test_snippet_renders_script_with_site_name(self):
        html = Template("{% include 'growth/_install.html' %}").render(
            Context({'SITE_NAME': 'TestSite', 'user': None}),
        )
        self.assertIn('growth/install.js', html)
        self.assertIn('data-site="TestSite"', html)
        self.assertIn('data-authed="0"', html)

    def test_script_encodes_the_rules(self):
        js = self.js()
        for needle in (
            'beforeinstallprompt', 'display-mode: standalone',
            'navigator.standalone', '30 * DAY', 'PAGES_NEEDED = 3',
            'Add to Home Screen', 'appinstalled',
        ):
            self.assertIn(needle, js)

    def test_storage_is_guarded_and_site_name_not_hardcoded(self):
        js = self.js()
        self.assertEqual(js.count('localStorage'), 2)
        self.assertEqual(js.count('try {'), js.count('catch (e)'))
        self.assertNotIn('BloodBond', js)
        self.assertNotIn('Blood Lagbe', js)

    def test_hub_includes_the_script_once_per_panel(self):
        user = member()
        self.client.force_login(user)
        response = self.client.get(reverse('log-donation'))
        self.assertContains(response, 'growth/install.js', count=1)
        self.assertContains(response, 'data-authed="1"')


class AdminTests(TestCase):
    def test_models_are_registered(self):
        from django.contrib import admin
        for model in (Invite, Referral, ReferralReward, VerifiedHospital):
            self.assertIn(model, admin.site._registry)

    def test_admin_pages_render(self):
        staff = member()
        staff.staff = staff.admin = True
        staff.save()
        VerifiedHospital.objects.create(name='X Hospital', district='Dhaka')
        self.client.force_login(staff)
        for name in ('invite', 'referral', 'referralreward', 'verifiedhospital'):
            url = reverse(f'admin:growth_{name}_changelist')
            self.assertEqual(self.client.get(url).status_code, 200, name)
        self.assertEqual(
            self.client.get(reverse('admin:growth_verifiedhospital_add'))
            .status_code, 200,
        )
