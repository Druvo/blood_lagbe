from django.urls import path

from rewards import directory_views, views

urlpatterns = [
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    path('requests/', views.request_list, name='request-list'),
    path('requests/new/', views.request_create, name='request-create'),
    path('requests/<int:pk>/', views.request_detail, name='request-detail'),
    path(
        'requests/<int:pk>/respond/', views.request_respond,
        name='request-respond',
    ),
    path(
        'requests/<int:pk>/close/', views.request_close,
        name='request-close',
    ),
    path('donors/', directory_views.donor_directory, name='donor-directory'),
    path(
        'donors/inbox/', directory_views.contact_inbox, name='contact-inbox',
    ),
    path(
        'donors/<int:user_id>/contact/', directory_views.contact_donor,
        name='contact-donor',
    ),
    path(
        'donors/contact/<int:pk>/respond/', directory_views.contact_respond,
        name='contact-respond',
    ),
]
