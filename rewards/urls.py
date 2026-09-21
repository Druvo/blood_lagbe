from django.urls import path

from rewards import views

urlpatterns = [
    path('log/', views.log_donation, name='log-donation'),
]
