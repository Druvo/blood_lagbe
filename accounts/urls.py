from django.urls import path
from accounts import views as account_views


urlpatterns = [
    path('signup/', account_views.signup_view, name='signup'),
    path('login/', account_views.login_view, name='login'),
    path('privacy/', account_views.privacy_view, name='privacy'),
    path('logout/', account_views.logout_view, name='logout'),
    # Same path and name as django.contrib.auth.urls, so reverse() is
    # unchanged; this one adds the site name to the email context.
    path(
        'password_reset/', account_views.SitePasswordResetView.as_view(),
        name='password_reset',
    ),
]
