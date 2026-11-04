from django.urls import path

from rewards import views

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
]
