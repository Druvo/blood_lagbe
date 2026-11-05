import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.forms import RegistrationForm, normalize_phone
from rewards.models import BloodRequest, Donation, RequestResponse

User = get_user_model()

PASSWORD = 'Sturdy-pass-4821'


def make_user(phone='01711000001', password=PASSWORD, **extra):
    user = User.objects.create_user(phone=phone, password=password)
    for key, value in extra.items():
        setattr(user, key, value)
    user.save()
    return user


def signup_data(**overrides):
    data = {
        'first_name': 'Rahim', 'last_name': 'Uddin', 'email': '',
        'phone': '01712345678',
        'password1': PASSWORD, 'password2': PASSWORD,
        'blood_group': '', 'district': '',
    }
    data.update(overrides)
    return data


class NormalizePhoneTests(TestCase):
    def test_variants_fold_to_local_form(self):
        for raw in ('01712345678', '+8801712345678', '8801712345678',
                    '017 1234-5678', ' 01712345678 '):
            self.assertEqual(normalize_phone(raw), '01712345678', raw)

    def test_other_numbers_untouched(self):
        self.assertEqual(normalize_phone('12345'), '12345')
        self.assertEqual(normalize_phone(None), '')


class LoginTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_page_renders_with_kit_and_no_old_theme(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Forgot your password?')
        self.assertContains(response, 'bb-card')
        self.assertNotContains(response, 'form-control')

    def test_login_by_phone(self):
        response = self.client.post(reverse('login'), {
            'phone': '01711000001', 'password': PASSWORD,
        })
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_login_accepts_country_code_and_spaces(self):
        response = self.client.post(reverse('login'), {
            'phone': '+880 1711-000001', 'password': PASSWORD,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_wrong_password_shows_error_and_does_not_log_in(self):
        response = self.client.post(reverse('login'), {
            'phone': '01711000001', 'password': 'wrong-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'do not match')
        self.assertNotIn('_auth_user_id', self.client.session)
        # The phone typed is kept so the user need not retype it.
        self.assertContains(response, 'value="01711000001"')

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.client.post(reverse('login'), {
            'phone': '01711000001', 'password': PASSWORD,
        })
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_empty_post_shows_required_errors(self):
        response = self.client.post(reverse('login'), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required')

    def test_logged_in_user_is_redirected_away(self):
        self.client.force_login(self.user)
        for name in ('login', 'signup'):
            response = self.client.get(reverse(name))
            self.assertRedirects(
                response, reverse('home'), fetch_redirect_response=False,
            )

    def test_next_is_followed_when_local(self):
        target = reverse('privacy')
        response = self.client.post(
            reverse('login') + f'?next={target}',
            {'phone': '01711000001', 'password': PASSWORD},
        )
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_next_from_post_body_is_followed(self):
        target = reverse('learn')
        response = self.client.post(reverse('login'), {
            'phone': '01711000001', 'password': PASSWORD, 'next': target,
        })
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_next_is_carried_in_the_form(self):
        response = self.client.get(reverse('login') + '?next=/donations/log/')
        self.assertContains(response, 'name="next" value="/donations/log/"')

    def test_open_redirect_is_refused(self):
        for evil in ('https://evil.example/steal', '//evil.example/steal',
                     'javascript:alert(1)', '/\\evil.example'):
            self.client.logout()
            response = self.client.post(
                reverse('login'),
                {'phone': '01711000001', 'password': PASSWORD, 'next': evil},
            )
            self.assertRedirects(
                response, reverse('home'), fetch_redirect_response=False,
            ), evil

    def test_unsafe_next_is_not_echoed_into_the_form(self):
        response = self.client.get(reverse('login') + '?next=https://evil.example/')
        self.assertNotContains(response, 'evil.example')

    def test_login_required_redirects_here_with_next(self):
        response = self.client.get(reverse('privacy'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/accounts/login/?next='))


class LogoutTests(TestCase):
    def test_logout_ends_session_and_goes_home(self):
        user = make_user()
        self.client.force_login(user)
        response = self.client.get(reverse('logout'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_logout_when_anonymous_is_harmless(self):
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 302)


class SignupTests(TestCase):
    def test_page_renders_all_fields(self):
        response = self.client.get(reverse('signup'))
        self.assertEqual(response.status_code, 200)
        for name in ('first_name', 'last_name', 'phone', 'password1',
                     'password2', 'blood_group', 'district', 'in_directory'):
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, 'Dhaka')

    def test_signup_logs_in_and_goes_to_welcome(self):
        response = self.client.post(reverse('signup'), signup_data())
        self.assertRedirects(response, reverse('welcome'))
        user = User.objects.get(phone='01712345678')
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(user.first_name, 'Rahim')
        self.assertIsNone(user.email)

    def test_signup_saves_profile_fields(self):
        self.client.post(reverse('signup'), signup_data(
            blood_group='O-', district='Sylhet',
        ))
        profile = User.objects.get(phone='01712345678').profile
        self.assertEqual(profile.blood_group, 'O-')
        self.assertEqual(profile.district, 'Sylhet')
        self.assertFalse(profile.in_directory)

    def test_directory_is_opt_in_and_honoured_with_both_fields(self):
        self.client.post(reverse('signup'), signup_data(
            blood_group='A+', district='Dhaka', in_directory='on',
        ))
        self.assertTrue(User.objects.get(phone='01712345678').profile.in_directory)

    def test_directory_ignored_without_district(self):
        response = self.client.post(reverse('signup'), signup_data(
            blood_group='A+', in_directory='on',
        ), follow=True)
        profile = User.objects.get(phone='01712345678').profile
        self.assertFalse(profile.in_directory)
        self.assertContains(response, 'not in the donor directory yet')

    def test_directory_ignored_without_blood_group(self):
        self.client.post(reverse('signup'), signup_data(
            district='Dhaka', in_directory='on',
        ))
        self.assertFalse(User.objects.get(phone='01712345678').profile.in_directory)

    def test_directory_ignored_with_neither(self):
        self.client.post(reverse('signup'), signup_data(in_directory='on'))
        self.assertFalse(User.objects.get(phone='01712345678').profile.in_directory)

    def test_invalid_blood_group_and_district_rejected(self):
        response = self.client.post(reverse('signup'), signup_data(
            blood_group='Z+', district='Atlantis',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(phone='01712345678').exists())

    def test_duplicate_phone_rejected(self):
        make_user(phone='01712345678')
        response = self.client.post(reverse('signup'), signup_data())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already has an account')
        self.assertEqual(User.objects.filter(phone='01712345678').count(), 1)

    def test_duplicate_phone_in_other_format_rejected(self):
        make_user(phone='01712345678')
        response = self.client.post(
            reverse('signup'), signup_data(phone='+880 1712-345678'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_phone_is_stored_normalised(self):
        self.client.post(reverse('signup'), signup_data(phone='+8801712345678'))
        self.assertTrue(User.objects.filter(phone='01712345678').exists())

    def test_invalid_phone_rejected(self):
        for bad in ('12345678901', '01012345678', 'abc', '0171'):
            response = self.client.post(reverse('signup'), signup_data(phone=bad))
            self.assertEqual(response.status_code, 200, bad)
        self.assertEqual(User.objects.count(), 0)

    def test_first_name_required_last_name_optional(self):
        response = self.client.post(reverse('signup'), signup_data(first_name=''))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)
        self.client.post(reverse('signup'), signup_data(last_name=''))
        self.assertEqual(User.objects.count(), 1)

    def test_password_mismatch_and_weak_password_rejected(self):
        self.client.post(reverse('signup'), signup_data(password2='other-pass-1234'))
        self.client.post(reverse('signup'), signup_data(
            password1='12345678', password2='12345678',
        ))
        self.assertEqual(User.objects.count(), 0)

    def test_two_members_may_skip_email(self):
        self.client.post(reverse('signup'), signup_data())
        self.client.logout()
        self.client.post(reverse('signup'), signup_data(phone='01812345678'))
        self.assertEqual(User.objects.count(), 2)

    def test_duplicate_email_rejected(self):
        self.client.post(reverse('signup'), signup_data(email='a@example.com'))
        self.client.logout()
        response = self.client.post(reverse('signup'), signup_data(
            phone='01812345678', email='a@example.com',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_signup_url_works_without_trailing_slash_redirect(self):
        # Regression: /accounts/signup (no slash) must reach the page.
        response = self.client.get('/accounts/signup', follow=True)
        self.assertEqual(response.status_code, 200)

    def test_form_directory_ignored_flag(self):
        form = RegistrationForm(signup_data(in_directory='on'))
        self.assertTrue(form.is_valid())
        self.assertTrue(form.directory_ignored)


class WelcomeTests(TestCase):
    def setUp(self):
        self.user = make_user(first_name='Nadia')

    def steps(self, response):
        return {s['key']: s['done'] for s in response.context['steps']}

    def test_requires_login(self):
        response = self.client.get(reverse('welcome'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertIn('next=/welcome/', response.url)

    def test_fresh_member_has_nothing_done(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('welcome'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['done_count'], 0)
        self.assertEqual(response.context['total_count'], 5)
        self.assertEqual(response.context['percent'], 0)
        self.assertContains(response, 'Welcome, Nadia')
        self.assertEqual(set(self.steps(response).values()), {False})

    def test_profile_and_directory_steps_follow_real_state(self):
        profile = self.user.profile
        profile.blood_group = 'B+'
        profile.district = 'Khulna'
        profile.in_directory = True
        profile.save()
        self.client.force_login(self.user)
        steps = self.steps(self.client.get(reverse('welcome')))
        self.assertTrue(steps['profile'])
        self.assertTrue(steps['directory'])
        self.assertFalse(steps['donation'])

    def test_profile_incomplete_without_district(self):
        self.user.profile.blood_group = 'B+'
        self.user.profile.save()
        self.client.force_login(self.user)
        self.assertFalse(self.steps(self.client.get(reverse('welcome')))['profile'])

    def test_donation_step_ignores_rejected_donations(self):
        Donation.objects.create(
            donor=self.user, blood_group='B+', location='DMCH',
            donation_date=timezone.localdate() - timedelta(days=200),
            status=Donation.Status.REJECTED,
        )
        self.client.force_login(self.user)
        self.assertFalse(self.steps(self.client.get(reverse('welcome')))['donation'])
        Donation.objects.create(
            donor=self.user, blood_group='B+', location='DMCH',
            donation_date=timezone.localdate() - timedelta(days=150),
            status=Donation.Status.PENDING,
        )
        self.assertTrue(self.steps(self.client.get(reverse('welcome')))['donation'])

    def test_requests_step_done_after_responding(self):
        other = make_user(phone='01711000002')
        blood_request = BloodRequest.objects.create(
            requester=other, blood_group='O+', hospital='DMCH',
            district='Dhaka', needed_by=timezone.localdate() + timedelta(days=2),
            contact_phone='01711000002',
        )
        self.client.force_login(self.user)
        self.assertFalse(self.steps(self.client.get(reverse('welcome')))['requests'])
        RequestResponse.objects.create(blood_request=blood_request, donor=self.user)
        self.assertTrue(self.steps(self.client.get(reverse('welcome')))['requests'])

    def test_requests_hint_counts_matching_open_requests(self):
        self.user.profile.blood_group = 'O+'
        self.user.profile.save()
        other = make_user(phone='01711000002')
        BloodRequest.objects.create(
            requester=other, blood_group='O+', hospital='DMCH',
            district='Dhaka', needed_by=timezone.localdate() + timedelta(days=2),
            contact_phone='01711000002',
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('welcome'))
        self.assertContains(response, '1 open request need your blood group')

    def test_guide_step_done_after_visiting_learn(self):
        self.client.force_login(self.user)
        self.assertFalse(self.steps(self.client.get(reverse('welcome')))['guide'])
        self.client.get(reverse('learn'))
        self.assertTrue(self.steps(self.client.get(reverse('welcome')))['guide'])

    def test_all_done_shows_full_progress(self):
        profile = self.user.profile
        profile.blood_group, profile.district, profile.in_directory = 'A+', 'Dhaka', True
        profile.save()
        Donation.objects.create(
            donor=self.user, blood_group='A+', location='DMCH',
            donation_date=timezone.localdate() - timedelta(days=200),
            status=Donation.Status.APPROVED,
        )
        other = make_user(phone='01711000002')
        RequestResponse.objects.create(
            blood_request=BloodRequest.objects.create(
                requester=other, blood_group='A+', hospital='DMCH',
                district='Dhaka',
                needed_by=timezone.localdate() + timedelta(days=2),
                contact_phone='01711000002',
            ),
            donor=self.user,
        )
        self.client.force_login(self.user)
        self.client.get(reverse('learn'))
        response = self.client.get(reverse('welcome'))
        self.assertEqual(response.context['percent'], 100)
        self.assertTrue(response.context['all_done'])
        self.assertContains(response, '5 of 5 done')

    def test_every_step_links_to_a_real_page(self):
        self.client.force_login(self.user)
        for step in self.client.get(reverse('welcome')).context['steps']:
            target = reverse(step['url_name'])
            self.assertEqual(self.client.get(target).status_code, 200, target)

    def test_never_renders_phone_number(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse('welcome')), '01711000001')


@override_settings(SITE_NAME='TestBond')
class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = make_user(email='member@example.com')

    def request_reset(self, email='member@example.com'):
        return self.client.post(reverse('password_reset'), {'email': email})

    def test_pages_render(self):
        self.assertContains(self.client.get(reverse('password_reset')), 'Send reset link')
        self.assertContains(self.client.get(reverse('password_reset_done')), 'Check your email')
        self.assertContains(self.client.get(reverse('password_reset_complete')), 'Password updated')

    def test_full_flow(self):
        response = self.request_reset()
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['member@example.com'])
        self.assertIn('TestBond', message.subject)
        self.assertIn('TestBond', message.body)
        self.assertNotIn('Blood Lagbe', message.body + message.subject)
        link = re.search(r'https?://[^/]+(/accounts/reset/\S+)', message.body).group(1)

        # The token link redirects to a session-bound set-password URL.
        page = self.client.get(link, follow=True)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Choose a new password')
        set_url = page.redirect_chain[-1][0]

        # Weak password is refused with a visible error.
        weak = self.client.post(set_url, {
            'new_password1': '12345678', 'new_password2': '12345678',
        })
        self.assertEqual(weak.status_code, 200)
        self.assertContains(weak, 'errorlist')

        done = self.client.post(set_url, {
            'new_password1': 'Brand-new-pass-77', 'new_password2': 'Brand-new-pass-77',
        })
        self.assertRedirects(done, reverse('password_reset_complete'))

        self.assertFalse(self.client.login(phone='01711000001', password=PASSWORD))
        self.assertTrue(self.client.login(phone='01711000001', password='Brand-new-pass-77'))

    def test_token_cannot_be_reused(self):
        self.request_reset()
        link = re.search(r'(/accounts/reset/\S+)', mail.outbox[0].body).group(1)
        set_url = self.client.get(link, follow=True).redirect_chain[-1][0]
        self.client.post(set_url, {
            'new_password1': 'Brand-new-pass-77', 'new_password2': 'Brand-new-pass-77',
        })
        fresh = self.client_class()
        page = fresh.get(link, follow=True)
        self.assertContains(page, 'This link has expired')

    def test_bad_token_shows_expired_page(self):
        page = self.client.get('/accounts/reset/Mg/set-password-abc/', follow=True)
        self.assertContains(page, 'This link has expired')
        self.assertContains(page, reverse('password_reset'))

    def test_unknown_email_gives_same_answer_and_sends_nothing(self):
        response = self.request_reset('nobody@example.com')
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_user_gets_no_email(self):
        self.user.is_active = False
        self.user.save()
        self.request_reset()
        self.assertEqual(len(mail.outbox), 0)

    def test_login_page_links_to_reset(self):
        self.assertContains(self.client.get(reverse('login')), reverse('password_reset'))


class PrivacyPageTests(TestCase):
    def test_renders_for_member_without_old_theme(self):
        self.client.force_login(make_user())
        response = self.client.get(reverse('privacy'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="in_directory"')
        self.assertNotContains(response, 'form-control')
