import json
import xml.etree.ElementTree as ET

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from blogapp.models import Author, Post
from pages.content_views import compatibility_matrix
from rewards.blood import COMPATIBLE_DONORS

User = get_user_model()

SM = '{http://www.sitemaps.org/schemas/sitemap/0.9}'


class LearnTests(TestCase):
    def test_renders_with_sections_and_disclaimer(self):
        response = self.client.get(reverse('learn'))
        self.assertEqual(response.status_code, 200)
        for anchor in ('eligibility', 'blood-types', 'on-the-day',
                       'after-care', 'myths'):
            self.assertContains(response, f'id="{anchor}"')
        self.assertContains(response, '<strong>The blood bank decides.</strong>')
        self.assertContains(response, 'not medical clearance')

    def test_criteria_list_is_present_without_js(self):
        # Server-rendered, so it works when JS is off.
        response = self.client.get(reverse('learn'))
        self.assertContains(response, 'Age 18 to 60')
        self.assertContains(response, 'Weight at least 45 kg')

    def test_checker_gets_the_configured_interval(self):
        with override_settings(DONATION_INTERVAL_DAYS=90):
            response = self.client.get(reverse('learn'))
        self.assertContains(response, 'data-interval-days="90"')
        self.assertContains(response, '3 months')

    def test_matrix_matches_rewards_blood_data(self):
        for row in compatibility_matrix():
            ok = [c['donor'] for c in row['cells'] if c['ok']]
            self.assertEqual(ok, [
                g for g in [c['donor'] for c in row['cells']]
                if g in COMPATIBLE_DONORS[row['recipient']]
            ])
            self.assertEqual(
                sorted(ok), sorted(COMPATIBLE_DONORS[row['recipient']]),
            )
            for target in row['can_give_to']:
                self.assertIn(row['recipient'], COMPATIBLE_DONORS[target])

    def test_matrix_known_facts(self):
        rows = {r['recipient']: r for r in compatibility_matrix()}
        self.assertEqual(rows['O-']['can_receive_from'], ['O-'])
        self.assertEqual(len(rows['AB+']['can_receive_from']), 8)
        self.assertEqual(len(rows['O-']['can_give_to']), 8)
        self.assertEqual(rows['AB+']['can_give_to'], ['AB+'])

    def test_table_renders_a_tick_per_compatible_pair(self):
        response = self.client.get(reverse('learn'))
        expected = sum(len(v) for v in COMPATIBLE_DONORS.values())
        self.assertEqual(response.content.decode().count('&#10003;'), expected)

    def test_anonymous_visit_does_not_touch_session(self):
        self.client.get(reverse('learn'))
        self.assertNotIn('read_guide', self.client.session)

    def test_member_visit_marks_guide_read(self):
        user = User.objects.create_user(phone='01711000001', password='x')
        self.client.force_login(user)
        self.client.get(reverse('learn'))
        self.assertTrue(self.client.session['read_guide'])

    def test_no_hardcoded_old_name(self):
        self.assertNotContains(self.client.get(reverse('learn')), 'Blood Lagbe')


class FaqAboutTests(TestCase):
    def test_faq_uses_native_details_and_covers_topics(self):
        response = self.client.get(reverse('faq'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertGreaterEqual(html.count('<details>'), 10)
        for topic in ('Privacy', 'Verification', 'Phone numbers',
                      'Points and badges', 'Blood requests'):
            self.assertContains(response, topic)
        self.assertEqual(html.count('<h1'), 1)

    def test_faq_does_not_promise_public_phone_numbers(self):
        self.assertContains(
            self.client.get(reverse('faq')), 'never shown on public pages',
        )

    def test_about_renders_core_sections(self):
        response = self.client.get(reverse('about'))
        self.assertEqual(response.status_code, 200)
        for text in ('Our mission', 'How it is run', 'How privacy works',
                     'Safety principles'):
            self.assertContains(response, text)

    @override_settings(SITE_CONTACT_EMAIL='')
    def test_about_without_contact_email(self):
        self.assertNotContains(self.client.get(reverse('about')), 'mailto:')

    @override_settings(SITE_CONTACT_EMAIL='hello@example.org')
    def test_about_with_contact_email(self):
        self.assertContains(
            self.client.get(reverse('about')), 'mailto:hello@example.org',
        )

    @override_settings(SITE_NAME='TestBond', SITE_TAGLINE='Tag line here.')
    def test_about_uses_site_name_setting(self):
        response = self.client.get(reverse('about'))
        self.assertContains(response, 'TestBond')
        self.assertContains(response, 'Tag line here.')


class RobotsTests(TestCase):
    def test_robots_disallows_private_areas_and_links_sitemap(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')
        body = response.content.decode()
        for path in ('/admin/', '/accounts/', '/welcome/', '/donors/',
                     '/donations/'):
            self.assertIn(f'Disallow: {path}', body)
        self.assertIn('User-agent: *', body)
        self.assertIn('Sitemap: http://testserver/sitemap.xml', body)
        # Public pages stay crawlable.
        self.assertNotIn('Disallow: /learn', body)
        self.assertNotIn('Disallow: /\n', body)

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post('/robots.txt').status_code, 405)


class SitemapTests(TestCase):
    def locs(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertIn('xml', response['Content-Type'])
        root = ET.fromstring(response.content)
        return [e.find(f'{SM}loc').text for e in root.findall(f'{SM}url')]

    def test_static_pages_listed(self):
        locs = self.locs()
        for name in ('home', 'learn', 'faq', 'about', 'impact', 'stories',
                     'request-list', 'leaderboard', 'blogapp:blog-view'):
            self.assertIn('http://testserver' + reverse(name), locs, name)

    def test_private_pages_not_listed(self):
        joined = ' '.join(self.locs())
        for private in ('/accounts/', '/welcome/', '/donors/', '/admin/',
                        '/donations/'):
            self.assertNotIn(private, joined)

    def test_blog_posts_listed(self):
        author = Author.objects.create(
            author=User.objects.create_user(phone='01711000009', password='x'),
        )
        post = Post.objects.create(title='Why give', author=author, overview='Text')
        self.assertIn('http://testserver' + post.get_absolute_url(), self.locs())


class ManifestAndWorkerTests(TestCase):
    def test_manifest_is_valid_json_with_site_name(self):
        with override_settings(SITE_NAME='TestBond'):
            response = self.client.get(reverse('manifest'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        data = json.loads(response.content)
        self.assertEqual(data['name'], 'TestBond')
        self.assertEqual(data['display'], 'standalone')
        self.assertEqual(data['start_url'], '/')
        self.assertEqual(data['theme_color'], '#d8213a')

    def test_manifest_icons_exist_on_disk(self):
        data = json.loads(self.client.get(reverse('manifest')).content)
        sizes = {i['sizes'] for i in data['icons']}
        self.assertTrue({'192x192', '512x512'} <= sizes)
        for icon in data['icons']:
            relative = icon['src'].replace('/static/', '', 1)
            self.assertTrue(finders.find(relative), icon['src'])
        self.assertIn('maskable', {i['purpose'] for i in data['icons']})

    def test_manifest_and_worker_linked_from_pages(self):
        html = self.client.get(reverse('learn')).content.decode()
        self.assertIn('rel="manifest"', html)
        self.assertIn(reverse('manifest'), html)
        self.assertIn('serviceWorker', html)
        self.assertIn('try {', html)

    def test_service_worker_headers_and_scope(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript')
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn('no-cache', response['Cache-Control'])

    def test_service_worker_never_caches_pages_or_data(self):
        body = self.client.get('/sw.js').content.decode()
        self.assertIn(reverse('offline'), body)
        self.assertIn("request.method !== 'GET'", body)
        # Navigations go to the network; only static files are put in cache.
        self.assertIn("request.mode === 'navigate'", body)
        self.assertEqual(body.count('cache.put('), 1)
        self.assertIn('isStaticAsset(url)', body)
        self.assertIn('/static/css/bloodbond.css', body)

    def test_offline_page_is_standalone_and_noindex(self):
        response = self.client.get(reverse('offline'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You are offline')
        self.assertContains(response, 'noindex')
        self.assertNotContains(response, 'bb-tabbar')


class ErrorPageTests(TestCase):
    def test_404_is_branded_and_has_a_way_out(self):
        response = self.client.get('/definitely/not/here/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'We could not find that page', status_code=404)
        self.assertContains(response, reverse('home'), status_code=404)
        self.assertContains(response, 'bb-tabbar', status_code=404)

    def test_403_template_renders(self):
        html = render_to_string('403.html', {'user': None})
        self.assertIn('You cannot open this page', html)

    def test_500_template_is_standalone(self):
        html = render_to_string('500.html', {})
        self.assertIn('Something went wrong', html)
        self.assertIn('<style>', html)
        self.assertNotIn('bloodbond.css', html)
        self.assertNotIn('bb-nav', html)

    def test_500_handler_serves_template_when_a_view_crashes(self):
        client = Client(raise_request_exception=False)
        with override_settings(ROOT_URLCONF='pages.test_urls_crash'):
            response = client.get('/boom/')
        self.assertEqual(response.status_code, 500)
        self.assertIn('Something went wrong', response.content.decode())


class BaseTemplateTests(TestCase):
    def test_no_theme_leftovers_on_content_pages(self):
        for name in ('learn', 'faq', 'about', 'login', 'signup'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotIn('Blood Lagbe', html, name)
            self.assertNotIn('btn-danger', html, name)
            self.assertEqual(html.count('<h1'), 1, name)
