from django.urls import path
from accounts import views as account_views


urlpatterns = [
    path('signup/', account_views.signup_view, name='signup'),
    path('login/', account_views.login_view, name='login'),
    path('logout/', account_views.logout_view, name='logout'),
]
