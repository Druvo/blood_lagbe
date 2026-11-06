"""robots.txt, sitemap, web app manifest, service worker and offline page."""
from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.http import require_GET

from blogapp.models.post import Post
from events.models import Challenge, Event


class StaticViewSitemap(Sitemap):
    """Public pages that do not change often."""

    changefreq = 'weekly'
    NAMES = [
        'home', 'learn', 'faq', 'about', 'impact', 'stories',
        'request-list', 'leaderboard', 'blogapp:blog-view',
        'events:list', 'events:campaigns', 'events:challenges',
        'spotlight:home',
        'atlas:map', 'atlas:gap', 'atlas:divisions',
    ]

    def items(self):
        return self.NAMES

    def location(self, name):
        return reverse(name)

    def priority(self, name):
        return {'home': 1.0, 'request-list': 0.9, 'learn': 0.8}.get(name, 0.6)


class PostSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return Post.objects.order_by('-timestamp')

    def lastmod(self, post):
        return post.timestamp


class EventSitemap(Sitemap):
    """Approved upcoming drives only: pending and cancelled ones are not
    public."""

    changefreq = 'daily'
    priority = 0.6

    def items(self):
        return Event.objects.filter(
            status=Event.Status.APPROVED, starts_at__gte=timezone.now(),
        ).order_by('starts_at')

    def location(self, event):
        return reverse('events:detail', args=[event.pk])

    def lastmod(self, event):
        return event.updated_at


class ChallengeSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.4

    def items(self):
        return Challenge.objects.filter(is_published=True).order_by('-starts_on')

    def location(self, challenge):
        return reverse('events:challenge-detail', args=[challenge.pk])


SITEMAPS = {
    'pages': StaticViewSitemap, 'blog': PostSitemap,
    'events': EventSitemap, 'challenges': ChallengeSitemap,
}

# Private or personal areas: not for search engines. (robots.txt is a
# courtesy, not access control; these pages also require login.)
ROBOTS_DISALLOW = [
    '/admin/',
    '/accounts/',
    '/welcome/',
    '/donations/',
    '/donors/',
    '/requests/new/',
    '/requests/*/respond/',
    '/requests/*/close/',
    '/alerts/',
    '/today/',
    '/moments/',
]


@require_GET
def robots_txt(request):
    lines = ['User-agent: *']
    lines += [f'Disallow: {path}' for path in ROBOTS_DISALLOW]
    lines += [
        'Allow: /',
        '',
        f'Sitemap: {request.build_absolute_uri(reverse("sitemap"))}',
        '',
    ]
    return HttpResponse('\n'.join(lines), content_type='text/plain')


@require_GET
@cache_control(max_age=3600)
def manifest(request):
    icons = [
        {'src': static('images/brand/icon-192.png'), 'sizes': '192x192',
         'type': 'image/png', 'purpose': 'any'},
        {'src': static('images/brand/icon-512.png'), 'sizes': '512x512',
         'type': 'image/png', 'purpose': 'any'},
        {'src': static('images/brand/icon-maskable-512.png'),
         'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        {'src': static('images/brand/bloodbond-mark.svg'), 'sizes': 'any',
         'type': 'image/svg+xml', 'purpose': 'any'},
    ]
    data = {
        'name': settings.SITE_NAME,
        'short_name': settings.SITE_NAME[:12],
        'description': settings.SITE_TAGLINE,
        'id': '/',
        'start_url': reverse('home'),
        'scope': '/',
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#fff9f7',
        'theme_color': '#d8213a',
        'lang': 'en',
        'icons': icons,
        'shortcuts': [
            {'name': 'Urgent requests', 'url': reverse('request-list')},
            {'name': 'Find donors', 'url': reverse('donor-directory')},
        ],
    }
    return JsonResponse(data, content_type='application/manifest+json')


@require_GET
@never_cache
def service_worker(request):
    """Served from the site root so its scope covers every page."""
    response = render(
        request, 'pages/sw.js', {}, content_type='application/javascript',
    )
    response['Service-Worker-Allowed'] = '/'
    return response


@require_GET
def offline(request):
    return render(request, 'pages/offline.html')
