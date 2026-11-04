from django.urls import path

from rewards import directory_views, request_views, views

urlpatterns = [
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    path('requests/', request_views.request_list, name='request-list'),
    path(
        'requests/new/', request_views.request_create, name='request-create',
    ),
    path(
        'requests/mine/', request_views.request_mine, name='request-mine',
    ),
    path(
        'requests/<int:pk>/', request_views.request_detail,
        name='request-detail',
    ),
    path(
        'requests/<int:pk>/respond/', request_views.request_respond,
        name='request-respond',
    ),
    path(
        'requests/<int:pk>/close/', request_views.request_close,
        name='request-close',
    ),
    path(
        'requests/<int:pk>/update/', request_views.request_update,
        name='request-update',
    ),
    path(
        'requests/<int:pk>/report/', request_views.request_report,
        name='request-report',
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
