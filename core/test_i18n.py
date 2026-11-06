"""English / Bangla language switch.

English stays the default. Bangla comes from the `django_language` cookie (set
by POSTing the switcher form to /i18n/set-language/) or from the browser's
Accept-Language header. URLs carry no language prefix.
"""
import re

from django.conf import settings
from django.template import Context, Template
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import translation

from rewards import test_privacy_audit as audit
from rewards.audit_fixtures import build_cast, crawl_paths

COOKIE = settings.LANGUAGE_COOKIE_NAME

# Headings that must switch language.
HOME_EN = 'Every drop is a bond.'
HOME_BN = 'প্রতি ফোঁটা রক্তই একটি বন্ধন।'
LOGIN_EN = 'Welcome back'
LOGIN_BN = 'আবার স্বাগতম'
REQUESTS_EN = 'Blood requests'
REQUESTS_BN = 'রক্তের অনুরোধ'
LEARN_EN = 'Who can give to whom'
LEARN_BN = 'কে কাকে রক্ত দিতে পারেন'
NOT_FOUND_BN = 'আমরা পাতাটি খুঁজে পাইনি'
SET_LANGUAGE = '/i18n/set-language/'


def bangla_client():
    client = Client()
    client.cookies[COOKIE] = 'bn'
    return client


class DefaultLanguageTests(TestCase):
    def test_english_is_the_default(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, '<html lang="en"')
        self.assertContains(response, HOME_EN)
        self.assertNotContains(response, HOME_BN)

    def test_settings(self):
        self.assertEqual(settings.LANGUAGE_CODE, 'en-us')
        self.assertEqual([code for code, _ in settings.LANGUAGES], ['en', 'bn'])
        self.assertEqual(settings.LANGUAGE_COOKIE_SAMESITE, 'Lax')
        self.assertFalse(settings.USE_THOUSAND_SEPARATOR)

    def test_middleware_order(self):
        names = settings.MIDDLEWARE
        locale = names.index('django.middleware.locale.LocaleMiddleware')
        self.assertGreater(locale, names.index(
            'django.contrib.sessions.middleware.SessionMiddleware'))
        self.assertLess(locale, names.index(
            'django.middleware.common.CommonMiddleware'))

    def test_urls_have_no_language_prefix(self):
        self.assertEqual(reverse('home'), '/')
        self.assertEqual(reverse('request-list'), '/requests/')
        with translation.override('bn'):
            self.assertEqual(reverse('request-list'), '/requests/')

    def test_set_language_url(self):
        self.assertEqual(reverse('set_language'), SET_LANGUAGE)


class SwitchingTests(TestCase):
    def post_language(self, client, language, **extra):
        data = {'language': language}
        data.update(extra)
        return client.post(SET_LANGUAGE, data)

    def test_post_switches_to_bangla_and_sets_the_cookie(self):
        response = self.post_language(self.client, 'bn', next='/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/')
        cookie = response.cookies[COOKIE]
        self.assertEqual(cookie.value, 'bn')
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertTrue(cookie['httponly'])
        page = self.client.get('/')
        self.assertContains(page, '<html lang="bn"')
        self.assertContains(page, HOME_BN)
        self.assertNotContains(page, HOME_EN)

    def test_switching_back_to_english(self):
        self.post_language(self.client, 'bn')
        response = self.post_language(self.client, 'en', next='/')
        self.assertEqual(response.cookies[COOKIE].value, 'en')
        page = self.client.get('/')
        self.assertContains(page, '<html lang="en"')
        self.assertContains(page, HOME_EN)

    def test_unknown_language_is_ignored(self):
        response = self.post_language(self.client, 'xx', next='/')
        self.assertNotIn(COOKIE, response.cookies)
        self.assertContains(self.client.get('/'), '<html lang="en"')

    def test_get_is_refused(self):
        response = self.client.get(SET_LANGUAGE, {'language': 'bn', 'next': '/'})
        self.assertEqual(response.status_code, 405)
        self.assertNotIn(COOKIE, response.cookies)

    def test_csrf_is_enforced(self):
        strict = Client(enforce_csrf_checks=True)
        response = strict.post(SET_LANGUAGE, {'language': 'bn', 'next': '/'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(COOKIE, response.cookies)

    def test_open_redirect_is_not_followed(self):
        for target in ('https://evil.example/phish', '//evil.example/x',
                       'http://evil.example', 'javascript:alert(1)'):
            with self.subTest(target):
                response = self.post_language(self.client, 'bn', next=target)
                self.assertEqual(response.status_code, 302)
                self.assertNotIn('evil.example', response['Location'])
                self.assertNotIn('javascript', response['Location'])
                self.assertTrue(response['Location'].startswith('/'))

    def test_redirect_keeps_the_query_string(self):
        target = '/requests/?district=Dhaka&when=week'
        response = self.post_language(self.client, 'bn', next=target)
        self.assertEqual(response['Location'], target)

    def test_switcher_form_posts_the_current_path_and_query(self):
        page = self.client.get('/requests/?district=Dhaka&when=week')
        self.assertContains(
            page, 'name="next" value="/requests/?district=Dhaka&amp;when=week"')
        # Following the form lands on the same filtered page, in Bangla.
        self.post_language(
            self.client, 'bn', next='/requests/?district=Dhaka&when=week')
        again = self.client.get('/requests/?district=Dhaka&when=week')
        self.assertContains(again, REQUESTS_BN)
        self.assertEqual(again.context['district'], 'Dhaka')
        self.assertEqual(again.context['when'], 'week')

    def test_switcher_never_echoes_a_next_parameter_or_a_missing_path(self):
        page = self.client.get('/accounts/login/?next=https://evil.example/x&page=2')
        self.assertNotContains(page, 'evil.example')
        self.assertContains(page, 'name="next" value="/accounts/login/?page=2"')
        missing = self.client.get('/no-such-page-01955500002/')
        self.assertEqual(missing.status_code, 404)
        self.assertNotContains(missing, '01955500002', status_code=404)
        self.assertContains(missing, 'name="next" value="/"', status_code=404)

    def test_switching_does_not_touch_other_post_data(self):
        """The switcher is a separate form: it never carries or alters the
        fields of the form the visitor is filling in."""
        page = self.client.get(reverse('login'))
        html = page.content.decode()
        forms = re.findall(r'<form[^>]*>.*?</form>', html, re.S)
        switchers = [f for f in forms if 'set-language' in f]
        self.assertGreaterEqual(len(switchers), 2)  # nav (wide) and footer
        for form in switchers:
            self.assertIn('method="post"', form)
            self.assertNotIn('name="phone"', form)
            self.assertNotIn('name="password"', form)
        login_form = [f for f in forms if 'name="phone"' in f][0]
        self.assertNotIn('language', login_form)

    def test_language_does_not_leak_between_clients(self):
        first, second = Client(), Client()
        self.post_language(first, 'bn')
        self.assertContains(first.get('/'), HOME_BN)
        self.assertContains(second.get('/'), HOME_EN)
        self.assertNotIn(COOKIE, second.cookies)

    def test_logged_in_member_can_switch(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(
            phone='01911100001', password='pw-for-tests-123')
        self.client.force_login(user)
        self.post_language(self.client, 'bn', next=reverse('log-donation'))
        page = self.client.get(reverse('log-donation'))
        self.assertContains(page, '<html lang="bn"')
        self.assertContains(page, 'আমার হাব')


class PagesInBanglaTests(TestCase):
    def test_pages_render_bangla_headings(self):
        cases = (
            (reverse('home'), HOME_BN, HOME_EN),
            (reverse('login'), LOGIN_BN, LOGIN_EN),
            (reverse('request-list'), REQUESTS_BN, REQUESTS_EN),
            (reverse('learn'), LEARN_BN, LEARN_EN),
        )
        client = bangla_client()
        for path, bangla, english in cases:
            with self.subTest(path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '<html lang="bn"')
                self.assertContains(response, bangla)
                self.assertNotContains(response, '>%s<' % english)

    def test_more_pages_render_in_bangla(self):
        client = bangla_client()
        for name in ('signup', 'faq', 'about', 'leaderboard', 'impact',
                     'stories', 'password_reset', 'events:list',
                     'donor-directory'):
            with self.subTest(name):
                response = client.get(reverse(name), follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '<html lang="bn"')

    def test_nav_and_footer_are_translated(self):
        response = bangla_client().get(reverse('home'))
        self.assertContains(response, 'জরুরি অনুরোধ')
        self.assertContains(response, 'রক্তদাতা খুঁজুন')
        self.assertContains(response, 'মূল অংশে যান')  # skip link
        self.assertContains(response, 'গোপনীয়তা সেটিংস')  # footer

    def test_product_name_stays_latin(self):
        response = bangla_client().get(reverse('home'))
        self.assertContains(response, '<span>%s</span>' % settings.SITE_NAME)
        self.assertContains(response, '%s হোম' % settings.SITE_NAME)

    def test_digits_stay_ascii(self):
        html = bangla_client().get(reverse('home')).content.decode()
        self.assertEqual(re.findall('[০-৯]', html), [])

    def test_bangla_text_uses_the_bangla_font_stack(self):
        css = (settings.BASE_DIR / 'staticfiles' / 'css' / 'bloodbond.css').read_text(
            encoding='utf-8')
        self.assertIn('html[lang="bn"]', css)
        self.assertRegex(css, r'html\[lang="bn"\] \{\s*--bb-font: "Hind Siliguri"')

    def test_404_page_in_bangla(self):
        response = bangla_client().get('/no-such-page-anywhere/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, NOT_FOUND_BN, status_code=404)
        self.assertContains(response, '<html lang="bn"', status_code=404)

    def test_404_page_in_english(self):
        response = self.client.get('/no-such-page-anywhere/')
        self.assertContains(
            response, 'We could not find that page', status_code=404)

    def test_csrf_failure_page_in_bangla(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies[COOKIE] = 'bn'
        response = client.post(reverse('login'), {'phone': '1', 'password': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'পাতাটির মেয়াদ শেষ হয়ে গেছে', status_code=403)

    def test_login_error_message_in_bangla(self):
        client = bangla_client()
        response = client.post(
            reverse('login'), {'phone': '01911100099', 'password': 'wrong-pass'})
        self.assertContains(response, 'ফোন নম্বর ও পাসওয়ার্ড মিলছে না')


class AcceptLanguageTests(TestCase):
    def test_first_time_visitor_with_bangla_browser_gets_bangla(self):
        response = self.client.get('/', HTTP_ACCEPT_LANGUAGE='bn')
        self.assertContains(response, '<html lang="bn"')
        self.assertContains(response, HOME_BN)

    def test_bangladeshi_locale_variant_gets_bangla(self):
        response = self.client.get('/', HTTP_ACCEPT_LANGUAGE='bn-BD,bn;q=0.9,en;q=0.5')
        self.assertContains(response, '<html lang="bn"')

    def test_english_browser_gets_english(self):
        response = self.client.get('/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
        self.assertContains(response, '<html lang="en"')

    def test_unsupported_browser_language_falls_back_to_english(self):
        response = self.client.get('/', HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9')
        self.assertContains(response, '<html lang="en"')
        self.assertContains(response, HOME_EN)

    def test_english_is_still_available_to_a_bangla_browser(self):
        client = Client()
        client.post(SET_LANGUAGE, {'language': 'en', 'next': '/'},
                    HTTP_ACCEPT_LANGUAGE='bn')
        response = client.get('/', HTTP_ACCEPT_LANGUAGE='bn')
        self.assertContains(response, '<html lang="en"')
        self.assertContains(response, HOME_EN)

    def test_cookie_beats_the_browser_header(self):
        client = bangla_client()
        response = client.get('/', HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(response, '<html lang="bn"')

    def test_responses_vary_on_the_language_inputs(self):
        response = self.client.get('/')
        vary = response.get('Vary', '')
        self.assertIn('Accept-Language', vary)
        self.assertIn('Cookie', vary)

    def test_content_language_header(self):
        response = bangla_client().get('/')
        self.assertEqual(response['Content-Language'], 'bn')


class SwitcherMarkupTests(TestCase):
    def switcher_buttons(self, response):
        html = response.content.decode()
        return re.findall(
            r'<button type="submit" name="language" value="(en|bn)" lang="(?:en|bn)"([^>]*)>', html)

    def test_english_is_marked_current_in_english(self):
        buttons = self.switcher_buttons(self.client.get('/'))
        self.assertGreaterEqual(len(buttons), 4)  # nav and footer, two each
        for value, attrs in buttons:
            if value == 'en':
                self.assertIn('aria-current="true"', attrs)
            else:
                self.assertNotIn('aria-current', attrs)

    def test_bangla_is_marked_current_in_bangla(self):
        buttons = self.switcher_buttons(bangla_client().get('/'))
        for value, attrs in buttons:
            if value == 'bn':
                self.assertIn('aria-current="true"', attrs)
            else:
                self.assertNotIn('aria-current', attrs)

    def test_bangla_label_has_a_lang_attribute(self):
        html = self.client.get('/').content.decode()
        self.assertIn('lang="bn">বাংলা</button>', html)
        self.assertRegex(html, r'lang="en"[^>]*>EN</button>')

    def test_switcher_is_in_the_account_menu_for_members(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(
            phone='01911100002', password='pw-for-tests-123')
        self.client.force_login(user)
        html = self.client.get('/').content.decode()
        menu = html.split('class="bb-menu__panel"')[1].split('</header>')[0]
        self.assertIn('set-language', menu)

    def test_anonymous_visitors_can_switch(self):
        html = self.client.get('/').content.decode()
        self.assertIn('action="/i18n/set-language/"', html)

    def test_switcher_is_a_labelled_group(self):
        html = self.client.get('/').content.decode()
        self.assertRegex(
            html, r'<form class="bb-lang [^"]*" method="post" action="/i18n/set-language/" role="group" aria-label="Language">')


class FallbackTests(TestCase):
    def test_untranslated_msgid_falls_back_to_english(self):
        template = Template(
            '{% load i18n %}{% trans "A sentence nobody translated yet" %}')
        with translation.override('bn'):
            self.assertEqual(
                template.render(Context()), 'A sentence nobody translated yet')

    def test_untranslated_blocktrans_falls_back_to_english(self):
        template = Template(
            '{% load i18n %}{% blocktrans with n=3 %}{{ n }} brand new things'
            '{% endblocktrans %}')
        with translation.override('bn'):
            self.assertEqual(template.render(Context()), '3 brand new things')

    def test_no_translated_string_is_blank(self):
        from core import i18n_check
        blank = [e.msgid for e in i18n_check.load_po()
                 if e.msgid and any(not t.strip() for t in e.msgstr.values())]
        self.assertEqual(blank, [])

    def test_plural_rule(self):
        from django.utils.translation import ngettext
        with translation.override('bn'):
            self.assertEqual(ngettext('%(n)s month', '%(n)s months', 4) % {'n': 4}, '4 মাস')
            self.assertEqual(ngettext('%(n)s month', '%(n)s months', 1) % {'n': 1}, '1 মাস')

    def test_dates_keep_ascii_digits(self):
        import datetime
        from django.utils.formats import date_format
        day = datetime.date(2026, 3, 9)
        with translation.override('en'):
            self.assertEqual(date_format(day, 'M j, Y'), 'Mar 9, 2026')
        with translation.override('bn'):
            self.assertRegex(date_format(day, 'j'), r'^9$')
            self.assertEqual(date_format(day, 'Y'), '2026')

    def test_site_tagline_is_translated_only_when_it_is_the_default(self):
        from pages.context_processors import site
        with translation.override('bn'):
            self.assertEqual(
                site(None)['SITE_TAGLINE'], 'রক্ত দিন। বন্ধন গড়ুন। জীবন বাঁচান।')
        with translation.override('en'):
            self.assertEqual(
                site(None)['SITE_TAGLINE'], 'Give blood. Build bonds. Save lives.')


class PrivacyInBanglaTests(TestCase):
    """The privacy rules do not depend on the language: crawl the audit's
    pages in Bangla and make sure no phone number or email of another member
    appears."""

    @classmethod
    def setUpTestData(cls):
        cls.cast = build_cast()
        cls.users = cls.cast.users

    # Reuse the audit's rules (they only read `self.cast`).
    always_forbidden = audit.PrivacyAuditCase.always_forbidden
    allowed = audit.PrivacyAuditCase.allowed

    def test_no_phone_or_email_of_another_member_in_bangla(self):
        banned = {
            secret for key in audit.PEOPLE
            for secret in audit.phones_and_emails(key)
        } | set(audit.CONTACT_PHONES.values())
        problems = []
        paths = crawl_paths(self.cast)
        for viewer in (None, 'stranger', 'directory', 'asker_ok', 'staff'):
            client = bangla_client()
            if viewer:
                client.force_login(self.users[viewer])
            for name, path in paths:
                response = client.get(path)
                if response.status_code >= 500:
                    problems.append('%s GET %s: %s' % (viewer, path, response.status_code))
                    continue
                if response.status_code in (301, 302):
                    body = response['Location']
                else:
                    body = response.content.decode('utf-8', 'replace')
                allowed = self.allowed(viewer, name, path)
                for secret in sorted(banned - allowed):
                    if secret in body:
                        problems.append('%s GET %s leaks %r' % (viewer or 'anonymous', path, secret))
        self.maxDiff = None
        self.assertEqual(problems, [])

    def test_names_of_protected_people_stay_hidden_in_bangla(self):
        banned = self.always_forbidden()
        problems = []
        client = bangla_client()
        for name, path in crawl_paths(self.cast):
            response = client.get(path)
            if response.status_code != 200:
                continue
            body = response.content.decode('utf-8', 'replace')
            allowed = self.allowed(None, name, path)
            for secret in sorted(banned - allowed):
                if secret in body:
                    problems.append('anonymous GET %s leaks %r' % (path, secret))
        self.maxDiff = None
        self.assertEqual(problems, [])

    def test_language_cookie_holds_nothing_personal(self):
        client = Client()
        client.force_login(self.users['stranger'])
        response = client.post(SET_LANGUAGE, {'language': 'bn', 'next': '/'})
        self.assertEqual(response.cookies[COOKIE].value, 'bn')
        self.assertNotIn(self.users['stranger'].phone, response['Location'])
