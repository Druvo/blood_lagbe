from django.urls import path

from growth import views

app_name = 'growth'

# Mounted at the site root (last), so every route has its own prefix.
urlpatterns = [
    path('invite/', views.invite_home, name='invite-home'),
    path('invite/<str:code>/', views.invite_landing, name='invite'),
    path('u/<int:user_id>/', views.public_profile, name='profile'),
]
