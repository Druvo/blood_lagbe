# flake8: noqa: E501 (long literal lines in tables of URLs and payloads)
"""Accessibility audit: structural checks on every rendered page (one h1,
heading order, labels, names for icon buttons and links, alt text, landmarks,
skip link, ids, tables, charts, live regions) plus design-token checks on the
stylesheet (contrast of the colour pairs, focus, reduced motion, tap size).
"""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from rewards.audit_fixtures import build_cast, crawl_paths

CSS_DIR = Path(settings.BASE_DIR) / 'staticfiles' / 'css'

# Pages that are not HTML documents (or are only a redirect/an error).
NON_HTML = {
    'robots', 'sitemap', 'manifest', 'service-worker', 'impact-data',
    'impact-export', 'donation-export',
}
VOID = {'input', 'img', 'br', 'hr', 'meta', 'link', 'source', 'wbr', 'area',
        'col', 'embed', 'param', 'track'}
INTERACTIVE_INPUT_SKIP = {'hidden', 'submit', 'button', 'image', 'reset'}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []
        self.text = []

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def hidden(self):
        for node in [self, *self.ancestors()]:
            if 'hidden' in node.attrs or node.attrs.get('aria-hidden') == 'true':
                return True
        return False

    def own_text(self):
        return ''.join(self.text)

    def accessible_text(self):
        """Visible text plus aria-labels of descendants (aria-hidden parts
        and icon-only <i> elements do not count)."""
        if self.attrs.get('aria-hidden') == 'true':
            return ''
        parts = [self.own_text()]
        if self.tag == 'img' and self.attrs.get('alt'):
            parts.append(self.attrs['alt'])
        if self.tag == 'svg':
            parts.append(self.attrs.get('aria-label', ''))
        for child in self.children:
            if child.tag == 'script' or child.tag == 'style':
                continue
            parts.append(child.accessible_text())
        return ' '.join(parts)


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root', {}, None)
        self.current = self.root
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        self.nodes.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        self.nodes.append(node)

    def handle_endtag(self, tag):
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data):
        if self.current.tag in ('script', 'style'):
            return
        self.current.text.append(data)

    def find(self, tag):
        return [n for n in self.nodes if n.tag == tag]


def parse(html):
    page = Page()
    page.feed(html)
    return page


def label_targets(page):
    """ids that a <label for=...> points at, plus wrapped controls."""
    ids = set()
    for label in page.find('label'):
        if label.attrs.get('for'):
            ids.add(label.attrs['for'])
    return ids


def problems_in(html, name):
    page = parse(html)
    found = []
    html_tags = page.find('html')
    if not html_tags or not html_tags[0].attrs.get('lang'):
        found.append('<html> has no lang attribute')

    h1 = [n for n in page.find('h1')]
    if len(h1) != 1:
        found.append(f'{len(h1)} <h1> elements (want exactly 1)')
    elif not h1[0].accessible_text().strip():
        found.append('<h1> is empty')

    last = 0
    for node in page.nodes:
        if re.fullmatch(r'h[1-6]', node.tag):
            level = int(node.tag[1])
            if last and level > last + 1:
                found.append(
                    f'heading jumps from h{last} to h{level}: '
                    f'{node.accessible_text().strip()[:40]!r}'
                )
            last = level

    # The offline page is a standalone document (the service worker shows it
    # without the site shell), so it only needs <main>.
    standalone = name == reverse('offline')
    for tag in (('main',) if standalone else ('header', 'nav', 'main', 'footer')):
        if not page.find(tag):
            found.append(f'no <{tag}> landmark')
    if len(page.find('main')) > 1:
        found.append('more than one <main>')

    ids = {}
    for node in page.nodes:
        if node.attrs.get('id'):
            ids.setdefault(node.attrs['id'], 0)
            ids[node.attrs['id']] += 1
    for ident, count in ids.items():
        if count > 1:
            found.append(f'duplicate id {ident!r} ({count}x)')
    skip = [n for n in page.find('a') if n.attrs.get('href', '').startswith('#main')]
    if standalone:
        pass
    elif not skip:
        found.append('no skip link to #main')
    elif 'main' not in ids:
        found.append('skip link target #main does not exist')

    labelled = label_targets(page)
    for node in page.nodes:
        if node.tag not in ('input', 'select', 'textarea'):
            continue
        if node.attrs.get('type', 'text').lower() in INTERACTIVE_INPUT_SKIP:
            continue
        if node.hidden() and 'hidden' in node.attrs:
            continue
        named = (
            node.attrs.get('aria-label') or node.attrs.get('aria-labelledby')
            or node.attrs.get('title')
            or (node.attrs.get('id') in labelled)
            or any(a.tag == 'label' for a in node.ancestors())
        )
        if not named:
            ident = node.attrs.get('name') or node.attrs.get('id')
            found.append(f'<{node.tag} name={ident!r}> has no label')

    for node in page.find('button'):
        text = node.accessible_text().strip()
        if not (text or node.attrs.get('aria-label') or node.attrs.get('title')
                or node.attrs.get('aria-labelledby')):
            found.append(f'icon-only <button> with no name: {node.attrs}')
    for node in page.find('a'):
        if not node.attrs.get('href') or node.hidden():
            continue  # (a hidden link is a duplicate of a named one)
        text = node.accessible_text().strip()
        if not (text or node.attrs.get('aria-label') or node.attrs.get('title')
                or node.attrs.get('aria-labelledby')):
            found.append(f'link with no name: {node.attrs.get("href")!r}')
        if node.attrs.get('target') == '_blank' and 'noopener' not in node.attrs.get('rel', ''):
            found.append(f'target=_blank without rel=noopener: {node.attrs["href"]!r}')
    for node in page.find('img'):
        if 'alt' not in node.attrs:
            found.append(f'<img> without alt: {node.attrs.get("src")!r}')
    for node in page.find('svg'):
        if node.parent and node.parent.tag == 'button':
            continue
        if node.attrs.get('aria-hidden') == 'true':
            continue
        classes = node.attrs.get('class', '')
        if 'bb-chart' in classes:
            titles = [c for c in node.children if c.tag == 'title']
            if node.attrs.get('role') != 'img' or not (
                titles or node.attrs.get('aria-label')
            ):
                found.append('chart <svg> needs role="img" and a <title>')
    for node in page.find('th'):
        if 'scope' not in node.attrs:
            found.append(f'<th> without scope: {node.accessible_text().strip()[:30]!r}')
    for node in page.find('meta'):
        if node.attrs.get('name') == 'viewport' and 'user-scalable=no' in node.attrs.get('content', ''):
            found.append('viewport blocks zooming')
    if not any(n.accessible_text().strip() for n in page.find('title')):
        found.append('empty <title>')
    for node in page.find('table'):
        if not [n for n in page.nodes if n.tag in ('th',) and node in n.ancestors()]:
            found.append('<table> without header cells')
    return found


class RenderedPageTests(TestCase):
    VIEWERS = (None, 'owner', 'directory', 'squad_owner', 'visible', 'flagger',
               'staff')

    @classmethod
    def setUpTestData(cls):
        cls.cast = build_cast()

    def pages(self):
        return [
            (name, path) for name, path in crawl_paths(self.cast)
            if name not in NON_HTML
        ]

    def test_every_page_passes_the_structural_checks(self):
        report = {}
        for viewer in self.VIEWERS:
            client = Client()
            if viewer:
                client.force_login(self.cast.users[viewer])
            for name, path in self.pages():
                response = client.get(path)
                if response.status_code in (301, 302, 405):
                    continue
                if 'html' not in response.get('Content-Type', ''):
                    continue
                for issue in problems_in(response.content.decode(), path):
                    report.setdefault(f'{path}: {issue}', set()).add(viewer or 'anon')
        lines = [f'{key} [{", ".join(sorted(who))}]' for key, who in sorted(report.items())]
        self.maxDiff = None
        self.assertEqual('\n'.join(lines), '')

    def test_error_pages_have_the_basics(self):
        client = Client()
        for response in (client.get('/no/such/page/'),):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(problems_in(response.content.decode(), '404'), [])
        client.force_login(self.cast.users['owner'])
        forbidden = client.get(reverse(
            'donation-delete', args=[self.cast.owner_approved.pk]))
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(problems_in(forbidden.content.decode(), '403'), [])

    def test_current_section_is_marked_in_the_navigation(self):
        client = Client()
        cases = {
            reverse('request-list'): 'Urgent requests',
            reverse('stories'): 'Stories',
        }
        for path, label in cases.items():
            page = parse(client.get(path).content.decode())
            current = [
                n.accessible_text().strip() for n in page.find('a')
                if n.attrs.get('aria-current') == 'page'
            ]
            self.assertIn(label, current, path)
        # Leaderboard, Impact and Learn live on the home page, not the nav
        page = parse(client.get(reverse('faq')).content.decode())
        self.assertFalse(any(
            n.attrs.get('aria-current') == 'page' for n in page.find('a')))

    def test_home_page_links_to_leaderboard_impact_and_learn(self):
        html = Client().get(reverse('home')).content.decode()
        section = html.split('id="explore"', 1)[1].split('</section>', 1)[0]
        for name in ('leaderboard', 'impact', 'learn'):
            self.assertIn(f'href="{reverse(name)}"', section, name)

    def test_flash_messages_are_announced(self):
        client = Client()
        client.force_login(self.cast.users['stranger'])
        response = client.post(
            reverse('privacy'),
            {'blood_group': 'A+', 'district': 'Dhaka'}, follow=True,
        )
        page = parse(response.content.decode())
        regions = [n for n in page.nodes if 'bb-messages' in n.attrs.get('class', '')]
        self.assertEqual(len(regions), 1)
        alerts = [n for n in page.nodes
                  if n.attrs.get('role') in ('status', 'alert')
                  and regions[0] in n.ancestors()]
        self.assertTrue(alerts)
        self.assertIn('saved', alerts[0].accessible_text().lower())

    def test_form_errors_are_announced_and_fields_stay_labelled(self):
        client = Client()
        response = client.post(reverse('login'), {'phone': '017', 'password': 'x'})
        page = parse(response.content.decode())
        alerts = [n for n in page.nodes if n.attrs.get('role') == 'alert']
        self.assertTrue(alerts)
        self.assertEqual(problems_in(response.content.decode(), 'login'), [])


# ---------------------------------------------------------------- CSS

def css_text():
    return (CSS_DIR / 'bloodbond.css').read_text(encoding='utf-8')


def token_block(css, selector):
    start = css.index(selector + ' {') + len(selector) + 2
    end = css.index('}', start)
    return dict(re.findall(r'(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;', css[start:end]))


def luminance(color):
    color = color.lstrip('#')
    if len(color) == 3:
        color = ''.join(ch * 2 for ch in color)
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


class StylesheetTests(TestCase):
    TEXT = ('--bb-ink', '--bb-text', '--bb-muted', '--bb-red-text',
            '--bb-red-ink', '--bb-ok-text', '--bb-warn-text', '--bb-info-text')
    SURFACES = ('--bb-bg', '--bb-surface', '--bb-surface-2', '--bb-red-soft',
                '--bb-green-soft', '--bb-amber-soft', '--bb-blue-soft')

    def themes(self):
        css = css_text()
        light = token_block(css, ':root')
        dark = dict(light)
        dark.update(token_block(css, ':root[data-theme="dark"]'))
        auto_dark = dict(light)
        auto_dark.update(token_block(css, ':root:not([data-theme="light"])'))
        return {'light': light, 'dark': dark, 'auto-dark': auto_dark}

    def test_text_tokens_have_enough_contrast_in_light_and_dark(self):
        failures = []
        for theme, tokens in self.themes().items():
            for text in self.TEXT:
                for surface in self.SURFACES:
                    ratio = contrast(tokens[text], tokens[surface])
                    if ratio < 4.5:
                        failures.append(f'{theme}: {text} on {surface} = {ratio:.2f}')
        self.assertEqual(failures, [])

    def test_brand_colours_meet_the_ui_and_button_thresholds(self):
        for theme, tokens in self.themes().items():
            # white text on the brand red buttons (normal size: 4.5)
            self.assertGreaterEqual(contrast('#ffffff', tokens['--bb-red']), 4.5, theme)
            self.assertGreaterEqual(contrast('#ffffff', tokens['--bb-red-dark']), 4.5, theme)
            # focus ring, borders of controls: 3:1 against the page
            self.assertGreaterEqual(
                contrast(tokens['--bb-red-text'], tokens['--bb-bg']), 3.0, theme)
            self.assertGreaterEqual(
                contrast(tokens['--bb-red-text'], tokens['--bb-surface']), 3.0, theme)
        self.assertGreaterEqual(contrast('#ffffff', '#067647'), 4.5)  # .bb-btn--ok

    def test_focus_is_visible_and_motion_can_be_reduced(self):
        css = css_text()
        self.assertRegex(css, r':focus-visible\s*\{[^}]*outline:\s*3px solid')
        self.assertRegex(
            css, r'@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*animation: none')
        self.assertRegex(css, r'\.bb-skip:focus')
        # the skip link must be readable in both themes
        self.assertNotRegex(css, r'\.bb-skip\s*\{[^}]*color:\s*#fff')

    def test_no_outline_is_removed_without_a_replacement(self):
        offenders = []
        for path in CSS_DIR.glob('*.css'):
            if path.name.startswith(('style', 'bootstrap', 'themify', 'fontastic',
                                     'emoji', 'lightbox', 'video', 'feather')) or path.name in (
                                         'custom.css', 'styles-blood.css'):
                continue  # the old, no longer loaded theme and vendor files
            for match in re.finditer(r'([^{}]+)\{([^}]*)\}', path.read_text(encoding='utf-8')):
                selector, body = match.groups()
                if selector.strip() == '#main:focus':
                    continue  # <main> is only focused by the skip link
                if re.search(r'outline:\s*(none|0)\b', body) and not re.search(
                        r'box-shadow|border-color|outline-color', body):
                    offenders.append(f'{path.name}: {selector.strip()[:60]}')
        self.assertEqual(offenders, [])

    def test_tap_targets_are_at_least_44px(self):
        css = css_text()

        def rule(selector):
            match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
            self.assertIsNotNone(match, selector)
            return match.group(1)
        self.assertRegex(rule('.bb-btn'), r'min-height:\s*44px')
        icon = rule('.bb-iconbtn')
        self.assertRegex(icon, r'(width|min-width):\s*(4[4-9]|[5-9]\d)px')
        self.assertRegex(icon, r'(height|min-height):\s*(4[4-9]|[5-9]\d)px')
        self.assertRegex(css, r'\.bb-btn--sm\s*\{[^}]*min-height:\s*(4[4-9]|[5-9]\d)px')
        self.assertRegex(css, r'\.bb-chip\s*\{[^}]*min-height:\s*(4[4-9]|[5-9]\d)px')

    def test_old_theme_sheets_are_not_loaded(self):
        base = (Path(settings.BASE_DIR) / 'templates' / 'base' / 'base.html').read_text(
            encoding='utf-8')
        self.assertNotIn('css/style.css', base)
        self.assertIn('<html lang="{{ LANGUAGE_CODE }}"', base)


class CheckerSelfTests(TestCase):
    """The checks above must actually catch the problems they claim to."""

    BAD = (
        '<html><body><h1>a</h1><h1>b</h1><h4>skip</h4>'
        '<input name="x"><select name="s"></select><textarea name="t"></textarea>'
        '<button><i class="icon"></i></button><a href="/x"><i></i></a>'
        '<img src="a.png"><table><tr><th>h</th></tr></table>'
        '<svg class="bb-chart"></svg><div id="d"></div><div id="d"></div>'
        '<a target="_blank" href="http://e.example">out</a></body></html>'
    )

    def test_a_bad_page_trips_every_structural_check(self):
        issues = ' | '.join(problems_in(self.BAD, '/x/'))
        for needle in (
            'no lang', '2 <h1>', 'jumps from h1 to h4', 'no <main>',
            'no <header>', 'no <nav>', 'no <footer>', 'no skip link',
            "<input name='x'> has no label", "<select name='s'> has no label",
            "<textarea name='t'> has no label", 'icon-only <button>',
            'link with no name', '<img> without alt', '<th> without scope',
            'chart <svg>', "duplicate id 'd'", 'rel=noopener',
        ):
            self.assertIn(needle, issues)

    def test_a_good_page_passes(self):
        good = (
            '<html lang="en"><head><title>T</title></head><body>'
            '<a href="#main">Skip</a><header><nav aria-label="m">'
            '<a href="/">Home</a></nav></header><main id="main"><h1>Hi</h1>'
            '<h2>Sub</h2><label for="n">Name</label><input id="n" name="n">'
            '<button aria-label="Close"><i></i></button>'
            '<img src="a.png" alt=""><table><tr><th scope="col">h</th></tr>'
            '</table><svg class="bb-chart" role="img"><title>t</title></svg>'
            '</main><footer></footer></body></html>'
        )
        self.assertEqual(problems_in(good, '/x/'), [])

    def test_contrast_helper_matches_known_values(self):
        self.assertAlmostEqual(contrast('#000000', '#ffffff'), 21.0, places=1)
        self.assertAlmostEqual(contrast('#777777', '#ffffff'), 4.48, places=1)


class TabBarContrastTests(TestCase):
    def test_tab_bar_labels_meet_4_5_to_1_in_both_themes(self):
        css = css_text()
        # label colour is --bb-muted (idle) or --bb-red-text (active), on the
        # tab bar's surface; the bar is a 92% mix of --bb-surface.
        self.assertRegex(css, r'\.bb-tabbar a \{[^}]*color: var\(--bb-muted\)')
        self.assertRegex(css, r'\.bb-tabbar a\.is-active \{ color: var\(--bb-red-text\)')
        for theme, tokens in StylesheetTests().themes().items():
            for text in ('--bb-muted', '--bb-red-text'):
                for surface in ('--bb-surface', '--bb-bg'):
                    self.assertGreaterEqual(
                        contrast(tokens[text], tokens[surface]), 4.5,
                        f'{theme}: {text} on {surface}',
                    )
