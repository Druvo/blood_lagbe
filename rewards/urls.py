from django.urls import path

from rewards import views

urlpatterns = [
    path('log/', views.log_donation, name='log-donation'),
    path('share/<int:user_id>/', views.badge_card, name='badge-card'),
]
