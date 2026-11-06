from django.urls import path

from events import views

app_name = 'events'

urlpatterns = [
    path('', views.event_list, name='list'),
    path('past/', views.event_past, name='past'),
    path('mine/', views.my_events, name='mine'),
    path('new/', views.event_create, name='create'),
    path('campaigns/', views.campaign_list, name='campaigns'),
    path('challenges/', views.challenge_list, name='challenges'),
    path(
        'challenges/<int:pk>/', views.challenge_detail,
        name='challenge-detail',
    ),
    path('<int:pk>/', views.event_detail, name='detail'),
    path('<int:pk>/edit/', views.event_edit, name='edit'),
    path('<int:pk>/cancel/', views.event_cancel, name='cancel'),
    path('<int:pk>/rsvp/', views.event_rsvp, name='rsvp'),
    path('<int:pk>/rsvp/cancel/', views.event_rsvp_cancel, name='rsvp-cancel'),
    path(
        '<int:pk>/checkin/<int:rsvp_pk>/', views.event_checkin,
        name='checkin',
    ),
    path('<int:pk>/squad/', views.event_enter_squad, name='enter-squad'),
    path(
        '<int:pk>/squad/<int:squad_pk>/withdraw/',
        views.event_withdraw_squad, name='withdraw-squad',
    ),
    path('<int:pk>/calendar.ics', views.event_calendar, name='calendar'),
]
