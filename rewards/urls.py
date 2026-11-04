from django.urls import path

from rewards import hub_views, views

urlpatterns = [
    path('log/', views.log_donation, name='log-donation'),
    path('share/<int:user_id>/', views.badge_card, name='badge-card'),
    path('export.csv', hub_views.export_csv, name='donation-export'),
    path('history/', hub_views.history, name='donation-history'),
    path('card/', hub_views.donor_card, name='donor-card'),
    path(
        '<int:pk>/delete/', hub_views.delete_donation,
        name='donation-delete',
    ),
]
