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
from django.templatetags.static import static as static_url
from django.urls import path, include
from django.views.generic.base import RedirectView
from blogapp.views import postView


urlpatterns = [
    path('admin/', admin.site.urls),

    path(
        'favicon.ico',
        RedirectView.as_view(
            url=static_url('images/logo/blood_lagbe_logo.png'),
            permanent=True,
        ),
    ),

    # pages url ----
    path('', include("pages.urls"), name='index'),
    path('accounts/', include("accounts.urls"), name='accounts'),

    path('blog/', include("blogapp.urls")),
    path('post/<id>/', postView, name='post-view'),
    path('donations/', include('rewards.urls')),

    # auth urls -----
    path('accounts/', include('django.contrib.auth.urls')),
]

# just development environment
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )


# admin site title change
admin.sites.AdminSite.site_header = "Blood Lagbe Administration"
admin.sites.AdminSite.site_title = "Blood Lagbe Administration"
admin.sites.AdminSite.index_title = "Blood Lagbe Admin Panel"
