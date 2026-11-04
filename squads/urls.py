from django.urls import path

from squads import views

app_name = 'squads'

urlpatterns = [
    path('', views.squad_list, name='list'),
    path('new/', views.squad_create, name='create'),
    path('leaderboard/', views.squad_leaderboard, name='leaderboard'),
    path('join/', views.join_with_code, name='join'),
    path('join/<str:code>/', views.join_landing, name='join-code'),
    path('<int:pk>/', views.squad_detail, name='detail'),
    path('<int:pk>/edit/', views.squad_edit, name='edit'),
    path('<int:pk>/join/', views.join_public, name='join-public'),
    path('<int:pk>/leave/', views.squad_leave, name='leave'),
    path('<int:pk>/delete/', views.squad_delete, name='delete'),
    path('<int:pk>/new-code/', views.squad_new_code, name='new-code'),
    path('<int:pk>/kick/<int:user_id>/', views.squad_kick, name='kick'),
]
