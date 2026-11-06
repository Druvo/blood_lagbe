from django.urls import path

from alerts import views

app_name = 'alerts'

urlpatterns = [
    path('', views.inbox, name='inbox'),
    path('read-all/', views.mark_all, name='read-all'),
    path('settings/', views.settings_view, name='settings'),
    path('email-off/<str:token>/', views.email_off, name='email-off'),
    path('<int:pk>/read/', views.mark_read, name='read'),
]
