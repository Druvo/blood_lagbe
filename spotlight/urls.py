from django.urls import path

from spotlight import views

app_name = 'spotlight'

urlpatterns = [
    path('', views.home, name='home'),
    path('pulse.json', views.pulse_json, name='pulse'),
    path('style/', views.my_style, name='style'),
    path('style/card/<int:user_id>/', views.style_card, name='style-card'),
    path('certificate/<int:donation_id>/', views.certificate,
         name='certificate'),
    path('ready.ics', views.ready_ics, name='ready-ics'),
    path('<int:year>/<int:month>/', views.month, name='month'),
]
