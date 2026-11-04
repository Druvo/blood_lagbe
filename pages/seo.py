"""robots.txt, sitemap, web app manifest, service worker and offline page."""
from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.http import require_GET

from blogapp.models.post import Post


class StaticViewSitemap(Sitemap):
    """Public pages that do not change often."""

    changefreq = 'weekly'
    NAMES = [
        'home', 'learn', 'faq', 'about', 'impact', 'stories',
        'request-list', 'leaderboard', 'blogapp:blog-view',
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


SITEMAPS = {'pages': StaticViewSitemap, 'blog': PostSitemap}

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
