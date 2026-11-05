"""Cross-page checks for everything an anonymous visitor (and the auth flows)
can reach: one h1, unique titles, no leftover old name or old-theme classes,
every internal link and static file resolves, plus regressions for bugs found
in the browser crawl (hidden-on-mobile button, dark-mode contrast tokens,
footer under the tab bar, wrapping desktop nav, friendly CSRF page, ...)."""
import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from rewards.models import BloodRequest
from stories.models import Story

User = get_user_model()

CSS = (Path(settings.BASE_DIR) / 'staticfiles' / 'css' / 'bloodbond.css').read_text(
    encoding='utf-8',
)
OLD_CLASSES = ('fw-700', 'btn-danger', 'form-control', 'bg-current', 'me-4')


def public_pages(blood_request, story):
    return [
        reverse('home'), reverse('learn'), reverse('faq'), reverse('about'),
        reverse('blogapp:blog-view'), reverse('impact'),
        reverse('stories'), reverse('leaderboard'),
        reverse('squads:leaderboard'), reverse('request-list'),
        reverse('request-detail', args=[blood_request.pk]),
        reverse('story-detail', args=[story.pk]),
        '/impact/district/Dhaka/',
        reverse('login'), reverse('signup'), reverse('password_reset'),
        reverse('password_reset_done'), reverse('password_reset_complete'),
        '/accounts/reset/bad/token/', reverse('offline'),
    ]


class PublicCrawlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(phone='01711000001', password='x-Pass-1234')
        cls.user.first_name = 'Karim'
        cls.user.save()
        cls.blood_request = BloodRequest.objects.create(
            requester=cls.user, blood_group='O+', hospital='DMCH',
            district='Dhaka', needed_by=timezone.localdate() + timedelta(days=2),
            contact_phone='01711000001',
        )
        cls.story = Story.objects.create(
            author=cls.user, kind=Story.Kind.DONOR, title='My first donation',
            body='I donated blood for the first time and it was calm and kind. ' * 3,
            status=Story.Status.APPROVED,
        )
        client = Client()
        cls.pages = {}
        for path in public_pages(cls.blood_request, cls.story):
            response = client.get(path)
            cls.pages[path] = response

    def test_every_page_renders(self):
        for path, response in self.pages.items():
            self.assertEqual(response.status_code, 200, path)

    def test_exactly_one_h1_and_unique_title(self):
        titles = {}
        for path, response in self.pages.items():
            html = response.content.decode()
            self.assertEqual(len(re.findall(r'<h1[\s>]', html)), 1, path)
            title = re.search(r'<title>(.*?)</title>', html, re.S).group(1).strip()
            self.assertTrue(title.endswith(settings.SITE_NAME), (path, title))
            titles.setdefault(title, []).append(path)
        duplicates = {t: p for t, p in titles.items() if len(p) > 1}
        self.assertEqual(duplicates, {})

    def test_no_old_name_or_old_theme_classes(self):
        for path, response in self.pages.items():
            html = response.content.decode()
            self.assertNotIn('lagbe', html.lower(), path)
            for cls in OLD_CLASSES:
                self.assertNotRegex(html, rf'class="[^"]*\b{cls}\b', (path, cls))

    def test_every_internal_link_resolves(self):
        links = set()
        for response in self.pages.values():
            links.update(re.findall(r'href="(/[^"#?]*)"', response.content.decode()))
        links = {
            link for link in links
            if not link.startswith(('/static/', '/media/', '/admin/'))
        }
        self.assertGreater(len(links), 20)
        client = Client()
        for link in sorted(links):
            status = client.get(link).status_code
            self.assertIn(status, (200, 301, 302), (link, status))

    def test_every_static_file_exists(self):
        refs = set()
        for response in self.pages.values():
            refs.update(re.findall(r'(?:href|src)="/static/([^"?#]+)', response.content.decode()))
        self.assertGreater(len(refs), 5)
        for ref in refs:
            self.assertIsNotNone(finders.find(ref), ref)

    def test_no_phone_number_on_public_pages(self):
        for path, response in self.pages.items():
            html = response.content.decode()
            html = html.replace('01712345678', '')  # the sign-up placeholder
            self.assertNotRegex(html, r'01[3-9]\d{8}', path)

    def test_nav_and_tabbar_marks_current_section(self):
        html = self.pages[reverse('request-list')].content.decode()
        self.assertRegex(html, r'class="is-active"[^>]*>Urgent requests<')
        self.assertRegex(html, r'class="is-active"[^>]*><i class="feather-droplet"')

    def test_images_have_alt_and_inputs_have_labels(self):
        for path, response in self.pages.items():
            html = response.content.decode()
            for tag in re.findall(r'<img\b[^>]*>', html):
                self.assertIn('alt=', tag, (path, tag))
        html = self.pages[reverse('signup')].content.decode()
        for field_id in re.findall(r'<input[^>]*type="(?:text|tel|email|password)"[^>]*id="([^"]+)"', html):
            self.assertIn(f'for="{field_id}"', html, field_id)


class SharedShellTests(TestCase):
    def test_theme_is_restored_before_first_paint(self):
        html = self.client.get(reverse('home')).content.decode()
        head = html.split('</head>')[0]
        self.assertIn("localStorage.getItem('bb-theme')", head)

    def test_skip_link_target_is_focusable(self):
        html = self.client.get(reverse('home')).content.decode()
        self.assertIn('href="#main"', html)
        self.assertRegex(html, r'<main id="main"[^>]*tabindex="-1"')


class CssRegressionTests(TestCase):
    """The stylesheet is the design contract, so pin the rules that broke."""

    def test_hide_sm_is_not_overridden_by_buttons(self):
        # .bb-btn is display:inline-flex and comes later in the file, so a
        # plain `display: none` lost: the Log in button wrapped on phones.
        self.assertRegex(
            CSS,
            r'@media \(max-width: 959\.98px\) \{ \.bb-hide-sm \{ display: none !important; \} \}',
        )
        self.assertNotRegex(CSS, r'\.bb-hide-sm \{ display: initial')

    def test_footer_clears_the_fixed_tab_bar_on_phones(self):
        footer = re.search(r'\n\.bb-footer \{([^}]*)\}', CSS).group(1)
        self.assertIn('var(--bb-tabbar-h)', footer)
        self.assertIn('env(safe-area-inset-bottom)', footer)
        self.assertRegex(
            CSS, r'@media \(min-width: 960px\) \{ \.bb-footer \{ padding-bottom: 24px; \} \}',
        )

    def test_desktop_nav_links_do_not_wrap(self):
        self.assertRegex(CSS, r'\.bb-nav__links a \{ white-space: nowrap; \}')
        self.assertIn('max-width: 1149.98px', CSS)

    def test_dark_mode_overrides_text_safe_colours(self):
        tokens = ('--bb-red-text', '--bb-red-ink', '--bb-ok-text',
                  '--bb-warn-text', '--bb-info-text')
        dark_media = CSS.split('@media (prefers-color-scheme: dark)')[1].split('/* ---------- Reset')[0]
        for token in tokens:
            self.assertIn(token, CSS.split('@media (prefers-color-scheme: dark)')[0])
            self.assertEqual(dark_media.count(f'{token}:'), 2, token)

    def test_text_uses_tokens_not_raw_brand_red(self):
        # Raw #d8213a / --bb-red as *text* fails contrast on dark surfaces.
        for name in ('bloodbond.css', 'pages.css', 'community.css'):
            css = (Path(settings.BASE_DIR) / 'staticfiles' / 'css' / name).read_text(encoding='utf-8')
            self.assertNotRegex(css, r'(?<![-\w])color:\s*var\(--bb-red\)', name)

    def test_success_button_and_matrix_ticks_have_readable_contrast(self):
        self.assertIn('.bb-btn--ok { background: #067647; color: #fff; }', CSS)
        pages_css = (Path(settings.BASE_DIR) / 'staticfiles' / 'css' / 'pages.css').read_text(encoding='utf-8')
        self.assertIn('.pg-compat td.is-ok { background: #067647', pages_css)


class TierLabelTests(TestCase):
    def test_tier_label_keeps_text_readable(self):
        # Silver and bronze colours fail contrast as text, so the colour goes
        # on the icon only (through --tier).
        user = User.objects.create_user(phone='01711000009', password='x-Pass-1234')
        user.first_name = 'Rina'
        user.save()
        from rewards.models import Badge, Donation
        Badge.objects.get_or_create(
            name='Bronze Donor', badge_type=Badge.Type.TIER,
            defaults={'min_donations': 1, 'color': '#cd7f32', 'icon': 'award'},
        )
        Donation.objects.create(
            donor=user, donation_date=timezone.localdate() - timedelta(days=200),
            location='DMCH', status=Donation.Status.APPROVED,
        )
        for name in ('home', 'leaderboard'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotRegex(html, r'class="[^"]*cm-tier[^"]*" style="color:')
            self.assertIn('style="--tier:', html)


class FriendlyErrorPageTests(TestCase):
    def test_csrf_failure_shows_a_friendly_page(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('login'), {'phone': '01711000001', 'password': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, '403_csrf.html')
        self.assertContains(response, 'That page expired', status_code=403)
        self.assertContains(response, settings.SITE_NAME, status_code=403)
        self.assertContains(response, reverse('login'), status_code=403)

    def test_not_found_page_is_branded(self):
        response = self.client.get('/definitely/not/here/')
        self.assertEqual(response.status_code, 404)
        html = response.content.decode()
        self.assertIn(f'| {settings.SITE_NAME}', html)
        self.assertEqual(len(re.findall(r'<h1[\s>]', html)), 1)


class AuthMessageTests(TestCase):
    def test_signup_phone_message_says_what_to_type(self):
        response = self.client.post(reverse('signup'), {
            'first_name': 'Rahim', 'phone': '12345',
            'password1': 'Sturdy-pass-4821', 'password2': 'Sturdy-pass-4821',
        })
        self.assertContains(response, 'Enter a Bangladesh mobile number, like 01712345678.')
        self.assertNotContains(response, 'Invalid Phone number')

    def test_signup_duplicate_phone_message_points_to_login(self):
        User.objects.create_user(phone='01712345678', password='x-Pass-1234')
        response = self.client.post(reverse('signup'), {
            'first_name': 'Rahim', 'phone': '01712345678',
            'password1': 'Sturdy-pass-4821', 'password2': 'Sturdy-pass-4821',
        })
        self.assertContains(response, 'This phone number already has an account. Try logging in instead.')
        self.assertNotContains(response, 'User with this Mobile Phone')

    def test_expired_reset_link_has_its_own_title(self):
        html = self.client.get('/accounts/reset/bad/token/').content.decode()
        self.assertIn('<title>Link expired |', html)

    def test_logout_message_shows_once(self):
        user = User.objects.create_user(phone='01711000001', password='x-Pass-1234')
        self.client.force_login(user)
        self.client.get(reverse('logout'))
        first = self.client.get(reverse('home'))
        self.assertContains(first, 'You are logged out')
        second = self.client.get(reverse('home'))
        self.assertNotContains(second, 'You are logged out')
