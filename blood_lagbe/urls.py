"""blood_lagbe URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blogapp/', include('blogapp.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.templatetags.static import static as static_url
from django.urls import path, include
from django.views.generic.base import RedirectView
from accounts.views import welcome_view
from blogapp.views import postView
from pages import seo


urlpatterns = [
    path('admin/', admin.site.urls),

    path(
        'favicon.ico',
        RedirectView.as_view(
            url=static_url('images/brand/bloodbond-mark.svg'),
            permanent=True,
        ),
    ),

    # SEO / PWA / ops ----
    path('robots.txt', seo.robots_txt, name='robots'),
    path(
        'sitemap.xml', sitemap, {'sitemaps': seo.SITEMAPS},
        name='sitemap',
    ),
    path('manifest.webmanifest', seo.manifest, name='manifest'),
    path('sw.js', seo.service_worker, name='service-worker'),

    # pages url ----
    path('', include("pages.urls"), name='index'),
    path('accounts/', include("accounts.urls"), name='accounts'),
    path('welcome/', welcome_view, name='welcome'),

    path('blog/', include("blogapp.urls")),
    path('post/<id>/', postView, name='post-view'),
    path('donations/', include('rewards.urls')),
    path('', include('rewards.public_urls')),
    path('squads/', include('squads.urls')),
    path('impact/', include('insights.urls')),
    path('stories/', include('stories.urls')),
    path('moments/', include('moments.urls')),
    path('today/', include('habits.urls')),
    path('events/', include('events.urls')),
    path('alerts/', include('alerts.urls')),
    # growth owns a few root-level paths (invite/, u/, ...), so it goes last
    path('', include('growth.urls')),

    # auth urls -----
    path('accounts/', include('django.contrib.auth.urls')),
]

# just development environment
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )


# Error pages: Django's default handlers render templates/404.html, 403.html
# and 500.html (the 500 one is standalone on purpose).


# admin site title change: follows the configured site name
admin.sites.AdminSite.site_header = f"{settings.SITE_NAME} Administration"
admin.sites.AdminSite.site_title = f"{settings.SITE_NAME} Administration"
admin.sites.AdminSite.index_title = f"{settings.SITE_NAME} Admin Panel"
